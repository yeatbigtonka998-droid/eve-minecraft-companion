"""O175 'keep going' (learning's replay: it became control.resume and the body re-ran the last fight): a fight going on
-> she keeps fighting; a stopped progression run -> that run again; nothing -> she says so and does nothing."""
import time

from conftest import mind_env, run


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t, *a, **k):
        out.append(t)
        return await orig(t, *a, **k)
    e.mind.say = say
    return out


def test_keep_going_three_states(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            sent = []
            orig_send = e.mind.send_body

            async def send_body(kind, payload, *a, **k):
                sent.append(kind)
                return await orig_send(kind, payload, *a, **k)
            e.mind.send_body = send_body

            r = await e.mind._owner_text("keep going", "Silvershark3600", "owner")       # nothing on
            assert r == {"ok": True, "continued": None} and said[-1] == "There's nothing to continue."
            assert "control.resume" not in sent

            e.mind.autofight = {"paused": [], "at": time.monotonic(), "trigger": "zombie"}   # a fight going on
            r = await e.mind._owner_text("Eve, keep going!", "Silvershark3600", "owner")
            assert r == {"ok": True, "continued": "fight"} and said[-1] == "Still on it."
            assert "control.resume" not in sent
            e.mind.autofight = None

            e.mind.last_stop = {"milestone": "iron", "cmd": "mine 8 iron ore", "why": "no pickaxe.",   # a stopped run
                                "target": "iron", "at": time.monotonic()}
            got = []

            async def retry(mind, stop, sender, source):
                got.append(stop["target"])
                return {"ok": True, "retried": stop["target"]}
            from eve_mind import progress
            orig_retry, progress.retry = progress.retry, retry
            try:
                r = await e.mind._owner_text("keep going", "Silvershark3600", "owner")
            finally:
                progress.retry = orig_retry
            assert r == {"ok": True, "retried": "iron"} and got == ["iron"] and "control.resume" not in sent

            e.mind.last_stop, gen = None, e.mind.progress_gen     # his "keep going" does not end a running run
            await e.mind.owner_text("keep going", "Silvershark3600", "owner")
            assert e.mind.progress_gen == gen and said[-1] == "There's nothing to continue."
    run(go())
