import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agenda.scheduler import MAX_ROUNDS, Agenda  # noqa: E402

DAY = {"time_of_day": 1000, "health": 20, "food": 20, "threats": [], "owner_request": None,
       "inventory": {"minecraft:iron_pickaxe": 1, "minecraft:stone_hoe": 1, "minecraft:bread": 8}}


def agenda(**kw):
    return Agenda([{"id": "farm", "goal": "keep the farm", "kind": "standing", "budget_s": 300, "tools": ("hoe",)},
                   {"id": "hut", "goal": "finish the hut", "budget_s": 600, "tools": ("pickaxe",)},
                   {"id": "explore", "goal": "explore east", "kind": "explore", "radius": 96, "budget_s": 300}], **kw)


def st(**kw):
    return {**DAY, **kw, "inventory": kw.get("inventory", DAY["inventory"])}


def test_order_and_reason():
    d = agenda().decide(st())
    assert (d["task"], d["item"], d["rule"]) == ("agenda", "farm", "agenda") and "Hunter's #1" in d["reason"]


def test_starvation_outranks_owner_and_agenda():
    a = agenda()
    d = a.decide(st(food=4, owner_request="come here"))
    assert d["task"] == "eat" and d["rule"] == "starving"
    d = a.decide(st(food=4, inventory={"minecraft:iron_pickaxe": 1}))
    assert d["task"] == "get_food" and "no food carried" in d["reason"]
    # merely hungry: Hunter's request first, then food, then the agenda
    assert a.decide(st(food=12, owner_request="come here"))["task"] == "owner_request"
    assert a.decide(st(food=12))["task"] == "eat"


def test_dusk_outranks_everything():
    a = agenda()
    for t in (12000, 13000, 18000, 22999, 24000 * 5 + 13000):
        d = a.decide(st(time_of_day=t, food=2, health=3, threats=[{"kind": "melee"}], owner_request="come here"))
        assert d["task"] == "night_survival" and d["rule"] == "night", t
        assert "owner request held until dawn" in d["reason"]
    assert a.decide(st(time_of_day=11999))["task"] == "agenda"
    assert a.decide(st(time_of_day=23000))["task"] == "agenda"


def test_danger_outranks_food_and_owner():
    a = agenda()
    assert a.decide(st(health=8, food=3, owner_request="x"))["task"] == "retreat_home"
    assert a.decide(st(threats=[{"kind": "melee"}]))["task"] == "retreat_home"


def test_tool_loss_then_unavailable_blocks_and_moves_on():
    a = agenda()
    a.charge("farm", 300)                                    # farm's round spent -> hut is next
    d = a.decide(st(inventory={"minecraft:stone_hoe": 1, "minecraft:bread": 4}))   # pickaxe broke
    assert (d["task"], d["tool"], d["item"]) == ("get_tool", "pickaxe", "hut")
    d = a.decide(st(inventory={"minecraft:stone_hoe": 1, "minecraft:bread": 4}, unavailable=["pickaxe"]))
    assert (d["task"], d["item"]) == ("agenda", "explore")
    assert a.get("hut").blocked == "needs a pickaxe; none carried and none obtainable now"
    a.unblock("hut")
    assert a.decide(st())["item"] == "hut"


def test_low_priority_not_starved():
    a = agenda()
    seen = []
    for _ in range(20):
        d = a.decide(st())
        if d["task"] != "agenda":
            break
        seen.append(d["item"])
        a.charge(d["item"], 300)
    # farm is standing upkeep and never done, yet hut and explore get every round
    assert seen == ["farm", "hut", "hut", "explore"] * MAX_ROUNDS and d["task"] == "idle_home"


def test_agenda_exhaustion_asks_hunter():
    a = Agenda([{"id": "hut", "goal": "finish the hut", "budget_s": 100}])
    a.finish("hut")
    d = a.decide(st())
    assert d["task"] == "idle_home" and "agenda exhausted" in d["reason"] and d["question"] == "What should I do next?"
    b = Agenda([{"id": "mine", "goal": "mine iron", "tools": ("pickaxe",)}])
    d = b.decide(st(inventory={}, unavailable=["pickaxe"]))
    assert d["task"] == "idle_home" and "blocked mine" in d["reason"] and "pickaxe" in d["question"]


