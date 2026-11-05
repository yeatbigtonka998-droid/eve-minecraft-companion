"""S38 agenda-wire, agenda side: owner phrases, agenda from Hunter's words, replies, briefing lines."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agenda import adapter  # noqa: E402

ENV = json.loads((Path(__file__).resolve().parents[4] / "config" / "envelope.example.json").read_text("utf-8"))


@pytest.mark.parametrize("text,kind", [
    ("what did you do while I was gone?", "brief"), ("Eve, what have you been up to while I was away", "brief"),
    ("what did you do since I left", "brief"), ("brief me", "brief"), ("catch me up", "brief"),
    ("plan: keep the farm; then finish the hut", "plan"), ("Agenda: explore east", "plan"),
    ("what's on your agenda?", "show"), ("what is your plan", "show"), ("show me your agenda", "show"),
    ("what did you do yesterday at the farm", None), ("go to 3 64 3", None), ("plant a sapling", None),
])
def test_owner_phrase(text, kind):
    p = adapter.owner_phrase(text)
    assert (p or {}).get("kind") == kind, p


def test_plan_goals_split_and_kinds():
    p = adapter.owner_phrase("plan: keep the farm; then finish the hut then explore east")
    assert p["goals"] == ["keep the farm", "finish the hut", "explore east"]
    ag, why = adapter.agenda_from_goals(p["goals"], ENV)
    assert why is None and [i.kind for i in ag.items] == ["standing", "task", "explore"]
    assert adapter.agenda_line(ag) == "Agenda: 1) keep the farm (ongoing); 2) finish the hut; 3) explore east"
    again = adapter.agenda_load(adapter.agenda_json(ag), ENV)
    assert [i.goal for i in again.items] == [i.goal for i in ag.items]


@pytest.mark.parametrize("goals,frag", [
    (["finish the hut", "jump into the lava pool"], "#2"),          # the gate rules refuse it: never on the agenda
    (["attack the villager"], "#1"),
    (["mine the stone at 900 60 900"], "#1"),                        # beyond explore_radius (bounds rule)
    ([f"task {n}" for n in range(9)], "can't take"),                 # more than 8 items
])
def test_refused_goals_never_reach_the_agenda(goals, frag):
    ag, why = adapter.agenda_from_goals(goals, ENV)
    assert ag is None and frag in why


def test_show_reply_and_brief_lines():
    assert "agenda is empty" in adapter.show_reply(None, None)
    ag, _ = adapter.agenda_from_goals(["finish the hut"], ENV)
    r = adapter.show_reply(ag, {"task": "night_survival", "reason": "time_of_day 13500 is dusk/night"})
    assert r.startswith("Agenda: 1) finish the hut.") and "Next for me: night survival - time_of_day 13500" in r
    claims = [{"section": s, "text": f"{s} line"} for s in ("did", "memory", "deaths", "failed", "questions")] * 2
    lines = adapter.brief_lines(claims, max_lines=3)
    assert lines[:3] == ["deaths line", "deaths line", "failed line"] and lines[3].startswith("...and 7 more")
    assert adapter.brief_lines([]) == ["Nothing happened that I logged while you were away."]


@pytest.mark.parametrize("text,goals,later", [
    ("plan: mine 5 logs and then craft a crafting table", ["mine 5 logs", "craft a crafting table"], False),  # O127 45L
    ("plan: chop wood, then smelt iron; build a hut then rest", ["chop wood", "smelt iron", "build a hut", "rest"], False),
    ("plan: walk to 10, 64, 10 and then jump", ["walk to 10, 64, 10", "jump"], False),   # commas in coordinates stay
    ("plan for later: tidy the chest; sort the barrels", ["tidy the chest", "sort the barrels"], True),
    ("plan: water the crops while I'm away", ["water the crops"], True),
    ("agenda: 1) dig a pit 2) fill it", ["dig a pit", "fill it"], False),
])
def test_o127_split_and_later(text, goals, later):
    p = adapter.owner_phrase(text)
    assert p["goals"] == goals and p["later"] is later, p


def test_o127_later_phrase_before_the_colon():
    p = adapter.owner_phrase("plan when I'm gone: tidy the chest; then rest")
    assert p["goals"] == ["tidy the chest", "rest"] and p["later"] is True


VERB = __import__("re").compile(r"^(?:get|make|place|build|craft|smelt|mine|dig|fill)\b")    # a stub rule map


@pytest.mark.parametrize("text,goals", [
    ("plan: gather sand then make glass and place the glass", ["gather sand", "make glass", "place the glass"]),
    ("plan: get logs and planks then build a hut", ["get logs and planks", "build a hut"]),          # no verb: one
    ("plan: get logs and planks and make a table", ["get logs and planks", "make a table"]),         # greedy
    ("plan: dig a pit and fill it and place a torch", ["dig a pit", "fill it", "place a torch"]),
])
def test_o127_and_splits_only_between_orders(text, goals):
    assert adapter.owner_phrase(text, is_order=lambda s: bool(VERB.match(s)))["goals"] == goals
    assert all(" and " not in g for g in adapter.owner_phrase(text, is_order=lambda s: True)["goals"])
    no_map = adapter.owner_phrase(text)["goals"]                    # without the rule map " and " never splits
    assert sum(" and " in g for g in no_map) >= 1


def test_o146_a_livestock_plan_is_accepted():
    ag, why = adapter.agenda_from_goals(["kill 3 sheep", "come to me"], ENV)
    assert why is None and [i.goal for i in ag.items] == ["kill 3 sheep", "come to me"]
    ag, why = adapter.agenda_from_goals(["kill 3 villagers"], ENV)
    assert ag is None and "#1" in why
