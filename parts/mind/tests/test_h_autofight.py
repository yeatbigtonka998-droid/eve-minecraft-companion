"""H-autofight, mind side (parts/combat/H-AUTOFIGHT.md f61a43ae): body's auto_fight reflex start/end events. On start
the frame is suspended with one line; on end one line, and the paused action is resumed by body or - ended
`interrupted` - sent again by the mind; `floor` backs her off; nothing of the mind's own starts during a fight."""
import asyncio
import json

from conftest import mind_env, run, until

from eve_mind import mind as mind_mod


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t, *a, **k):
        out.append(t)
        return await orig(t, *a, **k)
    e.mind.say = say
    return out


def walks(e):
    return [(a, json.loads(p)["pos"]) for a, s, p in e.mind.db.execute(
        "SELECT action_id, skill, params FROM actions ORDER BY rowid") if s == "walk_to"]


async def fight(e, phase, **detail):
    await e.body.send("event", {"kind": "reflex", "detail": {"reflex": "auto_fight", "phase": phase, **detail}})


START = {"trigger": {"entity_id": 7, "type": "minecraft:zombie", "distance": 5.2, "reason": "near",
                     "skill": "fight_melee"}}


def test_af_an_interrupted_walk_is_sent_again_after_the_fight(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.5
            await until(lambda: e.mind.fresh_state() is not None)
            e.body.result_override = {"walk_to": [("failed", "interrupted")]}
            await e.mind.owner_text("go to 9 64 9", "Hunter")
            await until(lambda: walks(e), timeout=3)
            aid = walks(e)[0][0]
            await fight(e, "start", action_paused=aid, **START)
            await until(lambda: "A zombie - dealing with it." in said, timeout=3)
            await until(lambda: e.mind.status_of(aid) == "failed", timeout=3)
            assert not any(t.startswith("I couldn't finish") for t in said), said   # held: it is resumed, not failed
            await fight(e, "end", result="clear", elapsed_ms=4000, kills=1, health_min=16, resumes=None)
            await until(lambda: len(walks(e)) == 2, timeout=5, what="the walk sent again")
            assert walks(e)[1][1] == {"x": 9.0, "y": 64.0, "z": 9.0}, walks(e)
            assert "Clear. Back to it." in said, said
    run(go())


def test_af_a_body_resumed_action_is_not_sent_twice(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 1.5
            await until(lambda: e.mind.fresh_state() is not None)
            await e.mind.owner_text("go to 9 64 9", "Hunter")
            await until(lambda: walks(e), timeout=3)
            aid = walks(e)[0][0]
            await fight(e, "start", action_paused=aid, **START)
            await fight(e, "end", result="ok", elapsed_ms=3000, kills=1, health_min=18, resumes=aid)
            await until(lambda: e.mind.status_of(aid) == "ok", timeout=5)
            await asyncio.sleep(1.0)
            assert len(walks(e)) == 1, walks(e)
            assert "Clear. Back to it." in said, said
    run(go())


def test_af_floor_says_so_and_backs_off(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.1
            await until(lambda: e.mind.fresh_state() is not None)
            await fight(e, "start", action_paused=None, **START)
            await fight(e, "end", result="floor", elapsed_ms=9000, kills=0, health_min=6, resumes=None)
            await until(lambda: "I'm hurt - backing off." in said, timeout=3)
            await until(lambda: e.mind.backing_off or any("back" in t.lower() for t in said[1:]), timeout=3)
    run(go())


def test_af_nothing_of_the_minds_own_starts_during_a_fight(tmp_path, monkeypatch):
    monkeypatch.setattr(mind_mod, "IDLE_S", 0.3)

    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.1
            await until(lambda: e.mind.fresh_state() is not None)
            await fight(e, "start", action_paused=None, **START)
            await until(lambda: e.mind.autofight is not None, timeout=3)
            e.body.time_of_day = 12500                                      # dusk: the night guard would start
            await asyncio.sleep(1.5)
            assert e.mind.night.task is None, "night run started mid-fight"
            await fight(e, "end", result="clear", elapsed_ms=3000, kills=1, health_min=20, resumes=None)
            await until(lambda: e.mind.night.task is not None, timeout=5, what="the night guard after the fight")
            e.mind.night.task.cancel()
            assert said.count("Clear.") == 1, said                          # one line each way
    run(go())
