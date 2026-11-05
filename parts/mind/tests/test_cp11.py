"""CP-11 — validate and authenticate action messages, across the real WebSocket links.

Every case runs against a real hub on loopback with the fake body/sense. Invalid cases assert the frame was
refused *before* any side effect (no row stored, nothing dispatched). The CASES table is the fixture manifest.
"""
import asyncio
import json
import os
import uuid

import pytest
from websockets.asyncio.client import connect

from conftest import TOKEN, count, envelope, mind_env, run, until
from eve_mind.planner import Planner
from fake_body import FakeBody, chash
from fake_http import FakeHTTP
from fake_sense import FakeSense

BLOCK = {"x": 3, "y": 63, "z": 3}


def task_only(e):
    """Sense rule for a task_only zone: break/place only where a task.grant covers it."""
    e.sense.permissions = lambda q: (e.sense.granted(q), "home:task_only", "needs a task grant")

CASES = {}


def case(kind, desc):
    def reg(fn):
        CASES[fn.__name__] = (kind, desc, fn)
        return fn
    return reg


async def raw_first(url, frame):
    """Open a raw connection, send one frame, return (replies, close_code)."""
    replies = []
    async with connect(url, proxy=None, compression=None, ping_interval=None, max_size=None) as ws:
        await ws.send(frame)
        try:
            async for m in ws:
                replies.append(json.loads(m))
        except Exception:
            pass
        return replies, ws.close_code


def hello_frame(link, token, **payload_over):
    if link == "body":
        p = {"token": token, "mod_version": "t", "mc_version": "26.3", "account_name": "X", "server_address": "x",
             "level_name": "eve-dev", "caps": [], "registry_hash": "?"}
    else:
        p = {"token": token, "mod_version": "t", "mc_version": "26.3", "world_id": "w", "level_name": "eve-dev",
             "seed_hash": "s", "envelope_version": 1, "envelope_hash": "h"}
    p.update(payload_over)
    return json.dumps({"v": 1, "id": str(uuid.uuid4()), "ts": 1, "tick": 0, "from": link, "type": "hello",
                       "reply_to": None, "session": None, "world": None, "payload": p})


def last_error(peer, start=0):
    return peer.wait_for(lambda m: m["type"] == "error", start=start)


# ------------------------------------------------------------------ valid
@case("valid", "body hello with the right token gets hello.ok{session, registry_hash, state_hz, mode}")
async def v01_body_hello(e):
    ok = await e.body.wait_for(lambda m: m["type"] == "hello.ok")
    p = ok["payload"]
    assert set(p) == {"session", "registry_hash", "state_hz", "mode"}
    assert p["registry_hash"] == e.mind.registry.hash and p["state_hz"] == 4 and p["mode"] == "manual"
    assert count(e.db, "SELECT count(*) FROM sessions WHERE session_id=? AND link='body'", p["session"]) == 1


@case("valid", "sense hello gets hello.ok{session} and binds the world")
async def v02_sense_hello(e):
    ok = await e.sense.wait_for(lambda m: m["type"] == "hello.ok")
    assert set(ok["payload"]) == {"session"}
    assert e.mind.binding() == (True, "eve-dev:000000000000")


@case("valid", "body state is stored as an observation keyed by the bound world")
async def v03_state(e):
    await until(lambda: count(e.db, "SELECT count(*) FROM observations WHERE world_id='eve-dev:000000000000'") > 0)


@case("valid", "ping is answered with pong correlated by reply_to")
async def v04_ping(e):
    n = len(e.body.received)
    mid = await e.body.send("ping", {})
    await e.body.wait_for(lambda m: m["type"] == "pong" and m["reply_to"] == mid, start=n)


@case("valid", "walk_to dispatch: request reaches the body, result is correlated and persisted with evidence")
async def v05_walk_to(e):
    r = await e.mind.do("walk_to", {"pos": {"x": 5, "y": 64, "z": 5}}, wait=3)
    assert r["status"] == "ok" and r["evidence"]["final_pos"] == {"x": 5, "y": 64, "z": 5}
    assert e.body.executions[r["action_id"]] == 1
    row = e.mind.action(r["action_id"])
    assert row["sent_ts"] and row["result_ts"] and json.loads(row["evidence"])["distance_to_goal"] == 0.0


