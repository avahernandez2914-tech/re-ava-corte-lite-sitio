# Ava Live MCP: Claude produce en Ableton Live

Con esto, Claude puede **controlar Ableton Live y producir canciones**. Le escribes cosas como
*"hazme un reggaetón en La menor a 95 BPM con intro, dos versos y coro"*. Claude crea las pistas,
carga instrumentos, escribe batería, bajo, acordes y melodía, arma las escenas y deja la canción
en el Arrangement. Después puedes pedirle cambios: *"la melodía del coro más pegajosa"*,
*"baja el bajo 3 dB"*, *"ponle reverb a los acordes"*.

Está pensado como una versión mejorada y actualizada de proyectos como `ableton-mcp`:

- Usa el **SDK oficial de MCP 2.x** (`MCPServer`).
- Funciona con **Live 11 y Live 12**. Usa la API de notas extendida, con probabilidad por nota.
- Los comandos se ejecutan en el **hilo principal de Live**, a través de una cola. No usa hilos inseguros.
  Hay un comando `batch` que hace muchas operaciones en un solo ciclo, así que va rápido.
- Trae **herramientas de producción**, no solo de control:
  - generadores de batería por género, con variantes intro/build/break y fills;
  - bajos que siguen la progresión;
  - acordes con conducción de voces y arpegiador;
  - melodías basadas en motivos;
  - `create_song`, que arma una canción completa.
- Entiende progresiones en **números romanos** (`i VI III VII`, `ii7 V7 Imaj7`) o en **cifrado**
  (`Am F C G/B`).
- Géneros incluidos: reggaeton, dembow, trap, house, techno, hiphop (boom bap), lofi, pop,
  afrobeats y dnb.
- Nunca borra lo que ya tienes: añade pistas nuevas y usa las escenas vacías.

```
Claude (Desktop / Code)  ──MCP stdio──▶  ava-live-mcp (Python)  ──TCP 127.0.0.1:9877──▶  Remote Script AvaLiveMCP (dentro de Live)
```

## Requisitos

