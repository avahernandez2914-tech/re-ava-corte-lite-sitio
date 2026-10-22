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
STEM_FILES = {
    "Voz principal": "01 Voz principal.wav", "Coros": "02 Coros.wav", "Bateria": "03 Bateria.wav",
    "Bajo": "04 Bajo.wav", "Otros": "05 Otros (teclados, guitarras, sintes).wav",
    "Instrumental": "06 Instrumental (sin voz).wav", "Voz completa": "07 Voz completa.wav",
}
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


MAJOR_TRIADS = [(0, ""), (2, "m"), (4, "m"), (5, ""), (7, ""), (9, "m")]
MINOR_TRIADS = [(0, "m"), (3, ""), (5, "m"), (7, "m"), (7, ""), (8, ""), (10, "")]  # incl. harmonic V


def key_from_chords(chords: list, profile_key: tuple[int, str]) -> tuple[int, str]:
    """Key whose diatonic triads cover the most chord time; ties go to the tonic chord heard most."""
    found = [c for c in chords if c is not None]
    if not found:
        return profile_key
    counts = Counter(found)
    best = None
    for tonic in range(12):
        for scale, triads in (("major", MAJOR_TRIADS), ("minor", MINOR_TRIADS)):
            diatonic = {((tonic + r) % 12, q) for r, q in triads}
            coverage = sum(n for c, n in counts.items() if c in diatonic)
            tonic_chord = counts[(tonic, "" if scale == "major" else "m")]
            score = (coverage, tonic_chord, (tonic, scale) == profile_key)
            if best is None or score > best[0]:
                best = (score, (tonic, scale))
    return best[1]


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
    """One chord per ``beats_per_chord`` beats (window starts at beat_frames[0]), from harmonic + bass chroma."""
    import librosa

    ch = librosa.feature.chroma_cqt(y=harm, sr=SR, hop_length=hop)
    cb = librosa.feature.chroma_cqt(y=bass, sr=SR, hop_length=hop, fmin=librosa.note_to_hz("C1"), n_octaves=3)
    n = min(ch.shape[1], cb.shape[1])
    combined = ch[:, :n] + 0.6 * cb[:, :n]
    sync = librosa.util.sync(combined, beat_frames, aggregate=np.median, pad=False)
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


def find_downbeat(per_beat: list) -> int:
    """Beat offset (0-3) where chord changes line up with bar starts."""
    changes = [i for i in range(1, len(per_beat)) if per_beat[i] != per_beat[i - 1] and per_beat[i] is not None]
    if not changes:
        return 0
    return max(range(4), key=lambda o: (sum((i - o) % 4 == 0 for i in changes), sum((i - o) % 2 == 0 for i in changes)))


def estimate_period(y: np.ndarray, hop: int = 128) -> float:
    """Beat period in seconds from a comb of onset autocorrelations (more robust than beat_track on
    syncopated/ternary grooves, where it locks onto 2/3 or 3/2 of the real beat)."""
    import librosa

    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=hop)
    env = (env - env.mean()) / (env.std() or 1.0)

    def ac(sec):
        lag = int(round(sec * SR / hop))
        return float((env[:-lag] * env[lag:]).mean()) if 0 < lag < len(env) else 0.0

    def comb(period, span=8.0):
        # Same time span for every candidate so short periods get no advantage from extra multiples.
        ks = range(1, int(span / period) + 1)
        return sum(ac(k * period) for k in ks) / len(ks)

    coarse = np.arange(60 / 200, 60 / 50, 0.004)
    best = max(coarse, key=comb)
    fine = np.arange(best - 0.006, best + 0.006, 0.0002)
    return float(max(fine, key=comb))


def kick_phase(drums_mono: np.ndarray, beat_sec: float) -> float:
    """Offset (s) of the beat grid that best lines up with kick onsets."""
    import librosa
    from scipy.signal import butter, sosfiltfilt

    low = sosfiltfilt(butter(4, 120, btype="lowpass", fs=SR, output="sos"), drums_mono)
    env = librosa.onset.onset_strength(y=low, sr=SR, hop_length=128)
    duration = len(drums_mono) / SR

    def score(phase):
        frames = librosa.time_to_frames(np.arange(phase, duration, beat_sec), sr=SR, hop_length=128)
        return env[np.clip(frames, 0, len(env) - 1)].sum()

    return float(max(np.arange(0, beat_sec, beat_sec / 64), key=score))


def chord_segments(per_beat: list, min_beats: int = 2) -> list[list]:
    """Merge per-beat chords into [chord, start_beat_index, beats] segments, absorbing short blips."""
    segs = []
    for i, c in enumerate(per_beat):
        if segs and segs[-1][0] == c:
            segs[-1][2] += 1
        else:
            segs.append([c, i, 1])
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for j, (c, start, n) in enumerate(segs):
            if n >= min_beats and c is not None:
                continue
            # Give the blip to the longer neighbour.
            prev_n = segs[j - 1][2] if j > 0 else -1
            next_n = segs[j + 1][2] if j + 1 < len(segs) else -1
            if c is None and n >= min_beats:
                continue  # keep real silences
            k = j - 1 if prev_n >= next_n else j + 1
            if k < j:
                segs[k][2] += n
            else:
                segs[k][1], segs[k][2] = start, segs[k][2] + n
            del segs[j]
            changed = True
            break
        # merge neighbours that became equal
        merged = []
        for seg in segs:
            if merged and merged[-1][0] == seg[0]:
                merged[-1][2] += seg[2]
            else:
                merged.append(seg)
        segs = merged
    return segs


