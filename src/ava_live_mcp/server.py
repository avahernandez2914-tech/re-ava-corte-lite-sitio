"""MCP server exposing Ableton Live control and song-production tools."""
from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from . import __version__
from .composer import (
    DEFAULT_STRUCTURE, ROLES, SECTION_TYPES, generate_bass, generate_chords, generate_drums,
    generate_melody, plan_song,
)
from .connection import LiveConnection, LiveError
from .genres import DEFAULT_INSTRUMENTS, DRUM_NOTES, GENRES, get_genre
from .theory import Key, TheoryError, midi_to_note, note_to_midi, parse_progression

INSTRUCTIONS = """\
Controlas Ableton Live (11 o 12) mediante el Remote Script AvaLiveMCP.

Convenciones:
- Índices desde 0 (pistas, escenas/slots, dispositivos). track_index=-1 es la pista Master en mezcla y dispositivos.
- Tiempos y duraciones en beats (negras). En 4/4 un compás = 4 beats. Una semicorchea = 0.25.
- Notas: número MIDI o nombre con la convención de Ableton (C3 = 60, C1 = 36 = bombo en Drum Rack).
- Volumen de pista 0..1 (0.85 ≈ 0 dB). Pan -1..1.
- Los generadores asumen 4/4.

Flujo recomendado para producir:
1. live_status / get_session para ver el set.
2. create_song para un boceto completo (pistas, instrumentos, escenas por sección y Arrangement), o
   construir por partes: create_track (+instrument), generate_*_clip o write_notes.
3. Escuchar (fire_scene / transport), ajustar con write_notes, set_track, set_device_parameter.
Usa undo si algo sale mal. Antes de borrar pistas o clips del usuario, confírmalo.
"""

mcp = MCPServer(name="ava-live", version=__version__, instructions=INSTRUCTIONS)
_live = LiveConnection()

TRACK_COLORS = {"drums": 14, "bass": 26, "chords": 41, "melody": 3}
TRACK_NAMES = {"drums": "Drums", "bass": "Bass", "chords": "Chords", "melody": "Melody"}


def live(command: str, **params: Any) -> Any:
    try:
        return _live.send(command, **params)
    except LiveError as e:
        raise ToolError(str(e)) from e


def live_batch(commands: list[dict]) -> list:
    try:
        return _live.batch(commands)
    except LiveError as e:
        raise ToolError(str(e)) from e


