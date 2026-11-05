"""O53a/b: a creeper that keeps following after the body's creeper_flee reflex ends. The body resumes the paused walk,
which drove her back into the creeper (combat CP-29). Fixed rule, no model, for EVERY open walk_to (plan step, task or
direct `eve do`): on `reflex creeper_flee end`, if that creeper (the reflex start trigger's entity_id) is alive within
12 and still following - it moved toward her over the last ~2 s, body says it is closing, or this is a second flee
from it within 30 s (lane C a13: she outruns it during each flee, so her distance alone grows) - cancel the walk, then
  - with a bow in the hotbar and arrows: fight_ranged{entity_id}, re-dispatched after each return while it is alive,
    still following and beyond 4 (O53g; the skill itself only shoots beyond 8.5), else
  - hold until the creeper has stopped closing for 3 s, is beyond 16 or gone (60 s at most),
and dispatch the same walk again in the same task. A waiter on the cancelled walk follows `resuming` to the new one."""
import asyncio
import collections
import json
import math
import time

NEAR, CLEAR, CLOSING_S, CALM_S, HOLD_MAX_S, REPEAT_S = 12.0, 16.0, 2.0, 3.0, 90.0, 30.0
SHOOT_MIN, SHOT_CAP = 4.0, 12               # O53g: keep shooting beyond 4 blocks; 12 calls per creeper at most
FLEE_RANGE = 7.0                            # O72a: a flee that ends with the creeper this close has not shaken it
FINAL_SHOT_CODES = ("no_weapon", "no_ammo", "out_of_arrows", "protected_entity", "no_target", "zone_denied")
SELF_DEFENCE = "reflex:creeper"             # O53m2: the only non-owner source manual mode admits
ARROWS = ("minecraft:arrow", "minecraft:spectral_arrow", "minecraft:tipped_arrow")


