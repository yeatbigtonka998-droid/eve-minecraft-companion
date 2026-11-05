"""I23a: the mind's state-row prune, run once outside the mind (45L install of a pin older than d8949e1).

    python tools/prune_once.py <eve.db> --count   read-only (O40k): what the mind's first prune would delete
    python tools/prune_once.py <eve.db> --apply   with the mind STOPPED: add obs_ts, delete those rows in chunks

Same cutoff as the mind (meta state.retain_min, default 10 min; pinned rows kept). With obs_ts in place an old mind's
own minute prune is an index range scan, so it never scans the table again. Prints counts only, never payloads."""
import sqlite3
import sys
import time

CHUNK = 5000


def main(db, mode):
    ro = mode == "--count"
    con = sqlite3.connect(f"file:{db}?mode=ro" if ro else db, uri=ro, isolation_level=None, timeout=5)
    keep = int((con.execute("SELECT value FROM meta WHERE key='state.retain_min'").fetchone() or ["10"])[0])
    cut = int(time.time() * 1000) - keep * 60_000
    total, pinned = con.execute("SELECT count(*), coalesce(sum(pinned), 0) FROM observations").fetchone()
    old, lo = con.execute("SELECT count(*), min(ts) FROM observations WHERE pinned=0 AND ts < ?", (cut,)).fetchone()
    has_ix = con.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='obs_ts'").fetchone() is not None
    age = f"{(cut - lo) / 3_600_000 + keep / 60:.1f} h" if lo else "-"
    print(f"observations {total} (pinned {pinned}); retain {keep} min; first prune would delete {old} "
          f"(oldest {age}); obs_ts index {'present' if has_ix else 'absent'}")
    if ro:
        return
    t = time.perf_counter()
    con.execute("CREATE INDEX IF NOT EXISTS obs_ts ON observations(ts)")
    n = done = 1
    while n:
        n = con.execute("DELETE FROM observations WHERE id IN (SELECT id FROM observations WHERE ts < ? AND pinned=0 "
                        "LIMIT ?)", (cut, CHUNK)).rowcount
        done += n
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    print(f"applied: obs_ts present, {done - 1} rows deleted in {time.perf_counter() - t:.1f} s")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[2] not in ("--count", "--apply"):
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
