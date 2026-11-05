"""eve-watch server: GET /, /frame.jpg, /api/board. Read-only; nothing here can send anything to Eve.

python -m eve_watch [--host <LAN address>] [--port 8953]      env: EVE_DB, EVE_PORT (mind), EVE_LINK_TOKEN
"""
import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files

from .board import board, cli_status

PAGE = files("eve_watch").joinpath("index.html").read_bytes()


class Status:
    """`eve status` cached for `ttl` s so several viewers make one /cli call per period."""

    def __init__(self, port, ttl=1.5):
        self.port, self.ttl, self.at, self.val, self.lock = port, ttl, 0.0, (None, "not fetched"), threading.Lock()

    def get(self):
        with self.lock:
            if time.monotonic() - self.at >= self.ttl:
                self.val, self.at = cli_status(self.port), time.monotonic()
            return self.val


def make_handler(db_path, status, camera):
    class H(BaseHTTPRequestHandler):
        def send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                return self.send(200, "text/html; charset=utf-8", PAGE)
            if path == "/frame.jpg":
                jpeg = camera.frame() if camera else None
                return self.send(200, "image/jpeg", jpeg) if jpeg else self.send(204, "text/plain", b"")
            if path == "/api/board":
                b = board(db_path, *status.get())
                b["capture"] = camera.info() if camera else {"note": "capture disabled"}
                return self.send(200, "application/json", json.dumps(b, default=str).encode())
            self.send(404, "text/plain", b"not found")

        def log_message(self, *a):          # ponytail: no access log; the page polls ~3x a second
            pass

    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=os.environ.get("EVE_WATCH_HOST", "127.0.0.1"), help="LAN address only; never a Tailscale address")
    ap.add_argument("--port", type=int, default=8953)
    ap.add_argument("--no-capture", action="store_true", help="board only (e.g. on Shark against a copied DB)")
    ap.add_argument("--client-match", help="capture only a game window whose java command line contains this (lane B)")
    a = ap.parse_args()
    db = os.environ.get("EVE_DB", r"C:\eve\mind\eve.db")
    camera = None
    if not a.no_capture:
        from .capture import Camera
        from . import capture
        capture.CLIENT_MATCH = a.client_match
        camera = Camera()
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(db, Status(int(os.environ.get("EVE_PORT", 8951))), camera))
    print(f"READY http://{a.host}:{a.port} db={db} capture={'on' if camera else 'off'}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