@case("valid", "mine_block triggers permission.check pre-flight; correlated permission.result allows dispatch")
async def v06_permission(e):
    r = await e.mind.do("mine_block", {"pos": {"x": 3, "y": 63, "z": 3}}, wait=3)
    assert r["status"] == "ok"
    assert e.sense.permission_checks[-1] == {"effect": "break", "dim": "minecraft:overworld", "actor": "eve",
                                             "pos": {"x": 3, "y": 63, "z": 3}}


@case("valid", "storage.index is stored into containers/container_items")
async def v07_storage(e):
    c = {"id": "c1", "dim": "minecraft:overworld", "pos": {"x": 1, "y": 64, "z": 2}, "kind": "chest",
         "tag": "shared", "loaded": True, "items": [{"item": "minecraft:cobblestone", "count": 12}],
         "observed_tick": 5}
    await e.sense.send_storage([c])
    await until(lambda: count(e.db, "SELECT count FROM container_items WHERE container_id='c1'") == 12)


@case("valid", "trusted owner /eve stop via sense stops the mind and reaches the body")
async def v08_owner_stop(e):
    await e.sense.owner_command("Hunter", True, "stop")
    await until(lambda: e.mind.stopped and e.body.stopped)


@case("valid", "block.event batch is stored in block_events")
async def v09_block_events(e):
    ev = [{"kind": "break", "dim": "minecraft:overworld", "pos": {"x": i, "y": 64, "z": 0}, "before": "minecraft:dirt",
           "after": "minecraft:air", "actor": "eve", "tick": 1} for i in range(500)]
    await e.sense.send("block.event", {"events": ev})
    await until(lambda: count(e.db, "SELECT count(*) FROM block_events") == 500)


@case("valid", "body event (damage) is stored in events")
async def v10_event(e):
    await e.body.send("event", {"kind": "damage", "detail": {"amount": 2, "marker": "v10"}})
    await until(lambda: count(e.db, "SELECT count(*) FROM events WHERE payload LIKE '%v10%'") == 1)


@case("valid", "action.progress moves an accepted action to running")
async def v11_progress(e):
    e.body.walk_s = 1.0
    r = await e.mind.do("walk_to", {"pos": {"x": 2, "y": 64, "z": 2}})
    await until(lambda: e.mind.status_of(r["action_id"]) == "running")


@case("valid", "control.stop is acknowledged with control.ack{op, latency_ms} and recorded")
async def v12_stop_ack(e):
    await e.mind.stop("cli", "test")
    await until(lambda: e.mind.last_ack and e.mind.last_ack["op"] == "stop")


@case("valid", "lost ack+progress: the mind re-sends the same action_id; the body executes once; one result counted")
async def v13_lost_ack_retry(e):
    e.body.drop_ack = e.body.drop_progress = True
    e.body.walk_s = 1.5
    r = await e.mind.do("walk_to", {"pos": {"x": 4, "y": 64, "z": 0}}, wait=4)
    assert r["status"] == "ok"
    assert e.body.requests[r["action_id"]] >= 2 and e.body.executions[r["action_id"]] == 1
    assert e.mind.metrics["resends"] >= 1


# ------------------------------------------------------------------ invalid (must be refused before side effects)
@case("invalid", "wrong token -> hello.reject{auth}, close 4401, no session")
async def i01_wrong_token(e):
    replies, code = await raw_first(e.url + "/body", hello_frame("body", "wrong-token-xxxxxxxxxxxx"))
    assert replies[0]["type"] == "hello.reject" and replies[0]["payload"]["code"] == "auth" and code == 4401
    assert count(e.db, "SELECT count(*) FROM sessions") == 2


@case("invalid", "missing token -> hello.reject{auth}")
async def i02_missing_token(e):
    f = json.loads(hello_frame("sense", "x"))
    del f["payload"]["token"]
    replies, code = await raw_first(e.url + "/sense", json.dumps(f))
    assert replies[0]["payload"]["code"] == "auth" and code == 4401


@case("invalid", "non-hello first message -> closed 4400, no session")
async def i03_no_hello(e):
    f = json.loads(hello_frame("body", TOKEN))
    f["type"] = "state"
    replies, code = await raw_first(e.url + "/body", json.dumps(f))
    assert replies == [] and code == 4400
    assert count(e.db, "SELECT count(*) FROM sessions") == 2


@case("invalid", "malformed JSON -> error{bad_json}")
async def i04_bad_json(e):
    n = len(e.body.received)
    await e.body.send_raw('{"v":1, "id": ')
    assert (await last_error(e.body, n))["payload"]["code"] == "bad_json"


