"""CP-21 (mind side) — restart the mind service in the middle of a state-changing action.

The service runs as a real process and is killed with TerminateProcess (no cleanup), then restarted on the same
SQLite file. The fake body keeps running and reconnects. Pass: the action is never re-sent after the restart, the
body executes it exactly once, and the row is settled from *observed* state (a reconcile note naming the tick).
"""
import asyncio
import json
import sqlite3

from conftest import TOKEN, cli, envelope, free_port, run, service, until
from fake_body import FakeBody
from fake_sense import FakeSense

GOAL = {"x": 12, "y": 64, "z": -7}


def row(tmp_path, aid):
    con = sqlite3.connect(tmp_path / "eve.db")
    con.row_factory = sqlite3.Row
    try:
        a = con.execute("SELECT * FROM actions WHERE action_id=?", (aid,)).fetchone()
        t = con.execute("SELECT * FROM tasks WHERE task_id=?", (a["task_id"],)).fetchone()
        return dict(a), dict(t)
    finally:
        con.close()


async def connect_fakes(url, env, world_id=None):
    sense = FakeSense(url + "/sense", TOKEN, envelope=env, world_id=world_id).start()
    body = FakeBody(url + "/body", TOKEN, state_hz=10).start()
    await asyncio.wait_for(asyncio.gather(sense.ready.wait(), body.ready.wait()), 5)
    return body, sense


async def ready(port):
    async def ok():
        s = await cli(port, "status")
        return s["binding"]["ok"] and not s["reconcile_needed"]
    for _ in range(200):
        if await ok():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("mind never became ready")


async def scenario(tmp_path, *, on_link_loss, walk_s, outage_s, second_env=None, second_world=None):
    env, port = envelope(), free_port()
    async with service(tmp_path, port, env) as svc:
        body, sense = await connect_fakes(svc.url, env)
        body.drop_ack = body.drop_progress = True           # lost acknowledgements
        body.on_link_loss, body.walk_s = on_link_loss, walk_s
        await ready(port)
        r = await cli(port, "do", skill="walk_to", params={"pos": GOAL}, wait=0)
        assert r["ok"] and r["status"] == "sent", r
        aid = r["action_id"]
        await until(lambda: body.executions[aid] == 1, what="body started executing")
        first_session = body.session
    # service is now killed mid-action (context exit = TerminateProcess)
    await asyncio.sleep(outage_s)
    if second_world:
        await sense.close()
    async with service(tmp_path, port, second_env or env) as svc2:
        if second_world:
            sense = FakeSense(svc2.url + "/sense", TOKEN, envelope=second_env, world_id=second_world).start()
        await until(lambda: body.session not in (None, first_session), timeout=10, what="body reconnect")
        await ready(port)
        await asyncio.sleep(walk_s + 0.5)   # give a still-running action time to finish on the new link
        a, t = row(tmp_path, aid)
        tasks = await cli(port, "tasks")
    await body.close()
    await sense.close()
    after = [x for s, x in body.request_log if s != first_session]
    return aid, a, t, body, after, tasks


def test_cp21_lost_ack_body_finished_during_outage(tmp_path):
    """Body finished the walk while the mind was dead; its result was lost. Reconcile from observed position."""
    aid, a, t, body, after, _ = run(scenario(tmp_path, on_link_loss="finish", walk_s=0.8, outage_s=1.2))
    assert body.executions[aid] == 1 and aid not in after          # executed once, never replayed
    assert body.pos == GOAL
    assert a["status"] == "uncertain" and a["reconciled"] == 1
    assert a["reconcile_note"].startswith("postcondition holds in observed state") and "tick" in a["reconcile_note"]
    assert t["status"] == "done" and t["reason"] == "postcondition observed on reconcile"
    assert not any(m["type"] == "chat.send" and "restarted" in m["payload"]["text"] for m in body.received)


def test_cp21_lost_ack_body_aborted_idle_safe(tmp_path):
    """Body went IDLE-SAFE and cancelled; goal not reached. The mind must block the task, not replay."""
    aid, a, t, body, after, _ = run(scenario(tmp_path, on_link_loss="abort", walk_s=0.8, outage_s=0.5))
    assert body.executions[aid] == 1 and aid not in after
    assert body.pos != GOAL
    assert a["status"] == "uncertain" and a["reconciled"] == 1
    assert a["reconcile_note"].startswith("postcondition not observed")
    assert t["status"] == "blocked"
    lines = [m["payload"]["text"] for m in body.received if m["type"] == "chat.send"]
    assert any(x.startswith("I was restarted and lost: walk to") for x in lines), lines   # 45L 13:55: say it


