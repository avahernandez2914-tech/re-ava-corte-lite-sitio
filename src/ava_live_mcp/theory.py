"""Music theory helpers: note names, scales, chords and voice leading.

Pitch names follow Ableton's convention: middle C (MIDI 60) is ``C3``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_PITCH_CLASS = {
    "C": 0, "B#": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "FB": 4, "F": 5, "E#": 5,
    "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11, "CB": 11,
}

SCALES: dict[str, list[int]] = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
    "melodic_minor": [0, 2, 3, 5, 7, 9, 11],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "locrian": [0, 1, 3, 5, 6, 8, 10],
    "major_pentatonic": [0, 2, 4, 7, 9],
    "minor_pentatonic": [0, 3, 5, 7, 10],
    "blues": [0, 3, 5, 6, 7, 10],
    "phrygian_dominant": [0, 1, 4, 5, 7, 8, 10],
}
SCALE_ALIASES = {"ionian": "major", "aeolian": "minor", "natural_minor": "minor", "mayor": "major",
                 "menor": "minor"}

_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7}

# Chord-symbol suffixes -> intervals from the root.
_SUFFIXES = {
    "": [0, 4, 7], "maj": [0, 4, 7], "m": [0, 3, 7], "min": [0, 3, 7], "dim": [0, 3, 6], "°": [0, 3, 6],
    "aug": [0, 4, 8], "+": [0, 4, 8], "sus2": [0, 2, 7], "sus4": [0, 5, 7], "sus": [0, 5, 7],
    "5": [0, 7], "6": [0, 4, 7, 9], "m6": [0, 3, 7, 9], "7": [0, 4, 7, 10], "maj7": [0, 4, 7, 11],
    "m7": [0, 3, 7, 10], "mmaj7": [0, 3, 7, 11], "m7b5": [0, 3, 6, 10], "ø": [0, 3, 6, 10],
    "ø7": [0, 3, 6, 10], "dim7": [0, 3, 6, 9], "°7": [0, 3, 6, 9], "add9": [0, 4, 7, 14],
    "madd9": [0, 3, 7, 14], "9": [0, 4, 7, 10, 14], "maj9": [0, 4, 7, 11, 14], "m9": [0, 3, 7, 10, 14],
    "7sus4": [0, 5, 7, 10], "11": [0, 4, 7, 10, 14, 17], "m11": [0, 3, 7, 10, 14, 17],
}


class TheoryError(ValueError):
    pass


def pitch_class(name: str) -> int:
    key = name.strip().upper().replace("♯", "#").replace("♭", "B")
    if len(key) > 1 and key[1] == "B":
        key = key[0] + "B"
    if key not in _PITCH_CLASS:
        raise TheoryError(f"Nota desconocida: {name}")
    return _PITCH_CLASS[key]


def note_to_midi(note: str | int) -> int:
    """'C3' -> 60 (Ableton convention), 'F#1' -> 42. Ints pass through."""
    if isinstance(note, int):
        return note
    text = str(note).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    m = re.fullmatch(r"([A-Ga-g])([#b♯♭]?)(-?\d+)", text)
    if not m:
        raise TheoryError(f"Nota inválida: {note} (usa p.ej. 'C3', 'F#2' o un número MIDI)")
    pc = pitch_class(m.group(1) + m.group(2))
    midi = (int(m.group(3)) + 2) * 12 + pc
    if not 0 <= midi <= 127:
        raise TheoryError(f"Nota fuera de rango MIDI: {note}")
    return midi


def midi_to_note(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 2}"


def scale_intervals(scale: str) -> list[int]:
    key = scale.strip().lower().replace(" ", "_").replace("-", "_")
    key = SCALE_ALIASES.get(key, key)
    if key not in SCALES:
        raise TheoryError(f"Escala desconocida: {scale}. Opciones: {', '.join(SCALES)}")
    return SCALES[key]


@dataclass(frozen=True)
class Key:
    tonic: int  # pitch class 0-11
    scale: str

    @classmethod
    def parse(cls, key: str, scale: str | None = None) -> "Key":
        """'A minor', 'F# dorian', 'Am', 'C' (major)."""
        parts = key.strip().split()
        tonic_txt = parts[0]
        if scale is None:
            if len(parts) > 1:
                scale = "_".join(parts[1:])
            elif len(tonic_txt) > 1 and tonic_txt.endswith("m") and tonic_txt[:-1] in _ok_tonics():
                tonic_txt, scale = tonic_txt[:-1], "minor"
            else:
                scale = "major"
        scale_intervals(scale)
        return cls(pitch_class(tonic_txt), SCALE_ALIASES.get(scale.lower(), scale.lower()))

    @property
    def intervals(self) -> list[int]:
        return scale_intervals(self.scale)

    def pitch_classes(self) -> list[int]:
        return [(self.tonic + i) % 12 for i in self.intervals]

    def scale_notes(self, low: int, high: int) -> list[int]:
        pcs = set(self.pitch_classes())
        return [p for p in range(low, high + 1) if p % 12 in pcs]

    def degree_pitch_class(self, degree: int) -> int:
        """1-based scale degree (7-note scales), wraps for >7."""
        iv = self.intervals
        return (self.tonic + iv[(degree - 1) % len(iv)]) % 12

    def __str__(self) -> str:
        return f"{NOTE_NAMES[self.tonic]} {self.scale}"


def _ok_tonics():
    return {n.capitalize() for n in _PITCH_CLASS} | set(_PITCH_CLASS)


@dataclass(frozen=True)
class Chord:
    root: int  # pitch class
    intervals: tuple[int, ...]
    symbol: str
    bass: int | None = None  # slash-chord bass pitch class

    def pitch_classes(self) -> list[int]:
        return [(self.root + i) % 12 for i in self.intervals]


_ROMAN_RE = re.compile(r"^([b#]?)(VII|VI|IV|V|III|II|I|vii|vi|iv|v|iii|ii|i)(.*)$")
_SYMBOL_RE = re.compile(r"^([A-G][#b]?)(.*)$")


def parse_chord(token: str, key: Key) -> Chord:
    """Parse a roman numeral ('i', 'bVII', 'V7', 'ii°', 'IVmaj7') or a chord symbol ('Am7', 'F#', 'G/B')."""
    token = token.strip()
    bass = None
    if "/" in token:
        token, bass_txt = token.split("/", 1)
        bass = pitch_class(bass_txt) if _SYMBOL_RE.match(bass_txt) else None
    m = _ROMAN_RE.match(token)
    if m:
        accidental, numeral, suffix = m.groups()
        degree = _ROMAN[numeral.upper()]
        if len(key.intervals) != 7:
            # Pentatonic/blues keys: take roots from the parallel major/minor.
            ref = Key(key.tonic, "minor" if 3 in key.intervals else "major")
        else:
            ref = key
        root = ref.degree_pitch_class(degree)
        root = (root + (-1 if accidental == "b" else 1 if accidental == "#" else 0)) % 12
        minor = numeral.islower()
        suffix_key = suffix
        if suffix in ("", "7", "9", "11", "6", "add9"):
            if minor:
                suffix_key = {"": "m", "7": "m7", "9": "m9", "11": "m11", "6": "m6", "add9": "madd9"}[suffix]
        elif suffix in ("maj7", "maj9") and minor:
            suffix_key = "mmaj7" if suffix == "maj7" else "m9"
        if suffix_key not in _SUFFIXES:
            raise TheoryError(f"Acorde inválido: {token}")
        return Chord(root, tuple(_SUFFIXES[suffix_key]), token, bass)
    m = _SYMBOL_RE.match(token)
    if m:
        root_txt, suffix = m.groups()
        if suffix not in _SUFFIXES:
            raise TheoryError(f"Acorde inválido: {token}")
        return Chord(pitch_class(root_txt), tuple(_SUFFIXES[suffix]), token, bass)
    raise TheoryError(f"Acorde inválido: {token} (usa números romanos como 'vi' o cifrado como 'Am')")


def parse_progression(progression: str | list[str], key: Key) -> list[Chord]:
    tokens = progression.replace(",", " ").replace("-", " ").replace("|", " ").split() \
        if isinstance(progression, str) else list(progression)
    if not tokens:
        raise TheoryError("La progresión está vacía")
    return [parse_chord(t, key) for t in tokens]


def voice_chord(chord: Chord, center: int = 60, previous: list[int] | None = None,
                spread: bool = False) -> list[int]:
    """Return MIDI pitches for the chord near ``center`` with smooth voice leading."""
    pcs = chord.pitch_classes()
    # Base voicing: stack intervals upward from the root.
    root_note = center - 12 + ((chord.root - (center - 12)) % 12)
    stacked = [root_note + i for i in chord.intervals]
    candidates = []
    for inversion in range(len(stacked)):
        notes = sorted(stacked)
        for _ in range(inversion):
            notes = notes[1:] + [notes[0] + 12]
        for shift in (-24, -12, 0, 12):
            cand = [n + shift for n in notes]
            if min(cand) >= center - 14 and max(cand) <= center + 16:
                candidates.append(cand)
    if not candidates:
        candidates = [stacked]
    if previous:
        def cost(c):
            return sum(min(abs(n - p) for p in previous) for n in c) + abs(sum(c) / len(c) - center) * 0.3
    else:
        def cost(c):
            return abs(sum(c) / len(c) - center)
    best = min(candidates, key=cost)
    if spread and len(best) >= 3:
        best = [best[0] - 12] + best[1:]
    assert all(n % 12 in pcs for n in best)
    return best


def chord_bass_pitch_class(chord: Chord) -> int:
    return chord.bass if chord.bass is not None else chord.root
