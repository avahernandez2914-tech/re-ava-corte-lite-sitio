"""Command handlers. Every ``cmd_<name>`` method is a command the MCP server can call.

All handlers run on Live's main thread. Times and lengths are in beats
(quarter notes). Indices are 0-based. ``track_index=-1`` means the master
track where it makes sense (mixer and devices).
"""
from __future__ import absolute_import

from collections import deque

BROWSER_CATEGORIES = (
    "instruments", "drums", "sounds", "audio_effects", "midi_effects",
    "max_for_live", "plugins", "clips", "samples", "packs", "user_library",
    "current_project",
)

# Live's RecordQuantization enum values used by Clip.quantize.
QUANTIZE_GRIDS = {
    "1/4": 1, "1/8": 2, "1/8t": 3, "1/8+t": 4, "1/16": 5, "1/16t": 6,
    "1/16+t": 7, "1/32": 8,
}

MAX_BROWSER_NODES = 25000


class CommandError(Exception):
    pass


class Handlers(object):
    def __init__(self, song_getter, app_getter=None, log=None):
        self._song = song_getter
        self._app = app_getter
        self._log = log or (lambda msg: None)
        self._uri_cache = {}

    def dispatch(self, command, params):
        fn = getattr(self, "cmd_" + str(command), None)
        if fn is None:
            raise CommandError("Comando desconocido: %s" % command)
        return fn(**(params or {}))

    def command_names(self):
        return sorted(n[4:] for n in dir(self) if n.startswith("cmd_"))

    # ------------------------------------------------------------------
    # helpers
    @property
    def song(self):
        return self._song()

    def _track(self, track_index, allow_master=False):
        song = self.song
        if allow_master and track_index == -1:
            return song.master_track
        tracks = song.tracks
        if not 0 <= track_index < len(tracks):
            raise CommandError("track_index %s fuera de rango (hay %d pistas)" % (track_index, len(tracks)))
        return tracks[track_index]

    def _scene(self, scene_index):
        scenes = self.song.scenes
        if not 0 <= scene_index < len(scenes):
            raise CommandError("scene_index %s fuera de rango (hay %d escenas)" % (scene_index, len(scenes)))
        return scenes[scene_index]

    def _ensure_scenes(self, count):
        song = self.song
        while len(song.scenes) < count:
            song.create_scene(-1)

    def _clip_slot(self, track_index, slot_index, create_scenes=False):
        track = self._track(track_index)
        if create_scenes:
            self._ensure_scenes(slot_index + 1)
        slots = track.clip_slots
        if not 0 <= slot_index < len(slots):
            raise CommandError("slot_index %s fuera de rango (hay %d slots)" % (slot_index, len(slots)))
        return slots[slot_index]

    def _clip(self, track_index, slot_index):
        slot = self._clip_slot(track_index, slot_index)
        if not slot.has_clip:
            raise CommandError("No hay clip en pista %d, slot %d" % (track_index, slot_index))
        return slot.clip

    def _device(self, track_index, device_index):
        devices = self._track(track_index, allow_master=True).devices
        if not 0 <= device_index < len(devices):
            raise CommandError("device_index %s fuera de rango (hay %d dispositivos)" % (device_index, len(devices)))
        return devices[device_index]

    @staticmethod
    def _param_info(p, index):
        info = {"index": index, "name": p.name, "value": p.value, "min": p.min, "max": p.max,
                "quantized": bool(p.is_quantized)}
        try:
            info["display"] = p.str_for_value(p.value)
        except Exception:
            pass
        if p.is_quantized:
            try:
                info["options"] = list(p.value_items)
            except Exception:
                pass
        return info

    @staticmethod
    def _clip_summary(clip):
        return {"name": clip.name, "length": clip.length, "is_midi": bool(clip.is_midi_clip),
                "looping": bool(clip.looping), "loop_start": clip.loop_start, "loop_end": clip.loop_end,
                "is_playing": bool(clip.is_playing)}

    def _track_summary(self, index, track, with_slots=True):
        info = {
            "index": index, "name": track.name,
            "type": "midi" if track.has_midi_input else "audio",
            "mute": bool(track.mute), "solo": bool(track.solo),
            "arm": bool(track.arm) if track.can_be_armed else None,
            "volume": track.mixer_device.volume.value,
            "volume_display": _display(track.mixer_device.volume),
            "pan": track.mixer_device.panning.value,
            "devices": [d.name for d in track.devices],
        }
        if with_slots:
            info["clips"] = [dict(self._clip_summary(s.clip), slot=i)
                             for i, s in enumerate(track.clip_slots) if s.has_clip]
        return info

    # ------------------------------------------------------------------
    # general
    def cmd_ping(self):
        info = {"pong": True, "commands": self.command_names()}
        if self._app is not None:
            try:
                app = self._app()
                info["live_version"] = "%d.%d.%d" % (app.get_major_version(), app.get_minor_version(),
                                                     app.get_bugfix_version())
            except Exception:
                pass
        return info

    def cmd_get_session_info(self, include_clips=True):
        song = self.song
        return {
            "tempo": song.tempo,
            "signature": [song.signature_numerator, song.signature_denominator],
            "is_playing": bool(song.is_playing),
            "current_song_time": song.current_song_time,
            "metronome": bool(song.metronome),
            "loop": {"on": bool(song.loop), "start": song.loop_start, "length": song.loop_length},
            "tracks": [self._track_summary(i, t, include_clips) for i, t in enumerate(song.tracks)],
            "return_tracks": [{"index": i, "name": t.name} for i, t in enumerate(song.return_tracks)],
            "scenes": [{"index": i, "name": s.name} for i, s in enumerate(song.scenes)],
            "master": {"volume": song.master_track.mixer_device.volume.value,
                       "volume_display": _display(song.master_track.mixer_device.volume),
                       "devices": [d.name for d in song.master_track.devices]},
        }

    def cmd_batch(self, commands, stop_on_error=True):
        """Run several commands in one main-thread tick."""
        results = []
        for item in commands:
            try:
                results.append({"ok": True, "result": self.dispatch(item.get("command"), item.get("params"))})
            except Exception as e:
                results.append({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
                if stop_on_error:
                    break
        return results

    # ------------------------------------------------------------------
    # transport
    def cmd_set_tempo(self, tempo):
        self.song.tempo = float(tempo)
        return {"tempo": self.song.tempo}

    def cmd_set_time_signature(self, numerator, denominator):
        self.song.signature_numerator = int(numerator)
        self.song.signature_denominator = int(denominator)
        return {"signature": [self.song.signature_numerator, self.song.signature_denominator]}

    def cmd_start_playback(self, from_start=False):
        song = self.song
        if from_start:
            song.current_song_time = 0.0
        song.start_playing()
        return {"is_playing": True}

    def cmd_stop_playback(self):
        self.song.stop_playing()
        return {"is_playing": False}

    def cmd_stop_all_clips(self):
        self.song.stop_all_clips()
        return {}

    def cmd_set_song_position(self, time):
        self.song.current_song_time = float(time)
        return {"current_song_time": self.song.current_song_time}

    def cmd_set_metronome(self, on):
        self.song.metronome = bool(on)
        return {"metronome": bool(self.song.metronome)}

    def cmd_set_loop(self, on=True, start=None, length=None):
        song = self.song
        if start is not None:
            song.loop_start = float(start)
        if length is not None:
            song.loop_length = float(length)
        song.loop = bool(on)
        return {"on": bool(song.loop), "start": song.loop_start, "length": song.loop_length}

    def cmd_undo(self):
        if self.song.can_undo:
            self.song.undo()
            return {"undone": True}
        return {"undone": False}

    def cmd_redo(self):
        if self.song.can_redo:
            self.song.redo()
            return {"redone": True}
        return {"redone": False}

    # ------------------------------------------------------------------
    # tracks
    def cmd_get_track_info(self, track_index):
        track = self._track(track_index)
        info = self._track_summary(track_index, track)
        info["sends"] = [s.value for s in track.mixer_device.sends]
        return info

    def cmd_create_track(self, type="midi", index=-1, name=None, color_index=None):
        song = self.song
        if type == "midi":
            song.create_midi_track(index)
        elif type == "audio":
            song.create_audio_track(index)
        elif type == "return":
            song.create_return_track()
            track = song.return_tracks[-1]
            if name:
                track.name = name
            return {"index": len(song.return_tracks) - 1, "name": track.name, "type": "return"}
        else:
            raise CommandError("type debe ser 'midi', 'audio' o 'return'")
        new_index = len(song.tracks) - 1 if index < 0 else index
        track = song.tracks[new_index]
        if name:
            track.name = name
        if color_index is not None:
            track.color_index = int(color_index)
        song.view.selected_track = track
        return {"index": new_index, "name": track.name, "type": type}

    def cmd_delete_track(self, track_index):
        self._track(track_index)
        self.song.delete_track(track_index)
        return {"deleted": track_index}

    def cmd_duplicate_track(self, track_index):
        self._track(track_index)
        self.song.duplicate_track(track_index)
        return {"index": track_index + 1}

    def cmd_set_track(self, track_index, name=None, color_index=None, mute=None, solo=None, arm=None,
                      volume=None, pan=None, sends=None):
        """Update track properties. volume: 0..1 (0.85 ~= 0 dB). pan: -1..1."""
        track = self._track(track_index, allow_master=True)
        if name is not None:
            track.name = name
        if color_index is not None:
            track.color_index = int(color_index)
        if mute is not None:
            track.mute = bool(mute)
        if solo is not None:
            track.solo = bool(solo)
        if arm is not None:
            if not track.can_be_armed:
                raise CommandError("Esta pista no se puede armar")
            track.arm = bool(arm)
        mixer = track.mixer_device
        if volume is not None:
            mixer.volume.value = _clamp(float(volume), mixer.volume.min, mixer.volume.max)
        if pan is not None:
            mixer.panning.value = _clamp(float(pan), -1.0, 1.0)
        if sends is not None:
            for i, value in enumerate(sends):
                if value is not None and i < len(mixer.sends):
                    mixer.sends[i].value = _clamp(float(value), 0.0, 1.0)
        return {"name": track.name, "volume": mixer.volume.value, "volume_display": _display(mixer.volume),
                "pan": mixer.panning.value}

    def cmd_select_track(self, track_index):
        self.song.view.selected_track = self._track(track_index, allow_master=True)
        return {}

    # ------------------------------------------------------------------
    # scenes
    def cmd_create_scene(self, index=-1, name=None):
        song = self.song
        song.create_scene(index)
        new_index = len(song.scenes) - 1 if index < 0 else index
        if name:
            song.scenes[new_index].name = name
        return {"index": new_index}

    def cmd_set_scene(self, scene_index, name=None, color_index=None):
        scene = self._scene(scene_index)
        if name is not None:
            scene.name = name
        if color_index is not None:
            scene.color_index = int(color_index)
        return {"name": scene.name}

    def cmd_delete_scene(self, scene_index):
        self._scene(scene_index)
        self.song.delete_scene(scene_index)
        return {"deleted": scene_index}

    def cmd_fire_scene(self, scene_index):
        self._scene(scene_index).fire()
        return {}

    # ------------------------------------------------------------------
    # clips
    def cmd_write_clip(self, track_index, slot_index, notes=None, length=None, name=None, replace=True,
                       looping=True, color_index=None):
        """Create the clip if needed and write notes to it.

        notes: [{"pitch", "start", "duration", "velocity", "mute", "probability",
                 "velocity_deviation"}]
        """
        slot = self._clip_slot(track_index, slot_index, create_scenes=True)
        notes = notes or []
        if length is None:
            end = max([n["start"] + n["duration"] for n in notes] or [4.0])
            length = max(4.0, float(int((end + 3.999) // 4) * 4))
        length = float(length)
        if slot.has_clip:
            clip = slot.clip
            if not clip.is_midi_clip:
                raise CommandError("El clip existente es de audio")
            if replace:
                _remove_all_notes(clip)
            if abs(clip.length - length) > 1e-6:
                clip.loop_end = length
                clip.end_marker = length
        else:
            if not self._track(track_index).has_midi_input:
                raise CommandError("La pista %d no es MIDI" % track_index)
            slot.create_clip(length)
            clip = slot.clip
        if name is not None:
            clip.name = name
        if color_index is not None:
            clip.color_index = int(color_index)
        clip.looping = bool(looping)
        _add_notes(clip, notes)
        return {"track_index": track_index, "slot_index": slot_index, "length": clip.length,
                "notes_written": len(notes)}

    def cmd_get_clip(self, track_index, slot_index, include_notes=True):
        clip = self._clip(track_index, slot_index)
        info = self._clip_summary(clip)
        if include_notes and clip.is_midi_clip:
            info["notes"] = _get_notes(clip)
        return info

    def cmd_clear_clip(self, track_index, slot_index):
        _remove_all_notes(self._clip(track_index, slot_index))
        return {}

    def cmd_delete_clip(self, track_index, slot_index):
        slot = self._clip_slot(track_index, slot_index)
        if slot.has_clip:
            slot.delete_clip()
        return {}

    def cmd_duplicate_clip(self, track_index, slot_index, target_slot_index):
        slot = self._clip_slot(track_index, slot_index)
        target = self._clip_slot(track_index, target_slot_index, create_scenes=True)
        if not slot.has_clip:
            raise CommandError("No hay clip para duplicar")
        slot.duplicate_clip_to(target)
        return {"slot_index": target_slot_index}

    def cmd_set_clip(self, track_index, slot_index, name=None, looping=None, loop_start=None, loop_end=None,
                     color_index=None):
        clip = self._clip(track_index, slot_index)
        if name is not None:
            clip.name = name
        if looping is not None:
            clip.looping = bool(looping)
        if loop_end is not None:
            clip.loop_end = float(loop_end)
        if loop_start is not None:
            clip.loop_start = float(loop_start)
        if color_index is not None:
            clip.color_index = int(color_index)
        return self._clip_summary(clip)

    def cmd_quantize_clip(self, track_index, slot_index, grid="1/16", amount=1.0):
        clip = self._clip(track_index, slot_index)
        key = str(grid).lower()
        if key not in QUANTIZE_GRIDS:
            raise CommandError("grid debe ser uno de: %s" % ", ".join(sorted(QUANTIZE_GRIDS)))
        clip.quantize(QUANTIZE_GRIDS[key], float(amount))
        return {}

    def cmd_fire_clip(self, track_index, slot_index):
        self._clip_slot(track_index, slot_index).fire()
        return {}

    def cmd_stop_track(self, track_index):
        self._track(track_index).stop_all_clips()
        return {}

    # ------------------------------------------------------------------
    # arrangement
    def cmd_clip_to_arrangement(self, track_index, slot_index, time):
        track = self._track(track_index)
        clip = self._clip(track_index, slot_index)
        if not hasattr(track, "duplicate_clip_to_arrangement"):
            raise CommandError("Tu versión de Live no permite copiar clips al Arrangement por API")
        track.duplicate_clip_to_arrangement(clip, float(time))
        return {"time": float(time), "length": clip.length}

    def cmd_get_arrangement_clips(self, track_index):
        track = self._track(track_index)
        clips = getattr(track, "arrangement_clips", None) or []
        return [{"name": c.name, "start_time": c.start_time, "end_time": c.end_time, "length": c.length}
                for c in clips]

    def cmd_clear_arrangement(self, track_index):
        track = self._track(track_index)
        clips = list(getattr(track, "arrangement_clips", None) or [])
        for c in clips:
            track.delete_clip(c)
        return {"deleted": len(clips)}

    # ------------------------------------------------------------------
    # devices
    def cmd_get_devices(self, track_index, include_parameters=True):
        track = self._track(track_index, allow_master=True)
        result = []
        for i, d in enumerate(track.devices):
            info = {"index": i, "name": d.name, "class_name": d.class_name,
                    "is_active": bool(d.is_active), "can_have_chains": bool(d.can_have_chains)}
            if include_parameters:
                info["parameters"] = [self._param_info(p, j) for j, p in enumerate(d.parameters)]
            result.append(info)
        return result

    def cmd_set_device_parameter(self, track_index, device_index, parameter, value):
        """parameter: index or (case-insensitive) name. value: number, or option name for quantized params."""
        device = self._device(track_index, device_index)
        params = list(device.parameters)
        p = None
        if isinstance(parameter, int):
            if 0 <= parameter < len(params):
                p = params[parameter]
        else:
            wanted = str(parameter).strip().lower()
            for candidate in params:
                if candidate.name.lower() == wanted:
                    p = candidate
                    break
        if p is None:
            raise CommandError("Parámetro no encontrado: %s. Disponibles: %s"
                               % (parameter, ", ".join(x.name for x in params)))
        if isinstance(value, str):
            options = [str(o).lower() for o in p.value_items] if p.is_quantized else []
            if value.lower() not in options:
                raise CommandError("Valor '%s' no válido. Opciones: %s" % (value, list(p.value_items)))
            value = p.min + options.index(value.lower())
        p.value = _clamp(float(value), p.min, p.max)
        return self._param_info(p, params.index(p))

    def cmd_delete_device(self, track_index, device_index):
        track = self._track(track_index, allow_master=True)
        self._device(track_index, device_index)
        track.delete_device(device_index)
        return {}

    # ------------------------------------------------------------------
    # browser
    def _browser(self):
        if self._app is None:
            raise CommandError("Browser no disponible")
        return self._app().browser

    def _roots(self, category):
        browser = self._browser()
        names = [category] if category else BROWSER_CATEGORIES
        roots = []
        for name in names:
            if name not in BROWSER_CATEGORIES:
                raise CommandError("Categoría inválida: %s. Usa: %s" % (name, ", ".join(BROWSER_CATEGORIES)))
            root = getattr(browser, name, None)
            if root is not None:
                roots.append((name, root))
        return roots

    def _item_info(self, item, path):
        self._uri_cache[item.uri] = item
        return {"name": item.name, "uri": item.uri, "path": path, "is_folder": bool(item.is_folder),
                "is_loadable": bool(item.is_loadable)}

    def cmd_browser_list(self, category, path=None):
        """List the children of a browser folder. path: list of folder names below the category."""
        (name, node), = self._roots(category)
        walked = [name]
        for part in path or []:
            match = None
            for child in node.children:
                if child.name.lower() == str(part).lower():
                    match = child
                    break
            if match is None:
                raise CommandError("No existe '%s' dentro de %s" % (part, "/".join(walked)))
            node = match
            walked.append(match.name)
        return [self._item_info(c, "/".join(walked + [c.name])) for c in node.children]

    def cmd_browser_search(self, query, category=None, max_results=20, max_depth=4, loadable_only=True,
                           exact=False):
        words = [w for w in str(query).lower().split() if w]
        results = []
        visited = 0
        for cat_name, root in self._roots(category):
            queue = deque((child, cat_name + "/" + child.name, 1) for child in root.children)
            while queue and len(results) < max_results and visited < MAX_BROWSER_NODES:
                item, path, depth = queue.popleft()
                visited += 1
                lname = item.name.lower()
                base = lname.rsplit(".", 1)[0] if "." in lname else lname
                matched = (base == " ".join(words) or lname == " ".join(words)) if exact \
                    else all(w in lname for w in words)
                if matched and (item.is_loadable or not loadable_only):
                    results.append(self._item_info(item, path))
                if item.is_folder and depth < max_depth:
                    for child in item.children:
                        queue.append((child, path + "/" + child.name, depth + 1))
            if len(results) >= max_results:
                break
        return results

    def cmd_load_item(self, track_index, uri):
        """Load a browser item (instrument, effect, kit, preset...) onto a track. Use a uri from browser_search."""
        item = self._uri_cache.get(uri)
        if item is None:
            raise CommandError("URI desconocida; búscala primero con browser_search o browser_list")
        track = self._track(track_index, allow_master=True)
        self.song.view.selected_track = track
        self._browser().load_item(item)
        return {"loaded": item.name, "track_index": track_index}


# ----------------------------------------------------------------------
# note helpers (Live 11+ extended API, with a legacy fallback)

def _add_notes(clip, notes):
    if not notes:
        return
    try:
        import Live
        spec = Live.Clip.MidiNoteSpecification
    except (ImportError, AttributeError):
        spec = None
    if spec is not None and hasattr(clip, "add_new_notes"):
        specs = []
        for n in notes:
            specs.append(spec(
                pitch=int(n["pitch"]), start_time=float(n["start"]), duration=float(n["duration"]),
                velocity=float(n.get("velocity", 100)), mute=bool(n.get("mute", False)),
                probability=float(n.get("probability", 1.0)),
                velocity_deviation=float(n.get("velocity_deviation", 0.0)),
            ))
        clip.add_new_notes(tuple(specs))
    else:
        clip.set_notes(tuple((int(n["pitch"]), float(n["start"]), float(n["duration"]),
                              int(n.get("velocity", 100)), bool(n.get("mute", False))) for n in notes))


def _remove_all_notes(clip):
    if hasattr(clip, "remove_notes_extended"):
        clip.remove_notes_extended(0, 128, -16384.0, 1000000.0)
    else:
        clip.remove_notes(-16384.0, 0, 1000000.0, 128)


def _get_notes(clip):
    if hasattr(clip, "get_notes_extended"):
        return [{"pitch": n.pitch, "start": n.start_time, "duration": n.duration, "velocity": n.velocity,
                 "mute": bool(n.mute), "probability": n.probability}
                for n in clip.get_notes_extended(0, 128, -16384.0, 1000000.0)]
    return [{"pitch": n[0], "start": n[1], "duration": n[2], "velocity": n[3], "mute": bool(n[4])}
            for n in clip.get_notes(-16384.0, 0, 1000000.0, 128)]


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _display(param):
    try:
        return param.str_for_value(param.value)
    except Exception:
        return None
