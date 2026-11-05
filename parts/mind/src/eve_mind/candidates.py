"""O94f: eve-candidates/1 rows (parts/learning/CONTRACTS.md, 92b13d9c) - one per non-safety runner decision, with every
option the rule considered, its score, the pick, the state it read and the dispatched action's id. Never from a
safety path (stop, cancel, night seal, shelter, death avoidance, retreat, dig-out): the writer refuses safety=True.
No outcome field is ever written - outcomes join later by action_id.

Rows go to `<db dir>/candidates.jsonl` (EVE_CANDIDATES overrides); a lane harness copies its objective_ids' rows into
its run folder at run end (the contract's "mind journal exported there")."""
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

SCHEMA = "eve-candidates/1"
TYPES = ("task_choice", "tool_choice", "retool", "route_choice", "stand_choice", "go_home_when", "supply_source", "revise")
OUTCOME = ("status", "code", "result", "success", "outcome")
MAX_OPTIONS = 16


def state_hash(state):
    return "sha256:" + hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":"),
                                                 default=str).encode()).hexdigest()


class Candidates:
    def __init__(self, mind):
        self.mind = mind

    @property
    def path(self):
        return Path(os.environ.get("EVE_CANDIDATES") or Path(self.mind.cfg.db_path).parent / "candidates.jsonl")

    def row(self, runner, objective_id, phase, decision_type, state, options, pick, tick=None, safety=False):
        """A decision row, not yet written (the action it leads to is dispatched next: write() adds its id)."""
        if safety:
            raise ValueError("eve-candidates/1: safety paths never write rows")
        if decision_type not in TYPES or pick not in [o["id"] for o in options]:
            raise ValueError(f"eve-candidates/1: bad decision_type {decision_type!r} or pick {pick!r}")
        from . import mind as M                                          # (import here: mind imports the runners)
        opts = sorted(options, key=lambda o: (o.get("score") is None, -(o.get("score") or 0)))
        if len(opts) > MAX_OPTIONS:                          # the top by score, then every rejected one's id + reason
            keep = opts[:MAX_OPTIONS]
            keep += [o for o in opts[MAX_OPTIONS:] if o["id"] == pick]   # the pick always stays in its own row
            keep += [{"id": o["id"], "feasible": False, "rejected_by": o.get("rejected_by")} for o in opts[MAX_OPTIONS:]
                     if o.get("feasible") is False and o["id"] != pick]   # (eve-learning: a 17th-ranked pick fell out)
            opts = keep
        return {"schema": SCHEMA, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tick": tick,
                "objective_id": objective_id, "runner": runner, "rule": f"{runner}@{M.MIND_COMMIT or 'unknown'}",
                "phase": phase, "decision_type": decision_type, "decision_id": str(uuid.uuid4()),
                "state_hash": state_hash(state), "state": state,
                "options": [{k: v for k, v in o.items() if k not in OUTCOME} for o in opts], "pick": pick,
                "action_id": None, "safety": False}

    def write(self, row, action_id=None):
        """Append the row with the dispatched action's id (None when the pick dispatches nothing). Never raises into a
        runner: a learning row is not worth a stopped objective."""
        try:
            if row.get("safety") is not False or any(k in row for k in OUTCOME):
                raise ValueError("eve-candidates/1: safety or outcome field in a row")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps({**row, "action_id": action_id}, separators=(",", ":")) + "\n")
        except (OSError, ValueError, TypeError):
            self.mind.metrics["candidate_write_errors"] = self.mind.metrics.get("candidate_write_errors", 0) + 1
