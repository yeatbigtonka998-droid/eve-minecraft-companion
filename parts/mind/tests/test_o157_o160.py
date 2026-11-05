"""Session 4 (S87): O157 a "stop" inside a multi-sentence order ends nothing; O158 where to find wood is not what it
is; O160 "did you get all 8?" is one sentence - got of asked, then the reason. Owner-path tests on the exact lines."""
import asyncio
import json

import pytest

from conftest import mind_env, run, until
from eve_mind.owner_phrases import order_steps
from eve_mind.planner import Planner

LONG = ("collect 8 wood logs from nearby trees. Break the log blocks and pick up the dropped wood. Then stop and tell "
        "me what happened. If you cannot do it, explain what stopped you.")


@pytest.fixture(autouse=True)
def no_planner(monkeypatch):
    async def refused(self, *a, **k):
        return {"ok": False, "error": "planner_unavailable", "detail": "test stub", "model": "stub",
                "prompt_version": "stub"}
    monkeypatch.setattr(Planner, "plan", refused)


async def hear(e, text):
    q = len(e.body.received)
    await e.sense.send("owner.chat", {"sender": "Hunter", "trusted": True, "text": text})
    return (await e.body.wait_for(lambda m: m["type"] == "chat.send", start=q))["payload"]["text"]


def skills(e):
    return [(r[0], json.loads(r[1])) for r in e.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]


# ---- O157

def test_o157_steps_of_a_multi_sentence_order():
    assert order_steps(LONG) == ["collect 8 wood logs from nearby trees"]
    assert order_steps("stop") is None and order_steps("Eve stop") is None
    assert order_steps("mine 5 coal. craft a torch.") == ["mine 5 coal", "craft a torch"]
    assert order_steps("I moved the chest. It's at 1 2 3.") is None           # statements: not steps


def test_o157_the_long_line_gathers_and_never_stops(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            assert await hear(e, LONG) != "Stopped."
            await until(lambda: any(s == "gather" for s, _ in skills(e)), what="gather")
            assert any(sk == "gather" and (p["product"], p["count"]) == ("wood", 8) for sk, p in skills(e)), skills(e)
            assert not any(s == "stop" for s, _ in skills(e)) and not e.mind.stopped
    run(go())


@pytest.mark.parametrize("line,stops", [("stop", True), ("Eve stop", True), ("don't stop until you have 8", False)])
def test_o157_stop_is_a_line(tmp_path, line, stops):
    async def go():
        async with mind_env(tmp_path) as e:
            await hear(e, line)
            await asyncio.sleep(0.3)
            assert any(s == "stop" for s, _ in skills(e)) is stops, skills(e)
    run(go())


# ---- O158

@pytest.mark.parametrize("line,n", [("collect 8 wood logs from nearby trees", 8), ("Collect 8 logs from nearby trees", 8),
                                    ("get me some wood", 8), ("gather 10 logs around here", 10)])
def test_o158_where_is_not_what(tmp_path, line, n):
    async def go():
        async with mind_env(tmp_path) as e:
            reply = await hear(e, line)
            assert "Which block" not in reply and "which one" not in reply, reply
            await until(lambda: skills(e), what="a dispatch")
            sk, p = skills(e)[-1]                         # O177: a place in his words may add a radius
            assert sk == "gather" and (p["product"], p["count"]) == ("wood", n), skills(e)
    run(go())


# ---- O160

def test_o160_did_you_get_all_8_after_a_refusal(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.result_override = {"gather": ("failed", "zone_denied")}
            e.body.evidence_extra = {"gather": [{"gathered": 1}]}
            await hear(e, "collect 8 logs")
            await until(lambda: e.db.execute("SELECT status FROM actions WHERE skill='gather'").fetchone()[0] == "failed",
                        what="the failed gather")
            await asyncio.sleep(0.2)
            assert await hear(e, "did you get all 8?") == "No, 1 of 8. I'm not allowed to do that there."
    run(go())


def test_o160_a_running_task_is_the_one_reported(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.result_override = {"gather": ("failed", "zone_denied")}
            await hear(e, "collect 8 logs")
            await until(lambda: e.db.execute("SELECT status FROM actions WHERE skill='gather'").fetchone()[0] == "failed",
                        what="the first, failed gather")
            e.body.result_override, e.body.walk_s = None, 3.0
            await hear(e, "collect 8 logs")
            await until(lambda: e.mind.open_action() is not None, what="the second gather running")
            assert await hear(e, "did you get all 8?") == "Not yet - I'm still on the gather."
            aid = e.mind.open_action()["action_id"]
            await until(lambda: e.mind.progress_note.get(aid), what="the body's first progress note")   # no race
            e.mind.progress_note[aid] = "3/8"
            assert await hear(e, "how many so far?") == "Not yet - 3 of 8 so far."
    run(go())


def test_o160_done(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.evidence_extra = {"gather": [{"gathered": 8}]}
            await hear(e, "collect 8 logs")
            await until(lambda: e.db.execute("SELECT status FROM actions WHERE skill='gather'").fetchone()[0] == "ok",
                        what="the gather")
            assert await hear(e, "did you get all 8?") == "Yes, all of them."
    run(go())