@case("invalid", "NaN in a number -> error{bad_json}, nothing stored")
async def i05_nan(e):
    n = len(e.body.received)
    st = e.body.envelope("event", {"kind": "damage", "detail": {"marker": "i05"}})
    await e.body.send_raw(json.dumps(st).replace('"tick": 1', '"tick": NaN').replace('"ts": ', '"ts": NaN, "x": '))
    assert (await last_error(e.body, n))["payload"]["code"] in ("bad_json",)
    assert count(e.db, "SELECT count(*) FROM events WHERE payload LIKE '%i05%'") == 0


@case("invalid", "envelope with v=2 / extra key / bad id -> error{bad_envelope}")
async def i06_bad_envelope(e):
    for over in ({"v": 2}, {"extra": 1}, {"id": "not-a-uuid"}, {"ts": -5}):
        n = len(e.body.received)
        await e.body.send_raw(json.dumps(e.body.envelope("event", {"kind": "damage", "detail": {"m": "i06"}}, **over)))
        assert (await last_error(e.body, n))["payload"]["code"] == "bad_envelope", over
    assert count(e.db, "SELECT count(*) FROM events WHERE payload LIKE '%i06%'") == 0


@case("invalid", "frame over 262144 bytes -> error{payload_too_large} and close 1009")
async def i07_oversized(e):
    n = len(e.body.received)
    big = e.body.envelope("event", {"kind": "damage", "detail": {"pad": "x" * 262_200}})
    await e.body.send_raw(json.dumps(big))
    assert (await last_error(e.body, n))["payload"]["code"] == "payload_too_large"
    await until(lambda: e.hub.metrics["payload_too_large"] == 1 and e.body.ws.close_code == 1009)
    assert count(e.db, "SELECT count(*) FROM events WHERE length(payload) > 200000") == 0


@case("invalid", "duplicate message id -> error{duplicate_id}; the repeat is not stored")
async def i08_duplicate_id(e):
    f = e.body.envelope("event", {"kind": "damage", "detail": {"marker": "i08"}})
    n = len(e.body.received)
    await e.body.send_raw(json.dumps(f))
    await e.body.send_raw(json.dumps(f))
    err = await last_error(e.body, n)
    assert err["payload"]["code"] == "duplicate_id" and err["payload"]["about_id"] == f["id"]
    await asyncio.sleep(0.1)
    assert count(e.db, "SELECT count(*) FROM events WHERE payload LIKE '%i08%'") == 1


@case("invalid", "wrong session -> dropped and counted (metrics.dropped_session_mismatch), not stored")
async def i09_wrong_session(e):
    await e.body.send("event", {"kind": "damage", "detail": {"marker": "i09"}}, session=str(uuid.uuid4()))
    await until(lambda: e.hub.metrics["dropped_session_mismatch"] == 1)
    assert count(e.db, "SELECT count(*) FROM events WHERE payload LIKE '%i09%'") == 0


@case("invalid", "unknown type -> error{unknown_type}")
async def i10_unknown_type(e):
    n = len(e.body.received)
    await e.body.send("teleport", {"to": "anywhere"})
    assert (await last_error(e.body, n))["payload"]["code"] == "unknown_type"


@case("invalid", "type not accepted from this peer (body sends action.request, sense sends state)")
async def i11_wrong_direction(e):
    n = len(e.body.received)
    await e.body.send("action.request", {"action_id": str(uuid.uuid4()), "skill": "walk_to"})
    assert (await last_error(e.body, n))["payload"]["code"] == "unknown_type"
    n = len(e.sense.received)
    await e.sense.send("state", e.body.state())
    assert (await last_error(e.sense, n))["payload"]["code"] == "unknown_type"


@case("invalid", "invalid dimension in state -> error{bad_payload}, not stored")
async def i12_bad_dimension(e):
    n = len(e.body.received)
    st = e.body.state()
    st["dim"], st["inventory_hash"] = "overworld", "i12"
    await e.body.send("state", st)
    assert (await last_error(e.body, n))["payload"]["code"] == "bad_payload"
    assert count(e.db, "SELECT count(*) FROM observations WHERE payload LIKE '%i12%'") == 0


@case("invalid", "out-of-range position in state (y=9999) -> error{bad_payload}")
async def i13_bad_position(e):
    n = len(e.body.received)
    st = e.body.state()
    st["pos"]["y"], st["inventory_hash"] = 9999, "i13"
    await e.body.send("state", st)
    assert (await last_error(e.body, n))["payload"]["code"] == "bad_payload"
    assert count(e.db, "SELECT count(*) FROM observations WHERE payload LIKE '%i13%'") == 0


