"""CP-22 (mind side) — lack of progress is detected, cancelled with a concrete reason, retries are bounded."""
import asyncio
import uuid

from conftest import count, mind_env, run, until
from eve_mind.hub import now_ms


def test_container_change_seen_by_sense_counts_as_progress(tmp_path):
    """registry 0.3.0 smelt waits at a furnace for minutes: sense's index of that furnace changing is observed
    progress, so the 30 s stuck detector does not cancel it; with no change it still does."""
    import asyncio
    from conftest import mind_env, run, until

    furnace = {"id": "f1", "dim": "minecraft:overworld", "pos": {"x": 3, "y": 64, "z": 3}, "kind": "furnace",
               "tag": "shared", "loaded": True, "items": [{"item": "minecraft:raw_iron", "count": 8}]}

    async def go():
        async with mind_env(tmp_path, stuck_window_s=1.5) as e:     # updates every 0.3 s: 5x margin (was flaky at 0.8/0.5)
            await e.sense.send_storage([furnace])
            await until(lambda: e.mind.db.execute("SELECT count(*) FROM container_items").fetchone()[0] == 1)
            e.body.walk_s = 20.0                     # the action stays open for the whole test (was 3 s: a race)
            r = await e.mind.do("open_container", {"pos": furnace["pos"]})
            await asyncio.sleep(0.3)
            for n in (7, 6, 5, 4):                   # the furnace works: sense reports it every 0.5 s
                await e.sense.send_storage([{**furnace, "items": [{"item": "minecraft:raw_iron", "count": n}]}])
                await asyncio.sleep(0.3)
            assert e.mind.metrics["stuck"] == 0 and e.mind.status_of(r["action_id"]) in ("sent", "accepted", "running")
            await until(lambda: e.mind.metrics["stuck"] == 1, timeout=5)     # no further change: stuck as before
    run(go())


def test_no_world_progress_cancels_with_reason_and_releases(tmp_path):
    async def go():
        async with mind_env(tmp_path, stuck_window_s=0.8) as e:
            e.body.walk_s = 10.0                     # path "running" but she never moves
            r = await e.mind.do("walk_to", {"pos": {"x": 9, "y": 64, "z": 9}})
            e.db.execute("INSERT INTO reservations VALUES (?,?,?,?,?,?,?,?,?)",
                         (str(uuid.uuid4()), r["task_id"], "c1", "minecraft:cobblestone", 4, now_ms(), None, None,
                          None))
            await until(lambda: e.mind.status_of(r["action_id"]) == "cancelled", timeout=4)
            reason = count(e.db, "SELECT reason FROM tasks WHERE task_id=?", r["task_id"])
            assert reason.startswith("stuck: no observed world progress for 0.8 s during walk_to")
            assert count(e.db, "SELECT count(*) FROM reservations WHERE released_ts IS NOT NULL") == 1
            assert e.mind.metrics["stuck"] == 1 and not e.mind.stopped
    run(go())


def test_movement_counts_as_progress(tmp_path):
    async def go():
        async with mind_env(tmp_path, stuck_window_s=0.8) as e:
            e.body.walk_s = 2.0
            r = await e.mind.do("walk_to", {"pos": {"x": 9, "y": 64, "z": 9}})
            for i in range(8):                      # she keeps moving a block every 0.25 s
                e.body.pos = {"x": 1.0 + i, "y": 64.0, "z": 0.5}
                await asyncio.sleep(0.25)
            await until(lambda: e.mind.status_of(r["action_id"]) == "ok", timeout=3)
            assert e.mind.metrics["stuck"] == 0
    run(go())


def test_ignored_cancel_escalates_to_hard_stop(tmp_path):
    async def go():
        async with mind_env(tmp_path, stuck_window_s=0.5, cancel_grace_s=0.5) as e:
            e.body.walk_s, e.body.ignore_cancel = 10.0, True
            await e.mind.do("walk_to", {"pos": {"x": 9, "y": 64, "z": 9}})
            await until(lambda: e.mind.stopped and e.body.stopped, timeout=5)
            assert e.mind.meta("control.stopped_by") == "mind"
    run(go())


def test_repeated_failures_are_bounded(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.result_override = ("failed", "unreachable")
            target = {"pos": {"x": 7, "y": 64, "z": 7}}
            for _ in range(3):
                assert (await e.mind.do("walk_to", target, wait=3))["status"] == "failed"
            r = await e.mind.do("walk_to", target)
            assert r["error"] == "retry_limit" and sum(e.body.requests.values()) == 3
            e.body.result_override = None              # a different request is still allowed
            assert (await e.mind.do("walk_to", {"pos": {"x": 6, "y": 64, "z": 6}}, wait=3))["status"] == "ok"
    run(go())


def test_retry_limit_for_world_changes_counts_the_same_spot_only(tmp_path):
    """A place that failed 3 times from one standing spot is refused from that spot, but may be tried from another
    (CP-43 lane B: failures from a bad spot blocked the placement for 10 minutes)."""
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.result_override = ("failed", "obstructed")
            target = {"pos": {"x": 70, "y": 50, "z": 0}, "item": "minecraft:cobblestone"}
            e.body.pos = {"x": 70.5, "y": 50, "z": 3.5}
            await asyncio.sleep(0.6)                    # a state frame from this spot is stored
            for _ in range(3):
                assert (await e.mind.do("place_block", target, wait=3))["status"] == "failed"
            assert (await e.mind.do("place_block", target))["error"] == "retry_limit"
            e.body.pos = {"x": 72.5, "y": 50, "z": 0.5}
            await asyncio.sleep(0.6)
            e.body.result_override = None
            assert (await e.mind.do("place_block", target, wait=3))["status"] == "ok"
    run(go())
