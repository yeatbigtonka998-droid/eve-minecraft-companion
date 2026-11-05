"""CP-23 (mind side) — a paused task resumes only if its assumptions still hold; work done is read from the world."""
import json

from conftest import count, design_folder, mind_env, run, until

GOAL = {"x": 8, "y": 64, "z": 8}


async def paused_walk(e, assumptions=None):
    e.body.walk_s = 2.0
    r = await e.mind.do("walk_to", {"pos": GOAL})
    await until(lambda: e.mind.status_of(r["action_id"]) == "running")
    await e.mind.stop("cli", "pause for test")
    await until(lambda: e.mind.status_of(r["action_id"]) == "cancelled")
    await e.mind.resume("cli")
    if assumptions is not None:     # what the planner relied on when it made the task
        e.db.execute("UPDATE tasks SET assumptions=? WHERE task_id=?", (json.dumps(assumptions), r["task_id"]))
    e.body.walk_s = 0.1
    return r["task_id"]


def task_status(e, tid):
    return count(e.db, "SELECT status FROM tasks WHERE task_id=?", tid)


def chest(n, gone=False):
    return {"id": "c1", "dim": "minecraft:overworld", "pos": {"x": 1, "y": 64, "z": 1}, "kind": "chest",
            "tag": "shared", "loaded": True, "items": [{"item": "minecraft:cobblestone", "count": n}]}


def test_work_already_done_is_not_redone(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            tid = await paused_walk(e)
            e.body.pos = dict(GOAL)                    # someone moved her there meanwhile
            await until(lambda: e.mind.fresh_state()["pos"] == GOAL)
            r = await e.mind.continue_task(tid[:8], "cli")
            assert r["ok"] and r["dispatched"] is False and task_status(e, tid) == "done"
            assert sum(e.body.requests.values()) == 1
    run(go())


def test_valid_task_redispatches_same_task(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            tid = await paused_walk(e)
            r = await e.mind.continue_task(tid, "cli", wait=3)
            assert r["status"] == "ok" and r["task_id"] == tid and task_status(e, tid) == "done"
            assert sorted(e.body.executions.values()) == [1, 1]          # two distinct actions, each once
    run(go())


def test_moved_supplies_make_the_task_stale(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            await e.sense.send_storage([chest(12)])
            await until(lambda: count(e.db, "SELECT count FROM container_items") == 12)
            tid = await paused_walk(e, [{"kind": "container_has", "container_id": "c1",
                                         "item": "minecraft:cobblestone", "count": 12}])
            await e.sense.send_storage([chest(5)])           # another player took some
            await until(lambda: count(e.db, "SELECT count FROM container_items") == 5)
            r = await e.mind.continue_task(tid, "cli")
            assert r["error"] == "stale_assumptions" and r["detail"][0]["now"] == 5
            await e.sense.send_storage([])                   # chest broken: full index without it
            await until(lambda: count(e.db, "SELECT gone FROM containers") == 1)
            assert (await e.mind.continue_task(tid, "cli"))["detail"][0]["now"] == "gone"
            assert task_status(e, tid) == "paused" and sum(e.body.requests.values()) == 1
            assert "stale assumptions" in count(e.db, "SELECT reason FROM tasks WHERE task_id=?", tid)
    run(go())


def test_revoked_approval_and_revised_design_are_stale(tmp_path):
    v, h1 = design_folder(tmp_path / "designs", "hut", 1)

    async def go():
        async with mind_env(tmp_path, designs_dir=tmp_path / "designs") as e:
            assert e.mind.decide("approve", "hut", "v1", None, "cli")["ok"]
            tid = await paused_walk(e, [{"kind": "approval", "project_id": "hut", "version": 1,
                                         "design_hash": h1}])
            assert e.mind.stale_assumptions(e.db.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()) == []
            assert e.mind.revoke("hut", "v1", None, "changed my mind")["revoked"] == 1
            r = await e.mind.continue_task(tid, "cli")
            assert r["error"] == "stale_assumptions" and r["detail"][0]["now"].startswith("approval revoked")
            assert e.mind.decide("approve", "hut", "v1", None, "cli")["ok"]
            d = json.loads((v / "design.json").read_text())
            d["palette"].append("minecraft:glass")                            # design edited in place
            (v / "design.json").write_text(json.dumps(d))
            r = await e.mind.continue_task(tid, "cli")
            assert r["detail"][0]["now"] == "design changed since approval"
            assert sum(e.body.requests.values()) == 1
    run(go())


def test_owner_assumptions_from_the_cli(tmp_path):
    """`eve do --assume`: the live CP-23 fixtures record what an owner task relies on."""
    v, h1 = design_folder(tmp_path / "designs", "hut", 1)

    async def go():
        async with mind_env(tmp_path, designs_dir=tmp_path / "designs") as e:
            await e.sense.send_storage([chest(12)])
            await until(lambda: count(e.db, "SELECT count FROM container_items") == 12)
            assert (await e.mind.cli("do", {"skill": "walk_to", "params": {"pos": GOAL},
                                            "assume": ["approval:hut:v1"]}))["code"] == "no_current_approval"
            assert e.mind.decide("approve", "hut", "v1", None, "cli")["ok"]
            assert (await e.mind.cli("do", {"skill": "walk_to", "params": {"pos": GOAL},
                                            "assume": ["container_has:c1"]}))["code"] == "bad_assumption"
            e.body.walk_s = 2.0
            r = await e.mind.cli("do", {"skill": "walk_to", "params": {"pos": GOAL},
                                        "assume": ["container_has:c1:cobblestone:12", "approval:hut:v1"]})
            t = e.db.execute("SELECT * FROM tasks WHERE task_id=?", (r["task_id"],)).fetchone()
            assert json.loads(t["assumptions"]) == [
                {"kind": "container_has", "container_id": "c1", "item": "minecraft:cobblestone", "count": 12},
                {"kind": "approval", "project_id": "hut", "version": 1, "design_hash": h1}]
            await until(lambda: e.mind.status_of(r["action_id"]) == "running")
            await e.mind.stop("cli", "pause")
            await e.mind.resume("cli")
            await e.sense.send_storage([chest(3)])
            await until(lambda: count(e.db, "SELECT count FROM container_items") == 3)
            r2 = await e.mind.cli("continue", {"task_id": r["task_id"]})
            assert r2["code"] == "stale_assumptions" and r2["detail"][0]["now"] == 3
    run(go())


def test_task_from_another_world_never_continues(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            tid = await paused_walk(e)
            e.db.execute("UPDATE tasks SET world_id='other:1' WHERE task_id=?", (tid,))
            assert (await e.mind.continue_task(tid, "cli"))["error"] == "world_changed"
            assert (await e.mind.continue_task("nope", "cli"))["error"] == "no_task"
    run(go())


def test_assume_container_ids_with_colons(tmp_path):
    """45L CP-23 a2: sense's container ids contain colons; item and count are parsed from the right."""
    async def go():
        async with mind_env(tmp_path) as e:
            p = e.mind.owner_assumptions
            cid = "minecraft:overworld:-26,99,-32"
            want = [{"kind": "container_has", "container_id": cid, "item": "minecraft:dirt", "count": 8}]
            assert p([f"container_has:{cid}:minecraft:dirt:8"]) == want
            assert p([f"container_has:{cid}:dirt:8"]) == want
            assert p(["container_has:c1:minecraft:dirt:8"])[0]["container_id"] == "c1"
            assert p(["container_has:c1:dirt:8"])[0] == {**want[0], "container_id": "c1"}
            assert p(["container_has::dirt:8"])["error"] == "bad_assumption"
    run(go())