def main_loop(bars: list[str], size: int = 4) -> list[str]:
    """Most repeated sequence of ``size`` consecutive chord changes in the bar-by-bar progression."""
    names = [b for i, b in enumerate(bars) if b != "N" and (i == 0 or b != bars[i - 1])]
    grams = Counter(tuple(names[i:i + size]) for i in range(len(names) - size + 1))
    return list(grams.most_common(1)[0][0]) if grams else names


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
    ap.add_argument("--reanalizar", action="store_true",
                    help="Rehace solo el análisis y el MIDI usando los stems ya separados")
    args = ap.parse_args()

    song = args.song.resolve()
    out = (args.out or song.with_suffix("")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.reanalizar:
        stems = {name: out / "stems" / f for name, f in STEM_FILES.items() if (out / "stems" / f).exists()}
        if "Bateria" not in stems:
            sys.exit(f"No hay stems en {out / 'stems'}; ejecuta sin --reanalizar")
        mix = read(stems["Instrumental"]) + read(stems["Voz completa"])
        analyze(out, song, stems, mix, args.bpm)
        return
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
    audio = {"Voz principal": lead, "Coros": backing, "Bateria": drums, "Bajo": bass, "Otros": other,
             "Instrumental": instrumental, "Voz completa": vocals}
    stems = {name: write(stems_dir / STEM_FILES[name], data) for name, data in audio.items() if data is not None}
    analyze(out, song, stems, mix, args.bpm)
    shutil.rmtree(tmp, ignore_errors=True)


def analyze(out: Path, song: Path, stems: dict[str, Path], mix: np.ndarray, forced_bpm: float | None) -> None:
    log("4/4 Analizando tempo, tonalidad y acordes, y transcribiendo a MIDI")
    import librosa

    length = len(mix)
    drums = read(stems["Bateria"], length)
    bass = read(stems["Bajo"], length)
    other = read(stems["Otros"], length)
    hop = 512
    drums_mono = drums.mean(axis=1)
    source = drums_mono if np.abs(drums_mono).max() > 0.01 else mix.mean(axis=1)
    bpm = float(forced_bpm or 60.0 / estimate_period(source))
    if not forced_bpm:
        while bpm < 70:
            bpm *= 2
        while bpm > 180:
            bpm /= 2
        # Most productions use whole-number tempos.
        bpm = float(round(bpm)) if abs(bpm - round(bpm)) < 0.75 else round(bpm, 2)
    beat_sec = 60.0 / bpm
    phase = kick_phase(source, beat_sec)

    other_mono = other.mean(axis=1)
    bass_mono = bass.mean(axis=1)
    chroma = librosa.feature.chroma_cqt(y=other_mono + bass_mono, sr=SR, hop_length=hop)
    profile_key = detect_key(chroma.sum(axis=1))

    # Chords beat by beat (grid aligned to the kick), merged into segments at the real changes.
    beat_grid = np.arange(phase, length / SR, beat_sec)
    per_beat = detect_chords(other_mono, bass_mono, librosa.time_to_frames(beat_grid, sr=SR, hop_length=hop),
                             hop, beats_per_chord=1)
    segs = chord_segments(per_beat)
    offset = find_downbeat(per_beat)
    bar_start = phase + offset * beat_sec  # first downbeat, in seconds
    while bar_start - 4 * beat_sec >= 0:
        bar_start -= 4 * beat_sec
    tonic, scale = key_from_chords([c for c, _, n in segs for _ in range(n)], profile_key)

    phase_beats = phase / beat_sec
    start_beats = bar_start / beat_sec
    chord_notes, prev, timeline = [], None, []
    for c, i, n in segs:
        if c is None:
            continue
        start = round(phase_beats + i, 3)
        timeline.append({"chord": chord_symbol(c), "start_beat": start, "beats": n,
                         "bar": round((start - start_beats) / 4 + 1, 2), "seconds": round(start * beat_sec, 2)})
        ch = Chord(c[0], (0, 3, 7) if c[1] == "m" else (0, 4, 7), chord_symbol(c))
        prev = voice_chord(ch, 62, prev)
        for p in prev:
            chord_notes.append({"pitch": p, "start": start, "duration": float(n), "velocity": 90})

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

    def chord_at(beat):
        for seg in timeline:
            if seg["start_beat"] <= beat < seg["start_beat"] + seg["beats"]:
                return seg["chord"]
        return "N"

    n_bars = int((length / SR - bar_start) / (4 * beat_sec)) + 1
    progression = [chord_at(start_beats + 4 * k + 0.5) for k in range(n_bars)]
    beats_per_chord = Counter()
    for seg in timeline:
        beats_per_chord[seg["chord"]] += seg["beats"]
    common = [f"{name} ({count // 4} compases)" for name, count in beats_per_chord.most_common(8)]
    analysis = {
        "source": song.name,
        "duration_seconds": round(length / SR, 2),
        "bpm": bpm,
        "first_downbeat_seconds": round(bar_start, 3),
        "first_downbeat_beats": round(start_beats, 3),
        "key": f"{NOTE_NAMES[tonic]} {scale}",
        "main_loop": main_loop(progression),
        "chords_per_bar": progression,
        "chord_timeline": timeline,
        "most_common_chords": common,
        "stems": {k: str(v.relative_to(out)) for k, v in stems.items()},
        "midi": {k: f"midi/{k}.mid" for k in parts},
        "parts": parts,
    }
    (out / "song.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=1))

    log(f"Listo en {out}")
    log(f"BPM {bpm} · Tonalidad {analysis['key']} · loop principal: {' - '.join(analysis['main_loop'])}")
    log(f"Acordes más usados: {', '.join(common[:6])}")


if __name__ == "__main__":
    main()
