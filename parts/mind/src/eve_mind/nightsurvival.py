"""O54: `objective night_survival` - one night, dusk to dawn, deterministic (same pattern as iron_kit; no model).

She waits for dusk, goes to the shelter (go_home) and stays there; the body's reflexes flee, shield, dodge and handle
creepers (O53 covers a creeper that follows a walk). The mind only adds: back to the shelter whenever she is idle
outside it, and fight_melee when a melee mob has her cornered at the shelter and she is healthy enough. On a death
(keep-inventory off: everything drops at the death spot): wait for the respawn, equip_best, walk to the death spot
while the drops can still be there (6000 game ticks, same dimension, not lava or the void), equip_best again, then
back to the shelter and on with the night. Every death is recorded with its position, dimension, cause and killer and
whether the drops were recovered; reflexes are counted. The run ends at dawn (`done`), on a stop, or when it cannot
reach the shelter.
"""
import asyncio
import json
import math
import time
import uuid
from collections import Counter

from .ironkit import IronKit

DUSK, DAWN = 13000, 23000               # time_of_day: night is DUSK <= t < DAWN
SEAL_AT = 12000                         # S28b (CP-87 19:11:33: dark and mobs before 13000, sealed only at 19:12:07):
#                                         the run counts sunset on as its night - shelter + seal from here
OPEN_AT, OPEN_CLEAR, OPEN_WAIT_S = 23500, 12.0, 300.0   # CP-87: open at dawn once zombies burn (~23460) and no
#                                         hostile is within OPEN_CLEAR of the doorway (5 real min at most)
DAY_TICKS = 24000
DESPAWN, MARGIN = 6000, 600             # vanilla item despawn; do not set out with less than MARGIN ticks left
NO_RECOVERY = ("lava", "outOfWorld", "outside_border")   # drops are gone (or the spot kills again)
SHELTER_R = 3.0                         # go_home's own tolerance
PATH_CLEAR = 16.0                        # O83b: a night recover walk needs no hostile this close to its path
WALLED = "night_walled_in"              # O101g meta: the doorway cells still sealed when the last night ended
NEEDS_PICK = ("minecraft:cobblestone", "minecraft:cobbled_deepslate", "minecraft:andesite", "minecraft:diorite",
              "minecraft:granite", "minecraft:tuff")   # O101: by hand these drop nothing and take ages
# ponytail: the stone she realistically carries (kit + iron kit drops), not every block in the game - add one when she
#   carries it


def has_pickaxe(inv):
    return any(n > 0 and i.endswith("_pickaxe") for i, n in inv.items())


def hand_open(i):
    return i == "minecraft:dirt" or i.endswith(("_planks", "_log"))


def seal_items(inv, cells):
    """O101e (lane D night 0, b51d8970: cobblestone only and no pickaxe - the door was left open, 4 deaths): the door is
    never left open while she has a block. One item per doorway cell: a block she opens by hand first (dirt, then planks
    or logs), else ANY block she has - stone included, dug out by hand at dawn. Fewer items than cells only when she
    runs out."""
    left, out = dict(inv), []
    for _ in range(cells):
        i = max((i for i, n in left.items() if n > 0 and (hand_open(i) or i in NEEDS_PICK)),
                key=lambda i: (hand_open(i), i == "minecraft:dirt", left[i]), default=None)
        if i is None:
            break
        out.append(i)
        left[i] -= 1
    return out
MELEE_NEAR, FIGHT_HEALTH = 5.0, 10      # a melee mob this close, and her health at least this: fight it
REACH, EYE = 4.0, 1.62                  # O90: mine_block does not walk; vanilla reach is 4.5 from the eyes
DOOR_THREAT, THREAT_SEAL_S = 4.0, 5.0   # O98: a hostile this close to the open doorway seals it; tries this far apart
THREAT_SEALS = 3                        # O98: at most this many threat seals a night (the lane may have sealed it)
OUT = {(1, 0): "east", (-1, 0): "west", (0, 1): "south", (0, -1): "north"}
OPEN_EVERY_S, OPEN_RETRY_S = 20.0, 120.0   # O235: the dawn open tries again this often, this long
WAIT_DUSK_MAX_S = 900                   # at most 15 real minutes waiting for dusk


def night(t):
    return t is not None and DUSK <= int(t) % DAY_TICKS < DAWN


