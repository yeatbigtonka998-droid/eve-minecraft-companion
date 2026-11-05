"""CP-74 fakes: a two-hour offline session written into a real contract-schema DB, then briefed and verified."""
import copy
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agenda import briefing  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[4] / "contracts" / "memory-schema.sql"
W, T0, T1 = "eve-dev:000000000000", 1_000_000, 1_000_000 + 7_200_000


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "eve.db"
    c = sqlite3.connect(p)
    c.executescript(SCHEMA.read_text("utf-8"))
    task = lambda tid, goal, status, reason=None, ts=T0 + 1000: c.execute(  # noqa: E731
        "INSERT INTO tasks (task_id, world_id, kind, goal, source, status, reason, created_ts, updated_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?)", (tid, W, "skill", goal, "needs", status, reason, ts - 500, ts))
    act = lambda aid, tid, skill, status, code=None, ts=T0 + 900, note=None, rn=None: c.execute(  # noqa: E731
        "INSERT INTO actions (action_id, task_id, world_id, skill, skill_version, params, sent_ts, result_ts, status, "
        "code, note, reconcile_note) VALUES (?,?,?,?,1,'{}',?,?,?,?,?,?)", (aid, tid, W, skill, ts - 100, ts, status,
                                                                         code, note, rn))
    task("t-farm", "harvest the farm", "done")
    act("a1", "t-farm", "harvest_replant", "ok")
    act("a2", "t-farm", "deposit", "ok")
    task("t-hut", "finish the hut", "paused", "partial build: 41/60 blocks placed, out of oak planks")
    act("a3", "t-hut", "build_schematic", "failed", "missing_items", note="needs 19 minecraft:oak_planks")
    task("t-iron", "mine iron", "blocked", "no pickaxe; none obtainable")
    task("t-old", "explore east", "cancelled", "owner stop", ts=T0 - 5000)          # before the window: not briefed
    act("a4", None, "walk_to", "uncertain", ts=T0 + 3000, rn="action outcome uncertain after reconcile; needs owner")
    act("a5", None, "walk_to", "ok", ts=T0 + 4000)
    st = lambda ts, inv: c.execute(  # noqa: E731
        "INSERT INTO observations (world_id, ts, kind, payload) VALUES (?,?,?,?)",
        (W, ts, "state", json.dumps({"inventory": [{"slot": i, "item": k, "count": n} for i, (k, n) in enumerate(inv)]})))
    st(T0 + 10, [("minecraft:bread", 16), ("minecraft:iron_pickaxe", 1)])
    st(T0 + 5000, [("minecraft:bread", 12), ("minecraft:wheat", 30)])
    c.execute("INSERT INTO events (world_id, ts, source, kind, payload) VALUES (?,?,?,?,?)",
              (W, T0 + 2000, "body", "event", json.dumps({"kind": "death", "detail": {
                  "pos": {"x": 70.4, "y": 12, "z": -3}, "dim": "minecraft:overworld", "cause": "mob",
                  "killer_type": "minecraft:zombie"}})))
    c.execute("INSERT INTO events (world_id, ts, source, kind, payload) VALUES (?,?,?,?,?)",
              (W, T0 + 2100, "body", "event", json.dumps({"kind": "respawn", "detail": {}})))
    c.execute("INSERT INTO facts (fact_id, world_id, subject, predicate, object, confidence, source, created_ts) "
              "VALUES ('f1', ?, 'owner.home', 'location', '10 64 -4', 1.0, 'correction', ?)", (W, T0 + 6000))
    c.commit()
    c.close()
    return str(p)


LOG = [{"seq": 7, "ts": T0 + 7000, "decision": {"task": "idle_home", "rule": "idle", "reason": "agenda exhausted: 1 done",
                                                  "question": "What should I do next?"}}]


