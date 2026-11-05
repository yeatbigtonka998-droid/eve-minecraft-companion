"""I23c/O82: the mind works on an in-memory copy; one writer thread replays its writes on the file (WAL,
synchronous=NORMAL) - the loop never touches the file. STOPPED and the action row before a dispatch commit with
synchronous=FULL and are awaited; a restart reads the file."""
import threading

from conftest import mind_env, run

from eve_mind.mind import open_db


def test_o82_the_loop_never_touches_the_file_and_stop_and_action_rows_are_durable(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            m = e.mind
            disk = m.db.disk
            assert disk.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
            assert disk.execute("PRAGMA synchronous").fetchone()[0] == 1               # NORMAL for the journal
            sql = []
            disk.set_trace_callback(lambda s: sql.append((threading.current_thread().name, s)))
            assert (await m.do("walk_to", {"pos": {"x": 2.5, "y": 64.0, "z": 0.5}}, source="cli", wait=5)).get("ok")
            r = await m.stop("test", "O82 durable stop")
            assert r["persisted"] is True, r
            assert await m.db.flush(5)
            disk.set_trace_callback(None)
            assert {t for t, s in sql} == {"eve-db-writer"}, {t for t, s in sql}          # never the loop thread
            level, under = 1, {}
            for _, s in sql:                            # the sync level each write committed under
                if "PRAGMA synchronous" in s:
                    level = 2 if "FULL" in s else 1
                elif s.startswith(("INSERT INTO actions", "INSERT OR REPLACE INTO meta")):
                    under.setdefault(s.split()[2] if s.startswith("INSERT INTO") else "meta", set()).add(level)
            assert under["actions"] == {2} and 2 in under["meta"], under                     # durable
            assert any("PRAGMA synchronous = NORMAL" in s for _, s in sql)                   # and back
        return e.cfg
    cfg = run(go())                                     # mind_env closed the DB: the queue drained to the file
    again = open_db(cfg)
    try:
        assert again.execute("SELECT value FROM meta WHERE key='control.stopped'").fetchone()[0] == "1"
    finally:
        again.close()


def test_o82_the_spend_cap_check_is_one_transaction_on_the_file_off_the_loop(tmp_path):
    """The money ledger stays on the file (eve-design writes it from its own process): reserve_spend through the
    mind's Db runs BEGIN IMMEDIATE ... COMMIT on the file, in a thread - never half on the working copy."""
    import asyncio

    from eve_mind.planner import off_loop, reserve_spend

    async def go():
        async with mind_env(tmp_path) as e:
            m, disk, mem = e.mind, [], []
            with m.db.lock:     # the writer thread may be mid-statement on the file: setting a trace callback then
                #                 waits on SQLite's connection mutex holding the GIL while the writer's callback
                #                 waits on the GIL - the 1c6d2963 gate's 19-min hang (pytest-954, this test last)
                m.db.disk.set_trace_callback(lambda s: disk.append((threading.current_thread().name, s)))
            m.db.mem.set_trace_callback(lambda s: mem.append(s))
            rid = await off_loop(m.db, reserve_spend, m.db, 1.0, "openrouter", "m", "job", 10, 10, 0.01)
            with m.db.lock:
                m.db.disk.set_trace_callback(None)
            m.db.mem.set_trace_callback(None)
            assert rid, rid
            mine = [(t, s) for t, s in disk if t != "eve-db-writer"]                     # not the journal's batches
            seq = [s for t, s in mine]
            assert seq[0] == "BEGIN IMMEDIATE" and "COMMIT" in seq and any(s.startswith("INSERT") for s in seq), seq
            assert all(t != threading.main_thread().name for t, s in mine), mine                # off the loop thread
            assert not any("spend" in s or s.startswith("BEGIN") for s in mem), mem          # nothing on the copy
            assert asyncio.get_running_loop()
    run(go())


def test_o40_a_reservation_that_meets_a_locked_file_leaves_the_writer_working(tmp_path):
    """eve-watch's O40 review of 725a83d: reserve_spend's BEGIN IMMEDIATE on a file another process holds raised
    'database is locked' and kept Db.lock, so the writer never committed again (every dispatch then db_unavailable).
    The failed BEGIN lets the lock go: the next write flushes and the next reservation goes through."""
    import sqlite3

    from eve_mind.db import Db, open_mem
    from eve_mind.planner import reserve_spend

    f = tmp_path / "m.db"
    disk = sqlite3.connect(f, isolation_level=None, timeout=0.2, check_same_thread=False)
    disk.execute("PRAGMA journal_mode=WAL")
    disk.execute("CREATE TABLE spend (id INTEGER PRIMARY KEY, ts INT, provider TEXT, model TEXT, job TEXT, "
                 "tokens_in INT, tokens_out INT, usd REAL, cached INT)")
    disk.execute("CREATE TABLE notes (x TEXT)")
    db = Db(open_mem(disk), disk)
    other = sqlite3.connect(f, isolation_level=None, timeout=0.2)
    other.execute("BEGIN IMMEDIATE")                      # another process holds the file's write lock
    out = []

    def reserve():                                        # in a worker thread, as off_loop runs it
        try:
            out.append(reserve_spend(db, 20.0, "openrouter", "m", "design:x", 1, 1, 0.5))
        except sqlite3.OperationalError as e:
            out.append(str(e))
    t = threading.Thread(target=reserve)
    t.start()
    t.join()
    other.execute("ROLLBACK")
    other.close()
    assert out == ["database is locked"], out
    db.execute("INSERT INTO notes VALUES ('after')")
    ok = run(db.flush(3.0))
    assert ok, (db.done, db.seq)                          # the writer is not wedged
    t = threading.Thread(target=reserve)
    t.start()
    t.join()
    assert isinstance(out[-1], int), out                  # and the ledger works again
    db.close()
    assert sqlite3.connect(f).execute("SELECT count(*) FROM notes").fetchone()[0] == 1
