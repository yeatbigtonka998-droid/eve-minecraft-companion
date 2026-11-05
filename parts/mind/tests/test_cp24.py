"""CP-24 (mind side) — degrade safely: link drops, a locked DB, delayed responses. Planner/cloud cases are in
test_cp41.py (stall -> bounded wait, honest blocked; DeepSeek disabled)."""
import asyncio
import sqlite3

from conftest import TOKEN, count, mind_env, run, until
from fake_sense import FakeSense


def test_sense_drop_blocks_new_work_but_not_reporting(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 0.8
            r = await e.mind.do("walk_to", {"pos": {"x": 4, "y": 64, "z": 4}})
            await e.sense.close()
            await until(lambda: e.mind.status_of(r["action_id"]) == "ok", timeout=3)   # result still recorded
            assert (await e.mind.do("walk_to", {"pos": {"x": 1, "y": 64, "z": 1}}))["error"] == "link_down"
            e.sense = FakeSense(e.url + "/sense", TOKEN, envelope=e.cfg.envelope).start()
            await asyncio.wait_for(e.sense.ready.wait(), 3)
            assert (await e.mind.do("walk_to", {"pos": {"x": 1, "y": 64, "z": 1}}, wait=3))["status"] == "ok"
    run(go())


def test_body_reconnect_mid_action_is_adopted_not_replayed(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 1.5
            r = await e.mind.do("walk_to", {"pos": {"x": 4, "y": 64, "z": 4}})
            await until(lambda: e.mind.status_of(r["action_id"]) == "running")
            old = e.body.session
            await e.body.ws.close()
            await until(lambda: e.body.session not in (None, old), timeout=5)
            await until(lambda: e.mind.status_of(r["action_id"]) == "ok", timeout=5)
            assert e.body.requests[r["action_id"]] == 1 and e.body.executions[r["action_id"]] == 1
            assert "adopted" in e.mind.action(r["action_id"])["reconcile_note"]
    run(go())


def test_locked_db_refuses_dispatch_but_stop_still_works(tmp_path):
    async def go():
        async with mind_env(tmp_path, db_timeout_s=0.2) as e:
            other = sqlite3.connect(tmp_path / "eve.db", isolation_level=None)
            other.execute("BEGIN EXCLUSIVE")
            try:
                r = await e.mind.do("walk_to", {"pos": {"x": 2, "y": 64, "z": 2}})
                assert r["error"] == "db_unavailable" and sum(e.body.requests.values()) == 0
                await until(lambda: e.mind.db.failures >= 1, timeout=3)    # O82: the writer retries; the loop runs on
                assert e.hub.links["body"] is not None
                s = await e.mind.stop("cli", "while the DB is locked")
                assert s["delivered"] and not s["persisted"]
                await until(lambda: e.body.stopped)
                assert (await e.mind.resume("cli"))["error"] == "db_unavailable" and e.mind.stopped
            finally:
                other.execute("ROLLBACK")
                other.close()
            assert (await e.mind.resume("cli"))["ok"]
            assert (await e.mind.do("walk_to", {"pos": {"x": 2, "y": 64, "z": 2}}, wait=3))["status"] == "ok"
    run(go())


def test_delayed_result_after_deadline_is_recorded_without_reviving(tmp_path):
    async def go():
        async with mind_env(tmp_path, result_grace_s=0.2) as e:
            e.body.late_result_s = 2.0
            r = await e.mind.do("select_hotbar", {"slot": 6}, wait=2)   # deadline 1.0 + 0.2 s
            a = e.mind.action(r["action_id"])
            assert a["status"] == "uncertain" and a["code"] == "no_result"
            await until(lambda: e.mind.action(r["action_id"])["reconciled"] == 1)
            assert count(e.db, "SELECT status FROM tasks WHERE task_id=?", r["task_id"]) == "done"   # observed slot
            await until(lambda: e.mind.metrics["late_results"] == 1, timeout=3)
            assert "late body result ok" in e.mind.action(r["action_id"])["reconcile_note"]
            assert e.mind.open_action() is None
    run(go())


def test_locked_db_in_the_real_service_keeps_the_cli_responsive(tmp_path):
    """45L CP-24 a1: with the default 5 s bound and state frames arriving at 20 Hz, a held EXCLUSIVE lock froze the
    event loop (sync sqlite busy wait) so `eve` timed out on connect. Now: status answers at once, `do` is refused
    db_unavailable after the bounded wait, stop is delivered with persisted:false, resume is refused, all over /cli."""
    import time
    from conftest import cli, envelope, free_port, service
    from fake_body import FakeBody

    async def go():
        env, port = envelope(), free_port()
        async with service(tmp_path, port, env) as svc:
            sense = FakeSense(svc.url + "/sense", TOKEN, envelope=env).start()
            body = FakeBody(svc.url + "/body", TOKEN, state_hz=20).start()
            await asyncio.wait_for(asyncio.gather(sense.ready.wait(), body.ready.wait()), 5)
            await asyncio.sleep(0.5)
            other = sqlite3.connect(tmp_path / "eve.db", isolation_level=None)
            other.execute("BEGIN EXCLUSIVE")
            try:
                await asyncio.sleep(1.0)                                   # state frames now fail to store
                t0 = time.monotonic()
                assert (await cli(port, "status"))["ok"] and time.monotonic() - t0 < 1.0
                t0 = time.monotonic()
                r = await cli(port, "do", skill="jump", params={}, wait=0)
                assert r["error"] == "db_unavailable" and r["status"] == "refused", r
                # the worst case: db_timeout_s 5 + one scheduled retry (db.py caps the backoff at 2.0) + flush's 0.1 = 7.1;
                # 0.9 s margin for a loaded 2-vCPU runner (7.06 s seen on Actions run 42071993048)
                assert 4.5 < time.monotonic() - t0 < 8.0
                s = await cli(port, "stop", reason="locked")
                assert s["ok"] and s["delivered"] and s["persisted"] is False, s
                await until(lambda: body.stopped)
                assert (await cli(port, "resume"))["error"] == "db_unavailable"
                assert sum(body.requests.values()) == 0
            finally:
                other.execute("ROLLBACK")
                other.close()
            assert (await cli(port, "resume"))["ok"]
            assert (await cli(port, "do", skill="jump", params={}, wait=3))["status"] == "ok"
            await body.close()
            await sense.close()
        log = (tmp_path / "mind.log").read_text(errors="replace")
        assert log.count("not stored") <= 4                                  # rate-limited, not one per frame
    run(go())


def test_result_during_a_db_lock_is_applied_after_it_and_never_becomes_a_stop(tmp_path):
    """45L CP-24 a3: a walk's progress/result arrived inside a lock window and was lost; the stuck detector then
    cancelled an action the body had finished and escalated to STOP. O82: the working copy applies it at once; the
    file gets it after the lock."""
    async def go():
        async with mind_env(tmp_path, stuck_window_s=1.0, cancel_grace_s=0.5) as e:
            e.body.walk_s = 0.6
            r = await e.mind.do("walk_to", {"pos": {"x": 2, "y": 64, "z": 2}})
            other = sqlite3.connect(tmp_path / "eve.db", isolation_level=None)
            other.execute("BEGIN EXCLUSIVE")
            try:
                await asyncio.sleep(2.5)                      # result arrives at ~0.6 s, lock held past the window
                assert e.mind.status_of(r["action_id"]) == "ok"     # O82: applied at once in the working copy
                assert other.execute("SELECT status FROM actions WHERE action_id=?",
                                     (r["action_id"],)).fetchone()[0] != "ok"       # the file waits for the lock
            finally:
                other.execute("ROLLBACK")
                other.close()
            assert await e.mind.db.flush(5)
            assert e.mind.db.disk.execute("SELECT status FROM actions WHERE action_id=?",
                                          (r["action_id"],)).fetchone()[0] == "ok"   # ... and gets it after
            assert not e.mind.stopped and e.mind.metrics["stuck"] == 0
    run(go())


def test_stuck_action_the_body_is_not_running_is_settled_not_stopped(tmp_path):
    """A result that never arrives (body idle, action still open in the mind): settle from observed state."""
    async def go():
        async with mind_env(tmp_path, stuck_window_s=0.8, cancel_grace_s=0.5) as e:
            e.body.walk_s, e.body.drop_result = 0.3, True
            r = await e.mind.do("walk_to", {"pos": {"x": 2, "y": 64, "z": 2}})
            await until(lambda: e.mind.status_of(r["action_id"]) == "uncertain", timeout=4)
            a = e.mind.action(r["action_id"])
            assert a["reconciled"] == 1 and a["reconcile_note"].startswith("postcondition holds")
            await asyncio.sleep(1.0)
            assert not e.mind.stopped and e.mind.metrics["settled_body_idle"] == 1
    run(go())


def test_flush_gives_up_after_the_scheduled_retry_even_if_retries_keep_coming():
    """S93-ci-1 (Actions: resume hung under a held lock): the writer's retry time keeps moving while the file is locked;
    flush waits for the one scheduled when its bound ran out, then answers False."""
    import time
    from types import SimpleNamespace

    from eve_mind import db

    cls = next(c for c in vars(db).values() if isinstance(c, type) and "flush" in vars(c))

    async def go():
        d = SimpleNamespace(seq=5, done=0, retry_until=time.monotonic() + 0.3)

        async def moving():
            while True:
                d.retry_until = time.monotonic() + 0.3
                await asyncio.sleep(0.05)
        t = asyncio.ensure_future(moving())
        t0 = time.monotonic()
        try:
            assert await asyncio.wait_for(cls.flush(d, 0.1), 3.0) is False
        finally:
            t.cancel()
        assert 0.3 < time.monotonic() - t0 < 1.5                       # past the scheduled retry, and no further
    run(go())