@case("invalid", "invented skill -> refused unknown_skill; no action row, nothing sent to the body")
async def i14_unknown_skill(e):
    r = await e.mind.do("fly_to_moon", {})
    assert r["ok"] is False and r["status"] == "refused" and r["code"] == r["error"] == "unknown_skill"
    assert r["action_id"] is None and "fly_to_moon" in r["reason"]
    assert count(e.db, "SELECT count(*) FROM actions") == 0 and sum(e.body.requests.values()) == 0


@case("invalid", "bad params (missing pos, extra key, wrong type) -> refused bad_params, no row")
async def i15_bad_params(e):
    for params in ({}, {"pos": {"x": 1, "y": 64, "z": 1}, "speed": 9}, {"pos": {"x": "1", "y": 64, "z": 1}},
                   {"pos": {"x": 1, "y": 64, "z": 1}, "tolerance": 99}):
        r = await e.mind.do("walk_to", params)
        assert r["error"] == "bad_params", params
    assert count(e.db, "SELECT count(*) FROM actions") == 0 and sum(e.body.requests.values()) == 0


@case("invalid", "out-of-bounds target (beyond explore_radius, y outside -64..320) -> refused, no row")
async def i16_out_of_bounds(e):
    for pos in ({"x": 100000, "y": 64, "z": 0}, {"x": 0, "y": 400, "z": 0}, {"x": 0, "y": -100, "z": 0}):
        assert (await e.mind.do("walk_to", {"pos": pos}))["error"] == "out_of_bounds", pos
    assert count(e.db, "SELECT count(*) FROM actions") == 0


@case("invalid", "action.result for an unknown action_id -> ignored, no row created")
async def i17_unknown_result(e):
    await e.body.send("action.result", {"action_id": str(uuid.uuid4()), "status": "ok", "evidence": {}})
    await until(lambda: e.mind.metrics["unknown_action_results"] == 1)
    assert count(e.db, "SELECT count(*) FROM actions") == 0


@case("invalid", "duplicate action.result -> second ignored; the action is counted once")
async def i18_duplicate_result(e):
    e.body.duplicate_result = True
    r = await e.mind.do("walk_to", {"pos": {"x": 6, "y": 64, "z": 0}}, wait=3)
    await until(lambda: e.mind.metrics["ignored_transitions"] >= 1)
    assert r["status"] == "ok" and count(e.db, "SELECT count(*) FROM actions") == 1
    assert count(e.db, "SELECT count(*) FROM tasks WHERE status='done'") == 1


@case("invalid", "wrong `from` for the link (from=sense on /body) -> error{bad_envelope}")
async def i19_wrong_from(e):
    n = len(e.body.received)
    await e.body.send("event", {"kind": "damage"}, **{"from": "sense"})
    assert (await last_error(e.body, n))["payload"]["code"] == "bad_envelope"


@case("invalid", "untrusted sender's /eve stop and approve are data, not commands")
async def i20_untrusted(e):
    await e.sense.owner_command("Griefer", True, "stop")       # sense says trusted, envelope does not list him
    await e.sense.owner_command("Hunter", False, "stop")       # listed, but not trusted by the server
    await until(lambda: e.mind.metrics["untrusted_commands"] == 2)
    assert not e.mind.stopped and not e.body.stopped
    assert count(e.db, "SELECT count(*) FROM approvals") == 0


@case("invalid", "message carrying a previous session after reconnect -> dropped")
async def i21_old_session(e):
    old = e.body.session
    await e.body.ws.close()
    await until(lambda: e.body.session not in (None, old), what="reconnect")
    await e.body.send("event", {"kind": "damage", "detail": {"marker": "i21"}}, session=old)
    await until(lambda: e.hub.metrics["dropped_session_mismatch"] >= 1)
    assert count(e.db, "SELECT count(*) FROM events WHERE payload LIKE '%i21%'") == 0


@case("invalid", "a second body hello supersedes the first: old link closed 4409")
async def i22_superseded(e):
    second = FakeBody(e.url + "/body", TOKEN, reconnect=False).start()
    try:
        await asyncio.wait_for(second.ready.wait(), 3)
        await until(lambda: e.hub.metrics["superseded"] == 1 and e.hub.links["body"].session == second.session)
    finally:
        await second.close()


@case("invalid", "binary frame -> error{bad_json}")
async def i23_binary(e):
    n = len(e.body.received)
    await e.body.send_raw(b"\x00\x01")
    assert (await last_error(e.body, n))["payload"]["code"] == "bad_json"