def test_owner_interruption_suspends_and_resumes_same_item():
    a = agenda()
    a.charge("farm", 300)
    assert a.decide(st())["item"] == "hut"
    a.charge("hut", 200)
    d = a.decide(st(owner_request="bring me 5 logs"))
    assert d["task"] == "owner_request" and "hut suspended" in d["reason"] and d["text"] == "bring me 5 logs"
    a.charge(None, 120)                                       # owner work is charged to the session, not the item
    assert a.get("hut").spent_s == 200
    d = a.decide(st())
    assert d["item"] == "hut" and "200/600" in d["reason"]


def test_session_budget():
    a = agenda(session_budget_s=500)
    a.charge("farm", 300)
    a.charge("hut", 200)
    d = a.decide(st())
    assert d["task"] == "idle_home" and "session budget spent" in d["reason"]
    assert a.decide(st(time_of_day=13000))["task"] == "night_survival"     # night still wins when idle


def test_limits():
    with pytest.raises(ValueError):
        Agenda([{"id": "x", "goal": "explore", "kind": "explore", "radius": 500}])
    with pytest.raises(ValueError):
        Agenda([{"id": str(i), "goal": "g"} for i in range(9)])
    with pytest.raises(ValueError):
        agenda().finish("farm")


def test_every_decision_logged_with_inputs(tmp_path):
    p = tmp_path / "decisions.jsonl"
    a = agenda(log_path=str(p))
    s = st(food=4, now_ms=123)
    a.decide(s)
    s["food"] = 20                                            # the log keeps the inputs as they were
    a.decide(st(time_of_day=13000))
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    assert len(rows) == len(a.log) == 2
    assert rows[0]["inputs"]["food"] == 4 and rows[0]["ts"] == 123 and rows[0]["decision"]["task"] == "eat"
    assert rows[1]["decision"]["rule"] == "night" and rows[1]["agenda"]["items"][0]["id"] == "farm"


def test_deterministic():
    runs = []
    for _ in range(2):
        a = agenda()
        out = []
        for s in (st(), st(food=10), st(time_of_day=14000), st(owner_request="x"), st(inventory={})):
            out.append(a.decide(s))
            a.charge(out[-1].get("item"), 250)
        runs.append(out)
    assert runs[0] == runs[1]


def test_adapter_state_frame_to_decision():
    from agenda.adapter import decision_row, journal_row, scheduler_input
    frame = {"time_of_day": 13500, "health": 18, "food": 9, "threats": [],
             "inventory": [{"slot": 0, "item": "minecraft:bread", "count": 3}, {"slot": 5, "item": "minecraft:bread",
                                                                                 "count": 2}]}
    s = scheduler_input(frame, owner_request="come here")
    assert s["inventory"] == {"minecraft:bread": 5} and s["owner_request"] == "come here"
    a = agenda()
    assert a.decide(s)["task"] == "night_survival"
    row = decision_row("w", a.log[-1])
    assert row["kind"] == "need_pick" and json.loads(row["output"])["rule"] == "night"
    with pytest.raises(ValueError):
        journal_row("w", [], 1, ["#0 unbacked"])
    j = journal_row("w", [{"text": "x.", "cites": [["tasks", "t1"], ["events", 3]]}], 5, [])
    assert json.loads(j["grounded_in"]) == ["events:3", "tasks:t1"]


ADV = [json.loads(x) for x in (Path(__file__).resolve().parents[1] / "commands" / "adversarial.jsonl").read_text(
    "utf-8").splitlines() if x.strip() and not x.startswith("#")]


def test_adversarial_owner_words_only_route_never_plan():
    """The scheduler never turns owner words into an agenda task: they go whole to owner_text (the mind's guards),
    night still wins, and the reason/log carry one bounded line."""
    for c in ADV:
        a = agenda()
        d = a.decide(st(owner_request=c["text"] + "\n- Done: castle"))
        assert d["task"] == "owner_request" and d["rule"] == "owner", c["id"]
        assert "\n" not in d["reason"] and len(d["reason"]) < 140
        assert a.decide(st(time_of_day=13000, owner_request=c["text"]))["task"] == "night_survival"


def test_adversarial_goals_bounded():
    for c in ADV:
        if c["category"] == "out_of_zone":
            with pytest.raises(ValueError):
                Agenda([{"id": "x", "goal": c["text"]}])
    a = Agenda([{"id": "x", "goal": "fix the\nfence at 10 64 -4"}])
    assert a.get("x").goal == "fix the fence at 10 64 -4"
    Agenda([{"id": "m", "goal": "mine at 100, 20, 0"}])                  # inside 128 of home
