import pytest

from ava_live_mcp.composer import generate_bass, generate_chords, generate_drums, generate_melody, plan_song
from ava_live_mcp.genres import DRUM_NOTES, GENRES, get_genre
from ava_live_mcp.theory import Key, TheoryError, midi_to_note, note_to_midi, parse_progression, voice_chord


def test_note_names_use_ableton_convention():
    assert note_to_midi("C3") == 60
    assert note_to_midi("C1") == 36
    assert note_to_midi("F#2") == 54
    assert note_to_midi("Bb-1") == 22
    assert midi_to_note(60) == "C3"
    with pytest.raises(TheoryError):
        note_to_midi("H2")


@pytest.mark.parametrize("text,tonic,scale", [("Am", 9, "minor"), ("A minor", 9, "minor"), ("C", 0, "major"),
                                               ("F# dorian", 6, "dorian"), ("Bbm", 10, "minor")])
def test_key_parse(text, tonic, scale):
    k = Key.parse(text)
    assert (k.tonic, k.scale) == (tonic, scale)


def test_roman_and_symbol_progressions():
    k = Key.parse("A minor")
    chords = parse_progression("i VI III VII", k)
    assert [c.root for c in chords] == [9, 5, 0, 7]
    assert chords[0].intervals == (0, 3, 7)
    assert chords[1].intervals == (0, 4, 7)
    lofi = parse_progression("ii7 V7 Imaj7 bVII", Key.parse("C"))
    assert [c.root for c in lofi] == [2, 7, 0, 10]
    assert lofi[0].intervals == (0, 3, 7, 10) and lofi[2].intervals == (0, 4, 7, 11)
    sym = parse_progression("Am F C G/B", k)
    assert sym[3].bass == 11


def test_voice_leading_is_smooth_and_correct():
    k = Key.parse("C")
    chords = parse_progression("I V vi IV", k)
    prev = None
    for c in chords:
        v = voice_chord(c, 62, prev)
        assert {p % 12 for p in v} == set(c.pitch_classes())
        if prev:
            assert sum(abs(a - b) for a, b in zip(sorted(v), sorted(prev))) <= 8
        prev = v


def test_dembow_drums():
    notes = generate_drums(get_genre("reggaeton"), 2, "full", fill=False, humanize=0)
    kicks = [n for n in notes if n["pitch"] == DRUM_NOTES["kick"]]
    snares = sorted(n["start"] for n in notes if n["pitch"] == DRUM_NOTES["snare"])
    assert len(kicks) == 8
    assert snares[:4] == [0.75, 1.5, 2.75, 3.5]


def test_bass_follows_roots():
    k = Key.parse("A minor")
    chords = parse_progression("i VI", k)
    notes = generate_bass(k, chords, 2, "x-------........", center=36, humanize=0)
    assert [n["pitch"] % 12 for n in notes] == [9, 5]
    assert all(28 <= n["pitch"] <= 44 for n in notes)


def test_chords_and_arp():
    k = Key.parse("C")
    chords = parse_progression("I IV", k)
    block = generate_chords(k, chords, 2, humanize=0)
    assert len(block) == 6
    arp = generate_chords(k, chords, 2, arp="up", arp_rate=2, humanize=0)
    assert len(arp) == 16


def test_melody_in_scale_and_range():
    k = Key.parse("A minor")
    chords = parse_progression("i VI III VII", k)
    for seed in range(20):
        notes = generate_melody(k, chords, 8, density=0.7, low=67, high=84, seed=seed, humanize=0)
        assert notes
        assert all(n["pitch"] % 12 in k.pitch_classes() for n in notes)
        assert all(67 <= n["pitch"] <= 84 for n in notes)
        assert all(0 <= n["start"] < 32 for n in notes)


@pytest.mark.parametrize("genre", list(GENRES))
def test_plan_song_every_genre(genre):
    plan = plan_song(genre, seed=7)
    assert plan.total_bars > 0
    for section in plan.sections:
        for role, notes in section.parts.items():
            assert all(0 <= n["start"] < section.bars * 4 for n in notes), (genre, section.name, role)
            assert all(0 <= n["pitch"] <= 127 and 1 <= n["velocity"] <= 127 for n in notes)


def test_plan_song_is_deterministic_and_reuses_hooks():
    a = plan_song("pop", structure=["verso", "coro:4", "verse", "chorus:4"], seed=3)
    b = plan_song("pop", structure=["verso", "coro:4", "verse", "chorus:4"], seed=3)
    assert a.summary() == b.summary()
    assert [s.name for s in a.sections] == ["Verse 1", "Chorus 1", "Verse 2", "Chorus 2"]
    assert a.sections[1].parts["melody"] == a.sections[3].parts["melody"]
    with pytest.raises(ValueError):
        plan_song("pop", structure=["estribote"])
