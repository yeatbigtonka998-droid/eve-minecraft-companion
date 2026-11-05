"""run_meta.json for eve-watch: every action row in each scenario's own DB (records setup + the line), in order.
A run without scenario DBs (CP-41: planner calls only, no work/) gets the same meta with no scenarios.
  py -3.12 tools/run_meta.py <out_dir> <mind_commit> [note]"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

out, commit = Path(sys.argv[1]), sys.argv[2]
head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
meta = {"mind_code_commit": commit, "repo_head_at_report": head, "lane": "shark",
        "note": sys.argv[3] if len(sys.argv) > 3 else "", "scenarios": {}}
for d in sorted((out / "work").iterdir()) if (out / "work").is_dir() else []:
    con = sqlite3.connect(d / "eve.db")
    rows = con.execute("SELECT skill, params, status, code FROM actions ORDER BY rowid").fetchall()
    con.close()
    meta["scenarios"][d.name] = [{"skill": s, "params": json.loads(p) if p else None, "status": st, "code": c}
                                 for s, p, st, c in rows]
(out / "run_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
print(f"{len(meta['scenarios'])} scenarios -> {out / 'run_meta.json'}")
