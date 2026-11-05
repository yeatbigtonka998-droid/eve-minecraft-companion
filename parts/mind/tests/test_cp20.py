"""CP-20 (mind side) — one owner of control: one action at a time, stop always wins, nothing old reactivates."""
import asyncio

from conftest import count, mind_env, run, until


def test_one_action_in_flight(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 1.0
            r1 = await e.mind.do("walk_to", {"pos": {"x": 3, "y": 64, "z": 3}})
            r2 = await e.mind.do("mine_block", {"pos": {"x": 1, "y": 63, "z": 1}})
            assert r1["ok"] and r2["error"] == "busy"
            await until(lambda: sum(e.body.requests.values()) == 1)
            await asyncio.sleep(0.3)
            assert sum(e.body.requests.values()) == 1
    run(go())


def test_stop_wins_and_late_results_do_not_revive(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 1.0
            r = await e.mind.do("walk_to", {"pos": {"x": 5, "y": 64, "z": 5}})
            await until(lambda: e.mind.status_of(r["action_id"]) == "running")
            await e.mind.stop("cli", "test")
            await until(lambda: e.mind.status_of(r["action_id"]) == "cancelled")
            assert count(e.db, "SELECT status FROM tasks WHERE task_id=?", r["task_id"]) == "paused"
            # a late/forged ok for the cancelled action changes nothing
            await e.body.send("action.result", {"action_id": r["action_id"], "status": "ok", "evidence": {}})
            await until(lambda: e.mind.metrics["ignored_transitions"] >= 1)
            assert e.mind.status_of(r["action_id"]) == "cancelled"
            await e.mind.resume("cli")
            await asyncio.sleep(0.3)
            assert count(e.db, "SELECT status FROM tasks WHERE task_id=?", r["task_id"]) == "paused"
            assert sum(e.body.requests.values()) == 1 and e.mind.open_action() is None
    run(go())


def test_output_from_before_a_stop_is_refused(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            epoch = e.mind.epoch                       # e.g. a planner call started here
            await e.mind.stop("cli", "test")
            await e.mind.resume("cli")
            r = await e.mind.do("walk_to", {"pos": {"x": 1, "y": 64, "z": 1}}, epoch=epoch)
            assert r["error"] == "stale_epoch" and sum(e.body.requests.values()) == 0
    run(go())


def test_cancel_is_not_a_hard_stop(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 2.0
            r = await e.mind.do("walk_to", {"pos": {"x": 5, "y": 64, "z": 5}})
            await until(lambda: e.mind.status_of(r["action_id"]) == "running")
            c = await e.mind.cancel_active("owner changed their mind", source="cli")
            assert c["ok"]
            await until(lambda: e.mind.status_of(r["action_id"]) == "cancelled")
            assert count(e.db, "SELECT status FROM tasks WHERE task_id=?", r["task_id"]) == "cancelled"
            assert not e.mind.stopped and not e.body.stopped
            e.body.walk_s = 0.1
            assert (await e.mind.do("walk_to", {"pos": {"x": 1, "y": 64, "z": 1}}, wait=3))["status"] == "ok"
    run(go())


def test_stopped_body_rejects_and_mind_never_auto_resumes(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            await e.sense.owner_command("Hunter", True, "stop")
            await until(lambda: e.mind.stopped and e.body.stopped)
            await e.body.ws.close()                         # reconnect must not resume anything
            await until(lambda: e.body.ready.is_set() and e.mind.fresh_state() is not None, timeout=5)
            await asyncio.sleep(0.3)
            assert e.mind.stopped and e.body.stopped
            assert not any(m["type"] == "control.resume" for m in e.body.received)
    run(go())


def test_stopped_task_is_parked_not_active_and_can_be_dropped(tmp_path):
    """A stop parks the task; resume does not continue it; status never calls it active; drop or TTL clears it."""
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 2.0
            r = await e.mind.do("walk_to", {"pos": {"x": 5, "y": 64, "z": 5}})
            await until(lambda: e.mind.status_of(r["action_id"]) == "running")
            assert e.mind.status()["active_task"]["task_id"] == r["task_id"]
            await e.mind.stop("cli", "cp09 stop mid-walk")
            await until(lambda: e.mind.status_of(r["action_id"]) == "cancelled")
            await e.mind.resume("cli")
            s = e.mind.status()
            assert s["active_task"] is None and s["parked_tasks"] == 1
            t = await e.mind.cli("tasks", {})
            assert t["active"] == [] and [x["task_id"] for x in t["parked"]] == [r["task_id"]]
            assert (await e.mind.cli("drop", {"task_id": "nope"}))["error"] == "no_task"
            d = await e.mind.cli("drop", {"task_id": r["task_id"][:8]})
            assert d["dropped"] == [r["task_id"]]
            assert count(e.db, "SELECT status FROM tasks WHERE task_id=?", r["task_id"]) == "cancelled"
            assert e.mind.status()["parked_tasks"] == 0 and sum(e.body.requests.values()) == 1
            # a running task cannot be dropped (that is eve cancel)
            e.body.walk_s = 2.0
            r2 = await e.mind.do("walk_to", {"pos": {"x": 2, "y": 64, "z": 2}})
            await until(lambda: e.mind.status_of(r2["action_id"]) == "running")
            assert (await e.mind.cli("drop", {"task_id": r2["task_id"]}))["error"] == "not_parked"
    run(go())


def test_parked_tasks_expire(tmp_path):
    async def go():
        async with mind_env(tmp_path, parked_ttl_s=0.5) as e:
            e.body.walk_s = 2.0
            r = await e.mind.do("walk_to", {"pos": {"x": 5, "y": 64, "z": 5}})
            await until(lambda: e.mind.status_of(r["action_id"]) == "running")
            await e.mind.stop("cli", "test")
            await until(lambda: count(e.db, "SELECT status FROM tasks WHERE task_id=?", r["task_id"]) == "cancelled",
                        timeout=4)
            assert count(e.db, "SELECT reason FROM tasks WHERE task_id=?", r["task_id"]).startswith("expired: parked")
            assert e.mind.stopped                 # expiry never touches STOPPED
    run(go())
