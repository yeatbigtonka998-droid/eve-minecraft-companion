r"""CP-11 [S] post-handshake cases on the REAL /body link: body's dev-only devtools jar (eve-body-devtools-0.2.0.jar,
body commit c17194f) sends 9 malformed frames on its live session when a trigger file appears; this tool pulls the
trigger and judges the mind side. Run ON the 45L with the mind's venv, devtools jar installed only for the run:
    C:\eve\mind\parts\mind\.venv\Scripts\python.exe C:\eve\mind\parts\mind\tools\live_cp11_fuzz.py --out <dir> \
        --gamedir C:\eve\client [--rounds 12]
FROZEN PASS CONDITION per round: the mind's metrics move by exactly bad_json +2 (bad JSON, binary), duplicate_id +1,
dropped_session_mismatch +1, unknown_type +2 (bogus.type, body-sent action.request), bad_payload +1, payload_too_large +1
(the 300 KB frame; the link closes 1009 and the body reconnects in a new session); no new actions row; no stored
observation with dim "overworld"; binding OK again afterwards. 12 rounds x 8 variants = 96 attempts here; with the 100
handshake/dispatch attempts of live_cp11.py the CP-11 S total is >= 100 per suite. Any miss = FAIL.
"""
import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from eve_mind.cli import call  # noqa: E402

TOKEN_FILE = Path(r"C:\eve\mind\config\eve-link-token.txt")
EXPECT = {"bad_json": 2, "duplicate_id": 1, "dropped_session_mismatch": 1, "unknown_type": 2, "bad_payload": 1,
          "payload_too_large": 1}


def judge(before, after, rows_before, rows_after, bad_obs, session_changed, bound):
    delta = {k: after.get(k, 0) - before.get(k, 0) for k in EXPECT}
    checks = {"metrics": delta == EXPECT, "nothing_sent": rows_before == rows_after, "bad_state_not_stored": bad_obs == 0,
              "reconnected": session_changed, "bound_again": bound}
    return all(checks.values()), delta, checks


def db_counts(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return (con.execute("SELECT coalesce(max(rowid), 0) FROM actions").fetchone()[0],
                con.execute("SELECT count(*) FROM observations WHERE payload LIKE '%\"dim\": \"overworld\"%'").fetchone()[0])
    finally:
        con.close()


async def run(a):
    trig = Path(a.gamedir) / "config" / "eve-devtools-malformed.trigger"
    done = Path(a.gamedir) / "config" / "eve-devtools-malformed.done"
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for rnd in range(a.rounds):
        st = await call("status", {}, a.port, 10)
        if not (st["binding"]["ok"] and not st["stopped"]):
            print("precondition failed (not counted):", st["binding"])
            return 2
        before, sess = st["metrics"], st["body"]["session"]
        r0, _ = db_counts(a.db)
        done.unlink(missing_ok=True)
        trig.write_text(str(time.time()))
        t0 = time.monotonic()
        while not done.exists() and time.monotonic() - t0 < 30:
            await asyncio.sleep(0.2)
        st2 = None
        while time.monotonic() - t0 < 60:
            st2 = await call("status", {}, a.port, 10)
            if st2["body"] and st2["body"]["session"] != sess and st2["binding"]["ok"]:
                break
            await asyncio.sleep(0.5)
        r1, bad = db_counts(a.db)
        ok, delta, checks = judge(before, st2["metrics"], r0, r1, bad,
                                  bool(st2["body"]) and st2["body"]["session"] != sess, st2["binding"]["ok"])
        row = {"round": rnd, "pass": ok and done.exists(), "delta": delta, "checks": checks,
               "devtools_done": done.read_text(errors="replace")[:2000] if done.exists() else None}
        rows.append(row)
        print(f"{'PASS' if row['pass'] else 'FAIL'} round {rnd} {json.dumps(delta)}", flush=True)
        await asyncio.sleep(2)
    (out / "fuzz_rounds.jsonl").write_text("\n".join(json.dumps(r) for r in rows), "utf-8")
    summary = {"rounds": len(rows), "attempts": len(rows) * 8, "passed_rounds": sum(r["pass"] for r in rows),
               "verdict": "PASS" if rows and all(r["pass"] for r in rows) else "FAIL"}
    (out / "fuzz_summary.json").write_text(json.dumps(summary, indent=2), "utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["verdict"] == "PASS" else 1


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--gamedir", default=r"C:\eve\client")
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--port", type=int, default=int(os.environ.get("EVE_PORT", 8951)))
    ap.add_argument("--db", default=os.environ.get("EVE_DB", r"C:\eve\mind\eve.db"))
    a = ap.parse_args(argv)
    if not os.environ.get("EVE_LINK_TOKEN") and TOKEN_FILE.exists():
        os.environ["EVE_LINK_TOKEN"] = TOKEN_FILE.read_text().strip()
    return asyncio.run(run(a))


if __name__ == "__main__":
    sys.exit(main())
