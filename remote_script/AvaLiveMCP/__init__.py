"""AvaLiveMCP - Ableton Live Remote Script.

Opens a local TCP socket (127.0.0.1:9877) so the Ava Live MCP server can
control Live. Requests are queued by the socket threads and executed on
Live's main thread from ``update_display`` because the Live API is not
thread-safe.

Requires Live 11 or newer (Python 3).
"""
from __future__ import absolute_import

try:
    import Live
    from _Framework.ControlSurface import ControlSurface
except ImportError:  # outside Live (tests)
    Live = None
    ControlSurface = object

from .handlers import Handlers
from .socket_server import SocketServer, DEFAULT_HOST, DEFAULT_PORT

VERSION = "1.0.0"


def create_instance(c_instance):
    return AvaLiveMCP(c_instance)


class AvaLiveMCP(ControlSurface):
    def __init__(self, c_instance):
        ControlSurface.__init__(self, c_instance)
        self._handlers = Handlers(
            song_getter=self.song,
            app_getter=Live.Application.get_application,
            log=self.log_message,
        )
        self._server = SocketServer(DEFAULT_HOST, DEFAULT_PORT, log=self.log_message)
        try:
            self._server.start()
            self.show_message("AvaLiveMCP %s escuchando en %s:%d" % (VERSION, DEFAULT_HOST, DEFAULT_PORT))
        except Exception as e:
            self.log_message("AvaLiveMCP: no se pudo abrir el socket: %s" % e)
            self.show_message("AvaLiveMCP: error abriendo el puerto %d" % DEFAULT_PORT)

    def update_display(self):
        ControlSurface.update_display(self)
        self._server.process_pending(self._handlers.dispatch)

    def disconnect(self):
        self._server.stop()
        ControlSurface.disconnect(self)
