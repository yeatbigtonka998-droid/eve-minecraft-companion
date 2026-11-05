"""Fake body: a test double for Eve's Fabric client mod that speaks link protocol v1 on /body.

Scriptable misbehaviour (attributes, may be flipped mid-test):
  drop_ack          never send action.accepted (lost ack)
  drop_progress     never send action.progress
  drop_result       execute but never deliver action.result (lost result)
  duplicate_result  send every action.result twice
  late_result_s     delay before sending action.result
  late_result_by_skill  {skill: seconds}: that skill's result delay instead (e.g. only `stop` slow)
  missing_evidence  send ok results with an empty evidence object
  stale_state       report freshness.age_ms = 5000
  silent            stop sending anything (state, pongs) -> dead link on the mind side
  on_link_loss      'finish' (finish the action, result is lost) or 'abort' (IDLE-SAFE: cancel it)
  walk_s            how long a skill takes
  reject_code       reject every new action.request with this code (e.g. precondition_failed)
  result_override   (status, code) to report instead of the real outcome
  ignore_cancel     do not cancel the running action on the `stop` skill
  result_note       optional action.result.note (the body's explanation of the outcome)
  reject_after_accept  send action.accepted, then action.rejected with this code (a contract violation)
  nearby            list of nearby_entities reported in state
  threats / time_of_day / health   state fields (O54 night tests)
Usage: EVE_LINK_TOKEN=... python tools/fake_body.py [--port 8951] [--drop-ack] [--walk-s 2]
"""
import argparse
import asyncio
import collections
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

ROOT = Path(os.environ.get("EVE_ROOT") or Path(__file__).resolve().parents[3])   # EVE_ROOT: try another registry
REGISTRY = ROOT / "contracts" / "skills" / "registry.json"


def chash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


