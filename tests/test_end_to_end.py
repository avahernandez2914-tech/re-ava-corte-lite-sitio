"""MCP tool -> TCP -> Remote Script handlers -> fake Live song."""
import asyncio
import json
import threading
import time

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from AvaLiveMCP.handlers import Handlers
from AvaLiveMCP.socket_server import SocketServer
from ava_live_mcp import server as srv
from ava_live_mcp.connection import LiveConnection
from fake_live import App, Song


@pytest.fixture
def live():
    song = Song(scenes=2)
    app = App(song)
    handlers = Handlers(lambda: song, lambda: app)
    sock = SocketServer("127.0.0.1", 0)
    sock.start()
    stop = threading.Event()

    def main_thread():  # plays the role of Live's update_display tick
        while not stop.is_set():
            sock.process_pending(handlers.dispatch)
            time.sleep(0.002)

    t = threading.Thread(target=main_thread, daemon=True)
    t.start()
    srv._live = LiveConnection("127.0.0.1", sock.port, timeout=10)
    yield song
    stop.set()
    srv._live.close()
    sock.stop()


def call(_tool, **args):
    result = asyncio.run(srv.mcp.call_tool(_tool, args))
    assert not result.is_error, result.content
    data = json.loads(result.content[0].text) if result.content else None
    if isinstance(data, list) or len(result.content) > 1:  # list results come as one block per item
        return [json.loads(c.text) for c in result.content]
    return data


def test_status_and_tempo(live):
    assert call("live_status")["live_version"] == "12.2.0"
    call("set_tempo", bpm=97)
    assert live.tempo == 97


def test_write_and_read_notes(live):
    call("create_track", name="Keys", instrument="Drift")
    assert live.tracks[0].devices[0].name == "Drift"
    call("write_notes", track_index=0, slot_index=3, name="riff",
         notes=[{"pitch": "C3", "start": 0, "duration": 1}, {"pitch": 64, "start": 1, "duration": 0.5}])
    assert len(live.scenes) == 4  # scenes created on demand
    clip = call("get_clip", track_index=0, slot_index=3)
    assert clip["length"] == 4.0 and clip["name"] == "riff"
    assert [n["name"] for n in clip["notes"]] == ["C3", "E3"]


def test_mixer_and_devices(live):
    call("create_track", name="Bass", instrument="Operator")
    call("set_track", track_index=0, volume=0.7, pan=-0.2, mute=True)
    assert live.tracks[0].mixer_device.volume.value == 0.7 and live.tracks[0].mute
    call("load_device", track_index=0, query="Reverb", category="audio_effects")
    devices = call("get_devices", track_index=0)
    assert [d["name"] for d in devices] == ["Operator", "Reverb"]
    call("set_device_parameter", track_index=0, device_index=1, parameter="filter freq", value=0.3)
    assert live.tracks[0].devices[1].parameters[1].value == 0.3
    call("set_device_parameter", track_index=0, device_index=1, parameter="Device On", value="Off")
    assert live.tracks[0].devices[1].parameters[0].value == 0


def test_errors_are_reported(live):
    with pytest.raises(ToolError, match="fuera de rango"):
        asyncio.run(srv.mcp.call_tool("get_track", {"track_index": 5}))


def test_create_song_end_to_end(live):
    out = call("create_song", genre="reggaeton", key="Am", structure=["intro", "verso", "coro"], seed=1)
    assert [t.name for t in live.tracks] == ["Drums", "Bass", "Chords", "Melody"]
    assert out["instruments"]["drums"] == "808 Core Kit.adg"
    assert live.tracks[0].devices[0].name == "808 Core Kit"
    assert [s.name for s in live.scenes] == ["Intro", "Verse 1", "Chorus 1"]
    assert live.tempo == 95
    drums = live.tracks[0]
    assert all(slot.has_clip for slot in drums.clip_slots)
    assert not live.tracks[1].clip_slots[0].has_clip  # no bass in the intro
    assert [c.start_time for c in drums.arrangement_clips] == [0.0, 16.0, 48.0]
    assert out["arrangement"]["end_beat"] == 80.0

    # A second song goes after the first one without touching it.
    call("create_song", genre="house", structure=["drop:4"], seed=2, to_arrangement=False, title="B")
    assert live.tracks[4].name == "B Drums"
    assert live.tracks[4].clip_slots[3].has_clip and live.scenes[3].name == "Drop 1"


def test_generators_write_clips(live):
    call("create_track", name="Drums")
    out = call("generate_drum_clip", track_index=0, slot_index=0, genre="trap", bars=2)
    assert out["notes_written"] > 10
    call("create_track", name="Lead")
    call("generate_melody_clip", track_index=1, slot_index=0, key="C minor", progression="i VI iv V", seed=4)
    assert live.tracks[1].clip_slots[0].clip.notes
    call("generate_chord_clip", track_index=1, slot_index=1, key="C", progression="Am F C G", arp="updown")
    call("generate_bass_clip", track_index=1, slot_index=0, key="C", progression="I V", bars=2, genre=None)
    call("session_to_arrangement", scene_order=[0, 1, 0])
    # scene 0 is 8 beats (drums), scene 1 is 16 beats (chords): 0, 8, 24
    assert [c.start_time for c in live.tracks[1].arrangement_clips] == [0.0, 8.0, 24.0]


def test_import_song(live, tmp_path):
    stems = {"Voz principal": "stems/01 Voz principal.wav", "Coros": "stems/02 Coros.wav",
             "Bateria": "stems/03 Bateria.wav", "Bajo": "stems/04 Bajo.wav", "Otros": "stems/05 Otros.wav",
             "Instrumental": "stems/06 Instrumental.wav"}
    song = {"bpm": 100.0, "key": "A minor", "duration_seconds": 30.0, "chords_per_bar": ["Am Am", "F F"],
            "most_common_chords": ["Am (2)"], "stems": stems,
            "parts": {"bass": [{"pitch": 33, "start": 0, "duration": 1, "velocity": 100}],
                      "chords": [{"pitch": 57, "start": 0, "duration": 2, "velocity": 90}],
                      "melody": [], "other": [], "drums": [{"pitch": 36, "start": 0, "duration": 0.25,
                                                            "velocity": 110}]}}
    (tmp_path / "song.json").write_text(json.dumps(song))
    out = call("import_song", folder=str(tmp_path))
    assert live.tempo == 100.0 and out["key"] == "A minor"
    names = [t.name for t in live.tracks]
    assert names == ["Voz principal", "Coros", "Bateria", "Bajo", "Otros", "Nueva voz",
                     "Acordes (MIDI)", "Bajo (MIDI)", "Batería (MIDI)"]
    vocal = live.tracks[0].arrangement_clips[0]
    assert vocal.file_path == str((tmp_path / stems["Voz principal"]).resolve()) and vocal.warping is False
    bass_midi = live.tracks[7]
    assert bass_midi.mute and bass_midi.devices[0].name == "Operator"
    assert bass_midi.clip_slots[0].clip.length == 52.0  # 30 s at 100 BPM = 50 beats -> 13 bars
    assert bass_midi.arrangement_clips[0].start_time == 0.0
    assert out["warnings"] == []
