"""S38 agenda-wire, mind side: Hunter's agenda phrases and the offline briefing through owner_text (fake links)."""
import asyncio
import json

from conftest import mind_env, run


def test_plan_show_brief_through_owner_text(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            m, said = e.mind, []
            orig = m.say

            async def say(text, *a, **k):
                said.append(text)
                return await orig(text, *a, **k)
            m.say = say
            n0 = m.db.execute("SELECT count(*) FROM actions").fetchone()[0]
            r = await m.owner_text("plan for later: keep the farm; then finish the hut", "Hunter")   # O127: stored only
            assert r["ok"] and said[-1] == "Agenda: 1) keep the farm (ongoing); 2) finish the hut. I'll work on it while you're away."
            assert [i["goal"] for i in json.loads(m.meta("agenda.items"))] == ["keep the farm", "finish the hut"]
            r = await m.owner_text("plan: finish the hut; then jump into the lava pool", "Hunter")
            assert r["ok"] is False and "#2" in said[-1]                       # refused goal: agenda unchanged
            assert len(json.loads(m.meta("agenda.items"))) == 2
            r = await m.owner_text("what's on your agenda?", "Hunter")
            assert said[-1].startswith("Agenda: 1) keep the farm (ongoing); 2) finish the hut.") and "Next for me:" in said[-1]
            assert m.db.execute("SELECT count(*) FROM actions").fetchone()[0] == n0   # agenda lines dispatch nothing
            w = await m.do("walk_to", {"pos": {"x": 1, "y": 64, "z": 1}}, wait=10)
            assert w["status"] == "ok", w
            k0 = len(said)
            r = await m.owner_text("what did you do while I was gone?", "Hunter")
            assert r["ok"], r
            assert any("walk_to action" in s for s in said[k0:]), said[k0:]
            assert m.db.execute("SELECT count(*) FROM journal WHERE kind='briefing'").fetchone()[0] == 1
            k1 = len(said)
            await m.owner_text("what did you do while I was gone?", "Hunter")     # since the last briefing: nothing new
            assert said[k1:] == ["Nothing happened that I logged while you were away."], said[k1:]
    run(go())


def test_o127_a_present_plan_runs_now_in_order_and_stays_stored(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            m, said = e.mind, []
            e.body.walk_s = 0.3
            orig = m.say

            async def say(text, *a, **k):
                said.append(text)
                return await orig(text, *a, **k)
            m.say = say
            n0 = m.db.execute("SELECT count(*) FROM actions").fetchone()[0]
            await m.owner_text("plan: walk to 3 64 3 and then jump", "Hunter")
            assert said[0] == "Agenda: 1) walk to 3 64 3; 2) jump. Starting now.", said
            assert [i["goal"] for i in json.loads(m.meta("agenda.items"))] == ["walk to 3 64 3", "jump"]
            for _ in range(100):                                   # the sequence runs detached (O122)
                skills = [r[0] for r in m.db.execute("SELECT skill FROM actions ORDER BY rowid")][n0:]
                if skills == ["walk_to", "jump"]:
                    break
                await asyncio.sleep(0.1)
            assert skills == ["walk_to", "jump"], skills
            await m.owner_text("plan when I'm gone: jump", "Hunter")           # later: stored, nothing runs
            await asyncio.sleep(0.5)
            assert [r[0] for r in m.db.execute("SELECT skill FROM actions ORDER BY rowid")][n0:] == ["walk_to", "jump"]
            assert [i["goal"] for i in json.loads(m.meta("agenda.items"))] == ["jump"]
    run(go())


def test_o127_and_split_by_the_rule_map(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            m = e.mind
            await m.owner_text("plan for later: get 6 sand then make glass and place the glass", "Hunter")
            assert [i["goal"] for i in json.loads(m.meta("agenda.items"))] == ["get 6 sand", "make glass", "place the glass"]
            await m.owner_text("plan for later: get logs and planks then build a hut", "Hunter")
            assert [i["goal"] for i in json.loads(m.meta("agenda.items"))] == ["get logs and planks", "build a hut"]
    run(go())