@case("invalid", "mc_version or registry_hash mismatch -> hello.reject{version|registry}")
async def i24_version_registry(e):
    r1, _ = await raw_first(e.url + "/body", hello_frame("body", TOKEN, mc_version="1.21"))
    r2, _ = await raw_first(e.url + "/body", hello_frame("body", TOKEN, registry_hash="0" * 64))
    assert r1[0]["payload"]["code"] == "version" and r2[0]["payload"]["code"] == "registry"
    assert r2[0]["payload"]["expected"] == e.mind.registry.hash


@case("invalid", "world mismatch (sense reports another world) -> dispatch refused, nothing sent")
async def i25_world_mismatch(e):
    await e.sense.close()
    other = FakeSense(e.url + "/sense", TOKEN, envelope=e.cfg.envelope, world_id="other:111111111111").start()
    try:
        await asyncio.wait_for(other.ready.wait(), 3)
        r = await e.mind.do("walk_to", {"pos": {"x": 1, "y": 64, "z": 1}})
        assert r["error"] == "world_mismatch" and sum(e.body.requests.values()) == 0
    finally:
        await other.close()


@case("invalid", "silent peer -> declared dead after dead_s and closed 4408")
async def i26_dead_link(e):
    e.body.silent, e.body.reconnect = True, False
    await until(lambda: e.hub.metrics["dead_link"] == 1 and e.hub.links["body"] is None, timeout=4)
    assert (await e.mind.do("walk_to", {"pos": {"x": 1, "y": 64, "z": 1}}))["error"] == "link_down"


@case("invalid", "stale state (age_ms 5000) -> dispatch refused stale_state")
async def i27_stale(e):
    e.body.stale_state = True
    await asyncio.sleep(0.2)
    assert (await e.mind.do("walk_to", {"pos": {"x": 1, "y": 64, "z": 1}}))["error"] == "stale_state"


@case("invalid", "permission denied by sense -> refused zone_denied, nothing sent to the body")
async def i28_zone_denied(e):
    e.sense.permissions = lambda q: (False, "home:break", "task_only zone")
    r = await e.mind.do("mine_block", {"pos": {"x": 3, "y": 63, "z": 3}})
    assert r["error"] == "zone_denied" and "home:break" in r["detail"] and sum(e.body.requests.values()) == 0


@case("invalid", "entity target that is not observed nearby (invented id) -> refused bad_target, nothing sent")
async def i29_unobserved_entity(e):
    r = await e.mind.do("attack_entity", {"entity_id": 4242})
    assert r["error"] == "bad_target" and sum(e.body.requests.values()) == 0
    assert count(e.db, "SELECT count(*) FROM actions") == 0


@case("invalid", "body logged in as another account than envelope owner.eve_account -> dispatch refused")
async def i30_account_mismatch(e):
    await e.body.close()
    other = FakeBody(e.url + "/body", TOKEN, account_name="SomeoneElse", reconnect=False).start()
    try:
        await asyncio.wait_for(other.ready.wait(), 3)
        await until(lambda: e.mind.fresh_state() is not None)
        r = await e.mind.do("walk_to", {"pos": {"x": 1, "y": 64, "z": 1}})
        assert r["error"] == "account_mismatch" and sum(other.requests.values()) == 0
    finally:
        await other.close()


@case("valid", "storage.index partial with removed:true marks the container gone and clears its items")
async def v14_storage_removed(e):
    c = {"id": "c9", "dim": "minecraft:overworld", "pos": {"x": 1, "y": 64, "z": 2}, "kind": "chest",
         "tag": "shared", "loaded": True, "items": [{"item": "minecraft:dirt", "count": 3}]}
    await e.sense.send_storage([c])
    await until(lambda: count(e.db, "SELECT count FROM container_items WHERE container_id='c9'") == 3)
    await e.sense.send_storage([{"id": "c9", "removed": True}], partial=True)
    await until(lambda: count(e.db, "SELECT gone FROM containers WHERE container_id='c9'") == 1)
    assert count(e.db, "SELECT count(*) FROM container_items WHERE container_id='c9'") == 0


@case("valid", "v1.1: a direct owner command in a task_only zone sends task.grant (exact target, future expiry) "
                "before the permission check, and is dispatched")
