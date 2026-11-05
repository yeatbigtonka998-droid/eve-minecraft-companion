"""H-recover (Hunter's rule after S28-DEATH-2): after any death outside a night run (the night has its own O54 recover),
she goes back for her drops - they despawn 5 minutes after the death - and walks the pile until nothing is left.
Skipped when the spot is outside her play area, in another dimension, or the death was lava / the void.

S28-DEATH-3/4 (18:19:56 / 18:20:20: she walked straight back into the mob that killed her, twice):
- before and during the walk: a hostile within DANGER_R of the death spot or within PATH_R of her path = she does not
  approach; she keeps SAFE_R from it with whatever she has and looks again every RETRY_S until the window ends;
- "My things are gone." only when she stands at the spot and sees no drops there, or the window ran out;
- a death during a recovery ends that recovery; a second death within TWICE_S: no recovery - she goes to the shelter
  (if no hostile is near it, else stays), says "I've died twice; I need you." and waits for his next line.
His orders (except stop) wait while she recovers."""
import asyncio
import math
import time

from .nightsurvival import NO_RECOVERY, dist, seg_dist

HURT_HP = 8                                           # mind.HURT_HP: the back-off line

WINDOW_S, PILE_R, AT_R = 300.0, 6.0, 3.0
DANGER_R, PATH_R, SAFE_R, RETRY_S = 12.0, 6.0, 16.0, 20.0
KILLER_R, ME_R = 16.0, 12.0                           # O220: the killer alive this near the pile; any hostile this near her
TWICE_S = 600.0