def dark(t):
    """The night run's own night: sunset (SEAL_AT) to the open (OPEN_AT)."""
    return t is not None and SEAL_AT <= int(t) % DAY_TICKS < OPEN_AT


class NightSurvival(IronKit):
    KEY = "night"

    def __init__(self, mind):
        super().__init__(mind)
        self.events = asyncio.Queue()

    def start(self, source="cli", doorway=None, late_seal=False):
        """late_seal (O98, the lane's late-seal nights): the doorway is not sealed at dusk - it stays open only while no
        hostile threatens it (seal_threat), then it is sealed at once from inside."""
        if self.task and not self.task.done():
            return {"ok": False, "error": "busy", "detail": f"night_survival {self.current['id']} is running"}
        doorway = doorway if doorway is not None else self.mind.env["home"].get("doorway")   # O83e: the 1x2 doorway
        if doorway is not None and not (isinstance(doorway, list) and 1 <= len(doorway) <= 2 and all(
                isinstance(c, dict) and all(isinstance(c.get(a), (int, float)) for a in "xyz") for c in doorway)):
            return {"ok": False, "error": "bad_params", "detail": "doorway: one or two {x, y, z} cells"}
        self.current = r = {"id": uuid.uuid4().hex[:12], "objective": "night_survival", "status": "running",
                            "report": "", "phases": [], "refusals": [], "injuries": [], "notes": [], "stop_reason": None,
                            "deaths": [], "fights": [], "reflexes": {}, "breaks_by_phase": {}, "shelter_pos": None,
                            "doorway": [{a: int(c[a] // 1) for a in "xyz"} for c in doorway or []], "sealed": [],
                            "unsealed": "", "recheck": [],
                            "late_seal": bool(late_seal), "threat_seal_at": 0.0, "threat_seals": 0,
                            "start_gametime": None, "end_gametime": None, "day_ticks_used": 0,
                            "real_start_ms": int(time.time() * 1000), "real_end_ms": None}
        r["sealed"] = [s for s in json.loads(self.mind.meta(WALLED) or "[]") if s["pos"] in r["doorway"]]
        if r["sealed"]:                         # O101g: walled in since the last dawn - she tries again at this dawn
            r["recheck"] = [s["pos"] for s in r["sealed"]]     # re-checked once, at dusk (late-seal nights too)
            r["notes"].append(f"doorway: walled in since the last dawn ({len(r['sealed'])} cell(s))")
        self.events = asyncio.Queue()
        self.save(r)
        self.task = asyncio.get_running_loop().create_task(self.run(r, source))
        return {"ok": True, "objective_id": r["id"], "status": "running"}

    def sealed_in(self):
        """O126 (b): a night is running and she is shut in behind her own seal."""
        return bool(self.task and not self.task.done() and self.current and self.current.get("status") == "running"
                    and self.current.get("sealed"))

    def save(self, r):
        try:
            self.mind.set_meta({f"{self.KEY}.{r['id']}": json.dumps(r)})
        except Exception:
            self.mind.metrics["db_errors"] += 1

    def status(self, rid=None):
        if self.current and (not rid or self.current["id"] == rid):
            return {"ok": True, **self.current}
        raw = self.mind.meta(f"{self.KEY}.{rid}") if rid else None
        return {"ok": True, **json.loads(raw)} if raw else {"ok": False, "error": "unknown_objective", "detail": rid}

    def on_event(self, p):
        """Body events while a night runs: death, respawn, reflex."""
        if self.task and not self.task.done() and p.get("kind") in ("death", "respawn", "reflex"):
            self.events.put_nowait(p)

    def finish(self, r, status, report):
        r.update(status=status, report=report, end_gametime=self.tick(), real_end_ms=int(time.time() * 1000),
                 day_ticks_used=(self.tick() - r["start_gametime"]) if r["start_gametime"] is not None else 0)
        self.save(r)
        try:
            self.mind.set_meta({WALLED: json.dumps(r["sealed"])})    # O101g: what still shuts the doorway
            self.mind.event("mind", "objective", {"id": r["id"], "status": status, "report": report})
        except Exception:                       # shutting down (the DB is closed under a cancelled night)
            self.mind.metrics["db_errors"] += 1
        return r

    # ---- helpers
    def declares(self, skill, param):
        """O101g: a param goes out only when the LOADED registry declares it (the live 45L's 0.15.0 has no mine_block
        by_hand; the 0.16.0 draft does) - never by a version string."""
        return param in (((self.mind.registry.skills.get(skill) or {}).get("params") or {}).get("properties") or {})

    def away(self, st, r):
        """Distance from the shelter: where her last successful go_home left her, else the envelope home centre."""
        home = self.mind.env["home"]
        if st.get("dim") != home["dim"]:
            return math.inf
        spot = r.get("shelter_pos") or home["center"]
        return math.dist([st["pos"][a] for a in "xyz"], [spot[a] for a in "xyz"])

    def stranded_now(self):
        """O86a: the iron kit run ended `stranded` and she is still under its mine entry."""
        k = self.mind.ironkit.current or {}
        return k.get("stop_reason") == "stranded" and self.below(k.get("entry"))

    async def emergency(self, r):
        """O86a: a night stranded underground is not waited out in the hole - the owner is called and the night ends."""
        await self.stranded(r, "still no way up since the iron kit run")
        return self.finish(r, "partial", "emergency: stranded underground; the owner is called")

    def night_risk(self, st, r, death):
        """O83b: why a recover walk must not go out now, or "": the door is sealed (she stays in), or a hostile is
        within 16 of the line from her to the drops - unknown positions count as near."""
        if not night(st.get("time_of_day")):
            return ""
        if r["sealed"]:
            return "the doorway is sealed for the night"
        a, b = st.get("pos"), death["pos"]
        for e in st.get("nearby_entities", []):
            if e.get("hostile") and not e.get("protected") and (
                    not isinstance(e.get("pos"), dict) or not isinstance(a, dict) or seg_dist(e["pos"], a, b) <= PATH_CLEAR):
                return f"a hostile {e.get('type', '?')} within {PATH_CLEAR:g} of the path"
        return ""

    @staticmethod
    def to_dawn(st):
        t = int(st.get("time_of_day") or 0) % DAY_TICKS
        return DAWN - t if DUSK <= t < DAWN else 0

    def cornering(self, st, r=None):
        """The nearest hostile mob to fight at the shelter (not protected), or None: a melee mob within MELEE_NEAR, or
        a ranged one (a skeleton) that came INSIDE the shelter (O83: CP-31 a9 - skeletons walked in through the open
        doorway and she died four times without a swing; one outside is never chased)."""
        kinds = {t["entity_id"]: t.get("kind") for t in st.get("threats", [])}
        sealed = bool((r or {}).get("sealed"))           # O83b: once sealed she fights only what is inside the room
        near = [e for e in st.get("nearby_entities", []) if e.get("hostile") and not e.get("protected") and
                (e.get("distance", 99) <= MELEE_NEAR and (kinds.get(e.get("id")) == "melee" and not sealed or
                                                         kinds.get(e.get("id")) in ("melee", "ranged") and self.inside(e, r))
                 or self.in_doorway(e, r))                 # O98: whatever stands in the doorway cell is fought there
                and not (sealed and e.get("line_of_sight") is False)]   # CP-31 a14 night 10: the 3.0 sphere reaches
        #   past the walls - a skeleton hugging the outside of the west wall counted as inside; sealed, one she cannot
        #   see is behind a wall and out of reach (body's sense: vanilla hasLineOfSight)
        return min(near, key=lambda e: e.get("distance", 99)) if near else None

    def in_doorway(self, e, r):
        """O98: a mob standing in an (open) doorway cell - its feet in the cell's column, at the doorway's height."""
        pos = e.get("pos")
        return isinstance(pos, dict) and any(
            c["x"] <= pos["x"] < c["x"] + 1 and c["z"] <= pos["z"] < c["z"] + 1 and c["y"] - 1 <= pos["y"] < c["y"] + 1
            for c in (r or {}).get("doorway", []) if c not in [s["pos"] for s in (r or {}).get("sealed", [])])

    def at_door(self, st, r):
        """A hostile within OPEN_CLEAR of the doorway (the dawn open waits for none)."""
        door = [{a: c[a] + 0.5 for a in "xyz"} for c in r["doorway"]]
        return bool(door) and any(e.get("hostile") and isinstance(e.get("pos"), dict) and
                                  min(dist(e["pos"], d) for d in door) <= OPEN_CLEAR
                                  for e in st.get("nearby_entities", []))

    def door_threat(self, st, r):
        """O98 (CP-31 a16 night 1: a skeleton stood 1.3 outside the open doorway and shot her for 45 s, 20 -> 4 HP, and
        she died): why an open doorway must be sealed now, or "" - a hostile in or within DOOR_THREAT of it, or a ranged
        hostile that can see her."""
        if not r["doorway"]:
            return ""
        kinds = {t["entity_id"]: t.get("kind") for t in st.get("threats", [])}
        door = [{a: c[a] + 0.5 for a in "xyz"} for c in r["doorway"]]
        for e in st.get("nearby_entities", []):
            if not e.get("hostile") or e.get("protected"):
                continue
            pos = e.get("pos")
            if isinstance(pos, dict) and min(dist(pos, d) for d in door) <= DOOR_THREAT:
                return f"a {e.get('type', '?')} within {DOOR_THREAT:g} of the doorway"
            if kinds.get(e.get("id")) == "ranged" and e.get("line_of_sight") is True:
                return f"a ranged {e.get('type', '?')} with line of sight"
        return ""

    def doorway_param(self, r):
        """combat's fight_melee `doorway` {pos, out} (O98: never follows a target out; holds inside, out of the line
        through the opening) - only while the doorway is open and only when the registry declares the param."""
        props = ((self.mind.registry.skills.get("fight_melee") or {}).get("params") or {}).get("properties") or {}
        if "doorway" not in props or not r["doorway"] or r["sealed"]:
            return {}
        cell = min(r["doorway"], key=lambda c: c["y"])
        inside = self.door_sides(r, cell)[0]
        return {"doorway": {"pos": dict(cell), "out": OUT[(round(cell["x"] + 0.5 - inside["x"]),
                                                          round(cell["z"] + 0.5 - inside["z"]))]}}

    def inside(self, e, r):
        home = self.mind.env["home"]
        spot, pos = (r or {}).get("shelter_pos") or home["center"], e.get("pos")
        return isinstance(pos, dict) and math.dist([pos[a] for a in "xyz"], [spot[a] for a in "xyz"]) <= SHELTER_R

    async def drain(self, r, source):
        while not self.events.empty():
            p = self.events.get_nowait()
            d = p.get("detail") or {}
            if p["kind"] == "reflex":
                if d.get("phase") == "start":
                    r["reflexes"][d.get("reflex", "?")] = r["reflexes"].get(d.get("reflex", "?"), 0) + 1
            elif p["kind"] == "death":
                r["deaths"].append({"tick": self.tick(), "pos": d.get("pos"), "dim": d.get("dim"),
                                    "cause": d.get("cause"), "killer_type": d.get("killer_type"),
                                    "recovered": None, "note": ""})
                self.save(r)
            elif p["kind"] == "respawn" and r["deaths"] and r["deaths"][-1]["recovered"] is None:
                await self.recover(r, r["deaths"][-1], source)

    async def recover(self, r, death, source):
        p = self.phase(r, "recover")
        await self.act(r, p, "equip_best", {}, source)                       # whatever she still has (usually none)
        st = self.mind.fresh_state() or {}
        left = DESPAWN - ((self.tick() or 0) - (death["tick"] or 0))
        why = ("no death position" if not isinstance(death["pos"], dict) else
               "another dimension" if death["dim"] and death["dim"] != st.get("dim") else
               f"died to {death['cause']}" if death["cause"] in NO_RECOVERY else
               "the drops have despawned" if left < MARGIN else "")
        wait = self.night_risk(st, r, death) if not why else ""
        if wait and left - self.to_dawn(st) >= MARGIN:          # O83b (a11: a night walk to the drops, zombies at 1.8,
            death.update(recovered=None, deferred=True, note=f"waiting for dawn: {wait}")   # died again): wait it out
            self.close(r, p, "deferred", wait)
        elif wait:
            death.update(recovered=False, note=f"not recovered: {wait}, and the drops despawn before dawn (a death "
                                                  "costs more)")
            self.close(r, p, "skipped", wait)
        elif why:
            death.update(recovered=False, note=f"not recovered: {why}")
            self.close(r, p, "skipped", why)
        else:
            before = sum(self.inv()[0].values())
            res = await self.act(r, p, "walk_to", {"pos": death["pos"], "tolerance": 1.0}, source)
            await asyncio.sleep(1.0)                                         # vanilla pickup is on contact
            got = sum(self.inv()[0].values()) - before
            death.update(recovered=got > 0, note=f"walk {res['status']}; picked up {got} item(s)")
            if got:
                await self.act(r, p, "equip_best", {}, source)
                await self.act(r, p, "equip_best", {"weapon": True, "slots": []}, source)
            self.close(r, p, "ok" if got else "short", death["note"])
        await self.shelter(r, source)

    async def shelter(self, r, source):
        p = self.phase(r, "shelter")
        res = await self.act(r, p, "go_home", {}, source)
        if res["status"] != "ok" and r["sealed"]:              # O83e: locked out by her own seal - open, in, reseal
            self.close(r, p, res["status"], res["code"] or "")
            await self.unseal(r, source, "to get back in")
            p = self.phase(r, "shelter")
            res = await self.act(r, p, "go_home", {}, source)
            if res["status"] == "ok" and r["doorway"]:
                self.close(r, p, "ok")
                await asyncio.sleep(0.5)
                await self.seal(r, source)
                return True
        self.close(r, p, res["status"], res["code"] or "")
        if res["status"] == "ok":                               # go_home's own "within 3" is the shelter
            await asyncio.sleep(0.5)                            # the result can beat the state frame after it
            pos = (res.get("evidence") or {}).get("final_pos") or (self.mind.fresh_state() or {}).get("pos")
            r["shelter_pos"] = dict(pos) if isinstance(pos, dict) else None
        return res["status"] == "ok"

    async def seal(self, r, source, why="at dusk", only_recheck=False):
        """O83e: close the 1x2 doorway from inside (O101e: seal_items' order) - lower cell first, so even one block keeps
        out every two-high mob; every placement is logged. O98: on a late-seal night also later, on a threat - only the
        cells still open. O101g (eve-watch N1): a cell still shut since the last dawn (r["recheck"]) is re-checked once,
        by placing into it - `occupied` keeps it, a placement reseals it, no block to test it with keeps it for the dawn
        but it is never reported shut. With no block at all she stays inside and the doorway is reported UNSEALED (said
        once a night), never silently open. only_recheck: a late-seal dusk - re-check the walled-in cells, seal nothing
        new (O98: open by design until a threat)."""
        if not r["doorway"]:
            return
        cells, recheck = sorted(r["doorway"], key=lambda c: c["y"]), r["recheck"]
        done = [s["pos"] for s in r["sealed"]]
        todo = [c for c in cells if c in recheck or c not in done and not only_recheck]
        r["recheck"] = []
        if not todo:
            return
        items, used, unchecked = seal_items(self.inv()[0], len(todo)), [], 0
        if not items and not recheck:
            r["unsealed"] = f"no block to seal the doorway with ({why})"
            if not any(n.startswith("doorway: UNSEALED") for n in r["notes"]):    # once a night (a late-seal night
                await self.mind.say("I have no block to close my door tonight, so I'm staying inside with it open - "
                                    "could you bring me some dirt?")             # may try again on each threat)
            r["notes"].append(f"doorway: UNSEALED {why} - no block to seal it with")
            return
        p, at = self.phase(r, "seal"), (self.mind.fresh_state() or {}).get("pos")
        for cell in todo:
            old = next((s for s in r["sealed"] if s["pos"] == cell), None)        # walled in since the last dawn
            if not items:
                unchecked += bool(old)          # N1: nothing to re-check it with - kept for the dawn try only
                continue
            if not in_reach(at, cell):          # O90c: place_block does not walk either; a failed seal leaves the door
                at = await self.stand_at(r, p, self.door_sides(r, cell)[0], source) or at   # open all night - from
            res = await self.act(r, p, "place_block", {"pos": cell, "item": items[0]}, source)   # INSIDE only, never out
            if res["status"] == "ok":
                used.append(items.pop(0))
                if old:
                    r["sealed"].remove(old)
                r["sealed"].append({"pos": cell, "item": used[-1]})
            elif old and res["code"] != "occupied":   # `occupied`: still shut since the dawn - kept
                r["sealed"].remove(old)
        open_n = (0 if only_recheck else len(cells) - len(r["sealed"])) + unchecked
        r["unsealed"] = (f"{open_n} of {len(cells)} doorway cell(s) open" + (
            f" or not re-checked ({unchecked} walled in since the last dawn, no block to test them with)"
            if unchecked else "")) if open_n else ""
        self.close(r, p, "short" if open_n else "ok")
        kept = [s["item"] for s in r["sealed"] if s["pos"] in recheck and s["item"] not in used]
        what = ", ".join(dict.fromkeys(used + kept))
        r["notes"].append(f"doorway: sealed {len(r['sealed']) - unchecked}/{len(r['doorway'])} {why}" +
                          (f" with {what}" if what else "") + (f", {unchecked} not re-checked" if unchecked else ""))

    async def park(self, r, source):
        """O235/O236: the park spot (home centre + 0.5), off the doorway - where she holds the night and opens from."""
        c = self.mind.env["home"]["center"]
        spot = {"x": c["x"] + 0.5, "y": float(c["y"]), "z": c["z"] + 0.5}
        at = (self.mind.fresh_state() or {}).get("pos")
        if not isinstance(at, dict) or dist(at, spot) > 1.0:
            p = self.phase(r, "park")
            got = await self.stand_at(r, p, spot, source)
            self.close(r, p, "ok" if got is not None else "failed")

    async def unseal(self, r, source, why):
        """O235 (21:01 dawn: half a block from the door, her crosshair hit the top cell while she mined the bottom one -
        "obstructed" - and only the top opened): from the park spot, top cell first, each cell checked to be air,
        again every OPEN_EVERY_S for OPEN_RETRY_S before it waits for the next dawn."""
        r["unopened"] = []
        if not r["sealed"] or self.mind.stopped:
            return
        if why == "at dawn":                                      # locked out: no inside walk
            await self.park(r, source)
        r["sealed"].sort(key=lambda s: -s["pos"]["y"])            # the top cell first: it is in the crosshair
        t0 = time.monotonic()
        while True:
            await self.unseal_once(r, source, why)
            if not [u for u in r["unopened"] if u["code"] != "no_tool"] or self.mind.stopped or                     time.monotonic() - t0 >= OPEN_RETRY_S:
                break
            await asyncio.sleep(OPEN_EVERY_S)
        if why == "at dawn" and not self.mind.stopped:
            await self.park(r, source)          # O237 (22:01:14: the drop pickup left her on the doorway lip, and a
            #                                     creeper_flee 2 min later ran her off the step): back off the door

    async def unseal_once(self, r, source, why):
        """O83e: open only the cells she sealed (never anything else), logged. O101g, a stone cell: with a pickaxe it
        is equipped first (O101); without one, mine_block by_hand when the loaded registry declares it; else the cell
        stays shut (0.15.0 - she is walled in, run() reports it, the next dawn tries again). r["unopened"]: each cell
        left shut with its code (no_tool for a skipped stone cell); `wrong_block` = no longer her block, dropped."""
        r["unopened"] = []
        if not r["sealed"] or self.mind.stopped:
            return
        p, done, at = self.phase(r, "open"), 0, (self.mind.fresh_state() or {}).get("pos")
        stone = next((s["item"] for s in r["sealed"] if s["item"] in NEEDS_PICK), None)
        pick, hand = has_pickaxe(self.inv()[0]), self.declares("mine_block", "by_hand")
        if stone and pick:                              # O101: the pickaxe in hand before mine_block (it may sit
            await self.act(r, p, "equip_best", {"for_block": stone, "slots": []}, source)   # outside the hotbar)
        for s in list(r["sealed"]):
            by_hand = s["item"] in NEEDS_PICK and not pick
            if by_hand and not hand:
                r["unopened"].append({**s, "code": "no_tool"})
                continue
            code, at = await self.open_cell(r, p, s, at, source, {"by_hand": True} if by_hand else {})
            if code == "ok" and not await self.is_air(s["pos"]):
                code = "not_air"                        # O235: verified, not assumed
            if code == "ok":
                r["sealed"].remove(s)
                done += 1
            elif code == "wrong_block":                 # eve-watch N1: dug out or rebuilt since - not hers to open
                r["sealed"].remove(s)
                r["notes"].append(f"doorway: {s['pos']} no longer holds her {s['item']}; dropped")
            else:
                r["unopened"].append({**s, "code": code})
        self.close(r, p, "ok" if not r["sealed"] else "short")
        r["notes"].append(f"doorway: opened {done} cell(s) {why}" + (f", {len(r['sealed'])} still sealed"
                                                                       if r["sealed"] else ""))

    async def is_air(self, cell):
        """O235: the cell reads air (sense query.blocks); True when sense gives no answer (no evidence against)."""
        st = self.mind.fresh_state()
        if not st:
            return True
        y = math.floor(st["pos"]["y"])
        blocks = await self.mind.blocks_near(st, 4, (cell["y"] - y, cell["y"] - y)) or {}
        b = blocks.get((cell["x"], cell["y"], cell["z"]))
        return b is None or b in ("minecraft:air", "minecraft:cave_air")

    async def open_cell(self, r, p, s, at, source, extra=None):
        """O90 (CP-31 a13 night 0: both cells `out_of_range` at dawn): mine_block does not walk, so she stands within
        reach first - on her own side of the doorway - and if it still fails, once more from the other side.
        Returns ("ok" or the last failure code, where she stands)."""
        sides, code = self.door_sides(r, s["pos"]), "walk_failed"
        if isinstance(at, dict) and dist(at, sides[1]) < dist(at, sides[0]):
            sides.reverse()                                     # outside (locked out): her side first
        for i, stand in enumerate(sides):
            if self.mind.stopped:
                break
            if i or not in_reach(at, s["pos"]):
                got = await self.stand_at(r, p, stand, source)
                if got is None:
                    continue
                at = got
            res = await self.act(r, p, "mine_block", {"pos": s["pos"], "expect_block": s["item"], **(extra or {})},
                                 source)
            if res["status"] == "ok":
                return "ok", at
            code = res["code"] or res["status"]
            if code in ("no_tool", "wrong_block"):              # O101e: the other side has no tool (or block) either
                break
        return code, at

    async def stand_at(self, r, p, stand, source):
        """O90: walk_to a standing cell by the doorway (tolerance 1) -> where she stands, or None when the walk failed."""
        w = await self.act(r, p, "walk_to", {"pos": stand, "tolerance": 1.0}, source)
        return ((w.get("evidence") or {}).get("final_pos") or stand) if w["status"] == "ok" else None

    def door_sides(self, r, cell):
        """O90: where to stand for a doorway cell - one block in, then one block out, along the line from the shelter
        centre through the door, at the doorway's floor."""
        spot = r.get("shelter_pos") or self.mind.env["home"]["center"]
        dx, dz = cell["x"] + 0.5 - spot["x"], cell["z"] + 0.5 - spot["z"]
        ox, oz = (math.copysign(1, dx), 0) if abs(dx) >= abs(dz) else (0, math.copysign(1, dz))
        y = min(c["y"] for c in r["doorway"])
        return [{"x": cell["x"] + 0.5 + k * ox, "y": y, "z": cell["z"] + 0.5 + k * oz} for k in (-1, 1)]

    # ---- the night
    async def run(self, r, source):
        m = self.mind
        try:
            if self.stranded_now():                 # no idling till dusk in the mine
                return await self.emergency(r)
            p = self.phase(r, "dusk")
            t0 = time.monotonic()
            while not dark((m.fresh_state() or {}).get("time_of_day")):
                if m.stopped or time.monotonic() - t0 > WAIT_DUSK_MAX_S:
                    self.close(r, p, "stopped" if m.stopped else "timeout")
                    return self.finish(r, "stopped" if m.stopped else "partial", "no dusk came" if not m.stopped
                                       else "stopped before dusk")
                await asyncio.sleep(1.0)
            r["start_gametime"] = self.tick()
            self.close(r, p, "ok")
            if not await self.shelter(r, source):
                if self.stranded_now():
                    return await self.emergency(r)
                return self.finish(r, "partial", "could not reach the shelter at dusk")
            await self.seal(r, source, only_recheck=r["late_seal"])   # O101q: a late-seal dusk re-checks walled-in
            if r["doorway"]:
                await self.park(r, source)                            # O236: hold off the doorway, not at its cell
            while True:                                               # cells only; the rest stays open until a threat
                if m.stopped:
                    return self.finish(r, "stopped", "stopped during the night")
                await self.drain(r, source)
                st = m.fresh_state()
                if st is None:
                    await asyncio.sleep(0.5)
                    continue
                if not dark(st.get("time_of_day")):
                    break
                if m.open_action() is None and not st["control"].get("reflex_active") and st.get("health", 0) > 0 and \
                        not r.get("owner_fight"):                # O126 (b): his fight order owns her until it is back in
                    foe = self.cornering(st, r)
                    if foe and st.get("health", 0) >= FIGHT_HEALTH and self.away(st, r) <= SHELTER_R + 1:
                        p = self.phase(r, "fight")
                        res = await self.act(r, p, "fight_melee", {"entity_id": foe["id"],      # O83b: sealed - never
                                                                    "radius": SHELTER_R + 1 if r["sealed"] else 8,   # out
                                                                    "health_floor": 8, "max_targets": 3,
                                                                    **self.doorway_param(r)}, source)
                        r["fights"].append({"tick": self.tick(), "entity_id": foe["id"], "type": foe.get("type"),
                                            "status": res["status"], "code": res["code"]})
                        self.close(r, p, res["status"], res["code"] or "")
                    elif self.away(st, r) > SHELTER_R:
                        await self.shelter(r, source)
                    elif r["late_seal"] and len(r["sealed"]) < len(r["doorway"]) and \
                            r["threat_seals"] < THREAT_SEALS and time.monotonic() - r["threat_seal_at"] >= THREAT_SEAL_S and \
                            (why := self.door_threat(st, r)):
                        r["threat_seal_at"] = time.monotonic()           # O98: seal at once, from inside (she is home)
                        r["threat_seals"] += 1
                        await self.seal(r, source, f"on a threat ({why})")
                await asyncio.sleep(0.5)
            await self.drain(r, source)
            t0 = time.monotonic()                                     # CP-87: not into a mob at the door
            while r["sealed"] and not m.stopped and time.monotonic() - t0 < OPEN_WAIT_S and self.at_door(
                    m.fresh_state() or {}, r):
                await asyncio.sleep(1.0)
            await self.unseal(r, source, "at dawn")
            left = r["unopened"] if r["sealed"] and not m.stopped else []    # finish() keeps them for the next dawn
            shut = ", ".join(dict.fromkeys(s["item"] for s in left if s["code"] == "no_tool"))
            other = ", ".join(f"{s['item']} ({s['code']})" for s in left if s["code"] != "no_tool")
            if shut:                # O101g: stone, no pickaxe and no by_hand (or it said no_tool) - walled in
                r["stop_reason"] = "walled_in"
                r["notes"].append(f"doorway: walled_in (no_tool) - {shut} still in the doorway; tries again at the "
                                  "next dawn")
                await m.say(f"I'm walled in: I can't open the {shut} in my doorway without a pickaxe - could you bring "
                            "me one? I'll try again at the next dawn.")
            if other:               # eve-watch N2: not a tool problem - its own code, and no pickaxe asked for
                r["stop_reason"] = r["stop_reason"] or "shut_in"
                r["notes"].append(f"doorway: still shut at dawn - {other}; tries again at the next dawn")
                await m.say("I couldn't open my doorway at dawn; I'll try again at the next dawn.")
            for d in [d for d in r["deaths"] if d.get("deferred") and d["recovered"] is None and not left]:
                d.pop("deferred")                                # O83b: the walks the night ruled out, now in daylight
                await self.recover(r, d, source)
            lost = [d for d in r["deaths"] if not d["recovered"]]
            report = (f"night over: {len(r['deaths'])} death(s)" +
                      (f", {len(lost)} without recovering the drops" if lost else "") +
                      f", {len(r['fights'])} fight(s), reflexes {dict(Counter(r['reflexes']))}" +
                      (f"; UNSEALED: {r['unsealed']}" if r["unsealed"] else "") +
                      (f"; walled_in (no_tool): {shut} still in the doorway" if shut else "") +
                      (f"; still shut at dawn: {other}" if other else ""))
            return self.finish(r, "partial" if left else "done", report)
        except asyncio.CancelledError:
            self.finish(r, "stopped", "cancelled")
            raise


def in_reach(at, cell):
    """O90: mine_block / place_block do not walk - her eyes within REACH of the cell's centre."""
    return isinstance(at, dict) and math.dist([at["x"], at["y"] + EYE, at["z"]], [cell[a] + 0.5 for a in "xyz"]) <= REACH


def dist(a, b):
    return math.dist([a[k] for k in "xyz"], [b[k] for k in "xyz"])


def seg_dist(p, a, b):
    """Distance from point p to the segment a-b (x/y/z dicts)."""
    P, A, B = ([q[k] for k in "xyz"] for q in (p, a, b))
    ab = [B[i] - A[i] for i in range(3)]
    n = sum(c * c for c in ab)
    t = 0.0 if not n else max(0.0, min(1.0, sum((P[i] - A[i]) * ab[i] for i in range(3)) / n))
    return math.dist(P, [A[i] + t * ab[i] for i in range(3)])
