"""I23c/O82 stage 1: the event loop never waits on the disk.

The mind works on an in-memory copy of its SQLite DB (loaded from the file at start, before anything is served); every
write is also queued, in order, for ONE writer thread that replays it on the file in batched transactions - the file is
the journal other processes read. Writes made under `durable()` (STOPPED, the action row before a dispatch) commit with
synchronous=FULL and the caller awaits `flush()` with a bound; the journal commits with synchronous=NORMAL.

The `spend` table is the exception: other processes (eve-design) write the shared money ledger, so every statement on it
runs on the file itself (`ledger`), and a BEGIN ... COMMIT there holds the file for the whole transaction. Ledger calls
run off the loop thread (planner.off_loop), one whole transaction per worker call."""
import asyncio
import atexit
import collections
import contextlib
import logging
import re
import sqlite3
import threading
import time

log = logging.getLogger("eve.db")
WRITE = re.compile(r"^\s*(?:INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER)\b", re.I)
SPEND = re.compile(r"\bspend\b", re.I)
BATCH = 500


class Ledger:
    """The money ledger on the file itself: a transaction holds the file lock from BEGIN until the file has no open
    transaction again - a BEGIN that failed (the file locked by another process: CP-24), a COMMIT that went through, or
    the ROLLBACK. Only the thread that took it lets it go (eve-watch's O40 review of 725a83d: a failed BEGIN IMMEDIATE
    kept the lock and wedged the writer for good)."""
    def __init__(self, db):
        self.db, self.owner = db, None

    def execute(self, sql, params=()):
        if sql.lstrip()[:5].upper() == "BEGIN" and self.owner != threading.get_ident():
            self.db.lock.acquire()
            self.owner = threading.get_ident()
        try:
            with self.db.lock:
                return self.db.disk.execute(sql, params)
        finally:
            if self.owner == threading.get_ident() and not self.db.disk.in_transaction:
                self.owner = None
                self.db.lock.release()


class Db:
    def __init__(self, mem, disk):
        self.mem, self.disk = mem, disk
        self.lock = threading.RLock()                     # the file connection: writer thread vs the ledger
        self.cv = threading.Condition()
        self.queue = collections.deque()
        self.seq = self.done = 0                          # last queued / last committed write
        self.retry_until = 0.0                            # monotonic time the writer's pending retry runs
        self.durable_depth, self.closing, self.failures = 0, False, 0
        self.ledger = Ledger(self)
        self.writer = threading.Thread(target=self._run, name="eve-db-writer", daemon=True)
        self.writer.start()
        atexit.register(self.close)

    # ---- the loop side: memory only, never the disk
    def execute(self, sql, params=()):
        if SPEND.search(sql):
            return self.ledger.execute(sql, params)
        cur = self.mem.execute(sql, params)
        if WRITE.match(sql):
            self._queue(sql, params, False)
        return cur

    def executemany(self, sql, rows):
        rows = list(rows)
        if SPEND.search(sql):
            with self.lock:
                return self.disk.executemany(sql, rows)
        cur = self.mem.executemany(sql, rows)
        if WRITE.match(sql):
            self._queue(sql, rows, True)
        return cur

    def __getattr__(self, name):                         # row_factory, set_trace_callback, ... of the working copy
        return getattr(self.mem, name)

    @contextlib.contextmanager
    def durable(self):
        self.durable_depth += 1
        try:
            yield
        finally:
            self.durable_depth -= 1

    def _queue(self, sql, params, many):
        with self.cv:
            self.seq += 1
            self.queue.append((self.seq, sql, params, many, self.durable_depth > 0))
            self.cv.notify()

    async def flush(self, timeout):
        """True once everything queued so far is on the file (bounded; the writer keeps retrying after a False)."""
        target, deadline, last = self.seq, time.monotonic() + timeout, None
        while self.done < target:
            now = time.monotonic()
            if now >= deadline:                       # S93-ci-1: the retry already scheduled when the bound ran out is
                if last is None:                      #   waited for (its backoff is <= 2 s); no later one - under a
                    last = self.retry_until + 0.1     #   held lock retry_until keeps moving and this would never end
                if now >= last:
                    return False
            await asyncio.sleep(0.005)
        return True

    def close(self):
        """Drain the queue to the file (a restart reads the file), then close both connections."""
        if self.closing:
            return
        with self.cv:
            self.closing = True
            self.cv.notify()
        self.writer.join(60)
        with self.lock:
            self.disk.close()
        self.mem.close()

    # ---- the writer thread: the only code that writes the file (besides the ledger)
    def _run(self):
        while True:
            with self.cv:
                while not self.queue and not self.closing:
                    self.cv.wait()
                if not self.queue:
                    return
                batch = [self.queue.popleft() for _ in range(min(BATCH, len(self.queue)))]
            self._commit(batch)
            with self.cv:
                self.done = batch[-1][0]

    def _commit(self, batch):
        durable, wait = any(b[4] for b in batch), 0.05
        while True:
            try:
                with self.lock:
                    if durable:
                        self.disk.execute("PRAGMA synchronous = FULL")
                    self.disk.execute("BEGIN")
                    for _, sql, params, many, _ in batch:
                        (self.disk.executemany if many else self.disk.execute)(sql, params)
                    self.disk.execute("COMMIT")
                    if durable:
                        self.disk.execute("PRAGMA synchronous = NORMAL")
                return
            except sqlite3.OperationalError as e:             # locked / busy / I/O: the same batch again, in order
                self._rollback()
                self.failures += 1
                log.warning("db writer: %s; retrying %d statements in %.2f s", e, len(batch), wait)
                self.retry_until = time.monotonic() + wait
                time.sleep(wait)
                wait = min(wait * 2, 2.0)
            except sqlite3.Error as e:                        # a statement the file refuses: apply the rest one by one
                self._rollback()
                self.failures += 1
                log.error("db writer: batch refused (%s); applying statements one by one", e)
                for _, sql, params, many, _ in batch:
                    try:
                        with self.lock:
                            (self.disk.executemany if many else self.disk.execute)(sql, params)
                    except sqlite3.Error as e2:
                        log.error("db writer: dropped %r: %s", sql[:80], e2)
                return

    def _rollback(self):
        with contextlib.suppress(sqlite3.Error), self.lock:
            if self.disk.in_transaction:
                self.disk.execute("ROLLBACK")
            self.disk.execute("PRAGMA synchronous = NORMAL")


def open_mem(disk):
    """The working copy: the whole file, loaded before the loop serves anything (STOPPED included)."""
    mem = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    disk.backup(mem)
    mem.row_factory = sqlite3.Row
    return mem
