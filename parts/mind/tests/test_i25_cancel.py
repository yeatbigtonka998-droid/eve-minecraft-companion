"""I25 (CP-40 a14): after the mind cancels a hung skill, the next dispatch - go_home - is never refused busy on the
mind's own cancel: the body ended the skill before acking the stop, and the runner's next call came in between."""
import time

from conftest import mind_env, run


def test_i25_go_home_after_cancelling_a_hung_skill_is_accepted_within_5_s(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            m, b = e.mind, e.body
            b.walk_s = 120                                    # a hung skill: no result until cancelled
            b.late_result_by_skill = {"stop": 0.6}           # a14 order: the skill ends before the stop's result
            hung = m.spawn(m.do("walk_to", {"pos": {"x": 9.5, "y": 64.0, "z": 0.5}}, source="cli", wait=30))
            t0 = time.monotonic()
            while m.open_action() is None and time.monotonic() - t0 < 3:
                await __import__("asyncio").sleep(0.02)
            await m.cancel_active("test: hung skill", source="cli")
            r = await hung                                   # the hung walk ends cancelled; the stop is still open
            assert r.get("status") == "cancelled" or r.get("code") == "cancelled", r
            assert m.open_action() is not None and m.open_action()["skill"] == "stop"
            b.walk_s = 0.05
            t1 = time.monotonic()
            home = await m.do("go_home", {}, source="cli", wait=5)
            assert home.get("ok") and home.get("error") != "busy", home          # a14: refused busy
            assert time.monotonic() - t1 < 5
    run(go())


def test_i25_an_ignored_cancel_is_never_busy_it_settles_or_escalates(tmp_path):
    """The body ignores the cancel: within the bound the supervisor escalates (hard stop), so go_home gets a clear
    "stopped", never a silent "busy" against our own cancel."""
    async def go():
        async with mind_env(tmp_path) as e:
            m, b = e.mind, e.body
            b.walk_s, b.ignore_cancel = 120, True
            m.spawn(m.do("walk_to", {"pos": {"x": 9.5, "y": 64.0, "z": 0.5}}, source="cli", wait=30))
            t0 = time.monotonic()
            while m.open_action() is None and time.monotonic() - t0 < 3:
                await __import__("asyncio").sleep(0.02)
            await m.cancel_active("test: hung skill ignores cancel", source="cli")
            t1 = time.monotonic()
            home = await m.do("go_home", {}, source="cli", wait=5)
            assert home.get("error") != "busy", home
            assert time.monotonic() - t1 < 6.5
    run(go())


def test_i25c_a_runner_waits_once_on_our_open_stop_instead_of_spinning(tmp_path):
    """I25c (body's a14 read: 25 refusals between our stop and its ack): every runner dispatches through Mind.do, so an
    a14-style loop - next call on each refusal, no wait - makes ONE attempt that waits for our stop, and is refused 0
    times."""
    import asyncio

    async def go():
        async with mind_env(tmp_path) as e:
            m, b = e.mind, e.body
            b.walk_s, b.late_result_by_skill = 120, {"stop": 0.3}
            hung = m.spawn(m.do("walk_to", {"pos": {"x": 9.5, "y": 64.0, "z": 0.5}}, source="cli", wait=30))
            t0 = time.monotonic()
            while m.open_action() is None and time.monotonic() - t0 < 3:
                await asyncio.sleep(0.02)
            await m.cancel_active("test: hung skill", source="cli")
            await hung
            assert m.open_action()["skill"] == "stop"            # our stop is open: a14's 200 ms window
            b.walk_s, attempts = 0.05, 0
            for _ in range(25):
                attempts += 1
                if (await m.do("go_home", {}, source="cli", wait=5)).get("ok"):
                    break
            assert attempts == 1, attempts
            assert not m.db.execute("SELECT count(*) FROM events WHERE kind='dispatch_refused'").fetchone()[0]
    run(go())


def test_o81a_a_cancel_in_the_flush_window_ends_the_request_unsent(tmp_path):
    """O81a (eve-watch's run 3 under lane C load): dispatch awaits the row's flush before sending; a cancel that landed
    in that window sent its stop first and the walk_to went out 16 ms later, overtook it, and the supervisor hard-
    stopped 3.5 s after. Forced here with a slow flush: the cancel ends the unsent request - nothing reaches the body,
    no escalation, and the next dispatch is not busy."""
    import asyncio

    async def go():
        async with mind_env(tmp_path) as e:
            m, b = e.mind, e.body
            b.walk_s = 120
            fast = m.db.flush

            async def flush(timeout):                            # a loaded box: the row takes 0.5 s to the file
                await asyncio.sleep(0.5)
                return await fast(timeout)
            m.db.flush = flush
            walk = m.spawn(m.do("walk_to", {"pos": {"x": 9.5, "y": 64.0, "z": 0.5}}, source="cli", wait=30))
            t0 = time.monotonic()
            while (m.open_action() is None or m.open_action()["status"] != "pending") and time.monotonic() - t0 < 3:
                await asyncio.sleep(0.01)
            await m.cancel_active("test: cancel in the flush window", source="cli")
            r = await walk
            assert r["status"] == "cancelled" and r["code"] == "cancelled", r
            assert not any(a["skill"] == "walk_to" for a in b.actions.values()), b.actions   # never sent
            task = m.db.execute("SELECT status FROM tasks WHERE task_id=?", (r["task_id"],)).fetchone()
            assert task["status"] == "cancelled", dict(task)
            await asyncio.sleep(1.0)
            assert not m.stopped and r["action_id"] not in m.cancelling                     # nothing to supervise
            m.db.flush = fast
            b.walk_s = 0.05
            home = await m.do("go_home", {}, source="cli", wait=5)
            assert home.get("ok") and home.get("status") == "ok", home
    run(go())
