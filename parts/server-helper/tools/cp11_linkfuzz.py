r"""CP-11 (eve-mind) post-handshake frame cases on the live /sense link, qualification form (S class: >= 100 attempts).
Each round, `/evetest linkfuzz all` makes sense send 9 malformed frames once on its established link (bad JSON, a binary
frame, a duplicate id, a wrong session, an unknown type, a wrong-direction `state`, a storage.index container without
`dim`, an owner.command claiming trusted:true from a sender not in trusted_players, a 300 KB frame last -> the mind closes
with 1009 and sense reconnects). Needs eve-sense-devtools >= 0.1.5 build e1218581 (LinkFuzz) on the server.
Run with the mind venv on the mind's machine (the 45L):
    C:\eve\mind\parts\mind\.venv\Scripts\python.exe C:\eve\ops\cp11_linkfuzz.py [--rounds 12]
Writes <out>\cp11-linkfuzz-<stamp>\{RESULT.txt,rounds.jsonl,live_check.*}.

FROZEN CRITERIA (eve-mind's, per round): the mind's metrics change by exactly bad_json +2 (the binary frame counts as
bad JSON), duplicate_id +1, dropped_session_mismatch +1, unknown_type +2 (unknown + wrong direction), bad_payload +1,
untrusted_commands +1, payload_too_large +1; afterwards sense is re-bound (new session, binding ok), the mind is not
STOPPED, the sense STOPPED flag is absent, and nothing was stored from the bad storage.index (no containers row
`minecraft:overworld:0,64,0`). PASS = every round passes and >= 100 attempts (9 per round); then the mind's
`tools/live_check.py --since <start>` over the window must PASS too (its output is kept).
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=r"C:\eve\server-dev")
ap.add_argument("--lane", default="45L", choices=["45L", "shark"], help="which test lane this server is (O18): labels the evidence")
ap.add_argument("--ops", default=r"C:\eve\ops")
ap.add_argument("--db", default=r"C:\eve\mind\eve.db")
ap.add_argument("--eve-cli", default=os.environ.get("EVE_CLI", r"C:\eve\mind\parts\mind\.venv\Scripts\eve.exe"))
ap.add_argument("--live-check", default=os.environ.get("EVE_LIVE_CHECK", r"C:\eve\mind\parts\mind\tools\live_check.py"))
ap.add_argument("--out", default=r"C:\eve\logs")
ap.add_argument("--rounds", type=int, default=12)
a, _ = ap.parse_known_args()
sys.path.insert(0, a.ops)
import rcon  # noqa: E402

ROOT = Path(a.root)
OUT = Path(a.out) / time.strftime(f"cp11-linkfuzz-{a.lane.lower()}-%Y%m%d-%H%M")
OUT.mkdir(parents=True, exist_ok=True)
LOG = (OUT / "rounds.jsonl").open("a", encoding="utf-8")
DELTA = {"bad_json": 2, "duplicate_id": 1, "dropped_session_mismatch": 1, "unknown_type": 2, "bad_payload": 1,
         "untrusted_commands": 1, "payload_too_large": 1}
BAD_ID = "minecraft:overworld:0,64,0"


def status():
    r = subprocess.run([a.eve_cli, "--json", "status"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    return json.loads(r.stdout)


def stored_bad():
    db = sqlite3.connect(f"file:{Path(a.db).as_posix()}?mode=ro", uri=True)
    try:
        return db.execute("SELECT count(*) FROM containers WHERE container_id=?", (BAD_ID,)).fetchone()[0] > 0
    finally:
        db.close()


start = time.strftime("%Y-%m-%d %H:%M")
time.sleep(61 - time.localtime().tm_sec)              # live_check --since has minute resolution: start on a fresh minute
start = time.strftime("%Y-%m-%d %H:%M")
t0 = time.time()
rounds = []
for n in range(1, a.rounds + 1):
    s0 = status()
    out = rcon.command(ROOT, "evetest linkfuzz all")
    s1, end = s0, time.time() + 40
    while time.time() < end:                            # 9 frames x 0.4 s, the 1009 close, then sense's reconnect
        time.sleep(1.5)
        s1 = status()
        if (s1.get("sense") or {}).get("session") not in (None, (s0.get("sense") or {}).get("session")) and s1.get("binding", {}).get("ok"):
            break
    time.sleep(1.0)
    s1 = status()
    m0, m1 = s0["metrics"], s1["metrics"]
    deltas = {k: m1.get(k, 0) - m0.get(k, 0) for k in DELTA}
    checks = {"deltas_exact": deltas == DELTA,
              "rebound": (s1.get("sense") or {}).get("session") not in (None, (s0.get("sense") or {}).get("session")) and bool(s1.get("binding", {}).get("ok")),
              "mind_not_stopped": s1.get("stopped") is False,
              "sense_not_stopped": not (ROOT / "eve-sense" / "STOPPED").exists(),
              "bad_index_not_stored": not stored_bad(),
              "queued": "queued" in out}
    row = {"round": n, "ts": time.strftime("%H:%M:%S"), "pass": all(checks.values()), "checks": checks, "deltas": deltas,
           "rcon": out, "binding": s1.get("binding"), "body_session": (s1.get("body") or {}).get("session"), "sense_session": [(s0.get("sense") or {}).get("session"), (s1.get("sense") or {}).get("session")]}
    rounds.append(row)
    LOG.write(json.dumps(row) + "\n")
    LOG.flush()
    print(f"{'PASS' if row['pass'] else 'FAIL'} round {n}: {deltas} {checks}", flush=True)
    time.sleep(2)

lc = subprocess.run([sys.executable, a.live_check, "--db", a.db, "--since", start, "--out", str(OUT)], capture_output=True,
                    text=True, timeout=300, stdin=subprocess.DEVNULL) if Path(a.live_check).exists() else None
lc_text = (lc.stdout + lc.stderr) if lc else f"live_check not found at {a.live_check}"
(OUT / "live_check.txt").write_text(lc_text, encoding="utf-8")
lc_ok = lc is not None and lc.returncode == 0
ok = sum(r["pass"] for r in rounds)
attempts = 9 * len(rounds)
verdict = "PASS" if ok == len(rounds) and attempts >= 100 and lc_ok else "FAIL"
lines = [f"CP-11 /sense linkfuzz [{a.lane}] {verdict}  {start} .. {time.strftime('%H:%M')}  rounds {ok}/{len(rounds)}, attempts {attempts}",
         f"  live_check --since '{start}': {'PASS' if lc_ok else 'FAIL'} (exit {lc.returncode if lc else 'n/a'})",
         *(f"  round {r['round']:>2} {'PASS' if r['pass'] else 'FAIL'} {r['deltas']}" + ("" if r["pass"] else f" {r['checks']}") for r in rounds),
         f"elapsed {time.time() - t0:.0f}s"]
(OUT / "RESULT.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines), flush=True)
sys.exit(0 if verdict == "PASS" else 1)
