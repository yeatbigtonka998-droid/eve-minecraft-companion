"""O142 (45L: come-to-me / go-to walks ended unreachable, "no qualified route"): his walk that fails unreachable or
stuck is sent once more with allow_break/allow_place, said once; a second failure is said as usual. O143 (45L: "mine 8
stone" ended partial, max_blocks 20 with 5/8): a rule-mapped mine_resource carries max_blocks = max(64, 4 x count)."""
import json

from conftest import mind_env, run, until


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t, *a, **k):
        out.append(t)
        return await orig(t, *a, **k)
    e.mind.say = say
    return out


def actions(e):
    return [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]


def test_o142_an_unreachable_walk_of_his_digs_and_builds_once(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 0.05
            await until(lambda: e.mind.fresh_state() is not None)
            e.body.result_override = {"walk_to": [("failed", "unreachable")]}
            await e.mind.owner_text("go to 5 64 5", "Hunter")
            await until(lambda: len(actions(e)) == 2 and e.mind.open_action() is None, timeout=5, what="the retry")
            assert actions(e)[1] == ("walk_to", {"pos": {"x": 5.0, "y": 64.0, "z": 5.0}, "allow_break": True,
                                                 "allow_place": True, "max_blocks_broken": 32}), actions(e)
            assert "No plain path; digging and building my way there." in said
            assert not any(t.startswith("I couldn't finish walk to") for t in said), said
            e.body.result_override = {"walk_to": [("failed", "unreachable"), ("failed", "unreachable")]}
            await e.mind.owner_text("go to 6 64 6", "Hunter")
            await until(lambda: any(t.startswith("I couldn't finish walk to") for t in said), timeout=5)
            assert len(actions(e)) == 4, actions(e)                           # one retry, then it is said
    run(go())


def test_o143_mine_resource_gets_a_dig_budget_for_its_count(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 0.05
            await until(lambda: e.mind.fresh_state() is not None)
            await e.mind.owner_text("mine 8 stone", "Hunter")
            await until(lambda: actions(e) and e.mind.open_action() is None)
            assert actions(e)[-1][1]["max_blocks"] == 64, actions(e)
            await e.mind.owner_text("mine 40 stone", "Hunter")
            await until(lambda: len(actions(e)) == 2 and e.mind.open_action() is None)
            assert actions(e)[-1][1]["max_blocks"] == 160, actions(e)
    run(go())
