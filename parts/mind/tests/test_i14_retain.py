"""I14: `eve retain <minutes>` keeps state rows for a whole run (the 45L lava sample was pruned after 10 min)."""
import time

from conftest import mind_env, run, until


def test_retain_keeps_old_state_rows_and_the_default_prunes_them(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            m = e.mind
            assert not (await m.cli("retain", {"minutes": 5}))["ok"]             # below the default is refused
            old = int(time.time() * 1000) - 20 * 60_000

            def prune_with(minutes):
                m.db.execute("INSERT INTO observations (world_id, session_id, tick, ts, kind, payload) VALUES "
                             "('w', 's', 1, ?, 'state', '{}')", (old,))
                m.last_prune = 0.0                                                # next state frame prunes

            assert (await m.cli("retain", {"minutes": 30}))["ok"]
            prune_with(30)
            await until(lambda: m.last_prune > 0)
            assert m.db.execute("SELECT count(*) FROM observations WHERE ts=?", (old,)).fetchone()[0] == 1
            assert (await m.cli("retain", {"minutes": 10}))["ok"]
            prune_with(10)
            await until(lambda: m.last_prune > 0)
            assert m.db.execute("SELECT count(*) FROM observations WHERE ts=?", (old,)).fetchone()[0] == 0
    run(go())


def test_i23_a_retain_drop_prunes_in_bounded_chunks_on_the_ts_index(tmp_path):
    """I23 (lane D): after retain 300 -> 10 the backlog goes in PRUNE_CHUNK statements, one per state frame."""
    from eve_mind.mind import PRUNE_CHUNK

    async def go():
        async with mind_env(tmp_path) as e:
            m = e.mind
            old = int(time.time() * 1000) - 60 * 60_000
            m.db.executemany("INSERT INTO observations (world_id, session_id, tick, ts, kind, payload) VALUES "
                             "('w', 's', 1, ?, 'state', '{}')", [(old + i,) for i in range(2 * PRUNE_CHUNK + 200)])
            plan = " ".join(r[-1] for r in m.db.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM observations WHERE ts < ? AND pinned=0 LIMIT ?", (old, 1)))
            assert "obs_ts" in plan, plan
            m.last_prune = 0.0
            await until(lambda: m.prune_backlog)                                  # first frame: one full chunk
            left = m.db.execute("SELECT count(*) FROM observations WHERE ts < ?", (old + 10**6,)).fetchone()[0]
            assert left in (PRUNE_CHUNK + 200, 200)                               # whole chunks, not all at once
            await until(lambda: not m.db.execute("SELECT count(*) FROM observations WHERE ts < ?",
                                                 (old + 10**6,)).fetchone()[0])   # caught up over later frames
            assert not m.prune_backlog
    run(go())
