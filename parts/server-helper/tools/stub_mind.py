"""Loopback stand-in for the mind's /sense hub (link-protocol v1) until parts/mind is live.

Accepts the sense hello (token from env EVE_LINK_TOKEN, constant-time compare), prints every message,
answers ping, sends ping every 5 s, and lets you send requests from stdin or --send:
  storage [id ...]            storage.request
  check <effect> <dim> <x> <y> <z> [hazard]   permission.check
  zones                       zone.list
  raw <type> <json-payload>   anything else
  big                         a 300 KB frame (must be refused by sense)
  sleep <s>                   (--send only) wait before the next scripted command
Usage: py -3.12 tools/stub_mind.py [--log out.jsonl] [--send "storage" --send "zones" ...] [--exit-after 20]
"""
import argparse, asyncio, hmac, json, os, sys, time, uuid

import websockets

MAX_FRAME = 262_144


def env(type_, payload, session, reply_to=None):
    return {"v": 1, "id": str(uuid.uuid4()), "ts": int(time.time() * 1000), "tick": None, "from": "mind",
            "type": type_, "reply_to": reply_to, "session": session, "world": None, "payload": payload}


def parse_cmd(line):
    p = line.split()
    if not p:
        return None
    if p[0] == "storage":
        return "storage.request", ({"ids": p[1:]} if len(p) > 1 else {})
    if p[0] == "zones":
        return "zone.list", {}
    if p[0] == "check" and len(p) >= 6:
        d = {"effect": p[1], "dim": p[2], "actor": "eve", "pos": {"x": int(p[3]), "y": int(p[4]), "z": int(p[5])}}
        if len(p) > 6:
            d["hazard"] = p[6]
        return "permission.check", d
    if p[0] == "raw" and len(p) >= 3:
        return p[1], json.loads(" ".join(p[2:]))
    if p[0] == "big":
        return "raw.big", {"pad": "x" * 300_000}
    print(f"?? {line}", flush=True)
    return None


class Hub:
    def __init__(self, args):
        self.args, self.token = args, os.environ.get("EVE_LINK_TOKEN", "")
        self.log = open(args.log, "a", encoding="utf-8") if args.log else None
        self.ws = self.session = None
        self.scripted_done = False

    def show(self, direction, msg):
        shown = json.loads(json.dumps(msg))
        if shown.get("type") == "hello":
            shown["payload"]["token"] = "<redacted>"
        line = json.dumps(shown, separators=(",", ":"))
        print(f"{direction} {line[:2000]}", flush=True)
        if self.log:
            self.log.write(json.dumps({"t": time.time(), "dir": direction, "msg": shown}) + "\n")
            self.log.flush()

    async def send(self, type_, payload, reply_to=None):
        msg = env(type_, payload, self.session, reply_to)
        self.show(">>", msg if type_ != "raw.big" else {"type": "raw.big", "bytes": 300_000})
        await self.ws.send(json.dumps(msg))

    async def handler(self, ws):
        if ws.request.path != "/sense":
            await ws.close(1008, "path")
            return
        first = json.loads(await ws.recv())
        tok = first.get("payload", {}).get("token", "")
        self.show("<<", first)
        if first.get("type") != "hello" or not self.token or not hmac.compare_digest(tok.encode(), self.token.encode()):
            await ws.send(json.dumps(env("hello.reject", {"code": "auth"}, None, first.get("id"))))
            await ws.close(1008, "auth")
            return
        self.ws, self.session = ws, str(uuid.uuid4())
        await self.send("hello.ok", {"session": self.session}, first.get("id"))
        pinger = asyncio.create_task(self.pinger())
        scripted = asyncio.create_task(self.scripted())
        try:
            async for raw in ws:
                msg = json.loads(raw)
                self.show("<<", msg)
                if msg.get("session") != self.session:
                    print("!! session mismatch", flush=True)
                elif msg.get("type") == "ping":
                    await self.send("pong", {}, msg.get("id"))
        except websockets.ConnectionClosed as e:
            print(f"-- closed {e.code} {e.reason}", flush=True)
        finally:
            pinger.cancel()
            scripted.cancel()
            self.ws = self.session = None

    async def pinger(self):
        while True:
            await asyncio.sleep(5)
            await self.send("ping", {})

    async def scripted(self):  # once per process, not again after a reconnect
        if self.scripted_done:
            return
        self.scripted_done = True
        for line in self.args.send:
            if line.startswith("sleep "):
                await asyncio.sleep(float(line.split()[1]))
                continue
            await asyncio.sleep(self.args.gap)
            cmd = parse_cmd(line)
            if cmd:
                await self.send(*cmd)

    async def stdin_loop(self):
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                await asyncio.Future()  # no stdin (background run): --send/--exit-after drive the session
            cmd = parse_cmd(line.strip())
            if cmd and self.ws:
                await self.send(*cmd)
            elif cmd:
                print("!! sense not connected", flush=True)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log")
    ap.add_argument("--port", type=int, default=8951, help="8951 is the real hub's port; use another if it is taken")
    ap.add_argument("--send", action="append", default=[])
    ap.add_argument("--gap", type=float, default=1.0, help="seconds between --send commands")
    ap.add_argument("--exit-after", type=float, default=0, help="stop after N seconds (0 = run forever)")
    args = ap.parse_args()
    hub = Hub(args)
    if not hub.token:
        sys.exit("set EVE_LINK_TOKEN (same value the server sees)")
    async with websockets.serve(hub.handler, "127.0.0.1", args.port, max_size=MAX_FRAME):
        print(f"stub mind listening on ws://127.0.0.1:{args.port}/sense", flush=True)
        tasks = [asyncio.create_task(hub.stdin_loop())]
        if args.exit_after:
            tasks.append(asyncio.create_task(asyncio.sleep(args.exit_after)))
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        else:
            await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