async def v15_task_grant(e):
    task_only(e)
    await until(lambda: e.mind.sense_tick is not None)
    r = await e.mind.cli("do", {"skill": "mine_block", "params": {"pos": BLOCK}, "wait": 3})
    assert r["ok"] and r["status"] == "ok", r
    g = e.sense.grants[-1]
    assert g["action_id"] == r["action_id"] and g["effect"] == "break" and g["positions"] == [BLOCK]
    await until(lambda: ("revoke", r["action_id"]) in e.sense.order)
    assert g["expires_tick"] > e.sense.tick
    assert e.sense.order[-3:] == [("grant", r["action_id"]), ("check", None), ("revoke", r["action_id"])]


@case("invalid", "v1.1: a planner-derived step never gets a task.grant: task_only zone -> zone_denied, nothing sent")
async def i31_no_grant_for_model_output(e):
    task_only(e)
    fake = FakeHTTP()
    try:
        fake.ollama({"steps": [{"skill": "mine_block", "params": {"pos": BLOCK}, "depends_on": []}], "question": None})
        e.mind.planner = Planner(url=fake.url, model="qwen3:8b", timeout_s=3)
        r = await e.mind.plan_and_run("please dig out the block at 3 63 3 for me", "cli")
        # refused already by the plan validator (envelope: home zone break is task_only, planner steps get no grant)
        assert r["error"] == "invalid_plan" and any("task_only" in x for x in r["detail"]) and e.sense.grants == []
        assert sum(e.body.requests.values()) == 0
    finally:
        fake.close()


@case("valid", "v1.1: envelope.changed after /eve reload: the mind reloads its envelope and binding holds when they match")
async def v16_envelope_changed(e):
    env2 = json.loads(json.dumps(e.cfg.envelope))
    env2["envelope_version"] = 2
    e.cfg.envelope_path.write_text(json.dumps(env2), "utf-8")      # foundation deploys the same file to both
    await e.sense.owner_command("Hunter", True, "reload")
    await e.sense.send("envelope.changed", {"version": 2, "hash": chash(env2)})
    await until(lambda: e.mind.env["envelope_version"] == 2 and e.mind.binding()[0])
    assert (await e.mind.do("walk_to", {"pos": {"x": 1, "y": 64, "z": 1}}, wait=3))["status"] == "ok"


@case("invalid", "v1.1: envelope.changed to an envelope the mind does not have -> dispatch refused envelope_mismatch")
async def i32_envelope_changed_mismatch(e):
    e.cfg.envelope_path.write_text(json.dumps(e.cfg.envelope), "utf-8")
    await e.sense.send("envelope.changed", {"version": 9, "hash": "f" * 64})
    await until(lambda: not e.mind.binding()[0])
    r = await e.mind.do("walk_to", {"pos": {"x": 1, "y": 64, "z": 1}})
    assert r["error"] == "envelope_mismatch" and sum(e.body.requests.values()) == 0


@case("valid", "v1.1: a full index split over partial frames marks containers gone only at complete:true")
async def v17_split_index(e):
    chest = lambda i: {"id": i, "dim": "minecraft:overworld", "pos": {"x": 1, "y": 64, "z": 2}, "kind": "chest",  # noqa
                       "tag": "shared", "loaded": True, "items": []}
    await e.sense.send_storage([chest("a"), chest("b"), chest("c")])
    await until(lambda: count(e.db, "SELECT count(*) FROM containers WHERE gone=0") == 3)
    await e.sense.send_storage([chest("a")], partial=True)
    await asyncio.sleep(0.2)
    assert count(e.db, "SELECT count(*) FROM containers WHERE gone=0") == 3      # not complete yet
    await e.sense.send("storage.index", {"containers": [chest("b")], "partial": True, "complete": True})
    await until(lambda: count(e.db, "SELECT gone FROM containers WHERE container_id='c'") == 1)
    assert count(e.db, "SELECT count(*) FROM containers WHERE gone=0") == 2


@case("valid", "v1.1: permission.denied with the new effect values (use_block, use_entity + entity_type) is stored")
async def v18_new_denied_effects(e):
    await e.sense.send("permission.denied", {"effect": "use_block", "dim": "minecraft:overworld",
                                             "pos": {"x": 1, "y": 64, "z": 1}, "rule_id": "outside:interact", "tick": 5})
    await e.sense.send("permission.denied", {"effect": "use_entity", "dim": "minecraft:overworld", "entity_id": 3,
                                             "entity_type": "minecraft:villager", "rule_id": "protected", "tick": 6})
    await until(lambda: count(e.db, "SELECT count(*) FROM events WHERE kind='permission.denied'") == 2)


