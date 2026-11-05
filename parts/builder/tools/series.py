"""A series of one laneb_build.py mode until n VALID results (PASS or FAIL; INVALID rows don't count), at most 2n attempts.

    py -3.12 parts/builder/tools/series.py <B|C|45L> <n> [--from <first attempt>] [--next <chat>] [--keep-lock] <laneb_build.py args...>
    --keep-lock: a window script holds the lane lock around several series; do not release it at the end (one release, I13)
    e.g. series.py B 20 --from 7 cp52 --lane B          (the 45L: mind venv python, via foundation's guarded-run.ps1)

On a Shark lane it takes the lane lock once (waits for it) and keeps it between attempts (EVE_SERIES makes the harness skip
its own release); the 45L takes no lock (foundation schedules under its suite lock). Each attempt resets its own fixture
and writes its own summary; the last line printed is the tally.
"""
import os, subprocess, sys, time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252 (the harness prints "—")
lane, n, rest = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
first, nxt, keep = 1, None, False
while rest[:1] in (["--from"], ["--next"], ["--keep-lock"]):  # --next <chat>: hand the lane over on release (lane-lock I13)
    if rest[0] == "--keep-lock":
        keep, rest = True, rest[1:]
        continue
    if rest[0] == "--from":
        first = int(rest[1])
    else:
        nxt = rest[1]
    rest = rest[2:]
here = Path(__file__).resolve()
harness, repo = here.parent / "laneb_build.py", here.parents[3]
lock = {"B": r"C:\eve-shark\ops\lane-lock.ps1", "C": r"C:\eve-shark-c\ops\lane-lock.ps1"}.get(lane)
ps = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
if lock:
    while True:  # an outer run may already hold it for eve-builder: that is ours
        a_ = subprocess.run(ps + [lock, "acquire", "-By", "eve-builder", "-Minutes", str(10 + 6 * n), "-What", f"builder series x{n}"],
                            capture_output=True, text=True)
        print(a_.stdout.strip(), flush=True)
        if a_.returncode == 0 or "by=eve-builder " in a_.stdout:
            break
        time.sleep(10)
tally = {"PASS": 0, "FAIL": 0, "INVALID": 0}
try:
    for k in range(2 * n):
        if tally["PASS"] + tally["FAIL"] >= n:
            break
        a = first + k
        out = subprocess.run([sys.executable, str(harness), *rest, "--attempt", str(a)], cwd=repo, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", env={**os.environ, "EVE_SERIES": "1", "PYTHONIOENCODING": "utf-8"})
        logs = repo / "evidence" / "series-logs"  # evidence/ is gitignored
        logs.mkdir(parents=True, exist_ok=True)
        (logs / f"{lane}-{a}.log").write_text(out.stdout + out.stderr, encoding="utf-8")
        head = next((l for l in out.stdout.splitlines() if l.startswith("# ")), f"INVALID: no summary (exit {out.returncode})")
        verdict = "PASS" if ": PASS" in head else "FAIL" if ": FAIL" in head else "INVALID"
        tally[verdict] += 1
        print(f"[{a}] {head}", flush=True)
finally:
    if lock and not keep:
        subprocess.run(ps + [lock, "release", "-By", "eve-builder"] + (["-Next", nxt] if nxt else []))
print(f"series: {tally['PASS']} PASS, {tally['FAIL']} FAIL, {tally['INVALID']} INVALID", flush=True)
