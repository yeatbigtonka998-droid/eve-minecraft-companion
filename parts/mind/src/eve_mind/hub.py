"""WebSocket hub on 127.0.0.1:8951 — /body, /sense (link protocol v1) and /cli (mind-private).

Validation order for every frame (CP-11), all before anything is stored or dispatched:
size -> JSON -> envelope schema -> from/link -> session -> world -> duplicate id -> type -> payload schema.
"""
import asyncio
import hmac
import json
import logging
import sqlite3
import time
import uuid

from jsonschema import Draft202012Validator
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from .config import strict_loads

log = logging.getLogger("eve.hub")

CLOSE_PROTOCOL, CLOSE_AUTH, CLOSE_DEAD, CLOSE_SUPERSEDED, CLOSE_TOO_BIG = 4400, 4401, 4408, 4409, 1009

NS_ID = {"type": "string", "pattern": "^[a-z0-9_.-]+:[a-z0-9_./-]+$"}
POS = {"type": "object", "required": ["x", "y", "z"],
       "properties": {"x": {"type": "number"}, "y": {"type": "number", "minimum": -64, "maximum": 320},
                      "z": {"type": "number"}}}
UUID_RE = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
STR = {"type": "string"}
BLOCK_STATE = {"type": "string", "pattern": r"^[a-z0-9_.-]+:[a-z0-9_./-]+(\[[a-z0-9_=,]*\])?$"}
# mind -> sense grant_region (link v1.3): checked before sending, so the mind never sends a region sense would refuse
GRANT_REGION = {"type": "object", "required": ["action_id", "effect", "dim", "min", "max", "expires_tick"],
                "properties": {"action_id": STR, "effect": {"enum": ["place", "break", "use"]}, "dim": NS_ID,
                               "min": POS, "max": POS, "expires_tick": {"type": "integer", "minimum": 0}}}
REGION_MAX_BLOCKS = 32768


def grant_region_ok(g):
    """None if the grant_region payload is well formed and at most 32 768 blocks with min <= max, else why not."""
    errs = [e.message for e in Draft202012Validator(GRANT_REGION).iter_errors(g)]
    if errs:
        return errs[0]
    mn, mx = g["min"], g["max"]
    if any(mn[a] > mx[a] for a in "xyz"):
        return "min > max"
    n = 1
    for a in "xyz":
        n *= int(mx[a]) - int(mn[a]) + 1
    return None if n <= REGION_MAX_BLOCKS else f"{n} blocks > {REGION_MAX_BLOCKS}"
OBJ = {"type": "object"}
NULLSTR = {"type": ["string", "null"]}

ENVELOPE = {
    "type": "object", "additionalProperties": False,
    "required": ["v", "id", "ts", "from", "type", "payload"],
    "properties": {
        "v": {"const": 1}, "id": {"type": "string", "pattern": UUID_RE},
        "ts": {"type": "integer", "minimum": 0}, "tick": {"type": ["integer", "null"]},
        "from": {"enum": ["body", "sense", "mind"]}, "type": {"type": "string", "maxLength": 64},
        "reply_to": NULLSTR, "session": NULLSTR, "world": NULLSTR, "payload": OBJ,
    },
}


def obj(required, **props):
    return {"type": "object", "required": list(required), "properties": props}


ACTION_STATUS = ["ok", "failed", "cancelled", "uncertain", "timeout"]
REJECT_CODES = ["unknown_skill", "bad_params", "precondition_failed", "busy", "stopped", "mode_denied", "zone_denied"]
EVENT_KINDS = ["damage", "death", "respawn", "disconnect", "reconnect", "reflex", "stuck", "chat_received",
               "item_pickup", "container_opened", "control"]
PING = {"type": "object"}