@case("valid", "v1.1: body hello with level_name '' refuses dispatch until sense's eve:control world makes it re-hello")
async def v19_world_rebind(e):
    await e.body.close()
    blind = FakeBody(e.url + "/body", TOKEN, level_name="", reconnect=False).start()
    try:
        await asyncio.wait_for(blind.ready.wait(), 3)
        await until(lambda: e.mind.fresh_state() is not None)
        assert (await e.mind.do("walk_to", {"pos": {"x": 1, "y": 64, "z": 1}}))["error"] == "world_mismatch"
    finally:
        await blind.close()
    rebound = FakeBody(e.url + "/body", TOKEN, level_name="eve-dev", reconnect=False).start()    # the "rebind"
    try:
        await asyncio.wait_for(rebound.ready.wait(), 3)
        await until(lambda: e.mind.fresh_state() is not None and not e.mind.reconcile_needed)
        assert (await e.mind.do("walk_to", {"pos": {"x": 1, "y": 64, "z": 1}}, wait=3))["status"] == "ok"
    finally:
        await rebound.close()


@case("valid", "eve check: one permission.check over /sense, correlated result shown, nothing dispatched")
async def v20_eve_check(e):
    e.sense.permissions = lambda q: (q.get("pos") != BLOCK, "home:task_only" if q.get("pos") == BLOCK else "free",
                                     "fixture rule")
    r = await e.mind.cli("check", {"effect": "break", "pos": BLOCK})
    assert r["ok"] and r["allowed"] is False and r["rule_id"] == "home:task_only"
    assert e.sense.permission_checks[-1] == {"effect": "break", "dim": "minecraft:overworld", "actor": "eve", "pos": BLOCK}
    r = await e.mind.cli("check", {"effect": "attack", "entity_id": 12})
    assert r["allowed"] is True and e.sense.permission_checks[-1]["entity_id"] == 12
    r = await e.mind.cli("check", {"effect": "use_item", "pos": BLOCK, "face": "up"})
    assert r["ok"] and e.sense.permission_checks[-1] == {"effect": "use_item", "dim": "minecraft:overworld",
                                                         "actor": "eve", "pos": BLOCK, "face": "up"}
    for bad in ({"effect": "teleport", "pos": BLOCK}, {"effect": "break", "pos": {"x": 1.5, "y": 2, "z": 3}},
                {"effect": "break"}, {"effect": "use_item", "pos": BLOCK, "face": "sideways"}):
        assert not (await e.mind.cli("check", bad))["ok"]
    assert len(e.sense.permission_checks) == 3 and sum(e.body.requests.values()) == 0 and e.sense.grants == []


def refused(r, code, reason_has=""):
    """The one shape every mind-side refusal has (45L CP-10 finding: status/code were missing)."""
    assert r["ok"] is False and r["status"] == "refused" and r["code"] == code and r["action_id"] is None, r
    assert reason_has in r["reason"], r


async def do_cli(e, skill, params):
    return await e.mind.cli("do", {"skill": skill, "params": params, "wait": 3})     # what `eve do --json` returns


VILLAGER = {"id": 41, "type": "minecraft:villager", "protected": True, "pos": {"x": 2, "y": 64, "z": 2}}
WILBUR = {"id": 51, "type": "minecraft:pig", "name": "Wilbur", "protected": True, "pos": {"x": 2, "y": 64, "z": 3}}


@case("invalid", "45L CP-10 shapes: zone deny, private container, protected villager/named pig, check timeout and "
                 "retry limit all reply status=refused with a code and reason; no action row, nothing sent")
async def i33_refusal_shapes(e):
    e.sense.permissions = lambda q: (False, "no_zone", "break outside every zone")
    refused(await do_cli(e, "mine_block", {"pos": {"x": 4, "y": 63, "z": 30}}), "zone_denied", "no_zone")
    e.sense.permissions = lambda q: (False, "container:private(zone)", "private container")
    refused(await do_cli(e, "open_container", {"pos": {"x": 2, "y": 64, "z": 2}}), "zone_denied", "container:private")
    e.sense.permissions = lambda q: (True, "zone:mine", "ok")
    e.body.nearby = [VILLAGER, WILBUR]
    await until(lambda: len(e.mind.fresh_state()["nearby_entities"]) == 2)
    refused(await do_cli(e, "attack_entity", {"entity_id": 41}), "protected_entity", "villager")
    refused(await do_cli(e, "attack_entity", {"entity_id": 51}), "protected_entity", "Wilbur")
    e.sense.silent_permissions = True
    refused(await do_cli(e, "mine_block", {"pos": {"x": 3, "y": 63, "z": 3}}), "permission_unavailable", "1 s")
    e.sense.silent_permissions = False
    e.body.result_override = ("failed", "nothing_to_use")
    for _ in range(3):
        assert (await do_cli(e, "use_item", {"hold_ms": 100}))["code"] == "nothing_to_use"
    refused(await do_cli(e, "use_item", {"hold_ms": 100}), "retry_limit", "3 times")
    assert count(e.db, "SELECT count(*) FROM actions") == 3 and sum(e.body.requests.values()) == 3
    assert count(e.db, "SELECT count(*) FROM events WHERE kind='dispatch_refused'") == 6