class CreeperGuard:
    def __init__(self, m):
        self.m = m
        self.fleeing = None                       # entity_id of the creeper the running creeper_flee reflex flees
        self.track = {}                           # entity_id -> deque[(monotonic, distance, creeper pos, eve pos)]
        self.flee_ends = collections.defaultdict(collections.deque)   # entity_id -> monotonic times of flee ends
        self.shots = collections.Counter()        # entity_id -> fight_ranged dispatched
        self.resuming = {}                        # cancelled walk action_id -> Future(new action_id or None)
        self.engaged = set()                      # creepers being shot or held right now (O53m2 gate)
        self.busy = set()                         # creepers an after_flee is handling (O72i start + end)

    def on_reflex(self, d):
        if d.get("reflex") != "creeper_flee":
            return
        if d.get("phase") == "start":
            cid = (d.get("trigger") or {}).get("entity_id")
            if cid is not None:
                self.fleeing = cid
                self.track.setdefault(cid, collections.deque(maxlen=400))
                ends = self.flee_ends.get(cid)
                # O72i (a33: idle in a corner, the second flee ended only after the blast): idle, and this creeper's
                # last flee ended within 30 s - it came back, so it is following: act at the start, not the end
                if ends and time.monotonic() - ends[-1] <= REPEAT_S and self.m.open_action() is None:
                    dist = (d.get("trigger") or {}).get("distance")
                    self.m.spawn(self.after_flee(cid, True, dist, True))
        elif d.get("phase") == "end":
            # body f769eaf nests creeper_id / creeper_distance / creeper_closing in the end event's own detail
            inner = d.get("detail") if isinstance(d.get("detail"), dict) else {}
            cid = inner.get("creeper_id", d.get("creeper_id", self.fleeing))
            self.fleeing = None
            if cid is None:
                return
            self.track.setdefault(cid, collections.deque(maxlen=400))
            ends, now = self.flee_ends[cid], time.monotonic()
            ends.append(now)
            while ends and now - ends[0] > REPEAT_S:
                ends.popleft()
            closing = inner.get("creeper_closing", d.get("creeper_closing")) is True
            dist = inner.get("creeper_distance", d.get("creeper_distance"))
            # O72a (combat a28: a fast creeper corners her on the FIRST flee, no "safe" end, no second approach):
            # a flee that ends cornered, or with the creeper still in flee range, already shows it is following
            cornered = str(d.get("result") or "").startswith("cornered")
            near = isinstance(dist, (int, float)) and dist <= FLEE_RANGE
            self.m.spawn(self.after_flee(cid, closing or len(ends) >= 2 or cornered or near, dist, cornered))

    def on_state(self, st):
        now, me = time.monotonic(), st.get("pos")
        for cid, q in self.track.items():
            e = next((x for x in st.get("nearby_entities", []) if x.get("id") == cid), None)
            q.append((now, e["distance"], e.get("pos"), me) if e else (now, None, None, me))

    def distance(self, cid):
        q = self.track.get(cid)
        return q[-1][1] if q else None

    def closing(self, cid):
        """The creeper itself moved toward Eve over the last ~2 s: its old position is farther from where she is now
        than its current one (lane C a13: while she flees her distance grows, so the distance alone never drops)."""
        q = [x for x in self.track.get(cid, ()) if x[2] and x[3]]
        if len(q) < 2:
            return False
        t, _, cpos, me = q[-1]
        old = next((x for x in reversed(q) if x[0] <= t - CLOSING_S), q[0])
        at = lambda p: (p["x"], p["y"], p["z"])  # noqa: E731
        return math.dist(at(old[2]), at(me)) - math.dist(at(cpos), at(me)) > 0.5

    def bow_ready(self):
        inv = (self.m.state or {}).get("inventory", [])
        return any(x.get("item") == "minecraft:bow" and x.get("slot", 99) <= 8 for x in inv) and \
            any(x.get("item") in ARROWS for x in inv)

    def admits(self, skill, params):
        """O53m2 (manual mode forbids initiative, not survival): source reflex:creeper may run fight_ranged only, only
        on a creeper this guard is engaging as following, alive within 16, with a bow in the hotbar and arrows."""
        cid = (params or {}).get("entity_id")
        d = self.distance(cid)
        return skill == "fight_ranged" and set(params) == {"entity_id"} and cid in self.engaged and             d is not None and d <= CLEAR and self.bow_ready()

    async def after_flee(self, cid, following=False, body_distance=None, cornered=False):
        """One handling per creeper at a time: the O72i start trigger and that flee's end must not both engage."""
        if cid in self.busy:
            return
        self.busy.add(cid)
        try:
            await self._after_flee(cid, following, body_distance, cornered)
        finally:
            self.busy.discard(cid)

    async def _after_flee(self, cid, following=False, body_distance=None, cornered=False):
        m = self.m
        cur = m.open_action()
        d = self.distance(cid)
        d = d if d is not None else body_distance
        if m.stopped or d is None or d > NEAR or not (self.closing(cid) or following):
            return
        if cur is None:                             # O53l (combat a25): idle - nothing to cancel or resume
            m.event("mind", "creeper_follow", {"entity_id": cid, "distance": d, "walk": None,
                                               "plan": "shoot" if self.bow_ready() else "hold"})
            if self.bow_ready():                    # else hold: she stays idle, the reflexes have it
                await self.engage(cid, SELF_DEFENCE, None, m.epoch, cornered)
            self.track.pop(cid, None)
            return
        if cur["skill"] != "walk_to":
            return
        old, epoch = cur["action_id"], m.epoch
        task = m.db.execute("SELECT source FROM tasks WHERE task_id=?", (cur["task_id"],)).fetchone()
        source = task["source"] if task else "cli"
        fut = self.resuming[old] = asyncio.get_running_loop().create_future()
        new = None
        try:
            m.event("mind", "creeper_follow", {"entity_id": cid, "distance": d, "walk": old,
                                               "plan": "shoot" if self.bow_ready() else "hold"})
            stop = await m.cancel_active(f"creeper {cid} still following after creeper_flee", source="mind")
            for aid in (old, stop.get("action_id")):    # a14: shooting before our own stop settled was refused busy
                if aid in m.done:
                    try:
                        await asyncio.wait_for(m.done[aid].wait(), 5)
                    except asyncio.TimeoutError:
                        pass
            await self.engage(cid, source, cur["task_id"], epoch, cornered)
            if not m.stopped and m.epoch == epoch:
                r = await m.do("walk_to", json.loads(cur["params"]), source=source, task_id=cur["task_id"],
                               epoch=epoch)
                new = r.get("action_id") if r.get("ok") else None
                m.db.execute("UPDATE actions SET note=? WHERE action_id=?",
                             (f"creeper_follow: cancelled while creeper {cid} closed in; resumed as {new}", old))
        finally:
            fut.set_result(new)
            self.track.pop(cid, None)

    async def engage(self, cid, source, task_id, epoch, cornered=False):
        """Shoot (O53g) or hold until the creeper is dead, beyond 16, or has stopped following for 3 s."""
        m = self.m
        self.engaged.add(cid)
        try:
            t0, calm, first, shooting = time.monotonic(), None, True, True
            while time.monotonic() - t0 < HOLD_MAX_S and not m.stopped and m.epoch == epoch:
                d = self.distance(cid)
                if d is None or d > CLEAR:
                    break
                if shooting and self.bow_ready() and (d > SHOOT_MIN or cornered) and (
                        first or self.closing(cid)) and self.shots[cid] < SHOT_CAP:
                    # O53g: one call lands ~1 hit of the 3-4 a creeper takes - keep shooting while it is alive, still
                    # following (the flee verdict for the first call, its own approach after), arrows remain and it
                    # is beyond 4; closer than that, or out of arrows, the reflexes have it
                    sk = m.registry.skills["fight_ranged"]
                    r = await m.do("fight_ranged", {"entity_id": cid}, source=source, task_id=task_id,
                                   epoch=epoch, wait=sk["timeout_ms"] / 1000 + m.cfg.result_grace_s)
                    if r.get("ok"):
                        first = False
                        self.shots[cid] += 1
                        m.db.execute("UPDATE actions SET reconcile_note=? WHERE action_id=?",   # re-shots, not retries
                                     ("retry_exempt: creeper_follow re-shot", r["action_id"]))
                        if r.get("code") in FINAL_SHOT_CODES:
                            shooting = False                 # no bow/arrows, protected, gone, zone: hold
                        elif r.get("code") == "too_close":
                            await asyncio.sleep(0.5)
                    elif r.get("error") in ("busy", "stale_state", "reconcile_pending"):
                        await asyncio.sleep(0.25)            # transient: try again shortly
                    else:
                        shooting = False                     # refused for good (mode, zone, retry limit): hold
                    continue
                if self.closing(cid):
                    calm = None
                elif calm is None:
                    calm = time.monotonic()
                elif time.monotonic() - calm >= CALM_S:
                    break
                await asyncio.sleep(0.25)
        finally:
            self.engaged.discard(cid)