# Payload schemas of everything a peer may send us. A type not in its link's table is unknown_type.
INBOUND = {
    "body": {
        "hello": obj(["token", "mod_version", "mc_version", "account_name", "server_address", "level_name", "caps",
                      "registry_hash"], token=STR, mod_version=STR, mc_version=STR, account_name=STR,
                     server_address=STR, level_name=STR, registry_hash=STR,
                     caps={"type": "array", "maxItems": 256, "items": obj(["skill", "version"], skill=STR,
                                                                          version={"type": "integer"})}),
        "state": obj(["pos", "dim", "health", "inventory", "inventory_hash", "control", "freshness"],
                     pos=POS, dim=NS_ID, health={"type": "number", "minimum": 0},
                     inventory={"type": "array", "maxItems": 64}, inventory_hash=STR,
                     control=obj(["stopped"], stopped={"type": "boolean"}, mode=STR,
                                 active_action_id=NULLSTR),
                     freshness=obj(["tick", "age_ms"], tick={"type": "integer"},
                                   age_ms={"type": "number", "minimum": 0})),
        "event": obj(["kind"], kind={"enum": EVENT_KINDS}, detail=OBJ),
        "action.accepted": obj(["action_id"], action_id={"type": "string", "pattern": UUID_RE}),
        "action.rejected": obj(["action_id", "code"], action_id={"type": "string", "pattern": UUID_RE},
                               code={"enum": REJECT_CODES}, reason=STR),
        "action.progress": obj(["action_id"], action_id={"type": "string", "pattern": UUID_RE},
                               pct={"type": ["number", "null"], "minimum": 0, "maximum": 100}, note=NULLSTR,
                               partial_evidence=OBJ),
        "action.result": obj(["action_id", "status", "evidence"], action_id={"type": "string", "pattern": UUID_RE},
                             status={"enum": ACTION_STATUS}, code=NULLSTR, evidence=OBJ,
                             elapsed_ms={"type": "number", "minimum": 0}),
        "control.ack": obj(["op"], op=STR, latency_ms={"type": "number", "minimum": 0}),
        "query.result": obj(["kind"], kind=STR),
        "metrics": OBJ, "ping": PING, "pong": PING,
        "error": obj(["code"], code=STR, message=STR),
    },
    "sense": {
        "hello": obj(["token", "mod_version", "mc_version", "world_id", "level_name", "seed_hash",
                      "envelope_version", "envelope_hash"], token=STR, mod_version=STR, mc_version=STR,
                     world_id=STR, level_name=STR, seed_hash=STR, envelope_version={"type": "integer"},
                     envelope_hash=STR),
        "storage.index": obj(["containers"], partial={"type": "boolean"}, complete={"type": "boolean"}, containers={
            "type": "array", "maxItems": 4096, "items": {
                **obj(["id"], id=STR, dim=NS_ID, pos=POS, kind=STR, tag={"enum": ["shared", "private", "unknown"]},
                      loaded={"type": "boolean"}, removed={"type": "boolean"}, observed_tick={"type": "integer"},
                      items={"type": "array", "items": obj(["item", "count"], item=NS_ID,
                                                           count={"type": "integer", "minimum": 0})}),
                "if": {"required": ["removed"], "properties": {"removed": {"const": True}}},
                "else": {"required": ["dim", "pos", "kind", "tag", "loaded", "items"]}}}),
        "block.event": obj(["events"], events={"type": "array", "maxItems": 500, "items": obj(
            ["kind", "dim", "pos", "actor"], kind={"enum": ["break", "place", "explode", "burn", "fluid", "grow"]},
            dim=NS_ID, pos=POS, actor=STR)}),
        "explosion": obj(["dim", "center"], dim=NS_ID, center=POS),
        "permission.result": obj(["allowed"], allowed={"type": "boolean"}, rule_id=NULLSTR, reason=NULLSTR),
        "permission.denied": obj(["effect", "dim"], effect=STR, dim=NS_ID, rule_id=NULLSTR),
        "zone.list.result": OBJ, "zone.snapshot.saved": OBJ, "zone.diff.result": OBJ,
        # link v1.3 CP-52: the restore diff (sense Snapshots.repair); `block` may carry a [state]
        "zone.repair.result": obj(
            ["steps", "manual", "skipped"],
            steps={"type": "array", "maxItems": 4096, "items": obj(["op", "pos", "block"], op={"enum": ["remove", "place"]},
                                                                 pos=POS, block=BLOCK_STATE)},
            manual={"type": "array", "items": obj(["pos"], pos=POS, kind=STR, note=STR)},
            skipped={"type": "array", "items": obj(["pos"], pos=POS, reason=STR, block=STR, actual=STR)},
            total={"type": "integer", "minimum": 0}, truncated={"type": "boolean"}, counts=OBJ),
        "envelope.changed": obj(["version", "hash"], version={"type": "integer"}, hash=STR),
        "owner.command": obj(["sender", "trusted", "command", "args"], sender=STR, trusted={"type": "boolean"},
                             command={"enum": ["stop", "resume", "home", "mode", "approve", "reject",
                                               "save_zone", "say", "reload"]},
                             args={"type": "array", "maxItems": 16, "items": STR}, raw=STR),
        "owner.chat": obj(["sender", "trusted", "text"], sender=STR, trusted={"type": "boolean"},
                          text={"type": "string", "maxLength": 4096}),
        # O121: each online trusted player's position, about once a second; online:false when he leaves
        "owner.pos": obj(["sender"], sender=STR, uuid=STR, dim=NS_ID, x={"type": "number"}, y={"type": "number"},
                         z={"type": "number"}, ts={"type": "number"}, online={"type": "boolean"}),
        "query.blocks.result": obj(["palette", "data"], palette={"type": "array"}, data={"type": "array"},
                                   unloaded={"type": "array"}),
        "query.resources.result": OBJ, "eve.status": OBJ, "ping": PING, "pong": PING,
        "attack.grant.result": OBJ, "container.tag.result": OBJ,    # link v1.12 (sense 0.1.24): O136 / O135 replies
        # link v1.13 (sense 0.1.25, H-autofight O166): a hostile targeting a trusted player or Eve; data only
        "mob.targeting": obj(["entity_id", "entity_type", "target"], entity_uuid=STR, entity_id={"type": "integer"},
                             entity_type=STR, target={"enum": ["owner", "eve"]}, target_name=STR, dim=NS_ID, pos=POS,
                             tick={"type": "integer"}),
        "error": obj(["code"], code=STR, message=STR),
    },
}
_ENV_V = Draft202012Validator(ENVELOPE)
_PAYLOAD_V = {link: {t: Draft202012Validator(s) for t, s in tbl.items()} for link, tbl in INBOUND.items()}


