"""CP-19 (mind side) — bounded skill contracts: success needs observed evidence, failures stop, outcomes are explicit."""
import asyncio
import json

from jsonschema import Draft202012Validator

from conftest import count, mind_env, run, until
from eve_mind.config import MODES
from eve_mind.registry import EFFECTS


def test_registry_entries_are_complete_contracts(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            for name, s in e.mind.registry.skills.items():
                assert s["params"]["type"] == "object" and s["params"]["additionalProperties"] is False, name
                Draft202012Validator.check_schema(s["params"])
                assert set(s["effects"]) <= EFFECTS and isinstance(s["preconditions"], list), name
                assert 0 < s["timeout_ms"] <= 3_600_000 and isinstance(s["cancellable"], bool), name   # smelt: 15 min
                assert s["evidence"] and isinstance(s["failure_codes"], list) and s["min_mode"] in MODES, name
    run(go())


def test_ok_without_evidence_is_not_success(tmp_path):
    """Body claims ok but sends no evidence: stored uncertain; the task is done only because the mind observed
    the postcondition itself (walk_to), and blocked where it cannot observe one (chat_say)."""
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.missing_evidence = True
            r = await e.mind.do("walk_to", {"pos": {"x": 3, "y": 64, "z": 3}}, wait=3)
            a = e.mind.action(r["action_id"])
            assert a["status"] == "uncertain" and a["code"].startswith("missing_evidence:final_pos")
            await until(lambda: e.mind.action(r["action_id"])["reconciled"] == 1)
            assert "postcondition holds in observed state" in e.mind.action(r["action_id"])["reconcile_note"]
            assert count(e.db, "SELECT status FROM tasks WHERE task_id=?", r["task_id"]) == "done"
            r2 = await e.mind.do("chat_say", {"text": "hi"}, wait=3)
            assert e.mind.action(r2["action_id"])["status"] == "uncertain"
            assert count(e.db, "SELECT status FROM tasks WHERE task_id=?", r2["task_id"]) == "blocked"
    run(go())


def test_failed_precondition_stops_without_retry(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.reject_code = "precondition_failed"
            r = await e.mind.do("mine_block", {"pos": {"x": 2, "y": 63, "z": 2}}, wait=3)
            assert r["status"] == "rejected" and r["code"] == "precondition_failed"
            assert count(e.db, "SELECT status FROM tasks WHERE task_id=?", r["task_id"]) == "failed"
            await asyncio.sleep(1.5)                       # past the resend window: still one request
            assert e.body.requests[r["action_id"]] == 1
    run(go())


def test_no_result_by_deadline_is_explicitly_uncertain(tmp_path):
    async def go():
        async with mind_env(tmp_path, result_grace_s=0.3) as e:
            e.body.drop_result = True
            r = await e.mind.do("select_hotbar", {"slot": 4}, wait=3)      # timeout_ms 1000
            await until(lambda: e.mind.action(r["action_id"])["reconciled"] == 1)
            a = e.mind.action(r["action_id"])
            assert a["status"] == "uncertain" and a["code"] == "no_result" and a["reconciled"] == 1
            assert "hotbar_slot=4" in a["reconcile_note"]                    # observed, so the task is done
            r = await e.mind.do("chat_say", {"text": "x"}, wait=6)            # timeout_ms 3000, nothing observable
            await until(lambda: e.mind.action(r["action_id"])["reconciled"] == 1)
            assert e.mind.action(r["action_id"])["code"] == "no_result"
            assert count(e.db, "SELECT status FROM tasks WHERE task_id=?", r["task_id"]) == "blocked"
    run(go())


def test_result_note_is_kept_and_shown(tmp_path):
    """link v1.1: action.result.note (the body's cause) is stored with the action and shown by do/tasks."""
    why = "crosshair on minecraft:slime_block at 5, 151, 0"

    async def go():
        async with mind_env(tmp_path) as e:
            e.body.result_override, e.body.result_note = ("failed", "obstructed"), why
            r = await e.mind.cli("do", {"skill": "mine_block", "params": {"pos": {"x": 5, "y": 63, "z": 0}}, "wait": 3})
            assert r["status"] == "failed" and r["code"] == "obstructed" and r["note"] == why
            t = await e.mind.cli("tasks", {})
            assert t["actions"][0]["note"] == why
            e.body.result_override, e.body.result_note = None, None
            r = await e.mind.cli("do", {"skill": "jump", "params": {}, "wait": 3})
            assert r["status"] == "ok" and r["note"] is None
            assert "note" not in json.loads(e.mind.action(r["action_id"])["evidence"])
    run(go())


def test_failure_codes_are_a_closed_list(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.result_override = ("failed", "unreachable")
            r = await e.mind.do("walk_to", {"pos": {"x": 3, "y": 64, "z": 0}}, wait=3)
            assert r["status"] == "failed" and r["code"] == "unreachable"
            e.body.result_override = ("failed", "banana")
            r = await e.mind.do("walk_to", {"pos": {"x": 4, "y": 64, "z": 0}}, wait=3)
            assert r["code"] == "unlisted:banana" and e.mind.metrics["unlisted_failure_codes"] == 1
            assert json.loads(e.mind.action(r["action_id"])["evidence"])["final_pos"]
    run(go())