@case("valid", "body-side outcomes keep their shape: rejected/failed with the body's code, correlated action id")
async def v21_body_codes(e):
    e.body.result_override, e.body.result_note = ("failed", "not_a_container"), "stone at 1, 64, 1"
    r = await do_cli(e, "open_container", {"pos": {"x": 1, "y": 64, "z": 1}})
    assert r["ok"] and r["status"] == "failed" and r["code"] == "not_a_container" and r["note"] == "stone at 1, 64, 1"
    e.body.result_override = e.body.result_note = None
    e.body.reject_code = "precondition_failed"
    r = await do_cli(e, "mine_block", {"pos": {"x": 3, "y": 63, "z": 3}})
    assert r["status"] == "rejected" and r["code"] == "precondition_failed" and r["action_id"]


@case("invalid", "action.rejected AFTER action.accepted (contract violation) still ends the action as rejected, "
                 "noted and counted; the task is not left running")
async def i34_rejected_after_accept(e):
    e.body.reject_after_accept = "busy"
    r = await do_cli(e, "mine_block", {"pos": {"x": 3, "y": 63, "z": 3}})
    assert r["status"] == "rejected" and r["code"] == "busy" and r["note"].startswith("rejected after accepted")
    assert e.mind.metrics["rejected_after_accept"] == 1 and e.mind.open_action() is None
    assert count(e.db, "SELECT status FROM tasks WHERE task_id=?", r["task_id"]) == "failed"


@case("valid", "a server permission.denied during an action reaches the action's note (CP-15 path)")
async def v22_server_denial_reaches_action(e):
    e.body.walk_s = 0.6
    e.body.result_override, e.body.result_note = ("failed", "nothing_to_use"), "nothing happened"
    t = asyncio.create_task(do_cli(e, "use_item", {"target_pos": {"x": 3, "y": 63, "z": 3}, "face": "up"}))
    await until(lambda: e.mind.open_action() is not None)
    await e.sense.send("permission.denied", {"effect": "use_item_on", "dim": "minecraft:overworld",
                                             "pos": {"x": 3, "y": 63, "z": 3}, "rule_id": "zone:home:place=task_only"})
    r = await t
    assert r["code"] == "nothing_to_use" and r["note"] == ("nothing happened; server denied: use_item_on "
                                                          "zone:home:place=task_only")


@case("valid", "use_item on a block: grant and pre-check are place on the clicked block and its face neighbour "
               "(as sense enforces); a planner step gets no grant and is refused in a task_only zone")
async def v23_use_item_place_rule(e):
    task_only(e)
    await until(lambda: e.mind.sense_tick is not None)
    tp = {"x": 3, "y": 63, "z": 3}
    r = await do_cli(e, "use_item", {"target_pos": tp, "face": "up"})
    assert r["status"] == "ok", r
    g = e.sense.grants[-1]
    assert g["effect"] == "place" and g["positions"] == [tp, {"x": 3, "y": 64, "z": 3}]
    checks = e.sense.permission_checks[-2:]
    assert [c["effect"] for c in checks] == ["place", "place"] and [c["pos"] for c in checks] == g["positions"]
    await until(lambda: ("revoke", r["action_id"]) in e.sense.order)          # grant ends with the action
    assert e.sense.grants == []
    r = await e.mind.do("use_item", {"target_pos": tp, "face": "up"}, source="cli")      # no grant=True
    refused(r, "zone_denied", "task_only")
    assert e.sense.grants == []


def test_manifest_has_coverage():
    kinds = [k for k, _, _ in CASES.values()]
    assert kinds.count("valid") >= 10 and kinds.count("invalid") >= 10


@pytest.mark.parametrize("rep", range(int(os.environ.get("EVE_REPEAT", "1"))))
@pytest.mark.parametrize("name", list(CASES))
def test_cp11(name, rep, tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            await CASES[name][2](e)
    run(go())
