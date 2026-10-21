"""Genre presets: tempo, harmony and rhythm patterns.

Patterns are 16-step strings per bar (16th notes in 4/4), concatenated for
multi-bar patterns.

Drums:  'X' accent, 'x' hit, 'o' ghost/soft, 'r' 1/32 roll, '.' rest.
Bass:   'x' root, 'o' octave up, '5' fifth, '3' third, '7' seventh,
        'a' approach (a step below the next chord root), '-' sustain, '.' rest.
Chords: 'x' chord hit, '-' sustain, '.' rest.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# General MIDI drum map; matches the pad layout of Ableton's Drum Rack kits (C1 = 36).
DRUM_NOTES = {
    "kick": 36, "rim": 37, "snare": 38, "clap": 39, "snare2": 40, "low_tom": 41, "hat": 42,
    "tom": 43, "pedal_hat": 44, "mid_tom": 45, "open_hat": 46, "high_tom": 47, "perc": 48,
    "crash": 49, "perc2": 50, "ride": 51,
}


@dataclass
class Genre:
    name: str
    description: str
    bpm: int
    key: str
    progression: str
    drums: dict[str, str]
    bass: str
    chords: str
    swing: float = 0.0
    melody_density: float = 0.5
    bass_center: int = 36  # C1
    chord_center: int = 62
    melody_range: tuple[int, int] = (67, 84)
    chord_spread: bool = False
    instruments: dict[str, list[str]] = field(default_factory=dict)


# Browser search candidates per role, tried in order. Names of devices/kits in
# Live's Core Library; anything not found is skipped.
DEFAULT_INSTRUMENTS = {
    "drums": ["909 Core Kit", "808 Core Kit", "Kit"],
    "bass": ["Operator", "Drift", "Analog"],
    "chords": ["Electric", "Wavetable", "Drift"],
    "melody": ["Drift", "Analog", "Wavetable", "Operator"],
}


def _instr(drums=None, bass=None, chords=None, melody=None):
    out = {k: list(v) for k, v in DEFAULT_INSTRUMENTS.items()}
    for role, value in (("drums", drums), ("bass", bass), ("chords", chords), ("melody", melody)):
        if value:
            out[role] = value + [c for c in out[role] if c not in value]
    return out


GENRES: dict[str, Genre] = {
    "reggaeton": Genre(
        name="reggaeton", description="Dembow clásico, menor, 90-100 BPM",
        bpm=95, key="A minor", progression="i VI III VII",
        drums={
            "kick": "x...x...x...x...",
            "snare": "...x..x....x..x.",
            "hat": "x.o.x.o.x.o.x.o.",
            "perc": "..........o.....",
        },
        bass="x-----x-x-----x-", chords="x--x--x-x--x--x-",
        melody_density=0.55, bass_center=36,
        instruments=_instr(drums=["808 Core Kit"], chords=["Wavetable"]),
    ),
    "dembow": Genre(
        name="dembow", description="Dembow dominicano, rápido y percusivo",
        bpm=120, key="G minor", progression="i iv VII III",
        drums={
            "kick": "x...x...x...x...",
            "snare": "...x..x....x..x.",
            "hat": "x.xxx.xxx.xxx.xx",
            "clap": "....x.......x...",
        },
        bass="x-..x-..x-..x-..", chords="x..x..x.x..x..x.",
        melody_density=0.6, instruments=_instr(drums=["808 Core Kit"]),
    ),
    "trap": Genre(
        name="trap", description="Trap medio tiempo, 808 largo, hats con redobles",
        bpm=140, key="C minor", progression="i VI iv V",
        drums={
            "kick": "x.....x...x....." "x.....x...x..x..",
            "snare": "........x......." "........x.......",
            "hat": "x.x.x.x.x.x.xrx." "x.x.xrx.x.x.rrrr",
            "open_hat": "..............x." "................",
        },
        bass="x-----x---x-----" "x-----x---x--x--", chords="x---------------",
        melody_density=0.4, bass_center=31, melody_range=(65, 82),
        instruments=_instr(drums=["808 Core Kit"], bass=["Operator"], chords=["Wavetable"]),
    ),
    "house": Genre(
        name="house", description="House 4x4, bajo en contratiempo, acordes stab",
        bpm=124, key="A minor", progression="i VII VI VII",
        drums={
            "kick": "x...x...x...x...",
            "clap": "....x.......x...",
            "open_hat": "..x...x...x...x.",
            "hat": "o.o.o.o.o.o.o.o.",
            "perc": "...o.....o...o..",
        },
        bass="..x-..x-..x-..xo", chords="..x...x...x..x..",
        melody_density=0.45, chord_center=64,
        instruments=_instr(drums=["909 Core Kit"], chords=["Electric", "Wavetable"]),
    ),
    "techno": Genre(
        name="techno", description="Techno hipnótico, un acorde, rumble",
        bpm=130, key="F minor", progression="i i VI i",
        drums={
            "kick": "x...x...x...x...",
            "clap": "....x.......x...",
            "open_hat": "..x...x...x...x.",
            "hat": "xoxoxoxoxoxoxoxo",
            "rim": "...x..x...x..x..",
        },
        bass="..xx..xx..xx..xx", chords="x..x..x..x..x..x",
        melody_density=0.35, instruments=_instr(drums=["909 Core Kit"], chords=["Wavetable"]),
    ),
    "hiphop": Genre(
        name="hiphop", description="Boom bap con swing",
        bpm=90, key="D minor", progression="i iv VII III",
        drums={
            "kick": "x......x.xx.....",
            "snare": "....x.......x...",
            "hat": "x.x.x.x.x.x.x.xo",
        },
        bass="x-----.xx-------", chords="x-------x-------",
        swing=0.55, melody_density=0.45,
        instruments=_instr(chords=["Electric"]),
    ),
    "lofi": Genre(
        name="lofi", description="Lo-fi hip hop jazzy, acordes de séptima",
        bpm=80, key="F major", progression="ii7 V7 Imaj7 vi7",
        drums={
            "kick": "x.......x.x.....",
            "snare": "....x.......x...",
            "hat": "x.xox.x.x.xox.x.",
        },
        bass="x-------x---5---", chords="x---------------",
        swing=0.6, melody_density=0.3, chord_spread=True,
        instruments=_instr(chords=["Electric"], melody=["Electric", "Drift"]),
    ),
    "pop": Genre(
        name="pop", description="Pop moderno, mayor, I-V-vi-IV",
        bpm=118, key="C major", progression="I V vi IV",
        drums={
            "kick": "x.....x.x.......",
            "clap": "....x.......x...",
            "hat": "x.x.x.x.x.x.x.x.",
        },
        bass="x.x.x.x.x.x.x.xo", chords="x--x--x---x--x--",
        melody_density=0.55,
        instruments=_instr(chords=["Electric", "Wavetable"]),
    ),
    "afrobeats": Genre(
        name="afrobeats", description="Afrobeats/amapiano ligero, 3-3-2",
        bpm=105, key="G major", progression="vi IV I V",
        drums={
            "kick": "x.......x.......",
            "rim": "...x..x....x..x.",
            "clap": "....x.......x...",
            "perc": "oxoooxoooxoooxoo",
        },
        bass="x..x..x...x-x---", chords="x-..x-..x-..x-..",
        melody_density=0.5, instruments=_instr(chords=["Electric"]),
    ),
    "dnb": Genre(
        name="dnb", description="Drum and bass, breakbeat a 174 BPM",
        bpm=174, key="E minor", progression="i VI III VII",
        drums={
            "kick": "x.........x....." "x.........x.x...",
            "snare": "....x.......x..." "....x.......x..o",
            "hat": "x.x.x.x.x.x.x.x.",
        },
        bass="x-------x-----x-", chords="x---------------",
        melody_density=0.35, bass_center=31,
        instruments=_instr(drums=["909 Core Kit"], bass=["Wavetable", "Operator"], chords=["Wavetable"]),
    ),
}

GENRE_ALIASES = {
    "regueton": "reggaeton", "reguetón": "reggaeton", "reggaetón": "reggaeton", "perreo": "reggaeton",
    "hip hop": "hiphop", "hip-hop": "hiphop", "boom bap": "hiphop", "boombap": "hiphop",
    "lo-fi": "lofi", "lo fi": "lofi", "afro": "afrobeats", "afrobeat": "afrobeats", "amapiano": "afrobeats",
    "drum and bass": "dnb", "drum & bass": "dnb", "d&b": "dnb", "edm": "house", "deep house": "house",
}


def get_genre(name: str) -> Genre:
    key = name.strip().lower()
    key = GENRE_ALIASES.get(key, key)
    if key not in GENRES:
        raise ValueError(f"Género desconocido: {name}. Opciones: {', '.join(GENRES)}")
    return GENRES[key]
