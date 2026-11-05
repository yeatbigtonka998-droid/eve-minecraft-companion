"""A tiny scripted HTTP server standing in for Ollama (/api/chat, /api/tags) and an OpenAI-compatible API."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeHTTP:
    def __init__(self):
        self.requests, self.reply, self.delay_s, self.models = [], None, 0.0, ["qwen3:8b"]
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send({"models": [{"name": m} for m in outer.models]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
                time.sleep(outer.delay_s)
                r = outer.reply(body) if callable(outer.reply) else outer.reply
                try:
                    self._send(r)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def ollama(self, content):
        self.reply = {"message": {"role": "assistant", "content": content if isinstance(content, str)
                                  else json.dumps(content)}, "done": True}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
