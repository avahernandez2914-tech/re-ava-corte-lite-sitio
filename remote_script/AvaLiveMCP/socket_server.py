"""Newline-delimited JSON socket server.

Protocol (one JSON object per line):
    request:  {"id": 1, "command": "set_tempo", "params": {"tempo": 120}}
    response: {"id": 1, "status": "ok", "result": {...}}
              {"id": 1, "status": "error", "error": "message"}

Socket threads never touch the Live API: they enqueue requests and wait
for ``process_pending`` (called on Live's main thread) to answer them.
"""
from __future__ import absolute_import

import json
import socket
import threading
import traceback

try:
    import queue
except ImportError:  # pragma: no cover
    import Queue as queue

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9877
REQUEST_TIMEOUT = 120.0


class SocketServer(object):
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, log=None):
        self.host = host
        self.port = port
        self._log = log or (lambda msg: None)
        self._pending = queue.Queue()
        self._running = False
        self._sock = None
        self._thread = None

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(4)
        sock.settimeout(0.5)
        self.port = sock.getsockname()[1]
        self._sock = sock
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, name="AvaLiveMCP-accept")
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        # Unblock clients still waiting for an answer.
        self.process_pending(lambda command, params: _raise_shutdown())

    # -- main thread -------------------------------------------------------
    def process_pending(self, dispatch):
        """Execute every queued request. Must run on Live's main thread."""
        while True:
            try:
                request, reply = self._pending.get_nowait()
            except queue.Empty:
                return
            req_id = request.get("id")
            try:
                result = dispatch(request.get("command"), request.get("params") or {})
                reply.put({"id": req_id, "status": "ok", "result": result})
            except Exception as e:
                self._log("AvaLiveMCP error en %s: %s\n%s" % (request.get("command"), e, traceback.format_exc()))
                reply.put({"id": req_id, "status": "error", "error": "%s: %s" % (type(e).__name__, e)})

    # -- socket threads ----------------------------------------------------
    def _accept_loop(self):
        while self._running:
            try:
                client, _ = self._sock.accept()
            except socket.timeout:
                continue
            except Exception:
                if self._running:
                    self._log("AvaLiveMCP: error aceptando conexión\n" + traceback.format_exc())
                break
            t = threading.Thread(target=self._client_loop, args=(client,), name="AvaLiveMCP-client")
            t.daemon = True
            t.start()

    def _client_loop(self, client):
        client.settimeout(None)
        buf = b""
        try:
            while self._running:
                chunk = client.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        response = self._handle_line(line)
                        client.sendall((json.dumps(response) + "\n").encode("utf-8"))
        except Exception:
            if self._running:
                self._log("AvaLiveMCP: cliente desconectado\n" + traceback.format_exc())
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _handle_line(self, line):
        try:
            request = json.loads(line.decode("utf-8"))
        except ValueError as e:
            return {"id": None, "status": "error", "error": "JSON inválido: %s" % e}
        reply = queue.Queue(maxsize=1)
        self._pending.put((request, reply))
        try:
            return reply.get(timeout=REQUEST_TIMEOUT)
        except queue.Empty:
            return {"id": request.get("id"), "status": "error",
                    "error": "Live no respondió a tiempo (¿está abierto el set?)"}


def _raise_shutdown():
    raise RuntimeError("AvaLiveMCP se está cerrando")
