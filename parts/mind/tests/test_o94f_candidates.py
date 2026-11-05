"""O94f: eve-candidates/1 rows - the writer refuses safety rows, strips outcome fields, caps options and hashes the
state it was given; the runners' rows are checked in test_cp40 (route_choice, retool) and test_cp51 (supply_source)."""
import json
from types import SimpleNamespace

import pytest

from eve_mind.candidates import MAX_OPTIONS, Candidates, state_hash


def writer(tmp_path):
    mind = SimpleNamespace(cfg=SimpleNamespace(db_path=tmp_path / "eve.db"), metrics={})
    return Candidates(mind), mind


def opt(i, score=1.0, **k):
    return {"id": f"o{i}", "action": {"skill": "walk_to", "params": {}}, "score": score, "score_kind": "rule_rank",
            "feasible": True, **k}


def test_o94f_rows_follow_the_contract_and_never_carry_safety_or_outcome(tmp_path):
    c, mind = writer(tmp_path)
    with pytest.raises(ValueError):
        c.row("night_survival", "n1", "seal", "route_choice", {}, [opt(1)], "o1", safety=True)   # never from safety
    with pytest.raises(ValueError):
        c.row("iron_kit", "k1", "mine", "route_choice", {}, [opt(1)], "o2")                       # pick not an option
    with pytest.raises(ValueError):
        c.row("iron_kit", "k1", "mine", "dig_out", {}, [opt(1)], "o1")                            # unknown type
    many = [opt(i, score=i) for i in range(20)] + [opt(99, score=None, feasible=False, rejected_by="zone_denied")]
    many[3]["status"] = "ok"                                                 # an outcome slipped into an option
    row = c.row("iron_kit", "k1", "mine_raw_iron", "route_choice", {"pos": [1, 2, 3]}, many, "o19", tick=5)
    assert len(row["options"]) == MAX_OPTIONS + 1 and row["options"][0]["id"] == "o19", row["options"]   # top + rejected
    assert row["options"][-1] == {"id": "o99", "feasible": False, "rejected_by": "zone_denied"}
    assert not any("status" in o for o in row["options"])
    assert row["state_hash"] == state_hash({"pos": [1, 2, 3]}) == state_hash({"pos": [1, 2, 3]})
    low = c.row("iron_kit", "k1", "retool", "retool", {}, [opt(i, score=i) for i in range(20)], "o0")   # eve-learning:
    assert "o0" in [o["id"] for o in low["options"]] and len(low["options"]) == MAX_OPTIONS + 1, low["options"]  # kept
    assert row["schema"] == "eve-candidates/1" and row["safety"] is False and row["rule"].startswith("iron_kit@")
    c.write(row, "a1")
    c.write({**row, "status": "ok"}, "a2")                                   # an outcome field: refused, counted
    lines = [json.loads(x) for x in c.path.read_text("utf-8").splitlines()]
    assert [x["action_id"] for x in lines] == ["a1"] and mind.metrics["candidate_write_errors"] == 1
    assert c.path == tmp_path / "candidates.jsonl"
