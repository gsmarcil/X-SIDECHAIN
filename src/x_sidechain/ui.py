from __future__ import annotations

import contextlib
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path


class LocalUIRequestHandler(SimpleHTTPRequestHandler):
    """Serve the packaged prototype UI with local-only security headers."""

    def __init__(self, *args, directory: str | None = None, **kwargs) -> None:
        web_root = Path(str(files("x_sidechain").joinpath("web")))
        super().__init__(*args, directory=directory or str(web_root), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        super().log_message("UI " + format, *args)


def build_ui_server(port: int = 8765) -> ThreadingHTTPServer:
    if port < 0 or port > 65535:
        raise ValueError("UI port must be between 0 and 65535")
    return ThreadingHTTPServer(("127.0.0.1", port), LocalUIRequestHandler)


def serve_ui(port: int = 8765, open_browser: bool = True) -> None:
    """Run the preview UI on loopback; no provider secrets enter the browser."""
    server = build_ui_server(port)
    host, assigned_port = server.server_address
    url = f"http://{host}:{assigned_port}/"
    print(f"X-SIDECHAIN UI: {url}")
    print("Prototype mode: local interaction only; provider calls are not connected yet.")
    if open_browser:
        threading.Timer(0.25, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with contextlib.suppress(Exception):
            server.server_close()