def _theory(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (TheoryError, ValueError) as e:
        raise ToolError(str(e)) from e


class NoteIn(BaseModel):
    pitch: int | str = Field(description="Número MIDI o nombre estilo Ableton ('C3' = 60, 'F#1').")
    start: float = Field(ge=0, description="Inicio en beats desde el comienzo del clip.")
    duration: float = Field(gt=0, description="Duración en beats.")
    velocity: int = Field(100, ge=1, le=127)
    mute: bool = False
    probability: float = Field(1.0, ge=0, le=1, description="Probabilidad de que suene (Live 11+).")


def _notes_payload(notes: list[NoteIn]) -> list[dict]:
    out = []
    for n in notes:
        d = n.model_dump()
        d["pitch"] = _theory(note_to_midi, n.pitch)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Session & transport

@mcp.tool()
def live_status() -> dict:
    """Comprueba la conexión con Ableton Live y devuelve su versión."""
    info = live("ping")
    info.pop("commands", None)
    return info


@mcp.tool()
def get_session(include_clips: bool = True) -> dict:
    """Estado del set: tempo, compás, pistas (dispositivos, clips), escenas, retornos y master."""
    return live("get_session_info", include_clips=include_clips)


@mcp.tool()
def get_track(track_index: int) -> dict:
    """Detalle de una pista: mezcla, envíos, dispositivos y clips."""
    return live("get_track_info", track_index=track_index)


@mcp.tool()
def set_tempo(bpm: float = Field(ge=20, le=999)) -> dict:
    """Cambia el tempo del set."""
    return live("set_tempo", tempo=bpm)


@mcp.tool()
def set_time_signature(numerator: int, denominator: int = 4) -> dict:
    """Cambia el compás (p.ej. 4/4, 6/8)."""
    return live("set_time_signature", numerator=numerator, denominator=denominator)


@mcp.tool()
def transport(action: Literal["play", "play_from_start", "stop", "stop_all_clips"],
              position_beats: float | None = None) -> dict:
    """Reproduce, detiene o para todos los clips. position_beats mueve el cursor del Arrangement."""
    if position_beats is not None:
        live("set_song_position", time=position_beats)
    if action == "play":
        return live("start_playback")
    if action == "play_from_start":
        return live("start_playback", from_start=True)
    if action == "stop":
        return live("stop_playback")
    return live("stop_all_clips")


@mcp.tool()
def set_metronome(on: bool) -> dict:
    """Activa o desactiva el metrónomo."""
    return live("set_metronome", on=on)


@mcp.tool()
def set_loop(on: bool = True, start_beats: float | None = None, length_beats: float | None = None) -> dict:
    """Configura el loop del Arrangement."""
    return live("set_loop", on=on, start=start_beats, length=length_beats)


@mcp.tool()
def undo(steps: int = Field(1, ge=1, le=50)) -> dict:
    """Deshace las últimas acciones en Live."""
    done = 0
    for _ in range(steps):
        if not live("undo")["undone"]:
            break
        done += 1
    return {"undone_steps": done}


@mcp.tool()
def redo() -> dict:
    """Rehace la última acción deshecha."""
    return live("redo")


# ---------------------------------------------------------------------------
# Tracks & scenes

def _load_first(track_index: int, queries: list[str], category: str) -> str | None:
    """Try each query (exact match first, then fuzzy) and load the first hit."""
    for exact in (True, False):
        for q in queries:
            hits = live("browser_search", query=q, category=category, max_results=1, exact=exact,
                        max_depth=2 if exact else 4)
            if hits:
                live("load_item", track_index=track_index, uri=hits[0]["uri"])
                return hits[0]["name"]
    return None


@mcp.tool()
def create_track(name: str | None = None, type: Literal["midi", "audio", "return"] = "midi",
                 index: int = -1, color_index: int | None = None,
                 instrument: str | None = None,
                 instrument_category: Literal["instruments", "drums", "sounds", "plugins",
                                              "user_library", "packs"] = "instruments") -> dict:
    """Crea una pista. instrument: nombre a buscar y cargar (p.ej. 'Drift', 'Operator', '808 Core Kit')."""
    result = live("create_track", type=type, index=index, name=name, color_index=color_index)
    if instrument:
        if type != "midi":
            raise ToolError("Solo se cargan instrumentos en pistas MIDI")
        loaded = _load_first(result["index"], [instrument], instrument_category)
        result["instrument"] = loaded or f"No encontré '{instrument}' en {instrument_category}"
    return result


@mcp.tool()
def delete_track(track_index: int) -> dict:
    """Borra una pista (confirma con el usuario si tiene material suyo)."""
    return live("delete_track", track_index=track_index)


@mcp.tool()
def duplicate_track(track_index: int) -> dict:
    """Duplica una pista con sus clips y dispositivos."""
    return live("duplicate_track", track_index=track_index)


@mcp.tool()
def set_track(track_index: int, name: str | None = None, volume: float | None = Field(None, ge=0, le=1),
              pan: float | None = Field(None, ge=-1, le=1), mute: bool | None = None, solo: bool | None = None,
              arm: bool | None = None, color_index: int | None = None,
              sends: list[float | None] | None = None) -> dict:
    """Mezcla y propiedades de pista. volume 0..1 (0.85≈0 dB), pan -1..1, sends 0..1 por retorno. -1 = Master."""
    return live("set_track", track_index=track_index, name=name, volume=volume, pan=pan, mute=mute,
                solo=solo, arm=arm, color_index=color_index, sends=sends)


@mcp.tool()
def create_scene(name: str | None = None, index: int = -1) -> dict:
    """Crea una escena (fila de clips en Session View)."""
    return live("create_scene", index=index, name=name)


@mcp.tool()
def set_scene(scene_index: int, name: str | None = None, color_index: int | None = None) -> dict:
    """Renombra o colorea una escena."""
    return live("set_scene", scene_index=scene_index, name=name, color_index=color_index)


@mcp.tool()
def delete_scene(scene_index: int) -> dict:
    """Borra una escena y sus clips."""
    return live("delete_scene", scene_index=scene_index)


@mcp.tool()
def fire_scene(scene_index: int) -> dict:
    """Lanza una escena (todas sus pistas)."""
    return live("fire_scene", scene_index=scene_index)


# ---------------------------------------------------------------------------
# Clips

@mcp.tool()
def write_notes(track_index: int, slot_index: int, notes: list[NoteIn], length_beats: float | None = None,
                name: str | None = None, replace: bool = True) -> dict:
    """Escribe notas MIDI en un clip de Session View (lo crea si no existe; crea escenas si faltan).

    replace=False añade las notas a las existentes. length_beats por defecto: redondeado al compás.
    """
    return live("write_clip", track_index=track_index, slot_index=slot_index, notes=_notes_payload(notes),
                length=length_beats, name=name, replace=replace)


@mcp.tool()
def get_clip(track_index: int, slot_index: int, include_notes: bool = True) -> dict:
    """Lee un clip y sus notas (con nombres de nota estilo Ableton)."""
    info = live("get_clip", track_index=track_index, slot_index=slot_index, include_notes=include_notes)
    for n in info.get("notes", []):
        n["name"] = midi_to_note(n["pitch"])
    return info


@mcp.tool()
def delete_clip(track_index: int, slot_index: int) -> dict:
    """Borra un clip de Session View."""
    return live("delete_clip", track_index=track_index, slot_index=slot_index)


@mcp.tool()
def duplicate_clip(track_index: int, slot_index: int, target_slot_index: int) -> dict:
    """Copia un clip a otro slot de la misma pista."""
    return live("duplicate_clip", track_index=track_index, slot_index=slot_index,
                target_slot_index=target_slot_index)


@mcp.tool()
def set_clip(track_index: int, slot_index: int, name: str | None = None, looping: bool | None = None,
             loop_start: float | None = None, loop_end: float | None = None,
             color_index: int | None = None) -> dict:
    """Cambia nombre, loop o color de un clip."""
    return live("set_clip", track_index=track_index, slot_index=slot_index, name=name, looping=looping,
                loop_start=loop_start, loop_end=loop_end, color_index=color_index)


@mcp.tool()
def quantize_clip(track_index: int, slot_index: int,
                  grid: Literal["1/4", "1/8", "1/8t", "1/16", "1/16t", "1/32"] = "1/16",
                  amount: float = Field(1.0, ge=0, le=1)) -> dict:
    """Cuantiza las notas de un clip."""
    return live("quantize_clip", track_index=track_index, slot_index=slot_index, grid=grid, amount=amount)


@mcp.tool()
def fire_clip(track_index: int, slot_index: int) -> dict:
    """Lanza un clip."""
    return live("fire_clip", track_index=track_index, slot_index=slot_index)


@mcp.tool()
def stop_track(track_index: int) -> dict:
    """Detiene los clips de una pista."""
    return live("stop_track", track_index=track_index)


# ---------------------------------------------------------------------------
# Devices & browser

BrowserCategory = Literal["instruments", "drums", "sounds", "audio_effects", "midi_effects", "max_for_live",
                          "plugins", "clips", "samples", "packs", "user_library", "current_project"]


@mcp.tool()
def search_browser(query: str, category: BrowserCategory | None = None, max_results: int = 20,
                   exact: bool = False) -> list[dict]:
    """Busca en el navegador de Live (instrumentos, kits, presets, efectos, plugins, samples)."""
    return live("browser_search", query=query, category=category, max_results=max_results, exact=exact)


@mcp.tool()
def browse(category: BrowserCategory, path: list[str] | None = None) -> list[dict]:
    """Lista el contenido de una carpeta del navegador. path: carpetas debajo de la categoría."""
    return live("browser_list", category=category, path=path)


@mcp.tool()
def load_device(track_index: int, query: str | None = None, uri: str | None = None,
                category: BrowserCategory | None = None) -> dict:
    """Carga un instrumento/efecto/preset en una pista (por uri de search_browser, o buscando query).

    Ejemplos: query='Reverb' category='audio_effects'; query='Grand Piano' category='sounds'.
    """
    if uri:
        return live("load_item", track_index=track_index, uri=uri)
    if not query:
        raise ToolError("Indica query o uri")
    hits = live("browser_search", query=query, category=category, max_results=1, exact=True) or \
        live("browser_search", query=query, category=category, max_results=1)
    if not hits:
        raise ToolError(f"No encontré '{query}' en el navegador")
    return live("load_item", track_index=track_index, uri=hits[0]["uri"])


@mcp.tool()
def get_devices(track_index: int, include_parameters: bool = True) -> list[dict]:
    """Dispositivos de una pista (-1 = Master) y sus parámetros."""
    return live("get_devices", track_index=track_index, include_parameters=include_parameters)


@mcp.tool()
def set_device_parameter(track_index: int, device_index: int, parameter: int | str,
                         value: float | str) -> dict:
    """Ajusta un parámetro por índice o nombre. value: número en el rango del parámetro u opción (texto)."""
    return live("set_device_parameter", track_index=track_index, device_index=device_index,
                parameter=parameter, value=value)


@mcp.tool()
def delete_device(track_index: int, device_index: int) -> dict:
    """Quita un dispositivo de una pista."""
    return live("delete_device", track_index=track_index, device_index=device_index)


# ---------------------------------------------------------------------------
# Arrangement

def _arrange(scene_order: list[int], start_beat: float, track_indices: list[int] | None) -> dict:
    session = live("get_session_info", include_clips=True)
    clips = {}  # (track, slot) -> length
    for t in session["tracks"]:
        if track_indices is not None and t["index"] not in track_indices:
            continue
        for c in t.get("clips", []):
            clips[(t["index"], c["slot"])] = c["length"]
    time = start_beat
    placed = []
    for scene in scene_order:
        in_scene = {track: length for (track, slot), length in clips.items() if slot == scene}
        if not in_scene:
            continue
        live_batch([{"command": "clip_to_arrangement",
                     "params": {"track_index": track, "slot_index": scene, "time": time}}
                    for track in sorted(in_scene)])
        placed.append({"scene": scene, "start_beat": time})
        time += max(in_scene.values())
    return {"placed": placed, "end_beat": time}


@mcp.tool()
def session_to_arrangement(scene_order: list[int], start_beat: float = 0,
                           track_indices: list[int] | None = None) -> dict:
    """Copia escenas de Session View al Arrangement en el orden dado (se pueden repetir)."""
    return _arrange(scene_order, start_beat, track_indices)


@mcp.tool()
def get_arrangement_clips(track_index: int) -> list[dict]:
    """Clips de una pista en el Arrangement."""
    return live("get_arrangement_clips", track_index=track_index)


@mcp.tool()
def clear_arrangement(track_index: int) -> dict:
    """Borra todos los clips del Arrangement de una pista (confirma con el usuario)."""
    return live("clear_arrangement", track_index=track_index)


# ---------------------------------------------------------------------------
# Composition

@mcp.tool()
def list_genres() -> dict:
    """Géneros disponibles con sus valores por defecto, secciones y mapa de batería."""
    return {
        "genres": {g.name: {"description": g.description, "bpm": g.bpm, "key": g.key,
                            "progression": g.progression, "swing": g.swing} for g in GENRES.values()},
        "sections": {k: v["bars"] for k, v in SECTION_TYPES.items()},
        "default_structure": DEFAULT_STRUCTURE,
        "drum_map": {k: f"{v} ({midi_to_note(v)})" for k, v in DRUM_NOTES.items()},
    }


@mcp.tool()
def describe_progression(key: str, progression: str) -> dict:
    """Traduce una progresión (romanos 'i VI III VII' o cifrado 'Am F C G') a acordes y notas."""
    k = _theory(Key.parse, key)
    chords = _theory(parse_progression, progression, k)
    from .theory import voice_chord
    return {"key": str(k), "scale_notes": [midi_to_note(p) for p in k.scale_notes(60, 71)],
            "chords": [{"symbol": c.symbol, "notes": [midi_to_note(p) for p in voice_chord(c, 62)]}
                       for c in chords]}


def _write_generated(track_index: int, slot_index: int, notes: list[dict], bars: int, name: str) -> dict:
    result = live("write_clip", track_index=track_index, slot_index=slot_index, notes=notes,
                  length=bars * 4.0, name=name)
    result["preview"] = [dict(n, name=midi_to_note(n["pitch"])) for n in notes[:12]]
    return result


@mcp.tool()
def generate_drum_clip(track_index: int, slot_index: int, genre: str = "reggaeton", bars: int = 4,
                       variant: Literal["full", "light", "break", "build"] = "full", fill: bool = True,
                       patterns: dict[str, str] | None = None, swing: float | None = None,
                       humanize: float = 0.3, seed: int | None = None) -> dict:
    """Genera un patrón de batería en un clip (la pista debe tener un Drum Rack).

    patterns (opcional) sustituye al del género: {"kick": "x...x...x...x...", "snare": "....x......."}
    con 16 pasos por compás: X acento, x golpe, o suave, r redoble, . silencio.
    """
    g = _theory(get_genre, genre)
    notes = _theory(generate_drums, g, bars, variant, fill, False, swing, humanize, seed, patterns)
    return _write_generated(track_index, slot_index, notes, bars, f"{g.name} drums")


@mcp.tool()
def generate_bass_clip(track_index: int, slot_index: int, key: str, progression: str, bars: int = 4,
                       genre: str | None = "reggaeton", pattern: str | None = None, center: str | int = "C1",
                       bars_per_chord: float = 1, seed: int | None = None) -> dict:
    """Genera una línea de bajo que sigue la progresión.

    pattern: 16 pasos/compás. x raíz, o octava, 5 quinta, 3 tercera, 7 séptima, a aproximación, - sostener, . silencio.
    """
    k = _theory(Key.parse, key)
    chords = _theory(parse_progression, progression, k)
    g = _theory(get_genre, genre) if genre else None
    pat = pattern or (g.bass if g else "x-------x-------")
    notes = _theory(generate_bass, k, chords, bars, pat, _theory(note_to_midi, center), bars_per_chord,
                    g.swing if g else 0.0, seed=seed)
    return _write_generated(track_index, slot_index, notes, bars, "bass")


@mcp.tool()
def generate_chord_clip(track_index: int, slot_index: int, key: str, progression: str, bars: int = 4,
                        genre: str | None = None, pattern: str | None = None, center: str | int = "D3",
                        bars_per_chord: float = 1, spread: bool = False,
                        arp: Literal["up", "down", "updown", "random"] | None = None, arp_rate: int = 2,
                        seed: int | None = None) -> dict:
    """Genera acordes con conducción de voces. pattern: 16 pasos/compás (x golpe, - sostener, . silencio).

    arp convierte los acordes en arpegio (arp_rate en semicorcheas: 1=1/16, 2=1/8, 4=1/4).
    """
    k = _theory(Key.parse, key)
    chords = _theory(parse_progression, progression, k)
    g = _theory(get_genre, genre) if genre else None
    pat = pattern or (g.chords if g else "x" + "-" * 15)
    notes = _theory(generate_chords, k, chords, bars, pat, _theory(note_to_midi, center), bars_per_chord,
                    spread, arp, arp_rate, g.swing if g else 0.0, seed=seed)
    return _write_generated(track_index, slot_index, notes, bars, "chords")


@mcp.tool()
def generate_melody_clip(track_index: int, slot_index: int, key: str, progression: str, bars: int = 4,
                         density: float = Field(0.5, ge=0, le=1), low: str | int = "G3", high: str | int = "C5",
                         bars_per_chord: float = 1, motif_bars: int = 2, swing: float = 0.0,
                         seed: int | None = None) -> dict:
    """Genera una melodía basada en un motivo que se adapta a cada acorde. Cambia seed para otra idea."""
    k = _theory(Key.parse, key)
    chords = _theory(parse_progression, progression, k)
    notes = _theory(generate_melody, k, chords, bars, density, _theory(note_to_midi, low),
                    _theory(note_to_midi, high), bars_per_chord, motif_bars, swing, seed=seed)
    return _write_generated(track_index, slot_index, notes, bars, "melody")


@mcp.tool()
def create_song(genre: str = "reggaeton", key: str | None = None, progression: str | None = None,
                bpm: float | None = None, structure: list[str] | None = None, seed: int | None = None,
                bars_per_chord: float = 1, melody_density: float | None = None,
                roles: list[Literal["drums", "bass", "chords", "melody"]] | None = None,
                load_instruments: bool = True, instruments: dict[str, str] | None = None,
                to_arrangement: bool = True, title: str | None = None) -> dict:
    """Produce un boceto completo de canción en el set abierto.

    Crea pistas (Drums, Bass, Chords, Melody) con instrumentos, una escena por sección con sus clips,
    y opcionalmente la canción entera en el Arrangement.
    structure: secciones en orden, con compases opcionales: ["intro", "verso:8", "coro:8", "puente", "outro"].
    Secciones: intro, verse, prechorus, chorus, bridge, breakdown, build, drop, outro (o en español).
    instruments: sobreescribe la búsqueda por rol, p.ej. {"chords": "Grand Piano", "drums": "808 Core Kit"}.
    No borra nada: añade pistas y usa escenas vacías tras el material existente.
    """
    plan = _theory(plan_song, genre, key, progression, bpm, structure, seed, bars_per_chord, melody_density)
    wanted = [r for r in ROLES if (roles is None or r in roles) and any(r in s.parts for s in plan.sections)]

    live("set_tempo", tempo=plan.bpm)
    session = live("get_session_info", include_clips=True)
    used_slots = [c["slot"] for t in session["tracks"] for c in t.get("clips", [])]
    first_scene = max(used_slots) + 1 if used_slots else 0

    tracks: dict[str, int] = {}
    loaded: dict[str, str | None] = {}
    prefix = f"{title} " if title else ""
    for role in wanted:
        res = live("create_track", type="midi", name=prefix + TRACK_NAMES[role], color_index=TRACK_COLORS[role])
        tracks[role] = res["index"]
        if load_instruments:
            category = "drums" if role == "drums" else "instruments"
            custom = (instruments or {}).get(role)
            name = _load_first(res["index"], [custom], None) if custom else None
            loaded[role] = name or _load_first(res["index"], plan.genre.instruments.get(role)
                                               or DEFAULT_INSTRUMENTS[role], category)

    needed = first_scene + len(plan.sections)
    have = len(session["scenes"])
    commands = [{"command": "create_scene", "params": {"index": -1}} for _ in range(max(0, needed - have))]
    commands += [{"command": "set_scene", "params": {"scene_index": first_scene + i, "name": s.name}}
                 for i, s in enumerate(plan.sections)]
    live_batch(commands)

    for i, section in enumerate(plan.sections):
        live_batch([{"command": "write_clip", "params": {
            "track_index": tracks[role], "slot_index": first_scene + i, "notes": notes,
            "length": section.bars * 4.0, "name": f"{section.name} {TRACK_NAMES[role]}",
            "color_index": TRACK_COLORS[role]}}
            for role, notes in section.parts.items() if role in tracks])

    result = plan.summary()
    result.update(tracks=tracks, instruments=loaded, scenes={s.name: first_scene + i
                                                            for i, s in enumerate(plan.sections)})
    if to_arrangement:
        result["arrangement"] = _arrange([first_scene + i for i in range(len(plan.sections))], 0.0,
                                         list(tracks.values()))
    result["next_steps"] = ("Escucha con transport('play_from_start') o fire_scene. Ajusta partes con "
                            "generate_*_clip (otra seed) o write_notes; mezcla con set_track.")
    return result


# ---------------------------------------------------------------------------
# Imported songs (output of scripts/separar_cancion.py)

AUDIO_STEMS = ["Voz principal", "Coros", "Bateria", "Bajo", "Otros"]
MIX_STEMS = ["Instrumental", "Voz completa"]
MIDI_PARTS = {  # part -> (track name, browser category, instrument candidates)
    "melody": ("Melodía voz (MIDI)", "instruments", ["Drift", "Wavetable", "Analog"]),
    "chords": ("Acordes (MIDI)", "instruments", ["Electric", "Wavetable", "Drift"]),
    "bass": ("Bajo (MIDI)", "instruments", ["Operator", "Drift", "Analog"]),
    "other": ("Otros (MIDI)", "instruments", ["Wavetable", "Drift"]),
    "drums": ("Batería (MIDI)", "drums", ["808 Core Kit", "909 Core Kit", "Kit"]),
}


@mcp.tool()
def import_song(folder: str, midi_parts: list[Literal["melody", "chords", "bass", "other", "drums"]] | None = None,
                include_mix_stems: bool = False, mute_midi: bool = True, load_instruments: bool = True,
                new_vocal_tracks: int = Field(1, ge=0, le=8), to_arrangement: bool = True) -> dict:
    """Monta en Live una canción separada con scripts/separar_cancion.py (carpeta con song.json).

    Crea: pistas de audio por stem (voz principal, coros, batería, bajo, otros) alineadas en el
    Arrangement; pistas MIDI con instrumentos para melodía, acordes, bajo, otros y batería
    (silenciadas por defecto para que suene el original: desmútalas para reemplazar partes);
    y pistas de audio vacías "Nueva voz" para grabar o añadir otra voz.
    Pon el tempo detectado y no borra nada existente.
    """
    import json
    from pathlib import Path

    root = Path(folder).expanduser()
    meta_file = root / "song.json"
    if not meta_file.exists():
        raise ToolError(f"No encuentro {meta_file}. Primero ejecuta scripts/separar_cancion.py")
    song = json.loads(meta_file.read_text(encoding="utf-8"))
    bpm = song["bpm"]
    live("set_tempo", tempo=bpm)
    session = live("get_session_info", include_clips=True)
    used = [c["slot"] for t in session["tracks"] for c in t.get("clips", [])]
    slot = max(used) + 1 if used else 0
    length_beats = song["duration_seconds"] * bpm / 60.0
    clip_len = max(4.0, -(-length_beats // 4) * 4)  # whole bars

    created: dict[str, Any] = {"audio": {}, "midi": {}, "new_vocals": []}
    warnings = []
    stems = [s for s in AUDIO_STEMS + (MIX_STEMS if include_mix_stems else []) if s in song["stems"]]
    for name in stems:
        path = (root / song["stems"][name]).resolve()
        t = live("create_track", type="audio", name=name)
        try:
            if to_arrangement:
                live("load_audio_file", track_index=t["index"], file_path=str(path), arrangement_time=0.0,
                     warping=False, name=name)
            else:
                live("load_audio_file", track_index=t["index"], file_path=str(path), slot_index=slot,
                     warping=False, name=name)
        except ToolError as e:
            warnings.append(f"{name}: {e}. Arrastra {path} a la pista.")
        created["audio"][name] = t["index"]

    for _ in range(new_vocal_tracks):
        t = live("create_track", type="audio", name="Nueva voz")
        created["new_vocals"].append(t["index"])

    for part in midi_parts or list(MIDI_PARTS):
        notes = [n for n in song["parts"].get(part, []) if n["start"] < clip_len]
        if not notes:
            continue
        track_name, category, candidates = MIDI_PARTS[part]
        t = live("create_track", type="midi", name=track_name)
        if load_instruments:
            created.setdefault("instruments", {})[part] = _load_first(t["index"], candidates, category)
        live("write_clip", track_index=t["index"], slot_index=slot, notes=notes, length=clip_len,
             name=track_name)
        if to_arrangement:
            live("clip_to_arrangement", track_index=t["index"], slot_index=slot, time=0.0)
        if mute_midi:
            live("set_track", track_index=t["index"], mute=True)
        created["midi"][part] = t["index"]

    if to_arrangement:
        live("set_song_position", time=0.0)
    return {"bpm": bpm, "key": song["key"], "chords_per_bar": song["chords_per_bar"][:32],
            "most_common_chords": song.get("most_common_chords"), "tracks": created, "warnings": warnings,
            "tip": "Las pistas MIDI están silenciadas: desmútalas y silencia el stem original para "
                   "reemplazar esa parte. Graba la nueva voz en 'Nueva voz'."}


# ---------------------------------------------------------------------------
# Prompts

@mcp.prompt()
def produce_song(idea: str) -> str:
    """Guía para producir una canción a partir de una idea."""
    return (
        f"Quiero producir esta canción en Ableton Live: {idea}\n\n"
        "1. Revisa el set con get_session.\n"
        "2. Propón género, tonalidad, BPM, progresión y estructura en 3-4 líneas y pregúntame si te parece.\n"
        "3. Con mi visto bueno usa create_song.\n"
        "4. Mejora las partes: vuelve a generar lo que no encaje (otra seed), escribe a mano con write_notes "
        "melodías/ganchos más musicales, ajusta mezcla (set_track) y añade efectos (load_device: Reverb, "
        "Delay, Glue Compressor...).\n"
        "5. Resume qué hiciste y qué puedo probar a continuación."
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