- Ableton Live **11 o 12**. Funciona en cualquier edición; con Suite tienes más instrumentos y kits.
- [uv](https://docs.astral.sh/uv/getting-started/installation/), que instala Python y las dependencias solo.
- Claude Desktop o Claude Code **en la misma computadora** donde corre Live.

## Instalación (una sola vez)

### 1. Descarga este repositorio

```bash
git clone https://github.com/avahernandez2914-tech/re-ava-corte-lite-sitio.git ava-live-mcp
cd ava-live-mcp
```

### 2. Instala el Remote Script en Live

```bash
uv run python scripts/install_remote_script.py
```

El script lo copia aquí:

- **macOS:** `~/Music/Ableton/User Library/Remote Scripts/AvaLiveMCP`
- **Windows:** `Documentos\Ableton\User Library\Remote Scripts\AvaLiveMCP`

Si tu User Library está en otro sitio (lo ves en *Preferencias → Library*), indícalo así:
`--dest "ruta/User Library/Remote Scripts"`.

### 3. Activa el Remote Script en Live

1. Reinicia Live.
2. Abre *Preferencias → Link, Tempo & MIDI*.
3. En **Control Surface**, elige **AvaLiveMCP**. En *Input* y *Output* deja *None*.
4. En la barra de estado de Live aparecerá: `AvaLiveMCP 1.0.0 escuchando en 127.0.0.1:9877`.

### 4. Conecta Claude

**Claude Code:**

```bash
claude mcp add ava-live -- uv --directory /RUTA/A/ava-live-mcp run ava-live-mcp
```

**Claude Desktop:** ve a *Settings → Developer → Edit Config* y añade esto a `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ava-live": {
      "command": "uv",
      "args": ["--directory", "/RUTA/A/ava-live-mcp", "run", "ava-live-mcp"]
    }
  }
}
```

En Windows, la ruta se escribe así: `"C:\\Users\\tu_usuario\\ava-live-mcp"`. Reinicia Claude Desktop después.

## Cómo usarlo

Abre un set en Live y pídele cosas a Claude. Por ejemplo:

- *"¿Estás conectado a Live? Dime qué hay en el set."*
- *"Produce un reggaetón en Do# menor a 92 BPM: intro, verso, precoro, coro, verso, coro, puente, coro, outro."*
- *"Haz un lofi con la progresión ii7 V7 Imaj7 vi7 en Fa mayor y acordes de Electric piano."*
- *"Genera otra melodía para el coro, más rítmica, con seed distinta."*
- *"Escribe un riff de bajo 808 que deslice entre Do y Mib en la escena 2."*
- *"Pon un Reverb en Chords y sube el Dry/Wet a 30 %."*
- *"Baja el volumen de la batería y pasa la escena 3 al Arrangement dos veces."*

También tienes el prompt **`produce_song`**: Claude te propone el plan, espera tu visto bueno y luego produce.

### Importar una canción existente: separar stems, voz, acordes y MIDI

`scripts/separar_cancion.py` toma un MP3, WAV, M4A o FLAC y genera una carpeta con:

- **stems/**: voz principal, coros, batería, bajo, otros (teclados, guitarras, sintes),
  instrumental sin voz y voz completa. Están en WAV 24 bits, con la misma duración, para que queden alineados.
- **midi/**: melodía de la voz, acordes, bajo, otros instrumentos y batería.
- **song.json**: BPM, tonalidad y acordes por compás.

```bash
# una sola vez (Python 3.10-3.11 y ffmpeg instalados)
uv venv -p 3.11 .venv-separar && uv pip install -p .venv-separar -r scripts/requirements-separacion.txt
# cada canción
.venv-separar/bin/python scripts/separar_cancion.py "Mi cancion.mp3" --out "Mi cancion"
```

En Windows, el intérprete está en `.venv-separar\Scripts\python`. Los modelos (unos 1.6 GB) se descargan
la primera vez. En CPU tarda unos 10 minutos por cada minuto de canción; con GPU NVIDIA es mucho más rápido.

Después, pídele a Claude: *"Importa la canción de la carpeta /ruta/Mi cancion"*. La herramienta
`import_song` hace esto:
- pone el tempo detectado;
- crea una pista de audio por stem, alineadas en el Arrangement;
- crea pistas MIDI con instrumentos (melodía, acordes, bajo, otros y batería), silenciadas para que
  suene el original;
- crea una pista vacía **"Nueva voz"** para grabar o añadir otra voz.

Para reemplazar una parte, silencia su stem y activa su pista MIDI. Cargar audio por API requiere
**Live 12**; en Live 11 tendrás que arrastrar los WAV a mano.

Modelos usados: BS-Roformer (voz), Mel-Roformer Karaoke (voz principal y coros), MDX-Net de
kuielab (batería y bajo) y Basic Pitch de Spotify (audio a MIDI). La separación es muy buena pero no
perfecta. El MIDI de acordes y de "otros" es aproximado y conviene revisarlo.

### Herramientas que tiene Claude

| Área | Herramientas |
|---|---|
| Estado y transporte | `live_status`, `get_session`, `get_track`, `set_tempo`, `set_time_signature`, `transport`, `set_metronome`, `set_loop`, `undo`, `redo` |
| Pistas y escenas | `create_track` (con instrumento), `delete_track`, `duplicate_track`, `set_track` (volumen/pan/mute/solo/arm/envíos), `create_scene`, `set_scene`, `delete_scene`, `fire_scene` |
| Clips | `write_notes`, `get_clip`, `delete_clip`, `duplicate_clip`, `set_clip`, `quantize_clip`, `fire_clip`, `stop_track` |
| Sonido | `search_browser`, `browse`, `load_device`, `get_devices`, `set_device_parameter`, `delete_device` |
| Arrangement | `session_to_arrangement`, `get_arrangement_clips`, `clear_arrangement` |
| Canciones importadas | `import_song` (stems + MIDI + pista "Nueva voz") |
| Composición | `list_genres`, `describe_progression`, `generate_drum_clip`, `generate_bass_clip`, `generate_chord_clip`, `generate_melody_clip`, `create_song` |

### Convenciones

- Los índices empiezan en 0. `track_index = -1` es la pista Master.
- Los tiempos van en *beats* (negras). En 4/4, un compás son 4 beats.
- Los nombres de nota siguen la convención de Ableton: **C3 = 60**. En un Drum Rack: C1 = 36 es el bombo,
  D1 = 38 la caja y F#1 = 42 el hi-hat.
- Patrones de 16 pasos por compás:
  - batería: `X` acento, `x` golpe, `o` suave, `r` redoble, `.` silencio;
  - bajo: `x` raíz, `o` octava, `5` quinta, `-` sostener.

## Estructura del proyecto

```
remote_script/AvaLiveMCP/   Remote Script que corre dentro de Live (Python 3 de Live)
  __init__.py               ControlSurface: arranca el socket y procesa la cola en update_display
  socket_server.py          servidor TCP JSON por líneas, sin tocar la API de Live desde hilos
  handlers.py               comandos (pistas, clips, notas, dispositivos, navegador, Arrangement)
src/ava_live_mcp/           servidor MCP (lo lanza Claude)
  server.py                 herramientas MCP
  connection.py             cliente TCP con reconexión
  theory.py                 notas, escalas, acordes, progresiones, voicings
  genres.py                 presets por género (BPM, tonalidad, progresión, patrones, instrumentos)
  composer.py               generadores y planificador de canciones
scripts/install_remote_script.py
tests/                      tests con un Live simulado (de extremo a extremo por TCP)
```

## Desarrollo

```bash
uv run --extra dev pytest
```

Los tests levantan el Remote Script real contra un Live simulado (`tests/fake_live.py`) y llaman a las
herramientas MCP por TCP. Puedes cambiar el host y el puerto con las variables `AVA_LIVE_HOST` y `AVA_LIVE_PORT`.
El puerto del Remote Script se define en `socket_server.py`.

## Problemas comunes

- **"No me puedo conectar a Ableton Live"**: Live no está abierto o AvaLiveMCP no está elegido como
  Control Surface. Si cambiaste archivos del Remote Script, reinicia Live.
- **No suena la batería**: la pista necesita un kit (Drum Rack con samples). Pide *"carga el 808 Core Kit
  en Drums"* o usa `search_browser` con la categoría `drums` para ver tus kits.
- **No encuentra un instrumento**: depende de tu edición de Live y de tus packs. `create_song` prueba
  varias opciones (Operator, Drift, Wavetable, Electric…) y te dice cuál cargó en cada pista.
- **Ver errores**: el Remote Script escribe en el `Log.txt` de Live, buscando `AvaLiveMCP`.

## Límites

Todo funciona con **MIDI** y control de Live. No genera voces ni audio "real" como Suno. La calidad
del resultado depende de tus instrumentos y de cómo guíes a Claude. `create_song` te da un boceto
sólido para seguir produciendo; no es un máster terminado.
