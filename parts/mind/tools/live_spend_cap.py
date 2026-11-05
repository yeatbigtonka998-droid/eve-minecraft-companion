r"""Spend-cap check on the 45L's real config without spending a cent. Run with the mind's venv and the mind's environment
(EVE_DEEPSEEK_*, OPENROUTER_API_KEY as the mind task has them; the key is never printed):
    C:\eve\mind\parts\mind\.venv\Scripts\python.exe C:\eve\mind\parts\mind\tools\live_spend_cap.py --out <dir>
1. `eve spend` from the running mind == the month-to-date summed straight from eve.db (read-only).
2. On a COPY of eve.db, a DeepThinker built from the live settings with the cap set to the copy's month-to-date (remaining
   0) refuses `budget_exhausted` for both roles, writes no spend row, and its base URL points at a dead port, so even a
   broken check could not reach OpenRouter.
3. mind.log has a `spend <date>: month-to-date ...` line for today.
PASS only if all three hold."""
import argparse
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from eve_mind.cli import call  # noqa: E402
from eve_mind.planner import DeepThinker, spend_summary  # noqa: E402

TOKEN_FILE = Path(r"C:\eve\mind\config\eve-link-token.txt")


async def run(a):
    live = await call("spend", {}, a.port, 10)
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    db_view = spend_summary(con, live.get("cap_usd"))
    con.close()
    same = live.get("ok") and abs(live["month_to_date_usd"] - db_view["month_to_date_usd"]) < 1e-9 \
        and live["calls"] == db_view["calls"]
    with tempfile.TemporaryDirectory() as d:
        copy = Path(d) / "eve.db"
        src = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
        db = sqlite3.connect(copy, isolation_level=None)
        src.backup(db)                                  # includes WAL contents, unlike a file copy
        src.close()
        mtd = spend_summary(db, None)["month_to_date_usd"]
        rows0 = db.execute("SELECT count(*) FROM spend").fetchone()[0]
        dt = DeepThinker(db, {"budgets": {"deepseek_monthly_usd": mtd or 1e-9}}, base_url="http://127.0.0.1:9")
        refusals = {role: await dt.ask("cap-check", [{"role": "user", "content": "x"}], max_tokens=16, role=role)
                    for role in dt.models}
        rows1 = db.execute("SELECT count(*) FROM spend").fetchone()[0]
        db.close()
    refused = bool(refusals) and all(r.get("error") == "budget_exhausted" for r in refusals.values())
    today = f"spend {time.strftime('%Y-%m-%d')}:"
    log_line = next((ln.strip() for ln in Path(a.log).read_text("utf-8", "replace").splitlines() if today in ln), None) \
        if Path(a.log).exists() else None
    checks = {"eve_spend_matches_db": bool(same), "refused_at_cap": refused, "no_row_written": rows0 == rows1,
              "daily_line": log_line is not None}
    out = {"verdict": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "eve_spend": live,
           "db_month_to_date_usd": db_view["month_to_date_usd"], "enabled": dt.enabled,
           "refusals": {k: v.get("error") for k, v in refusals.items()}, "daily_line": log_line}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / "spend_cap.json").write_text(json.dumps(out, indent=2), "utf-8")
    print(json.dumps(out, indent=2))
    return 0 if out["verdict"] == "PASS" else 1


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=int(os.environ.get("EVE_PORT", 8951)))
    ap.add_argument("--db", default=os.environ.get("EVE_DB", r"C:\eve\mind\eve.db"))
    ap.add_argument("--log", default=r"C:\eve\logs\mind.log")
    a = ap.parse_args(argv)
    if not os.environ.get("EVE_LINK_TOKEN") and TOKEN_FILE.exists():
        os.environ["EVE_LINK_TOKEN"] = TOKEN_FILE.read_text().strip()
    return asyncio.run(run(a))


if __name__ == "__main__":
    sys.exit(main())