class Recover:
    def __init__(self, mind):
        self.mind, self.death, self.task, self.current = mind, None, None, None
        self.deaths = []                                  # monotonic times of deaths outside a night run
        self.needs_owner = False                          # after a second death: wait for his next line

    def on_event(self, p):
        m = mind = self.mind
        if mind.night.task and not mind.night.task.done():
            return                                        # the night run recovers its own deaths
        d = p.get("detail") or {}
        if p.get("kind") == "death":
            now = time.monotonic()
            self.death = {"pos": d.get("pos"), "dim": d.get("dim"), "cause": d.get("cause"), "at": now,
                          "twice": any(now - t <= TWICE_S for t in self.deaths),
                          "killer_id": d.get("killer_id", d.get("killer")), "killer_type": d.get("killer_type")}
            self.deaths = [t for t in self.deaths if now - t <= TWICE_S] + [now]
            if self.death["twice"]:
                self.needs_owner = True                   # before the cancel below: no queued order of his replays
            if self.task and not self.task.done():
                self.task.cancel()                        # S28-DEATH-4: never carry on into the same mob
                self.task = None                          # cancelled is not yet done: the respawn must not wait on it
        elif p.get("kind") == "respawn" and self.death and not (self.task and not self.task.done()):
            death, self.death = self.death, None
            m.recovering = True
            self.task = m.spawn(self.twice() if death["twice"] or self.needs_owner else self.run(death))

    def inv_total(self):
        return sum(int(s.get("count") or 0) for s in (self.mind.fresh_state() or {}).get("inventory", []))

    def threats(self, st, spot, death=None):
        """Hostiles near the death spot, near the straight path from her to it, or near her (O220: the back-off and
        the recovery are one plan - a hostile near her means it waits); O220 (2): the killer alive within KILLER_R of
        the pile, hostile-flagged or not."""
        me, death = st.get("pos"), death or {}
        out = [e for e in self.mind.hostiles(st) if isinstance(e.get("pos"), dict) and (
            dist(e["pos"], spot) <= DANGER_R or e.get("distance", 99) <= ME_R or
            (isinstance(me, dict) and seg_dist(e["pos"], me, spot) <= PATH_R))]
        kid, ktype = death.get("killer_id"), death.get("killer_type")
        out += [e for e in st.get("nearby_entities", []) if e not in out and isinstance(e.get("pos"), dict) and
                dist(e["pos"], spot) <= KILLER_R and ((kid is not None and e.get("id") == kid) or
                                                      (ktype and e.get("type") == ktype))]
        return out

    def held_off(self, st):
        """O220 (1): back-off / go_home / anything else of hers running, or HP at the back-off line: recovery waits and
        sends nothing (18:19:59-18:20:50: its walks were refused busy behind go_home, then walked into the zombies)."""
        m = self.mind
        return m.backing_off or m.autofight or m.open_action() is not None or 0 < (st.get("health") or 20) <= HURT_HP

    async def keep_away(self, st, foes):
        """SAFE_R from the nearest threat, straight away from it; nothing when already that far."""
        me, foe = st.get("pos"), min(foes, key=lambda e: e.get("distance", 99))
        if not isinstance(me, dict) or foe.get("distance", 99) >= SAFE_R:
            return
        dx, dz = me["x"] - foe["pos"]["x"], me["z"] - foe["pos"]["z"]
        n, step = math.hypot(dx, dz) or 1.0, SAFE_R + 2 - foe.get("distance", 0)
        await self.mind.do("walk_to", {"pos": {"x": me["x"] + step * dx / n, "y": me["y"], "z": me["z"] + step * dz / n},
                                       "tolerance": 2.0}, source="cli", wait=30)

    async def walk(self, pos, spot, tolerance):
        """A walk that is cancelled the moment a threat shows up near the spot or the path. True = arrived."""
        m = self.mind
        r = await m.do("walk_to", {"pos": dict(pos), "tolerance": tolerance}, source="cli")
        aid = r.get("action_id")
        if not aid:
            return False
        while m.status_of(aid) not in ("ok", "failed", "timeout", "rejected", "cancelled", "uncertain"):
            if m.backing_off:
                return False                              # the back-off cancels it itself
            if self.threats(m.fresh_state() or {}, spot, self.current):
                await m.cancel_active("H-recover: a hostile near my things", source="mind")
                return False
            await asyncio.sleep(0.5)
        return m.status_of(aid) == "ok"

    async def run(self, death):
        m = self.mind
        try:
            await asyncio.sleep(1.0)                      # the respawn frame first
            st = m.fresh_state() or {}
            pos = death["pos"]
            why = ("no death position" if not isinstance(pos, dict) else
                   "another dimension" if death["dim"] and death["dim"] != st.get("dim") else
                   f"died to {death['cause']}" if death["cause"] in NO_RECOVERY else
                   m.out_of_play(pos, st))
            if why:
                await m.say("My things are gone.")
                return {"ok": False, "why": why}
            spot, self.current = {a: float(pos[a]) for a in "xyz"}, death
            await m.say("Going back for my things.")
            before, deadline, waited = self.inv_total(), death["at"] + WINDOW_S, False
            while time.monotonic() < deadline and not m.stopped:
                st = m.fresh_state() or {}
                if self.held_off(st):
                    await asyncio.sleep(1.0)
                    continue
                foes = self.threats(st, spot, death)
                if foes:                                  # never into the mob: keep away, look again
                    if not waited:
                        waited = True
                        await m.say("There are mobs at my things - I'll wait.")
                    await self.keep_away(st, foes)
                    await asyncio.sleep(RETRY_S)
                    continue
                me = st.get("pos")
                if not isinstance(me, dict) or dist(me, spot) > AT_R:
                    if not await self.walk(spot, spot, 1.0):
                        await asyncio.sleep(2.0)
                    continue
                pile = [e for e in st.get("nearby_entities", []) if e.get("type") == "minecraft:item"
                        and isinstance(e.get("pos"), dict) and dist(e["pos"], spot) <= PILE_R]
                if not pile:
                    break                                 # at the spot and nothing there: truly gone (or all back)
                for e in sorted(pile, key=lambda e: e.get("distance", 99)):   # pickup is on contact
                    if not await self.walk(e["pos"], spot, 0.5):
                        break
                await asyncio.sleep(0.5)
            got = self.inv_total() - before
            await m.say(f"Got my things back: {got} items." if got > 0 else "My things are gone.")
            if got > 0:
                t0 = time.monotonic()
                while self.held_off(m.fresh_state() or {}) and time.monotonic() - t0 < 30:
                    await asyncio.sleep(1.0)              # O220: never "refused busy"
                await m.do("equip_best", {}, source="cli", wait=10)
            return {"ok": got > 0, "got": got}
        finally:
            self.finish()

    async def twice(self):
        """S28-DEATH-4: the second death within TWICE_S - no recovery; somewhere safe, then his call."""
        m = self.mind
        try:
            await asyncio.sleep(1.0)
            home = (m.env.get("home") or {}).get("center")
            st = m.fresh_state() or {}
            if isinstance(home, dict) and not any(isinstance(e.get("pos"), dict) and dist(e["pos"], home) <= DANGER_R
                                                  for e in m.hostiles(st)):
                await m.do("go_home", {}, source="cli", wait=60)
            await m.say("I've died twice; I need you.")
        finally:
            self.finish()

    def finish(self):
        m = self.mind
        m.recovering = False
        if m.deferred and not self.needs_owner:          # his order, queued behind the recovery
            args, m.deferred = m.deferred, None
            m.spawn(m.owner_text(*args))
