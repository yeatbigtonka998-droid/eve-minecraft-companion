"""I23c/O82 acceptance: the mind's event loop under host disk contention.

    python tools/contention_test.py [--minutes 10] [--writers 4] [--out evidence_dir]

N separate processes keep writing and fsync'ing large files on the disk that holds the mind's DB, while a mind runs with
a fake body (state at 20 Hz), its supervisor, a status poll every 2 s, a walk every 10 s and an owner statement every
15 s. A monitor coroutine sleeps 20 ms at a time and records how late it wakes: that lateness is the loop stall.
Pass = no stall of 250 ms or more over the whole run. Writes result.json (and exits 1 on a fail)."""
import argparse
import asyncio
import faulthandler
import gc
import json
import multiprocessing
import os
import pathlib
import shutil
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "tests"), str(HERE.parent / "src"), str(HERE)]
LIMIT_MS = 250


def hog(path, stop, chunk_mb=4, sync_every_mb=64, file_mb=512):
    """One disk hog: write file_mb in chunks, fsync every sync_every_mb, delete, repeat until stopped."""
    buf = os.urandom(chunk_mb << 20)
    while not stop.is_set():
        with open(path, "wb") as f:
            for i in range(file_mb // chunk_mb):
                if stop.is_set():
                    break
                f.write(buf)
                if (i + 1) * chunk_mb % sync_every_mb == 0:
                    f.flush()
                    os.fsync(f.fileno())
        os.remove(path)


async def run(minutes, workdir, trace_ms=0, out=None):
    from conftest import mind_env
    lags, over, t_end = [], [], time.monotonic() + minutes * 60
    dump = open(pathlib.Path(out or workdir) / "stall_stacks.txt", "w") if trace_ms else None
    gcs, t_gc = [], {}
    if trace_ms:                                   # name a stall: every thread's stack once the loop is late
        def on_gc(phase, info):
            if phase == "start":
                t_gc[0] = time.perf_counter()
            elif 0 in t_gc and (time.perf_counter() - t_gc[0]) * 1000 > 50:
                gcs.append({"gen": info.get("generation"), "ms": round((time.perf_counter() - t_gc[0]) * 1000)})
        gc.callbacks.append(on_gc)

    async def monitor():
        while time.monotonic() < t_end:
            if dump:
                faulthandler.dump_traceback_later(trace_ms / 1000, file=dump)
            t = time.perf_counter()
            await asyncio.sleep(0.02)
            lag = (time.perf_counter() - t - 0.02) * 1000
            lags.append(lag)
            if lag >= LIMIT_MS:
                over.append({"at_s": round(minutes * 60 - (t_end - time.monotonic()), 1), "lag_ms": round(lag)})
        if dump:
            faulthandler.cancel_dump_traceback_later()   # the window is over: teardown's idle waits are not stalls

    async with mind_env(workdir, ping_s=5.0, dead_s=15.0) as e:
        m = e.mind

        async def poll():
            while time.monotonic() < t_end:
                await m.cli("status", {})
                await asyncio.sleep(2)

        async def walk():
            x = 0
            while time.monotonic() < t_end:
                x = (x + 3) % 30
                await m.do("walk_to", {"pos": {"x": x + 0.5, "y": 64.0, "z": 0.5}}, source="cli", wait=5)
                await asyncio.sleep(10)

        async def talk():
            n = 0
            while time.monotonic() < t_end:
                n += 1
                await m.owner_text(f"The storage chest {n} is at {n} 64 {n}.", "Hunter")
                await asyncio.sleep(15)
        await asyncio.gather(monitor(), poll(), walk(), talk())
    if dump:
        faulthandler.cancel_dump_traceback_later()
        dump.close()
    lags.sort()
    return {"minutes": minutes, "samples": len(lags), "max_ms": round(lags[-1], 1),
            "p99_ms": round(lags[int(len(lags) * 0.99)], 1), "p999_ms": round(lags[int(len(lags) * 0.999)], 1),
            "stalls_over_250ms": over[:50], "gc_pauses_over_50ms": gcs[:50], "pass": not over}


def memory():
    """I29: free physical memory, commit and pages/sec - a run while the host pages is not a valid run."""
    import ctypes
    import subprocess

    class MS(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("phys", ctypes.c_ulonglong),
                    ("phys_free", ctypes.c_ulonglong), ("commit_limit", ctypes.c_ulonglong),
                    ("commit_free", ctypes.c_ulonglong), ("virt", ctypes.c_ulonglong), ("virt_free", ctypes.c_ulonglong),
                    ("ext", ctypes.c_ulonglong)]
    m = MS()
    m.len = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    gb = lambda b: round(b / 2 ** 30, 1)  # noqa: E731
    pages = None
    try:
        out = subprocess.run(["typeperf", r"\Memory\Pages/sec", "-sc", "3"], capture_output=True, text=True,
                             timeout=20).stdout
        vals = [float(line.rsplit('","', 1)[1].strip('"')) for line in out.splitlines()
                if line.startswith('"') and line.count('","') == 1 and "PDH-CSV" not in line]
        pages = round(sum(vals) / len(vals)) if vals else None
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return {"phys_free_gb": gb(m.phys_free), "phys_gb": gb(m.phys), "commit_used_gb": gb(m.commit_limit - m.commit_free),
            "commit_limit_gb": gb(m.commit_limit), "pages_per_s": pages}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--writers", type=int, default=4)
    ap.add_argument("--out", default=None)
    ap.add_argument("--trace-ms", type=float, default=0, help="dump all stacks when the loop is this late")
    a = ap.parse_args()
    before = memory()
    work = pathlib.Path(tempfile.mkdtemp(prefix="eve-contention-"))
    stop = multiprocessing.Event()
    hogs = [multiprocessing.Process(target=hog, args=(str(work / f"hog{i}.bin"), stop), daemon=True)
            for i in range(a.writers)]
    for p in hogs:
        p.start()
    try:
        if a.out:
            pathlib.Path(a.out).mkdir(parents=True, exist_ok=True)
        res = asyncio.run(run(a.minutes, work, a.trace_ms, a.out))
    finally:
        stop.set()
        for p in hogs:
            p.join(30)
        shutil.rmtree(work, ignore_errors=True)         # the hogs' half-written files (up to 512 MB each)
    res.update(writers=a.writers, disk=str(work.anchor), memory_before=before, memory_after=memory())
    res["paging"] = any((x.get("pages_per_s") or 0) > 1000 or x["commit_used_gb"] > 0.95 * x["commit_limit_gb"]
                        for x in (before, res["memory_after"]))     # I29: then the run is not valid
    print(json.dumps(res, indent=1))
    if a.out:
        out = pathlib.Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "result.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    sys.exit(0 if res["pass"] else 1)


if __name__ == "__main__":
    main()
