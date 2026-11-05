r"""CP-43 [B] on a live lane: the mind's objective runner against eve-watch's fixture (parts/watch/laneb/cp43_fixture.py).
    python tools\live_cp43.py --out <evidence dir> [--port 8955] [--token-file C:\eve-shark\mind\config\eve-link-token.txt]
        [--variants V1,V2,V3,V4,V5] [--reps 4]
Per run: lane lock, fixture reset (+ V3's wall before the start), `objective` over /cli, faults from the objective's
own ledger (V2 after the first mine, V4 after the first place, V5 stop after the second place, then resume and an
explicit continue), wait for the end, fixture read. Judged against fixtures/CP-43.json (frozen):
  V1/V3/V4/V5: column 3 x cobblestone and status done; V2: column [cobblestone, cobblestone, air] and status not done
  (partial or asked); V4 also golden apples 2 -> 1 (reported; the body's eat reflex decides how many it eats);
  every run: budget kept (actions <= 24, <= 6 min, re-plans <= 4), no failed step executed twice.
PASS = >= 19/20 runs. The token is read from the file and never printed.
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from eve_mind.cli import call  # noqa: E402

MIND = Path(__file__).resolve().parents[1]
SPEC = json.loads((MIND / "fixtures" / "CP-43.json").read_text("utf-8"))
FIXTURE = MIND.parents[1] / "parts" / "watch" / "laneb" / "cp43_fixture.py"
LANE_LOCK = r"C:\eve-shark\ops\lane-lock.ps1"      # atomic (CreateNew); never overwrites another holder


def lane_lock(op, minutes=150):
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", LANE_LOCK, op, "-By", "eve-mind"]
    if op == "acquire":
        args += ["-Minutes", str(minutes), "-What", "CP-43 live runs"]
    return subprocess.run(args, capture_output=True, text=True, timeout=60)


def fixture(cmd):
    p = subprocess.run([sys.executable, str(FIXTURE), cmd], capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"fixture {cmd} failed: {p.stderr[-400:]}")
    out = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
    return json.loads(out[-1]) if out else None


async def cli(port, cmd, **args):
    return await call(cmd, args, port, 15)


def count(ledger, skill):
    return sum(1 for x in ledger if x["skill"] == skill)


def judge(variant, end, server, apples_before):
    col = server["column"]
    budget_ok = end["actions"] <= 24 and end["elapsed_s"] <= 360 and end["replans"] <= 4
    keys = [json.dumps(f["key"]) for f in end["failed"]]
    no_repeat = len(keys) == len(set(keys))
    checks = {"budget": budget_ok, "no_repeated_failure": no_repeat}
    if variant == "V2":
        checks["server"] = col == ["cobblestone", "cobblestone", "air"]
        checks["honest_report"] = end["status"] != "done"
    else:
        checks["server"] = col == ["cobblestone"] * 3
        checks["honest_report"] = end["status"] == "done" and end["report"].startswith("done: 3/3 placed")
    if variant == "V1":
        checks["supplies_used"] = server["M1"] == "air" and server["M2"] == "air"
    if variant == "V4":
        checks["paused_for_food"] = any(n.startswith("paused for food") for n in end["notes"])
        checks["fault_effective"] = "food_dropped" in end.get("faults_seen", [])
    return all(checks.values()), checks


async def one(a, variant, rep, out):
    port = a.port
    fixture("reset")
    if variant == "V3":
        fixture("v3")
    st = await cli(port, "status")
    if st.get("stopped"):
        await cli(port, "resume")
    for _ in range(40):
        st = await cli(port, "status")
        if st["binding"]["ok"] and not st["stopped"] and st.get("open_action") is None:
            break
        await asyncio.sleep(0.5)
    else:
        return {"variant": variant, "rep": rep, "pass": False, "error": "lane not ready", "status": st.get("binding")}
    server0 = fixture("read")
    r = await cli(port, "objective", text=SPEC["objective_text"])
    if not r.get("ok"):
        return {"variant": variant, "rep": rep, "pass": False, "error": r}
    oid, fired, t0, end, t_v4 = r["objective_id"], set(), time.monotonic(), None, 0.0
    while time.monotonic() - t0 < 480:
        s = await cli(port, "objective", status=oid)
        led = s["ledger"]
        if variant == "V2" and "v2" not in fired and count(led, "mine_block") >= 1:
            fixture("v2"); fired.add("v2")
        if variant == "V4" and "v4" not in fired and count(led, "mine_block") >= 1:
            fixture("v4"); fired.add("v4"); t_v4 = time.monotonic()       # mid-objective, with work still ahead
        if variant == "V4" and "v4" in fired and "food_dropped" not in fired and time.monotonic() - t_v4 > 2:
            if (fixture("read") or {}).get("food", 20) <= 6:
                fired.add("food_dropped")
        if variant == "V5" and "stop" not in fired and count(led, "place_block") >= 2:
            fired.add("stop")
            await cli(port, "stop", reason="owner stop (CP-43 V5)")
            await asyncio.sleep(3)
            await cli(port, "resume")
            await asyncio.sleep(2)
            s2 = await cli(port, "objective", status=oid)
            fired.add(f"after_resume:{s2['status']}")
            c = await cli(port, "objective", **{"continue": oid})
            fired.add(f"continue:{c.get('ok')}")
            continue
        if s["status"] != "running":
            end = s
            break
        await asyncio.sleep(0.5)
    if end is None:
        end = await cli(port, "objective", status=oid)
        end["status_note"] = "harness timeout"
    server = fixture("read")
    end["faults_seen"] = sorted(fired)
    ok, checks = judge(variant, end, server, server0["golden_apple"])
    row = {"variant": variant, "rep": rep, "pass": ok, "checks": checks, "status": end["status"],
           "report": end["report"], "actions": end["actions"], "replans": end["replans"],
           "elapsed_s": round(end["elapsed_s"], 1), "failed": end["failed"], "rejections": end["rejections"],
           "notes": end["notes"], "faults": sorted(fired), "server_before": server0, "server_after": server,
           "objective_id": oid}
    (out / f"{variant}-{rep}.json").write_text(json.dumps(row, indent=2), "utf-8")
    return row


async def main_async(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    got = lane_lock("acquire")
    if got.returncode != 0:
        print("lane B is held by someone else; not started:", (got.stdout + got.stderr).strip()[-300:])
        return 3
    try:
        for rep in range(1, a.reps + 1):
            for v in a.variants.split(","):
                row = await one(a, v, rep, out)
                rows.append(row)
                print(f"{'PASS' if row['pass'] else 'FAIL'} {v}#{rep} {row.get('status')} {row.get('report', row.get('error'))}",
                      flush=True)
    finally:
        try:
            fixture("reset")
        except Exception:
            pass
        lane_lock("release")
    passed = sum(r["pass"] for r in rows)
    summary = {"lane": os.environ.get("EVE_LANE", "shark"), "runs": len(rows), "passed": passed,
               "by_variant": {v: sum(r["pass"] for r in rows if r["variant"] == v) for v in a.variants.split(",")},
               "apples_after_V4": [r["server_after"]["golden_apple"] for r in rows if r["variant"] == "V4" and "server_after" in r],
               "verdict": "PASS" if len(rows) >= 20 and passed >= 19 else "FAIL",
               "differences_from_spec": ["V4 switches the server to easy for the hunger effect (peaceful never drains food)",
                                         "V3's wall is placed before the objective starts (it is before the walk to the column)",
                                         "owner stop/resume/continue go through the mind's /cli (the owner channel on lane B)"]}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), "utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["verdict"] == "PASS" else 1


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=8955)
    ap.add_argument("--token-file", default=r"C:\eve-shark\mind\config\eve-link-token.txt")
    ap.add_argument("--variants", default="V1,V2,V3,V4,V5")
    ap.add_argument("--reps", type=int, default=4)
    a = ap.parse_args(argv)
    os.environ["EVE_LINK_TOKEN"] = Path(a.token_file).read_text().strip()
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    sys.exit(main())