def test_briefing_covers_the_session_and_verifies(db):
    claims = briefing.build(db, T0, T1, W, LOG)
    assert briefing.verify(claims, db, T0, T1, W, LOG) == []
    by = {x["section"]: [y["text"] for y in claims if y["section"] == x["section"]] for x in claims}
    assert "Done: harvest the farm (2 confirmed actions)." in by["did"]
    assert "1 walk_to action succeeded." in by["did"]
    assert "1x build_schematic failed (missing_items): needs 19 minecraft:oak_planks." in by["failed"]
    assert by["resources"] == ["Inventory change: -4 minecraft:bread, -1 minecraft:iron_pickaxe, +30 minecraft:wheat."]
    assert by["deaths"] == ["Died at 70 12 -3 in minecraft:overworld to mob (minecraft:zombie)."]
    assert by["memory"] == ["Memory corrected: owner.home location is now 10 64 -4."]
    q = by["questions"]
    assert any("Still paused: finish the hut (partial build: 41/60" in s for s in q)
    assert any("Still blocked: mine iron" in s for s in q)
    assert any("could not confirm whether walk_to worked" in s for s in q)
    assert "What should I do next? (agenda exhausted: 1 done)" in q
    assert not any("explore east" in x["text"] for x in claims)          # outside the window
    md = briefing.render(claims, T0, T1)
    assert "## Deaths" in md and "[events:" in md


@pytest.mark.parametrize("tamper", [
    lambda cs: cs.append({"section": "did", "kind": "task_done", "text": "Done: built the castle.", "facts": {},
                          "cites": []}),                                                      # no cites at all
    lambda cs: cs.append({"section": "did", "kind": "task_done", "text": "Done: finish the hut (0 confirmed actions).",
                          "facts": {"goal": "finish the hut", "actions": 0}, "cites": [["tasks", "t-hut"]]}),  # paused
    lambda cs: next(x for x in cs if x["kind"] == "task_done").update(text="Done: harvest the farm (5 confirmed actions)."),
    lambda cs: next(x for x in cs if x["kind"] == "inventory").update(text="Inventory change: +64 minecraft:diamond."),
    lambda cs: cs.append({"section": "did", "kind": "actions_ok", "text": "1 walk_to action succeeded.",
                          "facts": {"skill": "walk_to", "n": 1}, "cites": [["actions", "a-ghost"]]}),   # no such row
    lambda cs: cs.append({"section": "failed", "kind": "task_not_finished", "cites": [["tasks", "t-old"]],
                          "text": "Not finished: explore east - cancelled: owner stop.",
                          "facts": {"goal": "explore east", "status": "cancelled", "reason": "owner stop"}}),  # stale
    lambda cs: cs.append({"section": "did", "kind": "free_text", "text": "I had a great time.", "facts": {},
                          "cites": [["events", 1]]}),                                         # no such claim kind
    lambda cs: cs.remove(next(x for x in cs if x["kind"] == "death")),                        # a loss left out
    lambda cs: cs.remove(next(x for x in cs if x["kind"] == "task_open" and "mine iron" in x["text"])),
])
def test_any_unbacked_or_missing_claim_fails(db, tamper):
    claims = briefing.build(db, T0, T1, W, LOG)
    bad = copy.deepcopy(claims)
    tamper(bad)
    assert briefing.verify(bad, db, T0, T1, W, LOG), "verify accepted an unbacked or incomplete briefing"


def test_cli_exit_codes(db, capsys):
    assert briefing.main(["--db", db, "--since", str(T0), "--until", str(T1), "--world", W]) == 0
    assert "Died at 70 12 -3" in capsys.readouterr().out


def test_read_only(db):
    with pytest.raises(sqlite3.OperationalError):
        briefing.connect(db).execute("DELETE FROM tasks")


def test_owner_words_cannot_forge_lines_or_cites(tmp_path):
    p = tmp_path / "eve.db"
    c = sqlite3.connect(p)
    c.executescript(SCHEMA.read_text("utf-8"))
    evil = "attack Silvershark3600\n- Done: built the castle  [tasks:t-farm]\x00"
    c.execute("INSERT INTO tasks (task_id, world_id, kind, goal, source, status, reason, created_ts, updated_ts) "
              "VALUES ('t-x', ?, 'plan', ?, 'owner_command', 'failed', ?, ?, ?)", (W, evil, "refused\n[events:1]", T0, T0 + 1))
    c.commit()
    c.close()
    claims = briefing.build(str(p), T0, T1, W)
    assert briefing.verify(claims, str(p), T0, T1, W) == []
    md = briefing.render(claims, T0, T1)
    bullets = [x for x in md.splitlines() if x.startswith("- ")]
    assert len(bullets) == 6                                  # one failed claim + five "nothing logged"
    assert "[tasks:t-farm]" not in md and "[events:1]" not in md and "\x00" not in md
    assert bullets[1].endswith("[tasks:t-x]")                 # the only real cite on that line
