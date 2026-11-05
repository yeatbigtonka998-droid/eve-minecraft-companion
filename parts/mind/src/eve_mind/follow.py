"""O111/O121 "follow me": his position is sense's owner.pos (<= 3 s old), else body's nearby list, else the last place
she knew him at (she keeps walking there and says "lost you" once - Hunter: "even out of view she should still follow").
She walks beside him again once he has moved more than STEP blocks from her last target and no action is open; while
following, explore_radius is measured from him (S38: she goes where he goes; zones still govern break and place). At
dusk she says one line and keeps following while he is within NIGHT_NEAR blocks; farther at night she heads home.
Ends only on STOP / stop / stay, a new order, his leaving the server, or his changing dimension (she asks) - never on
being out of view, no time cap. An end is a flag the loop reads - the task is never cancelled mid-dispatch (O81a)."""
import asyncio
import math
import re
import time

from .nightsurvival import night

STEP, TICK, NIGHT_NEAR = 4.0, 0.5, 32.0          # O126: he may be up to 32 blocks away at night
BOAT = re.compile(r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:please\s+)?(?:follow\s+me\s+(?:by|in\s+(?:a|your))\s+boat|"
                  r"get\s+in\s+your\s+boat|come\s+across\s+the\s+water)[\s.!]*$", re.I)
TERMINAL = ("ok", "failed", "timeout", "rejected", "cancelled", "uncertain")
NO_WATER_RETRY_S = 10.0                          # H-boat: no launch cell in reach - look again this often


def afloat(ent):
    """H-boat (body's additive nearby_entities fields): he rides a boat or is in water."""
    return bool(ent) and ("boat" in str(ent.get("vehicle") or "") or ent.get("in_water") is True)


class Follow:
    def __init__(self, mind):
        self.mind, self.task, self.current = mind, None, None

    def running(self):
        return self.current is not None and self.current["status"] == "running"

    def start(self, sender, source, boat=False):
        self.end("restarted")
        self.current = r = {"sender": sender, "walks": 0, "status": "running", "reason": "", "boat": boat}
        self.task = asyncio.get_running_loop().create_task(self.run(r, source))
        return {"ok": True, "following": sender}

    def end(self, why):
        """Ends a running follow (True); the walk it started is the caller's to cancel."""
        if not self.running():
            return False
        self.current.update(status="ended", reason=why)
        return True

    async def run(self, r, source):
        m, last, known = self.mind, None, None
        while r["status"] == "running":
            st = m.fresh_state() or {}
            if m.stopped:
                return self.done(r, "stopped")
            if m.owner_live.get(r["sender"], {}).get("online") is False:
                await m.say("You left, so I've stopped following.")
                return self.done(r, "offline")
            dark, at = night(st.get("time_of_day")), m.owner_pos(r["sender"])
            if at is not None and st.get("dim") and at.get("dim") != st.get("dim"):
                await m.say("You went to another dimension - I can't follow you there. Should I wait here or go home?")
                return self.done(r, "dimension")
            if at is not None:
                known, r["lost_said"] = at, False
            elif known is not None and not r.get("lost_said"):
                r["lost_said"] = True
                await m.say("Lost you - heading to where you were.")
            if dark and not r.get("dusk_said"):
                r["dusk_said"] = True
                await m.say("It's getting dark - I'll stay close to you.")
            if dark and not (known is not None and self.near(known, st)):   # S38: at night only while he is close
                await m.say("It's dark and you're too far away - I'm heading home.")
                self.done(r, "night_far")
                await m.home(f"{source}:follow")
                return r
            ent = m.owner_entity(r["sender"])
            if ent is None or not afloat(ent):
                r["no_water_at"] = None
            elif "follow_boat" in m.registry.skills and m.open_action() is None and not r.get("no_boat") and                     time.monotonic() - (r.get("no_water_at") or -1e9) >= NO_WATER_RETRY_S:
                await self.by_boat(r, ent, source)      # H-boat: he is on the water - her boat, then on foot again
                last = None
                continue
            if known is not None and m.open_action() is None and (
                    last is None or math.dist([known[a] for a in "xyz"], [last[a] for a in "xyz"]) > STEP):
                res = await m.walk_near(known, source, around_owner=True)
                if res.get("code") == "out_of_bounds":          # walk_near said why
                    return self.done(r, "out_of_bounds")
                if res.get("ok"):
                    last, r["walks"] = known, r["walks"] + 1
            await asyncio.sleep(TICK)
        return r

    async def by_boat(self, r, ent, source):
        """H-boat: body's follow_boat behind him while he is on the water (it lands, gets out and picks the boat up
        itself); one line when it launches and one when it heads for the shore; no boat / no water: the shore walk."""
        m = self.mind
        res = await m.do("follow_boat", {"entity_id": ent["id"]}, source=source)   # no block cells to grant
        aid = res.get("action_id")
        if not aid:
            return
        await m.say("Getting my boat out.")
        landing = False
        while m.status_of(aid) not in TERMINAL:
            if not landing and "land" in str(m.progress_note.get(aid, "")):
                landing = True
                await m.say("Heading for the shore.")
            await asyncio.sleep(TICK)
        row = m.action(aid)
        code = row["code"] if row else None
        if row and row["status"] == "ok":
            r["boats"] = r.get("boats", 0) + 1
        elif code == "no_boat":
            r["no_boat"] = True
            await m.say("I don't have a boat - I'll follow along the shore.")
        elif code == "no_water":
            r["no_water_at"] = time.monotonic()
            await m.say("I can't get on the water from here - I'll follow along the shore.")
        elif code == "owner_gone":
            await m.say("Lost you on the water.")

    @staticmethod
    def near(at, st):
        """He is within NIGHT_NEAR blocks of her: body's own distance when it gave one, else from her position."""
        if isinstance(at.get("distance"), (int, float)):
            return at["distance"] <= NIGHT_NEAR
        pos = st.get("pos")
        return isinstance(pos, dict) and math.dist([at[a] for a in "xyz"], [pos[a] for a in "xyz"]) <= NIGHT_NEAR

    def done(self, r, why):
        r.update(status="ended", reason=why)
        try:
            self.mind.event("mind", "follow", {"sender": r["sender"], "walks": r["walks"], "reason": why})
        except Exception:                       # shutting down: the DB may be closed
            self.mind.metrics["db_errors"] += 1
        return r