def test_cp21_body_still_running_is_adopted(tmp_path):
    """Mind came back while the body was still walking: adopt the running action and take its real result."""
    aid, a, t, body, after, _ = run(scenario(tmp_path, on_link_loss="finish", walk_s=3.0, outage_s=0.2))
    assert body.executions[aid] == 1 and aid not in after
    assert a["status"] == "ok" and json.loads(a["evidence"])["final_pos"] == GOAL
    assert "adopted, not replayed" in a["reconcile_note"]
    assert t["status"] == "done"


def test_cp21_world_changed_during_restart(tmp_path):
    """Mind restarts bound to another world: the old world's open action is settled uncertain, never continued."""
    other = envelope(world_id="other:111111111111")
    aid, a, t, body, after, _ = run(scenario(tmp_path, on_link_loss="finish", walk_s=0.8, outage_s=1.2,
                                             second_env=other, second_world="other:111111111111"))
    assert body.executions[aid] == 1 and aid not in after
    assert a["status"] == "uncertain" and a["reconcile_note"].startswith("world changed")
    assert t["status"] == "blocked"


def test_cp21_grant_from_before_the_restart_is_revoked_on_reconcile(tmp_path):
    """45L CP-21 a1: a mine_block's task.grant was sent by the killed process; the restarted mind settles the action
    from observed state and must revoke that grant too (it is recovered from `events`)."""
    async def go():
        env, port = envelope(), free_port()
        async with service(tmp_path, port, env) as svc:
            body, sense = await connect_fakes(svc.url, env)
            body.drop_ack = body.drop_progress = True
            body.on_link_loss, body.walk_s = "abort", 0.8
            await ready(port)
            await sense.send_storage([])                      # any sense frame gives the mind a server tick
            await asyncio.sleep(0.3)
            r = await cli(port, "do", skill="mine_block", params={"pos": {"x": 3, "y": 63, "z": 3}}, wait=0)
            assert r["ok"], r
            aid = r["action_id"]
            await until(lambda: any(g["action_id"] == aid for g in sense.grants), what="grant sent")
            await until(lambda: body.executions[aid] == 1, what="body started executing")
            first_session = body.session
        async with service(tmp_path, port, env):
            await until(lambda: body.session not in (None, first_session), timeout=10, what="body reconnect")
            await ready(port)
            await until(lambda: ("revoke", aid) in sense.order, timeout=5, what="grant revoked after restart")
            a, _ = row(tmp_path, aid)
        await body.close()
        await sense.close()
        assert a["status"] == "uncertain" and a["reconciled"] == 1
        assert not any(g["action_id"] == aid for g in sense.grants)
    run(go())


def test_parked_tasks_survive_a_restart(tmp_path):
    """Lane B (orchestrator, after 7b7a0b9): a mind restart must never drop parked tasks; only the parked_ttl_s
    backstop (or an explicit drop) cancels them."""
    import time
    env, port = envelope(), free_port()

    async def go():
        async with service(tmp_path, port, env) as svc:
            body, sense = await connect_fakes(svc.url, env)
            await ready(port)
            con = sqlite3.connect(tmp_path / "eve.db")
            now = int(time.time() * 1000)
            con.executemany("INSERT INTO tasks (task_id, world_id, kind, goal, params, source, status, reason, created_ts,"
                            " updated_ts, version) VALUES (?, ?, 'skill', 'walk_to', '{}', 'cli', ?, 'parked by test', ?, ?, 1)",
                            [(f"park-{i}", env["world_id"], st, now, now)
                             for i, st in enumerate(("paused", "blocked", "paused"))])
            con.commit()
            con.close()
        async with service(tmp_path, port, env) as svc:          # killed (TerminateProcess) and restarted
            body, sense = await connect_fakes(svc.url, env)
            await ready(port)
            await asyncio.sleep(2)                                # supervise loop (expiry backstop) has run
            con = sqlite3.connect(tmp_path / "eve.db")
            left = con.execute("SELECT count(*) FROM tasks WHERE task_id LIKE 'park-%' AND status IN "
                               "('paused','blocked')").fetchone()[0]
            con.close()
            assert left == 3
    run(go())


def test_parked_ttl_comes_from_the_envelope_when_set(tmp_path):
    """O31: the envelope's limits.parked_ttl_s (lanes: 21600) overrides the 3600 s default; absent, 3600 stays."""
    from eve_mind.config import load
    env = envelope()
    assert load(root=tmp_path, envelope=env, db_path=tmp_path / "a.db").parked_ttl_s == 3600
    env["limits"] = dict(env.get("limits") or {}, parked_ttl_s=21600)
    assert load(root=tmp_path, envelope=env, db_path=tmp_path / "b.db").parked_ttl_s == 21600
