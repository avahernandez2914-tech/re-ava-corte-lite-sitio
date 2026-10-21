"""TCP client for the AvaLiveMCP Remote Script running inside Ableton Live."""
from __future__ import annotations

import itertools
import json
import os
import socket
import threading
from typing import Any

DEFAULT_HOST = os.environ.get("AVA_LIVE_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("AVA_LIVE_PORT", "9877"))


class LiveError(RuntimeError):
    """Live answered with an error, or could not be reached."""


class LiveConnection:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 130.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._reader = None
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    def _connect(self) -> None:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=5)
        except OSError as e:
            raise LiveError(
                f"No me puedo conectar a Ableton Live en {self.host}:{self.port}. "
                "Abre Live y elige 'AvaLiveMCP' como Control Surface en Preferencias > Link, Tempo & MIDI."
            ) from e
        sock.settimeout(self.timeout)
        self._sock = sock
        self._reader = sock.makefile("rb")

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._reader.close()
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._reader = None

    def send(self, command: str, **params: Any) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        with self._lock:
            for attempt in (1, 2):
                if self._sock is None:
                    self._connect()
                req_id = next(self._ids)
                payload = (json.dumps({"id": req_id, "command": command, "params": params}) + "\n").encode()
                try:
                    self._sock.sendall(payload)
                    line = self._reader.readline()
                    if not line:
                        raise ConnectionError("conexión cerrada por Live")
                except (OSError, ConnectionError) as e:
                    self.close()
                    if attempt == 2 or isinstance(e, socket.timeout):
                        raise LiveError(f"Se perdió la conexión con Live: {e}") from e
                    continue
                response = json.loads(line)
                if response.get("status") != "ok":
                    raise LiveError(response.get("error", "error desconocido"))
                return response.get("result")
        raise LiveError("sin respuesta")  # pragma: no cover

    def batch(self, commands: list[dict], stop_on_error: bool = True) -> list:
        results = self.send("batch", commands=commands, stop_on_error=stop_on_error)
        failed = [r for r in results if not r.get("ok")]
        if failed and stop_on_error:
            raise LiveError(failed[0]["error"])
        return results
