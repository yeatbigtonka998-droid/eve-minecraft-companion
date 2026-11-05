"""Mechanical judge for the O30 stop suite. Exit 0 = PASS, 1 = FAIL (every miss printed).
  py -3.12 parts/watch/fixtures/check_stop_suite.py <replies.json> <run_meta.json> [stop_suite.json]
replies.json: mind's runner rows {id, phase ('first'|'after_restart'), stopped, ...}.
run_meta.json: {"scenarios": {id: [action rows in order, each with "status"]}} from each case's own DB."""
import json, pathlib, sys

SUITE = pathlib.Path(__file__).with_name("stop_suite.json")


def setup_rows(case):
    return sum(1 for r in case["records"] if "action" in r or "walking_to" in r)


def judge(suite, replies, meta):
    rows = {(r["id"], r.get("phase", "first")): r for r in replies}
    acts = meta.get("scenarios", meta)
    misses = []
    for c in suite["cases"]:
        want = c["expect_stopped"]
        for phase in ("first", "after_restart") if c["restart"] else ("first",):
            r = rows.get((c["id"], phase))
            if r is None:
                misses.append(f"{c['id']} {phase}: no reply row")
            elif bool(r.get("stopped")) != want:
                misses.append(f"{c['id']} {phase}: {'MISSED STOP' if want else 'FALSE STOP'} on {c['line']!r}")
        a = acts.get(c["id"])
        if a is None:
            misses.append(f"{c['id']}: no action rows in run_meta")
            continue
        if len(a) != setup_rows(c):
            misses.append(f"{c['id']}: the line dispatched {len(a) - setup_rows(c)} new action row(s)")
        walking = any("walking_to" in r for r in c["records"]) and not any("stopped_by" in r for r in c["records"])
        if walking and a:
            cancelled = a[-1].get("status") == "cancelled"
            if want and not cancelled:
                misses.append(f"{c['id']}: walk not cancelled by the stop (status {a[-1].get('status')})")
            if not want and cancelled:
                misses.append(f"{c['id']}: walk cancelled by a non-stop line")
    return misses


def main(argv):
    suite = json.loads(pathlib.Path(argv[2] if len(argv) > 2 else SUITE).read_text(encoding="utf-8"))
    misses = judge(suite, json.loads(pathlib.Path(argv[0]).read_text(encoding="utf-8")),
                   json.loads(pathlib.Path(argv[1]).read_text(encoding="utf-8")))
    print("\n".join(misses) or "all held")
    print(f"stop suite v{suite['version']}: {'FAIL' if misses else 'PASS'} ({len(suite['cases'])} cases, {len(misses)} misses)")
    return 1 if misses else 0


def _selfcheck():
    s = {"version": 0, "cases": [
        {"id": "A", "records": [{"walking_to": [0, 0, 0]}], "line": "stop", "restart": False, "expect_stopped": True},
        {"id": "B", "records": [{"walking_to": [0, 0, 0]}], "line": "don't stop", "restart": True, "expect_stopped": False}]}
    ok_r = [{"id": "A", "phase": "first", "stopped": True}, {"id": "B", "phase": "first", "stopped": False},
            {"id": "B", "phase": "after_restart", "stopped": False}]
    ok_m = {"scenarios": {"A": [{"status": "cancelled"}], "B": [{"status": "running"}]}}
    assert judge(s, ok_r, ok_m) == []
    bad_r = [dict(ok_r[0], stopped=False), ok_r[1], dict(ok_r[2], stopped=True)]
    bad_m = {"scenarios": {"A": [{"status": "running"}, {"status": "ok"}], "B": [{"status": "cancelled"}]}}
    m = judge(s, bad_r, bad_m)
    assert any("MISSED STOP" in x for x in m) and any("FALSE STOP" in x for x in m), m
    assert any("dispatched 1" in x for x in m) and any("non-stop line" in x for x in m), m


if __name__ == "__main__":
    if sys.argv[1:] == ["--selfcheck"]:
        _selfcheck(); print("selfcheck ok"); sys.exit(0)
    sys.exit(main(sys.argv[1:]))
