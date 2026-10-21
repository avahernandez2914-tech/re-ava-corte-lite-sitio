"""Note generators (drums, bass, chords, melody) and full-song planning.

Everything here is pure Python and returns notes as dicts:
    {"pitch": int, "start": float, "duration": float, "velocity": int}
with times in beats. Nothing talks to Live; the MCP server writes the result.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .genres import DRUM_NOTES, Genre, get_genre
from .theory import Chord, Key, chord_bass_pitch_class, parse_progression, voice_chord

STEP = 0.25  # one 16th note in beats
STEPS_PER_BAR = 16
BEATS_PER_BAR = 4.0

Note = dict


def _note(pitch, start, duration, velocity):
    return {"pitch": int(pitch), "start": round(float(start), 4), "duration": round(float(duration), 4),
            "velocity": int(max(1, min(127, round(velocity))))}


def _bar_slice(pattern: str, bar: int) -> str:
    bars = max(1, len(pattern) // STEPS_PER_BAR)
    i = bar % bars
    return pattern[i * STEPS_PER_BAR:(i + 1) * STEPS_PER_BAR].ljust(STEPS_PER_BAR, ".")


def _chord_at_bar(chords: list[Chord], bar: int, bars_per_chord: float) -> Chord:
    return chords[int(bar / bars_per_chord) % len(chords)]


# ----------------------------------------------------------------------------
# groove

def apply_groove(notes: list[Note], swing: float = 0.0, humanize: float = 0.0,
                 seed: int | None = None, skip_timing_pitches: tuple[int, ...] = ()) -> list[Note]:
    """Swing delays off-beat 16ths (0 = straight, 1 = heavy). Humanize jitters velocity and timing (0..1)."""
    rng = random.Random(seed)
    out = []
    for n in notes:
        n = dict(n)
        step = n["start"] / STEP
        if swing and abs(step - round(step)) < 1e-6 and int(round(step)) % 2 == 1:
            n["start"] += swing * STEP / 3
        if humanize:
            n["velocity"] = int(max(1, min(127, n["velocity"] + rng.uniform(-12, 12) * humanize)))
            if n["pitch"] not in skip_timing_pitches:
                n["start"] = max(0.0, n["start"] + rng.uniform(-0.012, 0.012) * humanize)
        n["start"] = round(n["start"], 4)
        out.append(n)
    return out


# ----------------------------------------------------------------------------
# drums

_DRUM_VELOCITY = {"X": 122, "x": 100, "o": 62, "r": 85}


def drum_notes_from_patterns(patterns: dict[str, str], bars: int) -> list[Note]:
    notes = []
    for instrument, pattern in patterns.items():
        pitch = DRUM_NOTES.get(instrument)
        if pitch is None:
            raise ValueError(f"Instrumento de batería desconocido: {instrument}. Usa: {', '.join(DRUM_NOTES)}")
        for bar in range(bars):
            for step, ch in enumerate(_bar_slice(pattern, bar)):
                if ch not in _DRUM_VELOCITY:
                    continue
                start = bar * BEATS_PER_BAR + step * STEP
                if ch == "r":
                    notes.append(_note(pitch, start, STEP / 2, 90))
                    notes.append(_note(pitch, start + STEP / 2, STEP / 2, 75))
                else:
                    notes.append(_note(pitch, start, STEP, _DRUM_VELOCITY[ch]))
    return notes


def drum_variant(base: dict[str, str], variant: str, bars: int) -> dict[str, str]:
    """Derive per-section patterns: full, light, break, build."""
    full_len = bars * STEPS_PER_BAR

    def expand(p):
        return "".join(_bar_slice(p, b) for b in range(bars))

    if variant == "full":
        return {k: expand(v) for k, v in base.items()}
    if variant == "light":
        keep = {"hat", "open_hat", "perc", "perc2", "rim", "pedal_hat", "ride"}
        out = {k: expand(v) for k, v in base.items() if k in keep}
        out["kick"] = ("x" + "." * 15) * bars
        return out
    if variant == "break":
        out = {k: expand(v) for k, v in base.items() if k not in ("kick",)}
        for k in ("snare", "clap"):
            if k in out:
                out[k] = out[k].replace("x", "o").replace("X", "o")
        return out
    if variant == "build":
        snare_key = "snare" if "snare" in base else "clap"
        roll = []
        for bar in range(bars):
            frac = (bar + 1) / bars
            every = 4 if frac <= 0.5 else 2 if frac < 1 else 1
            roll.append("".join("x" if s % every == 0 else "." for s in range(STEPS_PER_BAR)))
        out = {snare_key: "".join(roll), "kick": "x...x...x...x..." * max(0, bars - 1) + "x" + "." * 15}
        if "hat" in base:
            out["hat"] = expand(base["hat"])
        return {k: v[:full_len] for k, v in out.items()}
    raise ValueError("variant debe ser full, light, break o build")


def add_fill(patterns: dict[str, str]) -> dict[str, str]:
    """Snare/tom fill on the last beat of the pattern."""
    out = dict(patterns)
    snare_key = "snare" if "snare" in out else "clap" if "clap" in out else "snare"
    s = list(out.get(snare_key, "." * STEPS_PER_BAR))
    s[-4:] = list("oxxX")
    out[snare_key] = "".join(s)
    if "kick" in out:
        k = list(out["kick"])
        k[-3:] = list("...")
        out["kick"] = "".join(k)
    return out


def generate_drums(genre: Genre, bars: int, variant: str = "full", fill: bool = True,
                   crash: bool = False, swing: float | None = None, humanize: float = 0.3,
                   seed: int | None = None, patterns: dict[str, str] | None = None) -> list[Note]:
    pats = drum_variant(patterns or genre.drums, variant, bars)
    if fill and variant in ("full", "break"):
        pats = add_fill(pats)
    notes = drum_notes_from_patterns(pats, bars)
    if crash:
        notes.append(_note(DRUM_NOTES["crash"], 0, 1.0, 105))
    return apply_groove(notes, genre.swing if swing is None else swing, humanize, seed,
                        skip_timing_pitches=(DRUM_NOTES["kick"],))


# ----------------------------------------------------------------------------
# bass

def _near(pc: int, center: int) -> int:
    n = center + ((pc - center) % 12)
    return n - 12 if n > center + 6 else n


def generate_bass(key: Key, chords: list[Chord], bars: int, pattern: str, center: int = 36,
                  bars_per_chord: float = 1, swing: float = 0.0, humanize: float = 0.2,
                  seed: int | None = None) -> list[Note]:
    notes = []
    for bar in range(bars):
        chord = _chord_at_bar(chords, bar, bars_per_chord)
        nxt = _chord_at_bar(chords, bar + 1, bars_per_chord)
        root = _near(chord_bass_pitch_class(chord), center)
        steps = _bar_slice(pattern, bar)
        for step, ch in enumerate(steps):
            if ch in ".-":
                continue
            length = 1
            while step + length < STEPS_PER_BAR and steps[step + length] == "-":
                length += 1
            iv = chord.intervals
            pitch = {
                "x": root, "o": root + 12, "5": root + 7,
                "3": root + (iv[1] if len(iv) > 1 else 4),
                "7": root + (iv[3] if len(iv) > 3 else 10),
                "a": _near(chord_bass_pitch_class(nxt), center) - 1,
            }.get(ch, root)
            start = bar * BEATS_PER_BAR + step * STEP
            notes.append(_note(pitch, start, length * STEP - 0.02, 110 if step == 0 else 98))
    return apply_groove(notes, swing, humanize, seed)


# ----------------------------------------------------------------------------
# chords

def generate_chords(key: Key, chords: list[Chord], bars: int, pattern: str = "x" + "-" * 15,
                    center: int = 62, bars_per_chord: float = 1, spread: bool = False,
                    arp: str | None = None, arp_rate: int = 2, swing: float = 0.0,
                    humanize: float = 0.2, seed: int | None = None) -> list[Note]:
    """arp: None, 'up', 'down', 'updown' or 'random'. arp_rate in 16ths (2 = 8th notes)."""
    rng = random.Random(seed)
    notes = []
    previous = None
    for bar in range(bars):
        chord = _chord_at_bar(chords, bar, bars_per_chord)
        voicing = voice_chord(chord, center, previous, spread)
        previous = voicing
        bar_start = bar * BEATS_PER_BAR
        if arp:
            seq = sorted(voicing)
            if arp == "down":
                seq = seq[::-1]
            elif arp == "updown" and len(seq) > 2:
                seq = seq + seq[-2:0:-1]
            for i, step in enumerate(range(0, STEPS_PER_BAR, arp_rate)):
                pitch = rng.choice(seq) if arp == "random" else seq[i % len(seq)]
                notes.append(_note(pitch, bar_start + step * STEP, arp_rate * STEP * 0.9, 92 if i % 4 == 0 else 80))
            continue
        steps = _bar_slice(pattern, bar)
        for step, ch in enumerate(steps):
            if ch != "x":
                continue
            length = 1
            while step + length < STEPS_PER_BAR and steps[step + length] == "-":
                length += 1
            for p in voicing:
                notes.append(_note(p, bar_start + step * STEP, length * STEP * 0.95, 96 if step == 0 else 86))
    return apply_groove(notes, swing, humanize, seed)


# ----------------------------------------------------------------------------
# melody

_MOVES = [(-3, 1), (-2, 3), (-1, 6), (0, 2), (1, 6), (2, 3), (3, 1), (4, 1)]


def _weighted_move(rng):
    total = sum(w for _, w in _MOVES)
    r = rng.uniform(0, total)
    for move, w in _MOVES:
        r -= w
        if r <= 0:
            return move
    return 0


def _rhythm(rng, steps: int, density: float) -> list[tuple[int, int]]:
    starts = []
    for s in range(steps):
        if s % 8 == 0:
            p = 0.35 + density * 0.6
        elif s % 4 == 0:
            p = 0.2 + density * 0.5
        elif s % 2 == 0:
            p = density * 0.45
        else:
            p = density * 0.18
        if rng.random() < p:
            starts.append(s)
    # Leave breathing room at the end of the phrase.
    starts = [s for s in starts if s < steps - 2] or [0, 8]
    if len(starts) < 2:
        starts = sorted(set(starts + [0, 8]))
    out = []
    for i, s in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else steps
        out.append((s, max(1, min(nxt - s, 6))))
    return out


def generate_melody(key: Key, chords: list[Chord], bars: int, density: float = 0.5,
                    low: int = 67, high: int = 84, bars_per_chord: float = 1, motif_bars: int = 2,
                    swing: float = 0.0, humanize: float = 0.25, seed: int | None = None) -> list[Note]:
    """Motif-based melody: a rhythm + contour repeated over the progression, re-fitted to each chord."""
    rng = random.Random(seed)
    motif_bars = max(1, min(motif_bars, bars))
    motif_steps = motif_bars * STEPS_PER_BAR
    rhythm = _rhythm(rng, motif_steps, max(0.0, min(1.0, density)))
    contour = [_weighted_move(rng) for _ in rhythm]
    scale = key.scale_notes(low, high)
    if not scale:
        raise ValueError("Rango de melodía vacío")
    center = (low + high) // 2

    def snap_to_chord(idx, chord):
        pcs = set(chord.pitch_classes())
        best = min(range(len(scale)), key=lambda j: (scale[j] % 12 not in pcs, abs(j - idx)))
        return best

    notes = []
    idx = min(range(len(scale)), key=lambda j: abs(scale[j] - center))
    reps = (bars + motif_bars - 1) // motif_bars
    for rep in range(reps):
        vary = rep % 2 == 1
        for i, ((step, length), move) in enumerate(zip(rhythm, contour)):
            abs_step = rep * motif_steps + step
            bar = abs_step // STEPS_PER_BAR
            if bar >= bars:
                break
            chord = _chord_at_bar(chords, bar, bars_per_chord)
            if vary and step >= motif_steps // 2:
                move = _weighted_move(rng)
            idx = idx + move if i else idx
            if idx < 0 or idx >= len(scale):
                idx = max(0, min(len(scale) - 1, idx - 2 * move))
            if step % 8 == 0 or i == 0:
                idx = snap_to_chord(idx, chord)
            velocity = 100 if step % 4 == 0 else 86
            notes.append(_note(scale[idx], abs_step * STEP, length * STEP * 0.92, velocity))
    if notes:
        # Resolve the phrase on a chord tone of the last chord.
        last_chord = _chord_at_bar(chords, bars - 1, bars_per_chord)
        j = snap_to_chord(scale.index(notes[-1]["pitch"]), last_chord)
        notes[-1]["pitch"] = scale[j]
    return apply_groove(notes, swing, humanize, seed)


# ----------------------------------------------------------------------------
# song planning

SECTION_TYPES = {
    "intro": dict(bars=4, drums="light", bass=False, chords=True, melody=False),
    "verse": dict(bars=8, drums="full", bass=True, chords=True, melody=True, density=0.7),
    "prechorus": dict(bars=4, drums="build", bass=True, chords=True, melody=False),
    "chorus": dict(bars=8, drums="full", bass=True, chords=True, melody=True, density=1.0, crash=True),
    "bridge": dict(bars=8, drums="break", bass=False, chords=True, melody=True, density=0.5),
    "breakdown": dict(bars=8, drums=None, bass=False, chords=True, melody=True, density=0.4),
    "drop": dict(bars=8, drums="full", bass=True, chords=True, melody=True, density=1.0, crash=True),
    "build": dict(bars=4, drums="build", bass=False, chords=True, melody=False),
    "outro": dict(bars=4, drums="light", bass=False, chords=True, melody=False),
}
SECTION_ALIASES = {
    "verso": "verse", "estrofa": "verse", "coro": "chorus", "estribillo": "chorus", "hook": "chorus",
    "precoro": "prechorus", "pre-coro": "prechorus", "pre-chorus": "prechorus", "pre": "prechorus",
    "puente": "bridge", "break": "breakdown", "subida": "build", "buildup": "build", "final": "outro",
}
DEFAULT_STRUCTURE = ["intro", "verse", "prechorus", "chorus", "verse", "prechorus", "chorus", "bridge",
                     "chorus", "outro"]
ROLES = ("drums", "bass", "chords", "melody")


@dataclass
class Section:
    name: str
    kind: str
    bars: int
    parts: dict[str, list[Note]] = field(default_factory=dict)


@dataclass
class SongPlan:
    genre: Genre
    key: Key
    bpm: float
    progression: str
    sections: list[Section]
    seed: int = 0

    @property
    def total_bars(self) -> int:
        return sum(s.bars for s in self.sections)

    def summary(self) -> dict:
        return {
            "genre": self.genre.name, "seed": self.seed, "key": str(self.key), "bpm": self.bpm, "progression": self.progression,
            "total_bars": self.total_bars,
            "sections": [{"name": s.name, "bars": s.bars,
                          "parts": {r: len(n) for r, n in s.parts.items()}} for s in self.sections],
        }


def parse_section(token: str) -> tuple[str, int | None]:
    """'chorus', 'coro:16', 'verse 8' -> (kind, bars)."""
    text = token.strip().lower().replace(":", " ")
    parts = text.split()
    bars = int(parts[-1]) if len(parts) > 1 and parts[-1].isdigit() else None
    kind = " ".join(parts[:-1] if bars is not None else parts)
    kind = SECTION_ALIASES.get(kind, kind)
    if kind not in SECTION_TYPES:
        raise ValueError(f"Sección desconocida: {token}. Usa: {', '.join(SECTION_TYPES)}")
    return kind, bars


def plan_song(genre: str, key: str | None = None, progression: str | None = None, bpm: float | None = None,
              structure: list[str] | None = None, seed: int | None = None,
              bars_per_chord: float = 1, melody_density: float | None = None) -> SongPlan:
    g = get_genre(genre)
    k = Key.parse(key or g.key)
    prog = progression or g.progression
    chords = parse_progression(prog, k)
    seed = random.randrange(1 << 30) if seed is None else seed
    density = g.melody_density if melody_density is None else melody_density

    sections = []
    counts: dict[str, int] = {}
    for token in structure or DEFAULT_STRUCTURE:
        kind, bars = parse_section(token)
        spec = SECTION_TYPES[kind]
        bars = bars or spec["bars"]
        counts[kind] = counts.get(kind, 0) + 1
        label = kind.capitalize() + (f" {counts[kind]}" if kind in ("verse", "chorus", "prechorus", "drop") else "")
        # Same seed per section kind: repeated choruses share the hook.
        s_seed = seed + sum(ord(c) for c in kind) * 7919
        sec = Section(label, kind, bars)
        if spec["drums"]:
            sec.parts["drums"] = generate_drums(g, bars, spec["drums"], fill=True,
                                                crash=spec.get("crash", False), seed=s_seed)
        if spec["bass"]:
            sec.parts["bass"] = generate_bass(k, chords, bars, g.bass, g.bass_center, bars_per_chord,
                                              g.swing, seed=s_seed)
        if spec["chords"]:
            sec.parts["chords"] = generate_chords(k, chords, bars, g.chords if spec["drums"] else "x" + "-" * 15,
                                                  g.chord_center, bars_per_chord, g.chord_spread,
                                                  swing=g.swing, seed=s_seed)
        if spec["melody"]:
            low, high = g.melody_range
            sec.parts["melody"] = generate_melody(k, chords, bars, density * spec.get("density", 1.0), low, high,
                                                  bars_per_chord, swing=g.swing, seed=s_seed)
        sections.append(sec)
    return SongPlan(g, k, float(bpm or g.bpm), prog, sections, seed)