class Peer:
    link = None

    def __init__(self, url, token, reconnect=True, backoff_s=0.1):
        self.url, self.token, self.reconnect, self.backoff_s = url, token, reconnect, backoff_s
        self.ws, self.session, self.world = None, None, None
        self.received, self.sessions, self.rejects = [], [], []
        self.ready = asyncio.Event()
        self.silent = False
        self._task = None
        self._bg = []

    def envelope(self, type_, payload, reply_to=None, **over):
        env = {"v": 1, "id": str(uuid.uuid4()), "ts": int(time.time() * 1000), "tick": getattr(self, "tick", 0),
               "from": self.link, "type": type_, "reply_to": reply_to, "session": self.session,
               "world": self.world, "payload": payload}
        env.update(over)
        return env

    async def send(self, type_, payload, reply_to=None, **over):
        msg = self.envelope(type_, payload, reply_to, **over)
        try:
            await self.ws.send(json.dumps(msg))
        except (ConnectionClosed, AttributeError):
            return None
        return msg["id"]

    async def send_raw(self, text):
        await self.ws.send(text)

    def start(self):
        self._task = asyncio.get_running_loop().create_task(self.run())
        return self

    async def close(self):
        self.reconnect = False
        if self.ws:
            await self.ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def run(self):
        while True:
            try:
                async with connect(self.url, proxy=None, open_timeout=3, ping_interval=None,
                                   max_size=None, compression=None) as ws:
                    self.ws = ws
                    await ws.send(json.dumps(self.envelope("hello", self.hello_payload(), session=None)))
                    async for raw in ws:
                        msg = json.loads(raw)
                        self.received.append(msg)
                        if msg["type"] == "hello.ok":
                            self.session = msg["payload"]["session"]
                            self.sessions.append(self.session)
                            self.on_hello_ok(msg)
                            self.ready.set()
                        elif msg["type"] == "hello.reject":
                            self.rejects.append(msg["payload"])
                        else:
                            await self.handle(msg)
            except (OSError, ConnectionClosed, InvalidStatus, asyncio.TimeoutError):
                pass
            self.ready.clear()
            self.session = None
            for t in self._bg:
                t.cancel()
            self._bg = []
            self.on_disconnect()
            if not self.reconnect:
                return
            await asyncio.sleep(self.backoff_s)

    async def wait_for(self, pred, timeout=3.0, start=0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            for m in self.received[start:]:
                if pred(m):
                    return m
            await asyncio.sleep(0.01)
        raise TimeoutError("no matching message")

    def on_hello_ok(self, msg):
        pass

    def on_disconnect(self):
        pass

    async def handle(self, msg):
        if msg["type"] == "ping" and not self.silent:
            await self.send("pong", {}, reply_to=msg["id"])


class FakeBody(Peer):
    accepts = ()      # O173: hello "accepts" (e.g. ["mob.targeting"]); a class knob, set before the body starts
    link = "body"

    def __init__(self, url, token, level_name="eve-dev", pos=(0.5, 64.0, 0.5), dim="minecraft:overworld",
                 caps=None, walk_s=0.3, state_hz=20, account_name="FED_Chairman", devtools_dir=None, **kw):
        super().__init__(url, token, **kw)
        self.registry = json.loads(REGISTRY.read_text("utf-8"))
        self.skills = {s["name"]: s for s in self.registry["skills"]}
        self.caps = caps if caps is not None else [{"skill": s["name"], "version": s["version"]}
                                                  for s in self.registry["skills"]]
        self.level_name, self.dim, self.walk_s, self.state_hz = level_name, dim, walk_s, state_hz
        self.account_name = account_name
        self.devtools_dir = Path(devtools_dir) if devtools_dir else None   # emulates body's devtools jar (c17194f)
        self.pos = dict(zip("xyz", pos))
        self.mode, self.stopped, self.active, self.tick = "manual", False, None, 1000
        self.hotbar_slot, self.sneaking = 0, False
        self.actions = {}                       # action_id -> {"skill", "result": payload or None}
        self.executions = collections.Counter()  # action_id -> times actually executed (must stay <= 1)
        self.requests = collections.Counter()    # action_id -> action.request frames received
        self.request_log = []                    # (session, action_id) per action.request frame
        self.exec_task = None
        self.drop_ack = self.drop_progress = self.drop_result = self.duplicate_result = False
        self.food, self.inventory = 20, []         # state knobs (CP-43 runner tests)
        self.missing_evidence = self.ignore_cancel = False
        self.evidence_extra = None
        self.equipment = {}                       # head/chest/legs/feet/mainhand/offhand -> item
        self.reject_code, self.result_override, self.result_note = None, None, None
        self.reject_after_accept, self.nearby = None, []
        self.stale_state, self.late_result_s, self.on_link_loss = False, 0.0, "finish"
        self.late_result_by_skill = None      # {skill: seconds} overrides late_result_s for that skill
        self.health, self.threats, self.time_of_day = 20, [], 1000   # O54: night, threats, health

    def hello_payload(self):
        return {"token": self.token, "mod_version": "fake-body-0.1", "mc_version": "26.3",
                "account_name": self.account_name,
                "server_address": "127.0.0.1:25565", "level_name": self.level_name, "caps": self.caps,
                "registry_hash": chash(self.registry), **({"accepts": list(self.accepts)} if self.accepts else {})}

    def on_hello_ok(self, msg):
        self.mode = msg["payload"].get("mode", self.mode)
        self._bg.append(asyncio.get_running_loop().create_task(self.state_loop()))

    def on_disconnect(self):
        if self.on_link_loss == "abort" and self.exec_task and not self.exec_task.done():
            self.exec_task.cancel()   # IDLE-SAFE: not past a safe pause point, so cancel

    def state(self):
        return {"pos": dict(self.pos), "dim": self.dim, "yaw": 0, "pitch": 0, "health": self.health, "food": self.food,
                "saturation": 5, "air": 300, "xp_level": 0, "on_ground": True, "in_water": False, "in_lava": False,
                "fall_distance": 0, "sneaking": self.sneaking, "gamemode": "survival", "inventory": list(self.inventory),
                "inventory_hash": "sha1-" + str(abs(hash(json.dumps(self.inventory, sort_keys=True)))), "equipment": dict(self.equipment), "hotbar_slot": self.hotbar_slot,
                "nearby_entities": list(self.nearby), "threats": list(self.threats), "light_level": 15, "biome": "minecraft:plains",
                "time_of_day": self.time_of_day, "weather": "clear",
                "control": {"mode": self.mode, "stopped": self.stopped, "active_action_id": self.active,
                            "reflex_active": getattr(self, "reflex_active", None)},   # S28 knob
                "freshness": {"tick": self.tick, "age_ms": 5000 if self.stale_state else 10}}

    async def malformed_frames(self):
        """The 9 frames body's dev-only devtools jar sends on its live link when the trigger file appears."""
        ids = []
        send = self.ws.send
        await send('{"v":1,"id":')                                             # bad JSON
        await send(b"\x00\x01\x02")                                          # binary
        dup = self.envelope("metrics", {"tick_ms_avg": 1})
        await send(json.dumps(dup))
        await send(json.dumps(dup))                                             # duplicate id
        await send(json.dumps(self.envelope("event", {"kind": "damage"}, session=str(uuid.uuid4()))))
        await send(json.dumps(self.envelope("bogus.type", {})))
        await send(json.dumps(self.envelope("action.request", {"action_id": str(uuid.uuid4())})))
        bad = self.state()
        bad["dim"] = "overworld"
        await send(json.dumps(self.envelope("state", bad)))                    # bad_payload
        await send(json.dumps(self.envelope("event", {"kind": "damage", "detail": {"pad": "x" * 300_000}})))
        ids.append(dup["id"])
        return ids

    async def state_loop(self):
        while True:
            if self.devtools_dir and (self.devtools_dir / "eve-devtools-malformed.trigger").exists():
                (self.devtools_dir / "eve-devtools-malformed.trigger").unlink()
                sess = self.session
                try:
                    await self.malformed_frames()
                except ConnectionClosed:
                    pass
                (self.devtools_dir / "eve-devtools-malformed.done").write_text(f"session {sess}")
            self.tick += 1
            if not self.silent:
                await self.send("state", self.state())
            await asyncio.sleep(1 / self.state_hz)

    async def handle(self, msg):
        t, p = msg["type"], msg["payload"]
        if t == "action.request":
            return await self.on_request(msg)
        if t == "control.stop":
            t0 = time.monotonic()
            self.stopped = True
            aid = self.active
            if self.exec_task and not self.exec_task.done():
                self.exec_task.cancel()
                if aid and not self.executions[aid]:   # cancelled before it ever ran (the CP-43 v5 flake): it still
                    self.active = None                 # ends, cancelled - a real body ends what it accepted on stop
                    asyncio.get_running_loop().create_task(
                        self.finish(aid, "cancelled", "cancelled", {}, self.actions[aid].get("req")))
            return await self.send("control.ack", {"op": "stop", "latency_ms": (time.monotonic() - t0) * 1000,
                                                   "state_after": self.state()["control"]}, reply_to=msg["id"])
        if t == "control.resume":
            self.stopped = False
            return await self.send("control.ack", {"op": "resume", "latency_ms": 0}, reply_to=msg["id"])
        if t in ("control.home", "control.mode"):
            if t == "control.mode":
                self.mode = p["mode"]
            return await self.send("control.ack", {"op": t.split(".")[1], "latency_ms": 0}, reply_to=msg["id"])
        if t == "query":
            return await self.send("query.result", {"kind": p.get("kind"), "result": {}}, reply_to=msg["id"])
        await super().handle(msg)

    async def on_request(self, msg):
        p = msg["payload"]
        aid = p["action_id"]
        self.requests[aid] += 1
        self.request_log.append((self.session, aid))
        known = self.actions.get(aid)
        if known is not None:     # idempotency: report status, never re-execute
            if known["result"] is not None:
                return await self.send("action.result", known["result"], reply_to=msg["id"])
            return await self.send("action.accepted", {"action_id": aid}, reply_to=msg["id"])
        if self.stopped:
            return await self.send("action.rejected", {"action_id": aid, "code": "stopped", "reason": "STOPPED"},
                                   reply_to=msg["id"])
        if self.reject_code and p["skill"] != "stop":
            self.actions[aid] = {"skill": p["skill"], "result": None}
            return await self.send("action.rejected", {"action_id": aid, "code": self.reject_code,
                                                       "reason": "scripted"}, reply_to=msg["id"])
        if not any(c["skill"] == p["skill"] and c["version"] == p["version"] for c in self.caps):
            return await self.send("action.rejected", {"action_id": aid, "code": "unknown_skill", "reason": p["skill"]},
                                   reply_to=msg["id"])
        if self.active and p["skill"] != "stop":
            return await self.send("action.rejected", {"action_id": aid, "code": "busy", "reason": self.active},
                                   reply_to=msg["id"])
        self.actions[aid] = {"skill": p["skill"], "result": None, "req": msg["id"]}
        if self.reject_after_accept and p["skill"] != "stop":
            await self.send("action.accepted", {"action_id": aid}, reply_to=msg["id"])
            return await self.send("action.rejected", {"action_id": aid, "code": self.reject_after_accept,
                                                       "reason": "changed its mind after accepting"},
                                   reply_to=msg["id"])
        if not self.drop_ack:
            await self.send("action.accepted", {"action_id": aid}, reply_to=msg["id"])
        if p["skill"] == "stop":
            if self.exec_task and not self.exec_task.done() and not self.ignore_cancel:
                self.exec_task.cancel()
            return await self.finish(aid, "ok", None, {"inputs_cleared": True, "latency_ms": 1}, msg["id"])
        self.active = aid
        self.exec_task = asyncio.get_running_loop().create_task(self.execute(aid, p, msg["id"]))

    async def execute(self, aid, p, req_id):
        self.executions[aid] += 1
        skill, params, t0 = p["skill"], p["params"], time.monotonic()
        try:
            if not self.drop_progress:
                await self.send("action.progress", {"action_id": aid, "pct": 0, "note": "started"}, reply_to=req_id)
            await asyncio.sleep(self.walk_s)
            if skill == "walk_to":
                self.pos = dict(params["pos"])
            elif skill == "go_home":
                self.pos = {"x": 0.5, "y": 64.0, "z": 0.5}
            elif skill == "select_hotbar":
                self.hotbar_slot = params["slot"]
            elif skill == "sneak":
                self.sneaking = params["on"]
            ev = {k: self.evidence_value(k, params) for k in self.skills[skill]["evidence"]}
            extra = (self.evidence_extra or {}).get(skill)
            if isinstance(extra, list):             # evidence_extra: {skill: [dict, ...]} merged in order, then none
                ev.update(extra.pop(0) if extra else {})
            ov = self.result_override.get(skill) if isinstance(self.result_override, dict) else self.result_override
            if isinstance(ov, list):                 # outcomes consumed in order, then ok
                ov = ov.pop(0) if ov else None
            status, code = ov or ("ok", None)          # result_override: (status, code) or {skill: (status, code)}
        except asyncio.CancelledError:
            ev, status, code = {}, "cancelled", "cancelled"
        self.active = None
        await self.finish(aid, status, code, {} if self.missing_evidence else ev, req_id,
                          elapsed_ms=(time.monotonic() - t0) * 1000)

    def evidence_value(self, key, params):
        return {"final_pos": dict(self.pos), "distance_to_goal": 0.0, "blocks_broken": 0, "blocks_placed": 0,
                "path_length": 1, "hotbar_slot": self.hotbar_slot, "sneaking": self.sneaking}.get(key, True)

    async def finish(self, aid, status, code, ev, req_id, elapsed_ms=0):
        result = {"action_id": aid, "status": status, "code": code, "evidence": ev, "elapsed_ms": elapsed_ms,
                  "note": self.result_note}
        self.actions[aid]["result"] = result
        if self.drop_result:
            return
        late = (self.late_result_by_skill or {}).get(self.actions[aid]["skill"], self.late_result_s)
        if late:
            await asyncio.sleep(late)
        for _ in range(2 if self.duplicate_result else 1):
            await self.send("action.result", result, reply_to=req_id)


async def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("EVE_PORT", 8951)))
    ap.add_argument("--level", default="eve-dev")
    ap.add_argument("--walk-s", type=float, default=1.0)
    ap.add_argument("--drop-ack", action="store_true")
    ap.add_argument("--drop-result", action="store_true")
    ap.add_argument("--stale-state", action="store_true")
    a = ap.parse_args()
    body = FakeBody(f"ws://127.0.0.1:{a.port}/body", os.environ["EVE_LINK_TOKEN"], level_name=a.level,
                    walk_s=a.walk_s, state_hz=4)
    body.drop_ack, body.drop_result, body.stale_state = a.drop_ack, a.drop_result, a.stale_state
    body.start()
    while True:
        await asyncio.sleep(5)
        print(f"session={body.session} pos={body.pos} stopped={body.stopped} executions={dict(body.executions)}",
              flush=True)


if __name__ == "__main__":
    asyncio.run(_main())
