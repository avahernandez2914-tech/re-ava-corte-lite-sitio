"""Separa una canción en stems, la analiza y la convierte a MIDI para Ableton.

    python scripts/separar_cancion.py cancion.mp3 --out salida/

Genera en la carpeta de salida:
    stems/        voz principal, coros, voz completa, batería, bajo, otros (teclados/guitarras/sintes),
                  instrumental (todo menos la voz)
    midi/         bajo, acordes, melodía de la voz, otros instrumentos y batería (.mid)
    song.json     BPM, tonalidad, acordes por compás y notas de cada parte (lo usa la herramienta
                  MCP import_song para montarlo en Ableton)

Requisitos: pip install -r scripts/requirements-separacion.txt  (y ffmpeg instalado).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from ava_live_mcp.theory import NOTE_NAMES, Chord, voice_chord  # noqa: E402

SR = 44100
MODELS = {
    "vocals": "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
    "karaoke": "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt",
    "drums": "kuielab_a_drums.onnx",
    "bass": "kuielab_a_bass.onnx",
}


def log(msg: str) -> None:
    print(f"[separar] {msg}", flush=True)


# ---------------------------------------------------------------------------
# separation

def separate(model: str, wav: Path, out_dir: Path, model_dir: Path) -> dict[str, Path]:
    """Run one audio-separator model; returns {stem name (lowercase): file}."""
    from audio_separator.separator import Separator

    work = out_dir / ("tmp_" + Path(model).stem)
    work.mkdir(parents=True, exist_ok=True)
    sep = Separator(output_dir=str(work), model_file_dir=str(model_dir), output_format="WAV",
                    log_level=40)
    sep.load_model(model_filename=model)
    files = sep.separate(str(wav))
    result = {}
    for f in files:
        p = work / Path(f).name
        stem = p.name.split("_(", 1)[1].split(")", 1)[0].lower()
        result[stem] = p
    return result


def read(path: Path, length: int | None = None) -> np.ndarray:
    data, sr = sf.read(str(path), always_2d=True, dtype="float32")
    assert sr == SR, f"{path} tiene {sr} Hz"
    if length is not None:
        if len(data) < length:
            data = np.pad(data, ((0, length - len(data)), (0, 0)))
        data = data[:length]
    return data


def write(path: Path, data: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), data, SR, subtype="PCM_24")
    return path


# ---------------------------------------------------------------------------
# analysis

MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def detect_key(chroma_sum: np.ndarray) -> tuple[int, str]:
    best = (-2.0, 0, "major")
    for tonic in range(12):
        for scale, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            r = np.corrcoef(chroma_sum, np.roll(profile, tonic))[0, 1]
            if r > best[0]:
                best = (r, tonic, scale)
    return best[1], best[2]


def resolve_relative(tonic: int, scale: str, chords: list) -> tuple[int, str]:
    """Pick between a major key and its relative minor using which tonic chord dominates."""
    major = tonic if scale == "major" else (tonic + 3) % 12
    minor = (major + 9) % 12
    found = [c for c in chords if c is not None]
    counts = Counter(found)
    n_major, n_minor = counts[(major, "")], counts[(minor, "m")]
    if n_major == n_minor:
        for c in found:  # tie: the first tonic chord heard wins
            if c in ((major, ""), (minor, "m")):
                return (major, "major") if c == (major, "") else (minor, "minor")
        return tonic, scale
    return (major, "major") if n_major > n_minor else (minor, "minor")


def _templates():
    names, mats = [], []
    for root in range(12):
        for quality, ivs in (("", (0, 4, 7)), ("m", (0, 3, 7))):
            t = np.zeros(12)
            for i in ivs:
                t[(root + i) % 12] = 1
            t[root] += 0.5
            names.append((root, quality))
            mats.append(t / np.linalg.norm(t))
    return names, np.array(mats)


def detect_chords(harm: np.ndarray, bass: np.ndarray, beat_frames: np.ndarray, hop: int,
                  beats_per_chord: int = 2) -> list[tuple[int, str] | None]:
    """One chord per ``beats_per_chord`` beats, from harmonic + bass chroma."""
    import librosa

    ch = librosa.feature.chroma_cqt(y=harm, sr=SR, hop_length=hop)
    cb = librosa.feature.chroma_cqt(y=bass, sr=SR, hop_length=hop, fmin=librosa.note_to_hz("C1"), n_octaves=3)
    n = min(ch.shape[1], cb.shape[1])
    combined = ch[:, :n] + 0.6 * cb[:, :n]
    sync = librosa.util.sync(combined, beat_frames, aggregate=np.median)
    names, mats = _templates()
    chords = []
    for start in range(0, sync.shape[1], beats_per_chord):
        v = sync[:, start:start + beats_per_chord].mean(axis=1)
        if np.linalg.norm(v) < 1e-3 or v.max() < 0.05:
            chords.append(None)
            continue
        scores = mats @ (v / np.linalg.norm(v))
        chords.append(names[int(np.argmax(scores))])
    # Light smoothing: an isolated chord between two equal ones is probably noise.
    for i in range(1, len(chords) - 1):
        if chords[i - 1] == chords[i + 1] and chords[i] != chords[i - 1]:
            chords[i] = chords[i - 1]
    return chords


def chord_symbol(c) -> str:
    return "N" if c is None else NOTE_NAMES[c[0]] + c[1]


# ---------------------------------------------------------------------------
# transcription

def transcribe(path: Path, bpm: float, low_hz: float | None = None, high_hz: float | None = None,
               monophonic: bool = False, min_ms: float = 90, onset: float = 0.55,
               frame: float = 0.3) -> list[dict]:
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import predict

    onnx = Path(ICASSP_2022_MODEL_PATH)
    _, _, events = predict(str(path), onnx, onset_threshold=onset, frame_threshold=frame,
                           minimum_note_length=min_ms, minimum_frequency=low_hz, maximum_frequency=high_hz)
    beat = 60.0 / bpm
    notes = sorted(({"pitch": int(p), "start": round(s / beat, 4), "duration": round(max(0.06, (e - s) / beat), 4),
                     "velocity": int(max(30, min(127, a * 127 * 1.4)))} for s, e, p, a, *_ in events),
                   key=lambda n: (n["start"], -n["velocity"]))
    if monophonic:
        mono = []
        for n in notes:
            if mono and n["start"] < mono[-1]["start"] + mono[-1]["duration"] - 0.05:
                if n["velocity"] > mono[-1]["velocity"] and abs(n["start"] - mono[-1]["start"]) < 0.1:
                    mono[-1] = n
                continue
            mono.append(n)
        notes = mono
    return notes


def drum_notes(drums: np.ndarray, bpm: float) -> list[dict]:
    """Kick / snare / hat hits from band-limited onset detection."""
    import librosa
    from scipy.signal import butter, sosfiltfilt

    mono = drums.mean(axis=1)
    beat = 60.0 / bpm
    bands = {36: ("lowpass", 120), 38: ("bandpass", (180, 2500)), 42: ("highpass", 6000)}
    notes = []
    for pitch, (kind, freq) in bands.items():
        sos = butter(4, freq, btype=kind, fs=SR, output="sos")
        y = sosfiltfilt(sos, mono)
        env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=256)
        onsets = librosa.onset.onset_detect(onset_envelope=env, sr=SR, hop_length=256, units="time",
                                            backtrack=False, delta={36: 0.25, 38: 0.35, 42: 0.15}[pitch])
        peak = np.percentile(env, 99) or 1.0
        frames = librosa.time_to_frames(onsets, sr=SR, hop_length=256)
        for t, f in zip(onsets, frames):
            strength = env[min(f, len(env) - 1)] / peak
            if strength < 0.12:
                continue
            notes.append({"pitch": pitch, "start": round(t / beat, 4), "duration": 0.25,
                          "velocity": int(max(40, min(127, 60 + strength * 67)))})
    return sorted(notes, key=lambda n: n["start"])


def write_midi(path: Path, notes: list[dict], bpm: float, drums: bool = False) -> None:
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    inst = pretty_midi.Instrument(program=0, is_drum=drums)
    beat = 60.0 / bpm
    for n in notes:
        inst.notes.append(pretty_midi.Note(velocity=n["velocity"], pitch=n["pitch"], start=n["start"] * beat,
                                           end=(n["start"] + n["duration"]) * beat))
    pm.instruments.append(inst)
    path.parent.mkdir(parents=True, exist_ok=True)
    pm.write(str(path))


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("song", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--models", type=Path, default=Path.home() / ".cache" / "ava-live-mcp" / "models")
    ap.add_argument("--bpm", type=float, default=None, help="Fuerza el BPM si la detección falla")
    ap.add_argument("--no-backing", action="store_true", help="No separar voz principal de coros")
    args = ap.parse_args()

    song = args.song.resolve()
    out = (args.out or song.with_suffix("")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    args.models.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="separar_", dir=out))

    log("Convirtiendo a WAV 44.1 kHz estéreo")
    wav = tmp / "mix.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(song), "-ac", "2", "-ar", str(SR), str(wav)],
                   check=True)
    mix = read(wav)
    length = len(mix)

    log("1/4 Separando voz e instrumental (BS-Roformer)")
    vi = separate(MODELS["vocals"], wav, tmp, args.models)
    vocals = read(vi["vocals"], length)
    instrumental = read(vi["instrumental"], length)

    lead, backing = vocals, None
    if not args.no_backing:
        log("2/4 Separando voz principal y coros")
        lb = separate(MODELS["karaoke"], vi["vocals"], tmp, args.models)
        lead = read(lb["vocals"], length)
        backing = vocals - lead

    log("3/4 Separando batería y bajo")
    inst_wav = write(tmp / "instrumental.wav", instrumental)
    drums = read(separate(MODELS["drums"], inst_wav, tmp, args.models)["drums"], length)
    bass = read(separate(MODELS["bass"], inst_wav, tmp, args.models)["bass"], length)
    other = instrumental - drums - bass

    stems_dir = out / "stems"
    stems = {
        "Voz principal": write(stems_dir / "01 Voz principal.wav", lead),
        "Coros": write(stems_dir / "02 Coros.wav", backing) if backing is not None else None,
        "Bateria": write(stems_dir / "03 Bateria.wav", drums),
        "Bajo": write(stems_dir / "04 Bajo.wav", bass),
        "Otros": write(stems_dir / "05 Otros (teclados, guitarras, sintes).wav", other),
        "Instrumental": write(stems_dir / "06 Instrumental (sin voz).wav", instrumental),
        "Voz completa": write(stems_dir / "07 Voz completa.wav", vocals),
    }
    stems = {k: v for k, v in stems.items() if v is not None}

    log("4/4 Analizando tempo, tonalidad y acordes, y transcribiendo a MIDI")
    import librosa

    hop = 512
    drums_mono = drums.mean(axis=1)
    tempo, beat_frames = librosa.beat.beat_track(y=drums_mono if np.abs(drums_mono).max() > 0.01
                                                 else mix.mean(axis=1), sr=SR, hop_length=hop)
    bpm = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=SR, hop_length=hop)
    if len(beat_times) > 8:  # the average beat period is more precise than the tempogram estimate
        bpm = 60.0 / float(np.polyfit(np.arange(len(beat_times)), beat_times, 1)[0])
    bpm = float(args.bpm or bpm)
    if not args.bpm:
        while bpm < 70:
            bpm *= 2
        while bpm > 180:
            bpm /= 2
        # Most productions use whole-number tempos.
        bpm = float(round(bpm)) if abs(bpm - round(bpm)) < 0.75 else round(bpm, 2)
    beat_frames = librosa.util.fix_frames(beat_frames, x_min=0)
    first_beat = float(librosa.frames_to_time(beat_frames[1] if len(beat_frames) > 1 else 0, sr=SR,
                                              hop_length=hop))

    other_mono = other.mean(axis=1)
    bass_mono = bass.mean(axis=1)
    chroma = librosa.feature.chroma_cqt(y=other_mono + bass_mono, sr=SR, hop_length=hop)
    tonic, scale = detect_key(chroma.sum(axis=1))

    # Chords on a fixed grid from the detected tempo so they line up with the MIDI.
    beat_sec = 60.0 / bpm
    grid = librosa.time_to_frames(np.arange(0, length / SR, beat_sec), sr=SR, hop_length=hop)
    chords = detect_chords(other_mono, bass_mono, grid, hop, beats_per_chord=2)

    tonic, scale = resolve_relative(tonic, scale, chords)

    chord_notes, prev = [], None
    for i, c in enumerate(chords):
        if c is None:
            continue
        ch = Chord(c[0], (0, 3, 7) if c[1] == "m" else (0, 4, 7), chord_symbol(c))
        prev = voice_chord(ch, 62, prev)
        for p in prev:
            chord_notes.append({"pitch": p, "start": i * 2.0, "duration": 2.0, "velocity": 90})

    parts = {
        "bass": transcribe(stems["Bajo"], bpm, low_hz=30, high_hz=400, monophonic=True, onset=0.4, frame=0.2),
        "melody": transcribe(stems["Voz principal"], bpm, low_hz=80, high_hz=1100, monophonic=True, min_ms=110),
        "other": transcribe(stems["Otros"], bpm, low_hz=60, high_hz=2500),
        "chords": chord_notes,
        "drums": drum_notes(drums, bpm),
    }
    midi_dir = out / "midi"
    for name, notes in parts.items():
        write_midi(midi_dir / f"{name}.mid", notes, bpm, drums=name == "drums")

    bars = [chord_symbol(c) for c in chords]
    progression = [" ".join(bars[i:i + 2]) for i in range(0, len(bars), 2)]  # 2 chords per 4/4 bar
    common = [f"{name} ({count})" for name, count in Counter(b for b in bars if b != "N").most_common(8)]
    analysis = {
        "source": song.name,
        "duration_seconds": round(length / SR, 2),
        "bpm": bpm,
        "first_beat_seconds": round(first_beat, 3),
        "key": f"{NOTE_NAMES[tonic]} {scale}",
        "chords_per_bar": progression,
        "most_common_chords": common,
        "stems": {k: str(v.relative_to(out)) for k, v in stems.items()},
        "midi": {k: f"midi/{k}.mid" for k in parts},
        "parts": parts,
    }
    (out / "song.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=1))
    shutil.rmtree(tmp, ignore_errors=True)

    log(f"Listo en {out}")
    log(f"BPM {bpm} · Tonalidad {analysis['key']} · acordes más usados: {', '.join(common[:6])}")


if __name__ == "__main__":
    main()
