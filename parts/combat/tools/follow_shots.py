"""O72: per k_follow trial, the fight_ranged calls the mind made and what the bow did (shots, hits, first-shot distance,
ticks between shots, client ticks per second of the call). Read-only on the mind DB.

    py -3.12 parts/combat/tools/follow_shots.py <mind eve.db> <evidence dir with rows.jsonl>
"""
import json
import sqlite3
import sys
from pathlib import Path

db, ev = sys.argv[1], Path(sys.argv[2])
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
rows = [json.loads(l) for l in open(ev / "rows.jsonl", encoding="utf-8")]
rows = [r for r in rows if r["case"] in ("k_follow", "k_follow_shallow")]
tot = {"calls": 0, "shots": 0, "hits": 0}
for i, r in enumerate(rows):
    cf = r["creeper_follow"]
    if not cf:
        t0 = None
    else:
        t0 = cf[0]["ts"] - 1000
    # window: from the rule firing (or the blast - 25 s for invalid rows) to the blast / next trial
    end = int(r["blast"]["t"] * 1000) + 1000 if r["blast"] else (cf[0]["ts"] + 25000 if cf else None)
    if t0 is None:
        t0 = end - 25000 if end else None
    if t0 is None:
        print(f"#{r['trial']:2} {'I'} no blast, no follow event: no window")
        continue
    acts = c.execute("select accepted_ts, result_ts, status, code, evidence from actions where skill='fight_ranged' "
                     "and accepted_ts between ? and ? order by accepted_ts", (t0, end)).fetchall()
    tag = "P" if r["ok"] else ("F" if r["valid"] else "I")
    out = []
    for a_ts, r_ts, st, code, e in acts:
        e = json.loads(e or "{}")
        log = e.get("shot_log") or []
        ticks = [s["tick"] for s in log]
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        tps = round(max(ticks) / ((r_ts - a_ts) / 1000), 1) if ticks and r_ts and r_ts > a_ts else None
        out.append(f"{st}/{code} {(r_ts or a_ts) - a_ts}ms shots {e.get('shots')} hits {e.get('hits')} "
                   f"first@{log[0]['distance'] if log else None} t{ticks[0] if ticks else None} gaps {gaps} tps~{tps}")
        tot["calls"] += 1
        tot["shots"] += e.get("shots") or 0
        tot["hits"] += e.get("hits") or 0
    blast = r["blast"]["distance"] if r["blast"] else None
    print(f"#{r['trial']:2} {tag} blast {blast} follow@{cf[0]['distance'] if cf else None}: " + (" | ".join(out) or "no fight_ranged"))
print(tot)
