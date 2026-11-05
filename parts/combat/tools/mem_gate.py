"""I29: Shark memory gate around a timing-sensitive series. Prints one line (free GB, commit GB/limit, pages/s) and, with
--wait, holds until free RAM >= --min GB (default 8) for up to --max minutes (default 30). Exit 0 = gate open, 1 = timed out.

    py -3.12 parts/combat/tools/mem_gate.py [--wait] [--min 8] [--max 30] [--label before:k_follow]
"""
import subprocess
from pathlib import Path
import sys
import time


def arg(name, default):
    return type(default)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def sample():
    ps = ("$c = Get-Counter '\\Memory\\Available MBytes','\\Memory\\Committed Bytes','\\Memory\\Commit Limit','\\Memory\\Pages/sec' "
          "-SampleInterval 1 -MaxSamples 2; $s = $c[-1].CounterSamples; ($s | ForEach-Object { $_.CookedValue }) -join ' '")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60).stdout.split()
    avail_mb, committed, limit, pages = (float(x) for x in out[:4])
    return avail_mb / 1024, committed / 2**30, limit / 2**30, pages


def line(label):
    free, com, lim, pages = sample()
    print(f"{time.strftime('%H:%M:%S')} I29 {label}: free {free:.1f} GB, commit {com:.1f}/{lim:.1f} GB, {pages:.0f} pages/s", flush=True)
    return free


def pages60():
    """I29d: the 60 s paging average (12 samples, 5 s apart)."""
    ps = (r"$c = Get-Counter '\Memory\Pages/sec' -SampleInterval 5 -MaxSamples 12; "
          "($c | ForEach-Object { $_.CounterSamples[0].CookedValue } | Measure-Object -Average).Average")
    return float(subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=120).stdout.strip())


def monitor(path, every, stop=None):
    """--monitor <csv>: (ts, free GB, commit GB, Page Reads/s, Pages Output/s) every `every` s from one continuous Get-Counter stream, until
    killed. I29e: Pages/sec counts file I/O (world writes); Page Reads/sec is the hard-fault side, the pressure signal."""
    ps = (r"Get-Counter '\Memory\Available MBytes','\Memory\Committed Bytes','\Memory\Page Reads/sec','\Memory\Pages Output/sec' "
          f"-SampleInterval {int(every)} -Continuous | ForEach-Object {{ ($_.CounterSamples | ForEach-Object {{ $_.CookedValue }}) -join ' ' }}")
    proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", ps], stdout=subprocess.PIPE, text=True)
    with open(path, "a", encoding="utf-8") as f:
        for out in proc.stdout:
            v = out.split()
            if len(v) == 4:  # ts, free GB, commit GB, Page Reads/s (context), Pages Output/s (the I29f pressure leg)
                f.write(f"{time.time():.1f},{float(v[0]) / 1024:.2f},{float(v[1]) / 2**30:.2f},{float(v[2]):.0f},{float(v[3]):.0f}" + chr(10))
                f.flush()
            if stop and Path(stop).exists():  # --stop <file>: end the stream and its PowerShell child cleanly
                proc.kill()
                return


def i29e(rows_csv, a, b):
    """I29f verdict for a window [a, b] (unix s): INVALID if free < 2 GB at any sample, or Pages Output/s (pagefile writes)
    > 50 for >= 10 s in a row. Page Reads/s also counts chunk and jar loads: logged, not judged. A 4-column file (from
    before I29f) has no Pages Output column; judge its pressure leg from another sampler. Returns (ok, why)."""
    import csv
    rs = [r for r in csv.reader(open(rows_csv)) if a <= float(r[0]) <= b]
    if rs and len(rs[0]) < 5:
        return False, "no Pages Output column (pre-I29f file)"
    xs = [(float(r[0]), float(r[1]), float(r[4])) for r in rs]
    if not xs:
        return False, "no samples"
    if min(x[1] for x in xs) < 2:
        return False, f"free {min(x[1] for x in xs):.1f} GB"
    run0 = None
    for t, _, pr in xs:
        if pr > 50:
            run0 = t if run0 is None else run0
            if t - run0 >= 10:
                return False, f"pages output > 50 for >= 10 s from {time.strftime('%H:%M:%S', time.localtime(run0))}"
        else:
            run0 = None
    return True, f"free >= {min(x[1] for x in xs):.1f} GB, pages output max {max(x[2] for x in xs):.0f}/s"


if __name__ == "__main__":
    if "--monitor" in sys.argv:
        monitor(arg("--monitor", ""), arg("--every", 5.0), arg("--stop", ""))
        sys.exit(0)
    label, need, budget = arg("--label", "now"), arg("--min", 8.0), arg("--max", 30.0)
    free, t0 = line(label), time.time()
    if "--i29d" in sys.argv or "--wait" in sys.argv:  # I29d: >= 8 GB, or >= 7 GB with the 60 s paging average under 200 pages/s
        while True:
            avg = pages60()
            free = line(label)
            ok = free >= 8 or (free >= 7 and avg < 200)
            print(f"I29d {label}: 60 s paging avg {avg:.0f} pages/s -> gate {'OPEN' if ok else 'waiting'}", flush=True)
            if ok:
                sys.exit(0)
            if time.time() - t0 > budget * 60:
                print(f"I29 gate: not open after {budget:.0f} min; not starting", flush=True)
                sys.exit(1)
    while "--wait" in sys.argv and free < need:
        if time.time() - t0 > budget * 60:
            print(f"I29 gate: free stayed below {need} GB for {budget:.0f} min; not starting", flush=True)
            sys.exit(1)
        time.sleep(30)
        free = line(label + " (waiting)")
