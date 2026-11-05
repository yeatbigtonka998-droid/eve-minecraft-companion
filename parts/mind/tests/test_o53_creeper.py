"""O53a/b: after creeper_flee ends with that creeper still alive within 12 and closing, the mind cancels the resumed
walk, shoots (bow + arrows) or holds, then dispatches the same walk again; the caller's wait follows it."""
import asyncio
import json
import time

from conftest import mind_env, run, until

CREEPER = 77


def creeper(d):
    return {"id": CREEPER, "type": "minecraft:creeper", "name": None, "pos": {"x": d, "y": 64, "z": 0}, "distance": d,
            "hostile": True, "protected": False, "line_of_sight": True}


async def follow(e, start, stop_at, step=0.4):
    d = start
    while d > stop_at:
        e.body.nearby = [creeper(d)]
        d -= step
        await asyncio.sleep(0.2)
    e.body.nearby = [creeper(stop_at)]


async def flee(e):
    await e.body.send("event", {"kind": "reflex", "detail": {"reflex": "creeper_flee", "phase": "start",
                                                             "trigger": {"entity_id": CREEPER, "distance": 6}}})
    await asyncio.sleep(2.4)                          # the creeper follows while she flees: ~2 s of closing samples
    await e.body.send("event", {"kind": "reflex", "detail": {"reflex": "creeper_flee", "phase": "end",
                                                             "result": "safe_creeper_far"}})


def rows(e):
    return [(r["skill"], json.loads(r["params"]), r["status"], r["task_id"])
            for r in e.mind.db.execute("SELECT skill, params, status, task_id FROM actions ORDER BY rowid")]


