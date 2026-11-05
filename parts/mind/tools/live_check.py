r"""Mind-side verdict for a live run (CP-19..24 on the 45L): reads eve.db READ-ONLY and checks invariants that must hold
whatever the fixture did. Foundation runs its fixture, then:
    C:\eve\mind\parts\mind\.venv\Scripts\python.exe C:\eve\mind\parts\mind\tools\live_check.py --since "2026-10-19 18:29" \
        [--until "..."] [--db C:\eve\mind\eve.db] [--out <evidence dir>]
Exit 0 = no violation. The server-side truth (blocks, health, inventory) stays foundation's RCON judgement.

Invariants (each is a violation when broken):
  single_flight      at most one non-`stop` action in flight at any time (CP-20)
  stopped_quiet      no non-`stop` action sent while the mind was STOPPED (CP-12/CP-20)
  ok_has_evidence    every `ok` action carries all its registry evidence keys (CP-19)
  uncertain_settled  every `uncertain` action is reconciled with a note, or timed out as no_result (CP-21)
  retry_bounded      the same skill+params fails at most retry_limit (3) times in any 10 minutes (CP-22)
  grants_revoked     every task.grant of an ended action was revoked (link v1.1)
  task_consistent    no task is `running` without an open action at the end of the window
Reported, not judged: open actions at the end, refusals by code, stuck events, server denials, notes.
"""
import argparse
import datetime
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from eve_mind.config import lane  # noqa: E402

OPEN = ("pending", "sent", "accepted", "running")
FAILED = ("failed", "timeout", "rejected")


def ms(text):
    return int(datetime.datetime.fromisoformat(text).timestamp() * 1000)


def check(db_path, since, until, registry_path, retry_limit=3):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    reg = {s["name"]: s for s in json.loads(Path(registry_path).read_text("utf-8"))["skills"]}
    acts = [dict(r) for r in con.execute(
        "SELECT * FROM actions WHERE sent_ts IS NOT NULL AND sent_ts BETWEEN ? AND ? ORDER BY sent_ts", (since, until))]
    events = [dict(r, payload=json.loads(r["payload"])) for r in con.execute(
        "SELECT ts, source, kind, payload FROM events WHERE ts BETWEEN ? AND ? ORDER BY id", (since, until))]
    v = defaultdict(list)

    real = [a for a in acts if a["skill"] != "stop"]
    for a, b in zip(real, real[1:]):
        if b["sent_ts"] < (a["result_ts"] or until) and a["status"] not in ("rejected",):
            v["single_flight"].append(f"{b['action_id'][:8]} {b['skill']} sent while {a['action_id'][:8]} {a['skill']} "
                                      f"was {a['status']}")

    stopped, intervals = None, []
    prior = con.execute("SELECT payload FROM events WHERE kind='control' AND source='mind' AND ts < ? ORDER BY id DESC",
                        (since,)).fetchall()
    for (p,) in prior:
        op = json.loads(p).get("op")
        if op in ("stop", "resume"):
            stopped = since if op == "stop" else None
            break
    for e in events:
        if e["kind"] == "control" and e["source"] == "mind":
            if e["payload"].get("op") == "stop" and stopped is None:
                stopped = e["ts"]
            elif e["payload"].get("op") == "resume" and stopped is not None:
                intervals.append((stopped, e["ts"]))
                stopped = None
    if stopped is not None:
        intervals.append((stopped, until))
    for a in real:
        for lo, hi in intervals:
            if lo < a["sent_ts"] < hi:   # O81a: a send in the stop's own millisecond preceded it (the mind refuses
                #                          every request after setting STOPPED, before any await - eve-watch's tie)
                v["stopped_quiet"].append(f"{a['action_id'][:8]} {a['skill']} sent while STOPPED")

    for a in acts:
        if a["status"] == "ok":
            ev = json.loads(a["evidence"] or "{}")
            missing = [k for k in reg.get(a["skill"], {}).get("evidence", []) if k not in ev]
            if missing:
                v["ok_has_evidence"].append(f"{a['action_id'][:8]} {a['skill']} ok without {missing}")
        if a["status"] == "uncertain" and not (a["reconciled"] and a["reconcile_note"]) and a["code"] != "no_result":
            v["uncertain_settled"].append(f"{a['action_id'][:8]} {a['skill']} uncertain/{a['code']} not reconciled")

    fails = defaultdict(list)
    for a in acts:
        if a["status"] in FAILED or a["code"] == "no_result":
            fails[(a["skill"], a["params"])].append(a["result_ts"] or a["sent_ts"])
    for (skill, params), ts in fails.items():
        for i in range(len(ts) - retry_limit):
            if ts[i + retry_limit] - ts[i] < 600_000:
                v["retry_bounded"].append(f"{skill} {params[:60]} failed {retry_limit + 1}+ times within 10 min")
                break

    granted = {e["payload"]["action_id"] for e in events if e["kind"] == "task.grant"}
    revoked = {e["payload"]["action_id"] for e in events if e["kind"] == "task.grant.revoke"}
    status_of = {a["action_id"]: a["status"] for a in acts}
    for aid in granted - revoked:
        if status_of.get(aid) not in OPEN:     # refused-before-send grants have no action row: also must be revoked
            v["grants_revoked"].append(f"grant for {aid[:8]} ({status_of.get(aid, 'never sent')}) not revoked")

    open_now = [a for a in acts if a["status"] in OPEN]
    running = [dict(r) for r in con.execute("SELECT task_id, goal FROM tasks WHERE status='running'")]
    open_tasks = {a["task_id"] for a in open_now}
    for t in running:
        if t["task_id"] not in open_tasks and not con.execute(
                "SELECT 1 FROM actions WHERE task_id=? AND status IN ('pending','sent','accepted','running')",
                (t["task_id"],)).fetchone():
            v["task_consistent"].append(f"task {t['task_id'][:8]} {t['goal']} is running with no open action")
    con.close()
    return {
        "lane": lane(),
        "window": [since, until], "actions": len(acts), "by_status": dict(Counter(a["status"] for a in acts)),
        "violations": {k: vals for k, vals in v.items()},
        "open_at_end": [f"{a['action_id'][:8]} {a['skill']} {a['status']}" for a in open_now],
        "refusals": dict(Counter(e["payload"].get("code") for e in events if e["kind"] == "dispatch_refused")),
        "stuck": [e["payload"].get("reason") for e in events if e["kind"] == "stuck"],
        "server_denials": dict(Counter(f"{e['payload'].get('effect')} {e['payload'].get('rule_id')}"
                                       for e in events if e["kind"] == "permission.denied")),
        "notes": [f"{a['action_id'][:8]} {a['skill']} {a['status']}/{a['code']}: {a['note']}" for a in acts if a["note"]],
        "verdict": "PASS" if not v else "FAIL",
    }


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows pipes default to cp1252
    root = Path(__file__).resolve().parents[3]
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=r"C:\eve\mind\eve.db")
    ap.add_argument("--since", required=True, help="local time, e.g. '2026-10-19 18:29'")
    ap.add_argument("--until", help="local time (default: now)")
    ap.add_argument("--registry", default=str(root / "contracts" / "skills" / "registry.json"))
    ap.add_argument("--out", help="folder for live_check.json")
    a = ap.parse_args(argv)
    until = ms(a.until) if a.until else int(datetime.datetime.now().timestamp() * 1000)
    r = check(a.db, ms(a.since), until, a.registry)
    text = json.dumps(r, indent=2)
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)
        (Path(a.out) / "live_check.json").write_text(text, "utf-8")
    print(text)
    return 0 if r["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