class Reject(Exception):
    """A frame refused before side effects. code is what goes back in error{code}; None = silent drop."""
    def __init__(self, code, message, about_id=None, metric=None):
        super().__init__(message)
        self.code, self.message, self.about_id, self.metric = code, message, about_id, metric


def refusal(code, detail=""):
    """One reply shape for everything the mind refuses before sending: no action row exists, nothing reached the
    body. status "refused" + code + reason, alongside the older error/detail keys."""
    return {"ok": False, "status": "refused", "code": code, "reason": detail, "error": code, "detail": detail,
            "action_id": None}


def now_ms() -> int:
    return int(time.time() * 1000)


class Link:
    def __init__(self, hub, name, ws, session, hello):
        self.hub, self.name, self.ws, self.session, self.hello = hub, name, ws, session, hello
        self.last_rx = time.monotonic()
        self.started = now_ms()

    async def send(self, type_, payload, reply_to=None) -> str:
        mid = str(uuid.uuid4())
        await self.ws.send(json.dumps({
            "v": 1, "id": mid, "ts": now_ms(), "tick": None, "from": "mind", "type": type_,
            "reply_to": reply_to, "session": self.session, "world": self.hub.mind.world_for_link(self.name),
            "payload": payload}))
        return mid


class Hub:
    def __init__(self, cfg, mind):
        self.cfg, self.mind = cfg, mind
        mind.hub = self
        self.links = {"body": None, "sense": None}
        self._db_err_logged = 0.0  # rate limit for "not stored" lines while the DB is locked
        self.seen = {}           # message id -> monotonic time (10-minute replay window)
        self.waiters = {}        # outbound message id -> Future for its reply
        self.metrics = {k: 0 for k in ("dropped_session_mismatch", "world_mismatch", "duplicate_id", "bad_json",
                                       "bad_envelope", "bad_payload", "unknown_type", "payload_too_large",
                                       "auth_rejected", "dead_link", "superseded")}
        self.server = None

    async def start(self):
        self.server = await serve(self.handler, self.cfg.host, self.cfg.port, max_size=self.cfg.frame_max * 4,
                                  ping_interval=None, compression=None)
        self.port = self.server.sockets[0].getsockname()[1]
        log.info("hub listening on ws://%s:%d", self.cfg.host, self.port)
        return self

    async def close(self):
        self.server.close()
        await self.server.wait_closed()

    # ---- request/response helper used by the mind (permission.check, query.blocks, storage.request)
    async def request(self, link_name, type_, payload, timeout=1.0):
        link = self.links.get(link_name)
        if link is None:
            return None
        fut = asyncio.get_running_loop().create_future()
        mid = await link.send(type_, payload)
        self.waiters[mid] = fut
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self.waiters.pop(mid, None)

    # ---- connection handling
    async def handler(self, ws):
        path = ws.request.path
        if path == "/cli":
            return await self.cli(ws)
        if path not in ("/body", "/sense"):
            return await ws.close(CLOSE_PROTOCOL, "unknown path")
        name = path[1:]
        link = await self.handshake(ws, name)
        if link is None:
            return
        ka = asyncio.create_task(self.keepalive(link))
        try:
            async for raw in ws:
                link.last_rx = time.monotonic()
                try:
                    msg = self.check(raw, name, link)
                except Reject as r:
                    self.metrics[r.metric or r.code] = self.metrics.get(r.metric or r.code, 0) + 1
                    log.warning("%s: rejected frame %s: %s", name, r.code or r.metric, r.message)
                    if r.code:
                        await self.send_error(link, r.code, r.message, r.about_id)
                    if r.code == "payload_too_large":
                        await ws.close(CLOSE_TOO_BIG, "payload too large")
                        break
                    continue
                try:
                    await self.route(link, msg)
                except sqlite3.Error as e:      # CP-24: a locked/broken DB must not kill the link
                    self.mind.metrics["db_errors"] += 1
                    if name == "body" and msg["type"].startswith("action.") and len(self.mind.retry_frames) < 1000:
                        # an action's ack/progress/result must not be lost to a lock (45L CP-24 a3): apply it later
                        self.mind.retry_frames.append((link, msg))
                    if time.monotonic() - self._db_err_logged > 5:      # at most one line per 5 s per lock
                        self._db_err_logged = time.monotonic()
                        log.error("%s: %s not stored: %s (db_errors=%d)", name, msg["type"], e,
                                  self.mind.metrics["db_errors"])
        except ConnectionClosed:
            pass
        finally:
            ka.cancel()
            if self.links.get(name) is link:
                self.links[name] = None
                self.mind.on_link_down(link)

    async def handshake(self, ws, name):
        try:
            raw = await asyncio.wait_for(ws.recv(), self.cfg.dead_s)
        except (asyncio.TimeoutError, ConnectionClosed):
            await ws.close(CLOSE_PROTOCOL, "no hello")
            return None
        try:
            if isinstance(raw, bytes) or len(raw.encode("utf-8")) > self.cfg.frame_max:
                raise ValueError("bad first frame")
            msg = strict_loads(raw)
            if not isinstance(msg, dict) or msg.get("type") != "hello":
                raise ValueError("first message must be hello")
        except ValueError as e:
            await ws.close(CLOSE_PROTOCOL, str(e)[:100])
            return None
        payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
        token = payload.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(token.encode(), self.cfg.token.encode()):
            self.metrics["auth_rejected"] += 1
            log.warning("%s: hello rejected: auth", name)
            await self._raw_send(ws, "hello.reject", {"code": "auth"}, msg.get("id"))
            await ws.close(CLOSE_AUTH, "auth")
            return None
        if list(_ENV_V.iter_errors(msg)) or msg["from"] != name or list(_PAYLOAD_V[name]["hello"].iter_errors(payload)):
            await self._raw_send(ws, "hello.reject", {"code": "version", "reason": "malformed hello"}, msg.get("id"))
            await ws.close(CLOSE_PROTOCOL, "malformed hello")
            return None
        if payload["mc_version"] != self.cfg.mc_version:
            await self._raw_send(ws, "hello.reject", {"code": "version", "expected": self.cfg.mc_version}, msg["id"])
            await ws.close(CLOSE_PROTOCOL, "version")
            return None
        if name == "body" and payload["registry_hash"] != self.mind.registry.hash:
            await self._raw_send(ws, "hello.reject", {"code": "registry", "expected": self.mind.registry.hash,
                                                      "registry_version": self.mind.registry.version}, msg["id"])
            await ws.close(CLOSE_PROTOCOL, "registry")
            return None
        self.remember(msg["id"])
        old = self.links.get(name)
        if old is not None:
            self.metrics["superseded"] += 1
            self.links[name] = None
            self.mind.on_link_down(old)
            await old.ws.close(CLOSE_SUPERSEDED, "superseded")
        session = str(uuid.uuid4())
        link = Link(self, name, ws, session, {k: v for k, v in payload.items() if k != "token"})
        self.links[name] = link
        self.mind.on_link_up(link)
        reply = {"session": session}
        if name == "body":
            reply.update(registry_hash=self.mind.registry.hash, state_hz=self.cfg.state_hz, mode=self.mind.mode)
        await link.send("hello.ok", reply, reply_to=msg["id"])
        log.info("%s: hello.ok session=%s", name, session)
        await self.mind.after_hello(link)
        return link

    async def _raw_send(self, ws, type_, payload, reply_to):
        try:
            await ws.send(json.dumps({"v": 1, "id": str(uuid.uuid4()), "ts": now_ms(), "tick": None, "from": "mind",
                                      "type": type_, "reply_to": reply_to if isinstance(reply_to, str) else None,
                                      "session": None, "world": None, "payload": payload}))
        except ConnectionClosed:
            pass

    async def send_error(self, link, code, message, about_id):
        try:
            await link.send("error", {"code": code, "message": message[:300], "about_id": about_id},
                            reply_to=about_id)
        except ConnectionClosed:
            pass

    def remember(self, mid):
        t = time.monotonic()
        self.seen[mid] = t
        while self.seen:
            first = next(iter(self.seen))
            if t - self.seen[first] <= self.cfg.replay_s:
                break
            del self.seen[first]

    def check(self, raw, name, link):
        if isinstance(raw, bytes):
            raise Reject("bad_json", "binary frames are not accepted")
        if len(raw.encode("utf-8")) > self.cfg.frame_max:
            raise Reject("payload_too_large", f"frame exceeds {self.cfg.frame_max} bytes")
        try:
            msg = strict_loads(raw)
        except ValueError as e:
            raise Reject("bad_json", str(e)[:200])
        about = msg.get("id") if isinstance(msg, dict) and isinstance(msg.get("id"), str) else None
        errs = list(_ENV_V.iter_errors(msg))
        if errs:
            raise Reject("bad_envelope", errs[0].message[:200], about)
        if msg["from"] != name:
            raise Reject("bad_envelope", f"from={msg['from']} on /{name}", about)
        if msg.get("session") != link.session:
            raise Reject(None, "session mismatch", about, metric="dropped_session_mismatch")
        world = self.mind.world_for_link(name)
        if msg.get("world") is not None and msg["world"] != world:
            raise Reject(None, f"world {msg['world']} != {world}", about, metric="world_mismatch")
        if about in self.seen:
            raise Reject("duplicate_id", "message id seen in the last 10 minutes", about)
        self.remember(about)
        v = _PAYLOAD_V[name].get(msg["type"])
        if v is None or msg["type"] == "hello":
            raise Reject("unknown_type", f"type {msg['type']!r} not accepted on /{name}", about)
        perrs = list(v.iter_errors(msg["payload"]))
        if perrs:
            e = perrs[0]
            raise Reject("bad_payload", f"{'/'.join(map(str, e.path)) or 'payload'}: {e.message}"[:300], about)
        return msg

    async def route(self, link, msg):
        t = msg["type"]
        if link.name == "sense" and isinstance(msg.get("tick"), int):   # server tick, for task.grant expiry
            self.mind.sense_tick = (msg["tick"], time.monotonic())
        if t == "ping":
            return await link.send("pong", {}, reply_to=msg["id"])
        if t == "pong":
            return
        fut = self.waiters.get(msg.get("reply_to"))
        if fut is not None and not fut.done():
            fut.set_result(msg["payload"])
        await self.mind.on_message(link, msg)

    async def keepalive(self, link):
        try:
            while True:
                await asyncio.sleep(self.cfg.ping_s)
                if time.monotonic() - link.last_rx > self.cfg.dead_s:
                    self.metrics["dead_link"] += 1
                    log.warning("%s: no traffic for %.1fs, closing as dead", link.name, self.cfg.dead_s)
                    await link.ws.close(CLOSE_DEAD, "dead link")
                    return
                await link.send("ping", {})
        except ConnectionClosed:
            pass

    # ---- mind-private CLI path: one request per connection, same token, loopback only
    async def cli(self, ws):
        try:
            raw = await asyncio.wait_for(ws.recv(), 5)
            req = strict_loads(raw)
            tok = req.get("token")
            if not isinstance(tok, str) or not hmac.compare_digest(tok.encode(), self.cfg.token.encode()):
                await ws.send(json.dumps(refusal("auth", "wrong or missing token")))
                return await ws.close(CLOSE_AUTH, "auth")
            reply = await self.mind.cli(str(req.get("cmd")), req.get("args") or {})
        except (asyncio.TimeoutError, ValueError, AttributeError) as e:
            reply = refusal("bad_request", str(e)[:200])
        except ConnectionClosed:
            return
        await ws.send(json.dumps(reply))