def test_creeper_still_closing_is_shot_then_the_same_walk_resumes(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 6
            e.body.inventory = [{"slot": 1, "item": "minecraft:bow", "count": 1},
                                {"slot": 12, "item": "minecraft:arrow", "count": 16}]
            e.body.nearby = [creeper(11)]
            walk = asyncio.ensure_future(e.mind.do("walk_to", {"pos": {"x": 30, "y": 64, "z": 0}}, wait=30))
            await until(lambda: e.mind.open_action() is not None)
            mover = asyncio.ensure_future(follow(e, 11.5, 9.0))
            await flee(e)
            await until(lambda: any(r[0] == "fight_ranged" for r in rows(e)), timeout=10)
            await mover
            e.body.nearby = [creeper(20)]                # shot / gone beyond 16: resume
            e.body.walk_s = 0.2
            r = await walk
            a = rows(e)
            assert [x[0] for x in a] == ["walk_to", "stop", "fight_ranged", "walk_to"], a
            assert a[0][2] == "cancelled" and a[3][1] == a[0][1] and a[3][3] == a[0][3] == a[2][3]
            assert a[2][1] == {"entity_id": CREEPER}
            assert r["status"] == "ok" and r["action_id"] != a[0][0], r       # the caller got the resumed walk
    run(go())


def test_without_a_bow_the_walk_holds_until_the_creeper_stops_closing(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 6
            e.body.nearby = [creeper(11)]
            walk = asyncio.ensure_future(e.mind.do("walk_to", {"pos": {"x": 30, "y": 64, "z": 0}}, wait=30))
            await until(lambda: e.mind.open_action() is not None)
            mover = asyncio.ensure_future(follow(e, 11.5, 9.0))
            await flee(e)
            await until(lambda: any(r[0] == "stop" for r in rows(e)), timeout=5)
            await mover                                   # it stops closing at 9.0
            t0 = time.monotonic()
            e.body.walk_s = 0.2
            await until(lambda: sum(1 for r in rows(e) if r[0] == "walk_to") == 2, timeout=10)
            assert time.monotonic() - t0 >= 2.5           # held ~3 s of calm before resuming
            assert not any(r[0] == "fight_ranged" for r in rows(e))
            assert (await walk)["status"] == "ok"
    run(go())


def test_a_creeper_moving_away_does_not_interrupt_the_walk(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 3
            e.body.nearby = [creeper(9)]
            walk = asyncio.ensure_future(e.mind.do("walk_to", {"pos": {"x": 30, "y": 64, "z": 0}}, wait=30))
            await until(lambda: e.mind.open_action() is not None)
            await e.body.send("event", {"kind": "reflex", "detail": {"reflex": "creeper_flee", "phase": "start",
                                                                     "trigger": {"entity_id": CREEPER}}})
            for d in (9, 10, 11, 12, 13):
                e.body.nearby = [creeper(d)]
                await asyncio.sleep(0.5)
            await e.body.send("event", {"kind": "reflex", "detail": {"reflex": "creeper_flee", "phase": "end"}})
            assert (await walk)["status"] == "ok"
            assert [r[0] for r in rows(e)] == ["walk_to"]
    run(go())


def test_body_end_fields_alone_trigger_the_hold(tmp_path):
    """Body f769eaf: the end event's creeper_id + creeper_closing are enough even without a start event seen."""
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 6
            e.body.nearby = [creeper(9)]
            walk = asyncio.ensure_future(e.mind.do("walk_to", {"pos": {"x": 30, "y": 64, "z": 0}}, wait=30))
            await until(lambda: e.mind.open_action() is not None)
            await asyncio.sleep(0.3)
            await e.body.send("event", {"kind": "reflex", "detail": {
                "reflex": "creeper_flee", "phase": "end", "result": "safe", "detail": {
                    "creeper_id": CREEPER, "creeper_distance": 9.0, "creeper_closing": True}}})
            await until(lambda: any(r[0] == "stop" for r in rows(e)), timeout=5)
            e.body.walk_s = 0.2
            e.body.nearby = [creeper(20)]
            assert (await walk)["status"] == "ok"
            assert [r[0] for r in rows(e)] == ["walk_to", "stop", "walk_to"]
    run(go())


def test_a_second_flee_from_the_same_creeper_counts_as_following(tmp_path):
    """Lane C a13: she outruns the creeper in every flee (her distance grows), the resumed walk takes her back, and
    body says not closing - the repeat flee from the same creeper is the sign it follows."""
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 8
            walk = asyncio.ensure_future(e.mind.do("walk_to", {"pos": {"x": 30, "y": 64, "z": 0}}, wait=40))
            await until(lambda: e.mind.open_action() is not None)
            for _ in range(2):
                await e.body.send("event", {"kind": "reflex", "detail": {
                    "reflex": "creeper_flee", "phase": "start", "trigger": {"entity_id": CREEPER, "distance": 7}}})
                for d in (7.0, 8.0, 9.0):                 # she flees: the distance grows
                    e.body.nearby = [creeper(d)]
                    await asyncio.sleep(0.4)
                await e.body.send("event", {"kind": "reflex", "detail": {
                    "reflex": "creeper_flee", "phase": "end", "result": "safe", "detail": {
                        "creeper_id": CREEPER, "creeper_distance": 9.0, "creeper_closing": False}}})
                await asyncio.sleep(0.5)
            await until(lambda: any(r[0] == "stop" for r in rows(e)), timeout=5)
            e.body.walk_s = 0.2
            e.body.nearby = [creeper(20)]
            assert (await walk)["status"] == "ok"
            assert [r[0] for r in rows(e)] == ["walk_to", "stop", "walk_to"]
    run(go())


def test_status_names_the_minds_commit_and_start(tmp_path):
    """O53c: combat's harness checks the lane mind is >= 8661e44 from `eve status`."""
    import subprocess
    from conftest import ROOT

    async def go():
        async with mind_env(tmp_path) as e:
            s = e.mind.status()
            # the shared checkout may gain commits mid-run: the commit read at startup is HEAD or an ancestor of it
            assert s["mind_commit"] and s["mind_started"]
            assert subprocess.run(["git", "merge-base", "--is-ancestor", s["mind_commit"], "HEAD"], cwd=ROOT).returncode == 0
    run(go())


def test_mind_commit_from_file_then_git_then_env(tmp_path, monkeypatch):
    """O53d / 45L: an unpacked archive (no checkout) attests itself from MIND_COMMIT beside the package, else env
    EVE_MIND_COMMIT; nothing -> None, never a wrong commit from some other repo."""
    from eve_mind.mind import _mind_commit
    monkeypatch.delenv("EVE_MIND_COMMIT", raising=False)
    assert _mind_commit(tmp_path) is None
    monkeypatch.setenv("EVE_MIND_COMMIT", "abc1234")
    assert _mind_commit(tmp_path) == "abc1234"
    (tmp_path / "MIND_COMMIT").write_text("8e2f7fc\n", encoding="utf-8")
    assert _mind_commit(tmp_path) == "8e2f7fc"



def test_o83h_mind_src_is_the_commit_the_running_src_came_from(tmp_path):
    """O83h (lane E: daf8fd6's src under HEAD 6b666a9; --expect-mind against mind_commit refused): MIND_COMMIT when
    installed; else the newest commit whose parts/mind/src tree equals the checkout's; src edited in place -> None."""
    import subprocess

    from eve_mind.mind import _mind_src
    pkg = tmp_path / "parts" / "mind" / "src" / "eve_mind"
    pkg.mkdir(parents=True)

    def git(*a):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=tmp_path,
                              capture_output=True, text=True, check=True).stdout.strip()
    git("init", "-q")
    (pkg / "mind.py").write_text("A = 1\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "a")
    a = git("rev-parse", "--short=7", "HEAD")
    (pkg / "mind.py").write_text("A = 2\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("x", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "b")
    b = git("rev-parse", "--short=7", "HEAD")
    (tmp_path / "other.txt").write_text("y", encoding="utf-8")
    git("commit", "-qam", "c")                                  # HEAD moves, src does not
    assert _mind_src(pkg)[0] == b                               # the commit the src came from, not HEAD
    git("checkout", a, "--", "parts/mind/src")                  # the lane's mixed-rev swap
    commit, tree = _mind_src(pkg)
    assert commit == a and tree == git("rev-parse", f"{a}:parts/mind/src"), (commit, tree)
    (pkg / "mind.py").write_text("A = 3\n", encoding="utf-8")   # edited in place: no commit holds it
    assert _mind_src(pkg) == (None, None)
    (pkg / "MIND_COMMIT").write_text("daf8fd6\n", encoding="utf-8")
    assert _mind_src(pkg) == ("daf8fd6", None)                  # the installed archive attests itself

def test_a14_shoots_after_its_own_stop_settles_and_refusals_cost_no_try(tmp_path):
    """Lane C a14: fight_ranged went out within ms of the rule's own stop, was refused busy twice and both tries were
    spent. Now it waits for the stop's result and only a dispatched fight_ranged counts; the creeper here does not
    approach after the flee (she outran it) - the second flee already showed it follows."""
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s, e.body.late_result_s = 8, 0.3          # results (the stop's too) arrive 0.3 s late
            e.body.inventory = [{"slot": 2, "item": "minecraft:bow", "count": 1},
                                {"slot": 9, "item": "minecraft:arrow", "count": 32}]
            walk = asyncio.ensure_future(e.mind.do("walk_to", {"pos": {"x": 30, "y": 64, "z": 0}}, wait=40))
            await until(lambda: e.mind.open_action() is not None)
            for _ in range(2):
                await e.body.send("event", {"kind": "reflex", "detail": {
                    "reflex": "creeper_flee", "phase": "start", "trigger": {"entity_id": CREEPER, "distance": 7}}})
                for d in (7.0, 8.5, 9.5):
                    e.body.nearby = [creeper(d)]
                    await asyncio.sleep(0.3)
                await e.body.send("event", {"kind": "reflex", "detail": {
                    "reflex": "creeper_flee", "phase": "end", "result": "safe", "detail": {
                        "creeper_id": CREEPER, "creeper_distance": 9.5, "creeper_closing": False}}})
                await asyncio.sleep(0.4)
            await until(lambda: any(r[0] == "fight_ranged" for r in rows(e)), timeout=10)
            e.body.nearby = [creeper(20)]                         # shot: gone beyond 16
            e.body.walk_s = 0.2
            assert (await walk)["status"] == "ok"
            skills = [r[0] for r in rows(e)]
            assert skills.count("fight_ranged") >= 1 and skills[-1] == "walk_to", skills
            refused = e.mind.db.execute("SELECT count(*) FROM events WHERE kind='dispatch_refused' AND "
                                        "payload LIKE '%fight_ranged%'").fetchone()[0]
            assert refused == 0, refused
    run(go())


def test_a14_exact_sequence_dispatches_fight_ranged(tmp_path):
    """O53 a14 replay (orchestrator): the walk paused by creeper_flee; flee end with the creeper alive (~9.5),
    approaching her, body's creeper_closing true; our stop's result arrives late as on lane C. A fight_ranged must
    be dispatched (lane C: 0 of 20, every try refused busy), then the same walk resumes."""
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s, e.body.late_result_by_skill = 8, {"stop": 0.3}   # lane C: the walk's result came first
            e.body.inventory = [{"slot": 0, "item": "minecraft:iron_sword", "count": 1},
                                {"slot": 2, "item": "minecraft:bow", "count": 1},
                                {"slot": 9, "item": "minecraft:arrow", "count": 32}]
            e.body.nearby = [creeper(6.1)]
            walk = asyncio.ensure_future(e.mind.do("walk_to", {"pos": {"x": 30, "y": 64, "z": 0}}, wait=40))
            await until(lambda: e.mind.open_action() is not None)
            await e.body.send("event", {"kind": "reflex", "detail": {
                "reflex": "creeper_flee", "phase": "start", "trigger": {"entity_id": CREEPER, "distance": 6.1}}})
            mover = asyncio.ensure_future(follow(e, 11.5, 8.6, step=0.15))   # it keeps walking after her
            await asyncio.sleep(2.6)                           # ~2.5 s of approach before the flee ends
            await e.body.send("event", {"kind": "reflex", "detail": {
                "reflex": "creeper_flee", "phase": "end", "result": "safe", "detail": {
                    "creeper_id": CREEPER, "creeper_distance": 9.2, "creeper_closing": True}}})
            await until(lambda: any(r[0] == "fight_ranged" for r in rows(e)), timeout=10)
            await mover
            e.body.nearby, e.body.walk_s = [creeper(20)], 0.2
            assert (await walk)["status"] == "ok"
            a = rows(e)
            assert [x[0] for x in a][:3] == ["walk_to", "stop", "fight_ranged"] and a[-1][0] == "walk_to", a
            assert a[2][1] == {"entity_id": CREEPER} and a[-1][1] == a[0][1]
            assert not e.mind.db.execute("SELECT count(*) FROM events WHERE kind='dispatch_refused'").fetchone()[0]
    run(go())


def test_a16_keeps_shooting_a_following_creeper_until_it_dies(tmp_path):
    """O53g a16: each fight_ranged call landed ~1 hit and a creeper needs 3-4; the old 2-try cap ended shooting.
    Replay: the creeper keeps following, two calls return lost_target, the third kills it - three dispatches, none
    refused (re-shots are not retries), then the walk resumes."""
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s, e.body.late_result_by_skill = 8, {"stop": 0.1}
            e.body.inventory = [{"slot": 2, "item": "minecraft:bow", "count": 1},
                                {"slot": 9, "item": "minecraft:arrow", "count": 32}]
            e.body.result_override = {"fight_ranged": [("failed", "lost_target"), ("failed", "lost_target")]}
            base, calls = e.body.execute, []

            async def execute(aid, p, req_id):
                if p["skill"] == "fight_ranged":
                    calls.append(aid)
                    if len(calls) == 3:
                        mover.cancel()
                        e.body.nearby = []                         # the third call kills it
                return await base(aid, p, req_id)
            e.body.execute = execute
            e.body.nearby = [creeper(11.5)]
            walk = asyncio.ensure_future(e.mind.do("walk_to", {"pos": {"x": 30, "y": 64, "z": 0}}, wait=60))
            await until(lambda: e.mind.open_action() is not None)
            await e.body.send("event", {"kind": "reflex", "detail": {
                "reflex": "creeper_flee", "phase": "start", "trigger": {"entity_id": CREEPER, "distance": 7}}})
            mover = asyncio.ensure_future(follow(e, 11.5, 5.0, step=0.06))    # it keeps walking after her
            await asyncio.sleep(2.6)
            await e.body.send("event", {"kind": "reflex", "detail": {
                "reflex": "creeper_flee", "phase": "end", "result": "safe", "detail": {
                    "creeper_id": CREEPER, "creeper_distance": 10.0, "creeper_closing": True}}})
            e.body.walk_s = 0.3
            r = await walk
            assert r["status"] == "ok", r
            a = rows(e)
            assert [x[0] for x in a] == ["walk_to", "stop", "fight_ranged", "fight_ranged", "fight_ranged", "walk_to"], a
            assert [x[2] for x in a[2:5]] == ["failed", "failed", "ok"]
            assert not e.mind.db.execute("SELECT count(*) FROM events WHERE kind='dispatch_refused'").fetchone()[0]
    run(go())


async def idle_flee(e):
    """Idle Eve (no open action): the creeper approaches, she flees, the flee ends with it still closing (combat a25)."""
    await e.body.send("event", {"kind": "reflex", "detail": {
        "reflex": "creeper_flee", "phase": "start", "trigger": {"entity_id": CREEPER, "distance": 7}}})
    mover = asyncio.ensure_future(follow(e, 11.5, 6.0, step=0.06))
    await asyncio.sleep(2.6)
    await e.body.send("event", {"kind": "reflex", "detail": {
        "reflex": "creeper_flee", "phase": "end", "result": "safe", "detail": {
            "creeper_id": CREEPER, "creeper_distance": 9.0, "creeper_closing": True}}})
    return mover


def test_o53m2_idle_eve_shoots_a_following_creeper_under_the_self_defence_source(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            assert e.mind.mode == "manual"
            e.body.walk_s = 0.3
            e.body.inventory = [{"slot": 2, "item": "minecraft:bow", "count": 1},
                                {"slot": 9, "item": "minecraft:arrow", "count": 32}]
            e.body.nearby = [creeper(11.5)]
            mover = await idle_flee(e)
            await until(lambda: any(r[0] == "fight_ranged" for r in rows(e)), timeout=8)
            mover.cancel()
            e.body.nearby = []                                     # shot dead
            src = e.mind.db.execute("SELECT t.source FROM actions a JOIN tasks t USING (task_id) "
                                    "WHERE a.skill='fight_ranged'").fetchone()[0]
            assert src == "reflex:creeper"
            assert e.mind.db.execute("SELECT count(*) FROM events WHERE kind='self_defence'").fetchone()[0] >= 1
            assert not any(r[0] in ("walk_to", "stop") for r in rows(e))   # idle: nothing cancelled or resumed
    run(go())


def test_o53m2_idle_without_arrows_holds(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.inventory = [{"slot": 2, "item": "minecraft:bow", "count": 1}]
            e.body.nearby = [creeper(11.5)]
            mover = await idle_flee(e)
            await until(lambda: e.mind.db.execute("SELECT count(*) FROM events WHERE kind='creeper_follow'")
                        .fetchone()[0] == 1, timeout=5)
            await asyncio.sleep(1.0)
            mover.cancel()
            assert rows(e) == [] and '"hold"' in e.mind.db.execute(
                "SELECT payload FROM events WHERE kind='creeper_follow'").fetchone()[0]
    run(go())


def test_o53m2_the_self_defence_source_runs_nothing_else(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.inventory = [{"slot": 2, "item": "minecraft:bow", "count": 1},
                                {"slot": 9, "item": "minecraft:arrow", "count": 32}]
            e.body.nearby = [creeper(9)]
            await until(lambda: e.mind.fresh_state() is not None)
            w = await e.mind.do("walk_to", {"pos": {"x": 3, "y": 64, "z": 3}}, source="reflex:creeper")
            assert w["error"] == "mode_denied", w
            f = await e.mind.do("fight_ranged", {"entity_id": CREEPER}, source="reflex:creeper")
            assert f["error"] == "mode_denied", f                  # not a creeper the guard is engaging
            assert rows(e) == []
    run(go())


def test_o72a_a_first_flee_that_ends_cornered_already_counts_as_following(tmp_path):
    """Combat a28 (O72a): a fast creeper reaches her ~6 s after the summon during an open walk; the first flee ends
    cornered_shielded with it at 2.7 - no 'safe' end, no second approach. That is following: creeper_follow fires and
    fight_ranged is dispatched even inside 4 blocks (combat's skill shoots when there is no room to back away)."""
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 8
            e.body.inventory = [{"slot": 2, "item": "minecraft:bow", "count": 1},
                                {"slot": 9, "item": "minecraft:arrow", "count": 32}]
            e.body.nearby = [creeper(2.7)]
            walk = asyncio.ensure_future(e.mind.do("walk_to", {"pos": {"x": 30, "y": 64, "z": 0}}, wait=40))
            await until(lambda: e.mind.open_action() is not None)
            await e.body.send("event", {"kind": "reflex", "detail": {
                "reflex": "creeper_flee", "phase": "start", "trigger": {"entity_id": CREEPER, "distance": 6}}})
            await asyncio.sleep(0.4)
            await e.body.send("event", {"kind": "reflex", "detail": {
                "reflex": "creeper_flee", "phase": "end", "result": "cornered_shielded", "detail": {
                    "creeper_id": CREEPER, "creeper_distance": 2.7, "creeper_closing": False}}})
            await until(lambda: any(r[0] == "fight_ranged" for r in rows(e)), timeout=8)
            e.body.nearby, e.body.walk_s = [], 0.2                      # shot dead
            assert (await walk)["status"] == "ok"
            ev = e.mind.db.execute("SELECT payload FROM events WHERE kind='creeper_follow'").fetchone()[0]
            assert '"plan": "shoot"' in ev, ev
    run(go())


def test_o72i_a33_idle_creeper_back_within_30s_is_shot_at_the_flee_start(tmp_path):
    """Combat a33 #13 (O72i a): the walk had ended in the corner; flee 1 ended safe; the creeper came back and the
    second flee ended cornered only after the blast. Idle + the same creeper's last flee ended within 30 s = following:
    fight_ranged under reflex:creeper at the START of flee 2, before any end event."""
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.inventory = [{"slot": 2, "item": "minecraft:bow", "count": 1},
                                {"slot": 9, "item": "minecraft:arrow", "count": 32}]
            e.body.nearby = [creeper(9.0)]
            await until(lambda: e.mind.fresh_state() is not None)
            start = {"reflex": "creeper_flee", "phase": "start", "trigger": {"entity_id": CREEPER, "distance": 6.5}}
            await e.body.send("event", {"kind": "reflex", "detail": start})
            await asyncio.sleep(0.3)
            await e.body.send("event", {"kind": "reflex", "detail": {           # flee 1: safe, far, not closing
                "reflex": "creeper_flee", "phase": "end", "result": "safe", "detail": {
                    "creeper_id": CREEPER, "creeper_distance": 9.0, "creeper_closing": False}}})
            await asyncio.sleep(0.5)
            assert rows(e) == []                                               # nothing yet
            e.body.nearby = [creeper(5.5)]
            await e.body.send("event", {"kind": "reflex", "detail": start})   # flee 2 starts - no end event sent
            await until(lambda: any(r[0] == "fight_ranged" for r in rows(e)), timeout=6)
            e.body.nearby = []
            src = e.mind.db.execute("SELECT t.source FROM actions a JOIN tasks t USING (task_id) "
                                    "WHERE a.skill='fight_ranged'").fetchone()[0]
            assert src == "reflex:creeper"
            assert '"plan": "shoot"' in e.mind.db.execute(
                "SELECT payload FROM events WHERE kind='creeper_follow'").fetchone()[0]
    run(go())
