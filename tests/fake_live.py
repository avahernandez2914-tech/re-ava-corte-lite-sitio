"""Minimal stand-in for the Live object model, enough to exercise the handlers."""
from types import SimpleNamespace


class Param:
    def __init__(self, name, value=0.0, lo=0.0, hi=1.0, items=None):
        self.name, self.value, self.min, self.max = name, value, lo, hi
        self.value_items = items or []
        self.is_quantized = bool(items)

    def str_for_value(self, v):
        return "%.2f" % v


class Clip:
    is_midi_clip = True
    is_playing = False

    def __init__(self, length):
        self.name, self.looping = "", True
        self.loop_start, self.loop_end, self.end_marker = 0.0, length, length
        self.color_index = 0
        self.notes = []

    @property
    def length(self):  # like Live: a looped clip's length follows its loop
        return self.loop_end - self.loop_start

    def set_notes(self, notes):
        self.notes.extend(SimpleNamespace(pitch=p, start_time=t, duration=d, velocity=v, mute=m, probability=1.0)
                          for p, t, d, v, m in notes)

    def remove_notes_extended(self, *args):
        self.notes = []

    def get_notes_extended(self, *args):
        return list(self.notes)

    def quantize(self, grid, amount):
        self.quantized = (grid, amount)


class ClipSlot:
    def __init__(self):
        self.clip = None

    @property
    def has_clip(self):
        return self.clip is not None

    def create_clip(self, length):
        assert self.clip is None
        self.clip = Clip(length)

    def delete_clip(self):
        self.clip = None

    def fire(self):
        pass


class Device:
    def __init__(self, name):
        self.name = self.class_name = name
        self.is_active, self.can_have_chains = True, False
        self.parameters = [Param("Device On", 1, 0, 1, ["Off", "On"]), Param("Filter Freq", 0.5)]


class Track:
    def __init__(self, song, name, midi=True):
        self.name, self.has_midi_input = name, midi
        self.mute = self.solo = self.arm = False
        self.can_be_armed = True
        self.color_index = 0
        self.mixer_device = SimpleNamespace(volume=Param("Volume", 0.85), panning=Param("Pan", 0, -1, 1), sends=[])
        self.devices = []
        self.clip_slots = [ClipSlot() for _ in song.scenes]
        self.arrangement_clips = []

    def stop_all_clips(self):
        pass

    def duplicate_clip_to_arrangement(self, clip, time):
        self.arrangement_clips.append(SimpleNamespace(name=clip.name, start_time=time, end_time=time + clip.length,
                                                      length=clip.length))

    def delete_clip(self, clip):
        self.arrangement_clips.remove(clip)


class Scene:
    def __init__(self):
        self.name, self.color_index, self.fired = "", 0, 0

    def fire(self):
        self.fired += 1


class Song:
    def __init__(self, scenes=2):
        self.tempo, self.signature_numerator, self.signature_denominator = 120.0, 4, 4
        self.is_playing, self.current_song_time, self.metronome = False, 0.0, False
        self.loop, self.loop_start, self.loop_length = False, 0.0, 16.0
        self.can_undo = self.can_redo = False
        self.scenes = [Scene() for _ in range(scenes)]
        self.tracks, self.return_tracks = [], []
        self.master_track = Track(self, "Master", midi=False)
        self.view = SimpleNamespace(selected_track=None)

    def create_midi_track(self, index):
        t = Track(self, "MIDI")
        self.tracks.insert(len(self.tracks) if index < 0 else index, t)

    def create_audio_track(self, index):
        t = Track(self, "Audio", midi=False)
        self.tracks.insert(len(self.tracks) if index < 0 else index, t)

    def delete_track(self, index):
        del self.tracks[index]

    def create_scene(self, index):
        self.scenes.insert(len(self.scenes) if index < 0 else index, Scene())
        for t in self.tracks:
            t.clip_slots.insert(len(t.clip_slots) if index < 0 else index, ClipSlot())

    def start_playing(self):
        self.is_playing = True

    def stop_playing(self):
        self.is_playing = False

    def stop_all_clips(self):
        pass


class BrowserItem:
    def __init__(self, name, children=(), loadable=True):
        self.name, self.children = name, list(children)
        self.is_folder = bool(children)
        self.is_loadable = loadable and not children
        self.uri = "query:" + name


class Browser:
    def __init__(self, song):
        self._song = song
        self.instruments = BrowserItem("Instruments", [BrowserItem("Operator"), BrowserItem("Drift"),
                                                       BrowserItem("Wavetable")])
        self.drums = BrowserItem("Drums", [BrowserItem("Drum Rack"), BrowserItem("Kits", [
            BrowserItem("808 Core Kit.adg"), BrowserItem("909 Core Kit.adg")])])
        self.audio_effects = BrowserItem("Audio Effects", [BrowserItem("Reverb")])

    def load_item(self, item):
        self._song.view.selected_track.devices.append(Device(item.name.rsplit(".", 1)[0]))


class App:
    def __init__(self, song):
        self.browser = Browser(song)

    def get_major_version(self):
        return 12

    def get_minor_version(self):
        return 2

    def get_bugfix_version(self):
        return 0
