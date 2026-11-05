"""Loopback stand-in for the mind's /body hub (contracts/link-protocol.md) until parts/mind ships one. Hub() is importable.

    set EVE_LINK_TOKEN=<dev token>   (same value the client reads; never commit it)
    py -3.12 tools/stub_mind.py [--log frames.jsonl]

stdin: stop [reason] | resume | home | mode <m> | do <skill> [json params] | raw <json envelope> | quit
"""
import hashlib, hmac, json, os, sys, threading, time, uuid
from pathlib import Path

from websockets.sync.server import serve

# EVE_REGISTRY (dev only, O101j): the registry a dev client was built with (-Peve.registry=...), so the hashes agree
REGISTRY = Path(os.environ.get("EVE_REGISTRY") or Path(__file__).resolve().parents[3] / "contracts" / "skills" / "registry.json")


class Hub:
    def __init__(self, log_path=None, token=None, verbose=True):
        self.token = token or os.environ.get("EVE_LINK_TOKEN", "")
        self.reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.hash = hashlib.sha256(json.dumps(self.reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        self.versions = {s["name"]: s["version"] for s in self.reg["skills"]}
        self.conn = self.session = None
        self.frames, self.cv, self.mismatch, self.verbose = [], threading.Condition(), 0, verbose
        self.log = open(log_path, "a", encoding="utf-8") if log_path else None

    def start(self, host="127.0.0.1", port=int(os.environ.get("EVE_HUB_PORT", 8952))):  # 8951 is the real mind's
        self.server = serve(self.handler, host, port, max_size=262_144)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        threading.Thread(target=self._pinger, daemon=True).start()
        return self

    def _pinger(self):
        while True:
            time.sleep(5)
            if self.conn:
                self.send("ping", {})

    def record(self, direction, m):
        if m.get("type") == "hello":
            m = {**m, "payload": {**m["payload"], "token": "<redacted>"}}
        f = {"dir": direction, "t": time.time(), "msg": m}
        with self.cv:
            self.frames.append(f)
            self.cv.notify_all()
        if self.log:
            self.log.write(json.dumps(f) + "\n")
            self.log.flush()
        if self.verbose and m.get("type") not in ("ping", "pong"):
            p = m.get("payload", {})
            if m.get("type") == "state":
                print(f"{direction} state pos={p['pos']} hp={p['health']} ctl={p['control']} age={p['freshness']}")
            else:
                print(direction, m.get("type"), json.dumps(p)[:400])

    def handler(self, ws):
        if ws.request.path != "/body":
            return ws.close(1008, "unknown path")
        try:
            first = json.loads(ws.recv(timeout=10))
        except Exception:
            return ws.close(1002, "need hello")
        tok = str(first.get("payload", {}).get("token", ""))
        if first.get("type") != "hello" or not self.token or not hmac.compare_digest(tok.encode(), self.token.encode()):
            self._raw(ws, self.envelope("hello.reject", {"code": "auth"}, None, None))
            return ws.close(1008, "auth")
        self.record("in", first)
        body_hash = first["payload"].get("registry_hash")
        print("registry hash", "MATCH" if body_hash == self.hash else f"MISMATCH body={body_hash} stub={self.hash}")
        if self.conn:
            self.conn.close(1000, "superseded")
        self.conn, self.session = ws, str(uuid.uuid4())
        self.send("hello.ok", {"session": self.session, "registry_hash": self.hash, "state_hz": 4, "mode": "manual"})
        try:
            for raw in ws:
                m = json.loads(raw)
                self.record("in", m)
                if m.get("session") != self.session:
                    self.mismatch += 1
                elif m.get("type") == "ping":
                    self.send("pong", {}, m["id"])
        finally:
            if self.conn is ws:
                self.conn = None

    def envelope(self, type_, payload, reply_to, session):
        return {"v": 1, "id": str(uuid.uuid4()), "ts": int(time.time() * 1000), "tick": None, "from": "mind",
                "type": type_, "reply_to": reply_to, "session": session, "world": None, "payload": payload}

    def _raw(self, ws, m):
        ws.send(m if isinstance(m, str) else json.dumps(m))
        if not isinstance(m, str):
            self.record("out", m)

    def send(self, type_, payload, reply_to=None):
        m = self.envelope(type_, payload, reply_to, self.session)
        if self.conn:
            self._raw(self.conn, m)
        return m

    def request(self, skill, params=None, deadline_ms=None, action_id=None):
        aid = action_id or str(uuid.uuid4())
        self.send("action.request", {"action_id": aid, "skill": skill, "version": self.versions.get(skill, 1),
                                     "params": params or {}, "deadline_ms": deadline_ms, "idempotency_key": aid})
        return aid

    def wait(self, pred, timeout=10.0, since=0):
        """First inbound frame (index >= since) whose message satisfies pred, else None."""
        end = time.time() + timeout
        with self.cv:
            while True:
                for f in self.frames[since:]:
                    if f["dir"] == "in" and pred(f["msg"]):
                        return f
                left = end - time.time()
                if left <= 0:
                    return None
                self.cv.wait(left)

    def wait_result(self, aid, timeout=30.0, since=0):
        f = self.wait(lambda m: m["type"] in ("action.result", "action.rejected") and m["payload"].get("action_id") == aid, timeout, since)
        return f and f["msg"]["payload"]


def main():
    log = sys.argv[sys.argv.index("--log") + 1] if "--log" in sys.argv else None
    hub = Hub(log).start()
    print("stub mind on port", os.environ.get("EVE_HUB_PORT", 8952), "/body; registry", hub.hash[:12], "(token set)" if hub.token else "(NO EVE_LINK_TOKEN: every hello is rejected)")
    for line in sys.stdin:
        cmd, _, rest = line.strip().partition(" ")
        if cmd == "stop":
            hub.send("control.stop", {"reason": rest or "stub", "source": "stub"})
        elif cmd == "resume":
            hub.send("control.resume", {"source": "stub"})
        elif cmd == "home":
            hub.send("control.home", {"reason": rest or "stub"})
        elif cmd == "mode":
            hub.send("control.mode", {"mode": rest})
        elif cmd == "do":
            skill, _, params = rest.partition(" ")
            print("action_id", hub.request(skill, json.loads(params) if params else {}))
        elif cmd == "raw" and hub.conn:
            hub.conn.send(rest)
        elif cmd == "quit":
            break
        elif cmd:
            print("? stop|resume|home|mode <m>|do <skill> [json]|raw <json>|quit")


if __name__ == "__main__":
    main()
