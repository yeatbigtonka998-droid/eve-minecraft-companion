"""CP-40 (O41/O41c): `objective iron_kit` - full iron armour and an iron pickaxe, end to end, deterministic.

The model plans nothing; the recipe solver plans crafting; every step goes through Mind.do(). Phases (design in
STATUS, approved O41c): clock -> needs -> kit -> find+mine iron and coal -> home -> stations -> smelt -> craft ->
equip -> journal. The day clock is 72000 ticks from the first dispatched action on SENSE's clock; when it runs out the
run ends `partial: day clock`. Breaks are budgeted against the mining zone's hour cap (envelope Amendment 5): a plan
that would pass it ends `capped`, it never collects refusals. Every refusal, shortfall, injury and deferred need is
in the run record (meta `ironkit.<id>`), which skills' harness reads beside its own server truth.
"""
import asyncio
import json
import math
import re
import time
import uuid
from collections import Counter

from .supply import Supply

DAY = 72000
RAW_IRON = 27                      # helmet 5 + chestplate 8 + leggings 7 + boots 4 + pickaxe 3
COAL = 4                           # 8 smelts per coal
ARMOUR = ("iron_helmet", "iron_chestplate", "iron_leggings", "iron_boots")
CRAFT_ORDER = [("minecraft:stick", 4)] + [(f"minecraft:{a}", 1) for a in ARMOUR] + [("minecraft:iron_pickaxe", 1)]
GOOD = ("iron_", "diamond_", "netherite_")
HURT = 12
MINE_ZONE = "fresh_mine"
DESCEND_TO = 16                    # iron (and coal) level for prospect's staircase (skills' draft)
LOW_USES = 20                      # re-tool before a prospect when the only pickaxe has fewer uses left
RETOOLS = 5                        # stone pickaxes she may make in one run (skills: 3-5 for an iron kit)
TABLE_TRIES = 4                    # O93: cells (inside the zone) the retool table is tried in before giving up
FIGHTS, FIGHT_HEALTH = 4, 8        # O58: tunnel fights per run; she fights back only at this health or more
WEAPONS = ("_sword", "_axe")
FOODS = ("bread", "cooked_", "apple", "baked_potato", "carrot", "golden_carrot")
STONE = ("minecraft:cobblestone", "minecraft:cobbled_deepslate", "minecraft:blackstone")
STEP = {"east": (1, 0), "west": (-1, 0), "south": (0, 1), "north": (0, -1)}
HEADINGS = ("east", "south", "north", "west")    # O41l: the order the remaining headings are tried
RUN_STOPS = ("capped", "day_clock", "inventory_full", "hurt", "no_tool", "cancelled", "interrupted")   # not a heading's
FILLER = {"cobblestone", "stone", "cobbled_deepslate", "deepslate", "dirt", "gravel", "sand", "andesite", "diorite",
          "granite", "tuff", "calcite", "netherrack", "flint", "clay_ball"}    # tunnel spoil, never "ore found"
MIN_ROOM = 8                                    # O67: blocks of zone a heading needs ahead (and tunnel at the bottom)
BRANCH_MIN = 4                                  # O86v: a tunnel this long (blocks from its start) is worth branching off
PERP = {"north": ("west", "east"), "south": ("east", "west"), "east": ("north", "south"), "west": ("south", "north")}
DIR = re.compile(r"\bdir (north|south|east|west)\b")   # O86: the heading prospect last tunnelled, in its stop_reason
FAR = re.compile(r"\bfar (-?\d+) (-?\d+) (-?\d+) (north|south|east|west)\b")   # O86v: skills' farthest safe cell


class IronKit(Supply):
    def start(self, source="cli", entry=None, direction=None):
        """entry: the named entry spot of the mine zone (O41e: skills records it in fresh-world.json and the harness
        passes it); she walks there from the shelter herself - nobody teleports her into the mine."""
        if self.task and not self.task.done():
            return {"ok": False, "error": "busy", "detail": f"iron_kit {self.current['id']} is running"}
        self.current = r = {"id": uuid.uuid4().hex[:12], "objective": "iron_kit", "status": "running", "report": "",
                            "phases": [], "deferred": [], "refusals": [], "shortfalls": {}, "injuries": [],
                            "notes": [], "retools": 0, "headings": [], "dir": None, "tunnels": [], "branch": None, "fights": [],
                            "raw_iron": 0, "coal": 0, "ingots": 0, "crafted": {}, "equipped": {},
                            "breaks_by_phase": {}, "deaths": 0, "start_gametime": None, "end_gametime": None,
                            "real_start_ms": int(time.time() * 1000), "real_end_ms": None, "day_ticks_used": 0,
                            "stop_reason": None, "entry": entry, "direction": direction}
        self.save(r)
        self.task = asyncio.get_running_loop().create_task(self.run(r, source))
        return {"ok": True, "objective_id": r["id"], "status": "running"}

    def save(self, r):
        try:
            self.mind.set_meta({f"ironkit.{r['id']}": json.dumps(r)})
        except Exception:
            self.mind.metrics["db_errors"] += 1

    def status(self, rid=None):
        if self.current and (not rid or self.current["id"] == rid):
            return {"ok": True, **self.current}
        raw = self.mind.meta(f"ironkit.{rid}") if rid else None
        return {"ok": True, **json.loads(raw)} if raw else {"ok": False, "error": "unknown_objective", "detail": rid}

    # ---- helpers
    def tick(self):
        t = self.mind.sense_tick
        return int(t[0] + (time.monotonic() - t[1]) * 20) if t else None

    def clock_left(self, r):
        if r["start_gametime"] is None or self.tick() is None:
            return DAY
        return DAY - (self.tick() - r["start_gametime"])

    def inv(self):
        st = self.mind.fresh_state() or {}
        c = Counter()
        for s in st.get("inventory", []):
            c[s["item"]] += int(s.get("count") or 0)
        return c, st

    def phase(self, r, name):
        p = {"name": name, "start_tick": self.tick(), "end_tick": None, "actions": [], "breaks": 0, "result": None,
             "reason": ""}
        r["phases"].append(p)
        self.save(r)
        return p

    def close(self, r, p, result, reason=""):
        p.update(end_tick=self.tick(), result=result, reason=reason)
        self.save(r)

    async def act(self, r, p, skill, params, source):
        """One dispatched step, recorded with skill/status/code (skills' judge reads these without our DB)."""
        if self.clock_left(r) <= 0:
            return {"status": "clock", "code": "day_clock", "evidence": {}}
        if r["start_gametime"] is None:
            r["start_gametime"] = self.tick()
        hp0 = (self.mind.fresh_state() or {}).get("health")
        res = await self.step(skill, params, source)
        pend = self.pending.pop(skill, None)
        if pend:                                        # O94f: the decision this dispatch carried out, joined by id
            self.mind.candidates.write(pend, res.get("action_id"))
        hp1 = (self.mind.fresh_state() or {}).get("health")
        if isinstance(hp0, (int, float)) and isinstance(hp1, (int, float)) and hp1 < hp0:
            r["injuries"].append({"tick": self.tick(), "health_before": hp0, "health_after": hp1,
                                  "cause": f"during {skill}"})
        p["actions"].append({"action_id": res.get("action_id"), "skill": skill, "status": res["status"],
                             "code": res["code"]})
        if res["status"] not in ("ok",):
            r["refusals"].append({"skill": skill, "code": res["code"], "detail": res["status"]})
        ev = res["evidence"]
        # prospect: blocks_broken (tunnel + ores, server-confirmed); mine_resource: mined (one per break). `mined` of
        # prospect is an {item: n} map of inventory gains, never a break count (skills' STATUS, 0.12.0 draft)
        broke = ev.get("blocks_broken") if skill == "prospect" else ev.get("mined") if skill == "mine_resource" else 0
        broke = int(broke) if isinstance(broke, (int, float)) else 0
        p["breaks"] += broke
        r["breaks_by_phase"][p["name"]] = r["breaks_by_phase"].get(p["name"], 0) + broke
        self.save(r)
        return res

    def zone(self, zid):
        return next((z for z in self.mind.env.get("zones", []) if z.get("id") == zid), None)

    def breaks_this_hour(self, r):
        # ponytail: counts this run's own breaks; the server's per-zone hour window may also hold earlier breaks
        return sum(r["breaks_by_phase"].values())

    def cap_left(self, r):
        z = self.zone(MINE_ZONE) or {}
        cap = int(((z.get("limits") or {}).get("blocks_broken_per_hour"))
                  or self.mind.env.get("limits", {}).get("blocks_broken_per_hour", 200))
        return cap - self.breaks_this_hour(r)

    def finish(self, r, status, report):
        r.update(status=status, report=report, end_gametime=self.tick(), real_end_ms=int(time.time() * 1000),
                 day_ticks_used=(self.tick() - r["start_gametime"]) if r["start_gametime"] is not None else 0)
        r["deaths"] = self.mind.db.execute("SELECT count(*) FROM events WHERE kind='event' AND payload LIKE "
                                           "'%\"death\"%' AND ts >= ?", (r["real_start_ms"],)).fetchone()[0]
        self.mind.release(f"supply:{r['id']}", f"iron_kit {status}")
        self.save(r)
        self.mind.event("mind", "objective", {"id": r["id"], "status": status, "report": report})
        return r

    # ---- the run
    async def run(self, r, source):
        m = self.mind
        try:
            # 1. needs: already done? what would she skip to go mining (recorded for an honest journal, O41c)
            p = self.phase(r, "needs")
            inv, st = self.inv()
            eq = st.get("equipment") or {}
            if all(any(g in str(eq.get(s) or "") for g in GOOD) for s in ("head", "chest", "legs", "feet")):
                self.close(r, p, "done", "already in iron armour or better")
                return self.finish(r, "done", "already equipped with iron armour or better; nothing to do")
            crops = await m.hub.request("sense", "query.resources", {"kind": "crops", "dim": st.get("dim"),
                                        "center": {a: int(st["pos"][a]) for a in "xyz"}, "radius": 32}, timeout=5) \
                if st.get("pos") else None
            ripe = [c for c in (crops or {}).get("items", []) if "age=7" in str(c.get("state", ""))]
            if ripe:
                r["deferred"].append(f"{len(ripe)} ripe crop(s) left unharvested")
            broken = [f for f in m.memory.all(m.world_id) if f["predicate"] == "status" and "broken" in f["object"]]
            r["deferred"] += [f"{f['subject']} is broken (not repaired)" for f in broken]
            self.close(r, p, "ok", "; ".join(r["deferred"]) or "nothing else pressing")

            # 2. kit: food and torches are required, the rest is reported
            p = self.phase(r, "kit")
            food = sum(n for i, n in inv.items() if any(f in i for f in ("bread", "cooked_", "apple", "baked_potato")))
            if food < 8:
                r["shortfalls"]["food"] = 8 - food
            if inv["minecraft:torch"] < 16:
                r["shortfalls"]["minecraft:torch"] = 16 - inv["minecraft:torch"]
            if not any("pickaxe" in i for i in inv):
                self.close(r, p, "refused", "no pickaxe")
                return self.finish(r, "refused", "I have no pickaxe, so I cannot mine; nothing was started")
            optional = [x for x in ("minecraft:shield",) if not inv[x]]
            self.close(r, p, "ok", "missing (optional): " + ", ".join(optional) if optional else "kit complete")
            if "food" in r["shortfalls"]:
                return self.finish(r, "refused", f"not enough food ({food}); I won't go mining hungry")

            # 3. walk from the shelter to the mine's entry spot (O41e: her own agenda, never a teleport)
            entry = r.get("entry")
            try:
                entry = {a: float(entry[a]) for a in "xyz"}
            except (KeyError, TypeError, ValueError):
                return self.finish(r, "refused", "I don't know where the mine's entry is; nothing was started")
            if self.rooms(r, entry) == []:
                return self.finish(r, "refused", f"every heading from the entry has under {MIN_ROOM} blocks of "
                                                 f"{MINE_ZONE} ahead; nothing was started")
            p = self.phase(r, "to_mine")
            res = await self.act(r, p, "walk_to", {"pos": entry}, source)
            self.close(r, p, res["status"], res["code"] or "")
            if res["status"] != "ok":
                await self.go_home(r, source)
                return self.finish(r, "partial", f"could not reach the mine entry ({res['code']})")

            # 4. find and mine iron, then coal: branch mining from the entry
            for want, tag, need in (("raw_iron", "minecraft:iron_ores", RAW_IRON), ("coal", "minecraft:coal_ores", COAL)):
                p = self.phase(r, f"mine_{want}")
                item = "minecraft:raw_iron" if want == "raw_iron" else "minecraft:coal"
                stop = ""
                for _ in range(6 + RETOOLS + FIGHTS):
                    have = self.inv()[0][item]
                    if have >= need:
                        break
                    if self.clock_left(r) <= 0:
                        stop = "day clock"
                        break
                    left = self.cap_left(r)
                    if left < 8:
                        stop = "capped"
                        break
                    params = {"tag": tag, "count": min(64, need - have), "max_blocks": min(256, left)}
                    props = m.registry.skills["prospect"]["params"].get("properties", {})
                    if "direction" in props:               # most room inside the zone (O41n/O67), or where she turned
                        params["direction"] = self.tunnel(r, entry)
                    if "descend_to" in props and not r["branch"]:   # skills: a staircase down from the surface entry first
                        params["descend_to"] = self.descend_to(entry, self.cur_heading(r, entry) if "direction" in props
                                                               else None)
                    if self.uses_left() < LOW_USES and r["retools"] < RETOOLS:   # before it breaks mid-tunnel
                        await self.retool(r, p, entry, source)
                    # skills: prospect digs with the hotbar only - a retooled pickaxe may sit in the main inventory
                    await self.act(r, p, "equip_best", {"for_block": "minecraft:stone", "slots": []}, source)
                    res, far = await self.prospect(r, p, params, source)
                    went = DIR.search(str(res["evidence"].get("stop_reason") or ""))
                    if went:                        # O86 (CP-40 a18): resume the way she turned, never the param
                        r["dir"] = went.group(1)
                    said = FAR.search(str(res["evidence"].get("stop_reason") or ""))
                    if said:                        # skills (jar 3c3a9c39): the farthest cell she stood on + its heading
                        far = {"far": {"x": int(said[1]) + 0.5, "y": float(said[2]), "z": int(said[3]) + 0.5},
                               "reach": res["evidence"].get("tunnel_length") or far["reach"], "heading": said[4]}
                    r["tunnels"].append({"heading": r["dir"] or params.get("direction"), **far})   # O86v
                    code = res["code"] or res["evidence"].get("stop_reason")
                    if code == "no_tool" and r["retools"] < RETOOLS and await self.retool(r, p, entry, source):
                        continue                    # CP-40 a5: the stone pickaxe broke 131 blocks in - make another
                    why = (res.get("evidence") or {}).get("stop_reason") or code
                    if res["status"] not in ("ok", "refused") and code not in RUN_STOPS and not self.ores(res) and \
                            "direction" in props and await self.next_heading(r, p, entry, source, why):
                        continue                    # O41l/O74: a heading that ended with no ore - the next by room
                    if code == "hurt" and await self.fight_back(r, p, res, source):
                        continue                    # O58: a9 died to a zombie in the tunnel; fight it, eat, resume
                    if res["status"] != "ok" and code in ("not_found", "hazard", "inventory_full", "hurt", "no_tool",
                                                          "unreachable", "zone_denied", "capped", "day_clock"):
                        stop = code
                        break
                r[want] = self.inv()[0][item]
                ok = r[want] >= need
                self.close(r, p, "ok" if ok else "short", stop)
                if not ok:
                    r["shortfalls"][item] = need - r[want]
                    if stop == "capped":
                        r["stop_reason"] = "capped"
                        await self.go_home(r, source)
                        return self.finish(r, "partial", f"capped: the mine's break cap is reached with {r[want]} of "
                                                         f"{need} {want.replace('_', ' ')}; stopping instead of "
                                                         "hammering it")
                    if stop in ("hurt", "day clock", "day_clock") or want == "raw_iron":
                        r["stop_reason"] = stop or "short"
                        await self.go_home(r, source)
                        return self.finish(r, "partial", f"stopped mining ({stop or 'short'}) with {r[want]} of "
                                                         f"{need} {want.replace('_', ' ')}")

            # 5. home
            if not await self.go_home(r, source):
                return self.finish(r, "partial", "could not get home after mining")

            # 6. stations: the starting table, a furnace from 8 cobblestone, placed beside her
            p = self.phase(r, "stations")
            inv, st = self.inv()
            here = {a: int(st["pos"][a] // 1) for a in "xyz"}
            table = {"x": here["x"] + 1, "y": here["y"], "z": here["z"]}
            furnace = {"x": here["x"] - 1, "y": here["y"], "z": here["z"]}
            steps = [("place_block", {"pos": table, "item": "minecraft:crafting_table"})]
            if not inv["minecraft:furnace"]:          # a furnace is 8 cobblestone at a placed table
                steps.append(("craft", {"item": "minecraft:furnace", "count": 1, "pos": table}))
            steps.append(("place_block", {"pos": furnace, "item": "minecraft:furnace"}))
            for skill, params in steps:
                res = await self.act(r, p, skill, params, source)
                if res["status"] != "ok":
                    what = f"{skill} {params['item'].split(':')[1]} ({res['code']})"
                    self.close(r, p, "failed", what)
                    return self.finish(r, "partial", f"could not set up the stations: {what}")
            self.close(r, p, "ok")

            # 7. smelt all raw iron with coal
            p = self.phase(r, "smelt")
            res = await self.act(r, p, "smelt", {"pos": furnace, "item": "minecraft:raw_iron",
                                                "count": min(64, self.inv()[0]["minecraft:raw_iron"]),
                                                "fuel": "minecraft:coal"}, source)
            r["ingots"] = self.inv()[0]["minecraft:iron_ingot"]
            self.close(r, p, "ok" if res["status"] == "ok" else "failed", res["code"] or "")
            if r["ingots"] < RAW_IRON:
                r["shortfalls"]["minecraft:iron_ingot"] = RAW_IRON - r["ingots"]

            # 8. craft: sticks, the four armour pieces, the pickaxe (at the placed table)
            p = self.phase(r, "craft")
            for item, n in CRAFT_ORDER:
                res = await self.act(r, p, "craft", {"item": item, "count": n, "pos": table}, source)
                if res["status"] == "ok" and item != "minecraft:stick":
                    r["crafted"][item] = r["crafted"].get(item, 0) + n
            missing = [f"minecraft:{a}" for a in ARMOUR + ("iron_pickaxe",) if f"minecraft:{a}" not in r["crafted"]]
            self.close(r, p, "ok" if not missing else "short", ", ".join(missing))

            # 9. equip
            p = self.phase(r, "equip")
            res = await self.act(r, p, "equip_best", {}, source)                     # armour + offhand
            hand = await self.act(r, p, "equip_best", {"for_block": "minecraft:iron_ore", "slots": []}, source)
            ev = hand["evidence"] if isinstance(hand["evidence"].get("after"), dict) else res["evidence"]
            after = ev.get("after") if isinstance(ev.get("after"), dict) else \
                (self.mind.fresh_state() or {}).get("equipment") or {}
            r["equipped"] = {k: v for k, v in after.items() if v}
            self.close(r, p, res["status"], res["code"] or "")

            # 10. journal, grounded in this run's ok actions (O26: only what happened becomes history)
            ok_ids = [a["action_id"] for ph in r["phases"] for a in ph["actions"] if a["status"] == "ok"
                      and a["action_id"]]
            full = all(any(g in str(r["equipped"].get(s) or "") for g in GOOD) for s in ("head", "chest", "legs", "feet"))
            text = (f"Iron kit run: {r['raw_iron']} raw iron mined, {r['ingots']} ingots, crafted "
                    f"{', '.join(k.split(':')[1] for k in r['crafted']) or 'nothing'}; "
                    f"{'now in full iron armour' if full else 'armour incomplete'}."
                    + (f" Skipped to go mining: {'; '.join(r['deferred'])}." if r["deferred"] else ""))
            m.memory.journal(m.world_id, text, ok_ids, kind="objective", generated_by="mind")
            if full and "minecraft:iron_pickaxe" in r["crafted"]:
                return self.finish(r, "done", text)
            return self.finish(r, "partial", text + " Short: " + ", ".join(f"{n} {i}" for i, n in
                                                                          r["shortfalls"].items()))
        except asyncio.CancelledError:
            self.finish(r, "stopped", "cancelled")
            raise

    def cur_heading(self, r, entry):
        if not r["headings"]:
            h = self.heading(r, entry)
            self.route_row(r, "mine_raw_iron", entry, [(d, self.room(entry, d)) for d in HEADINGS], h, "first heading")
            r["headings"].append(h)
        return r["headings"][-1]

    def route_row(self, r, phase, at, sides, pick, why, tried=None):
        """O94f: an eve-candidates/1 route_choice row for the next prospect - every heading the rule weighed, scored by
        the zone room ahead (None without a box), tried or short of MIN_ROOM marked infeasible."""
        if pick is None:
            return
        opts = []
        for h, room in sides:
            bad = "tried" if h in (r["headings"] if tried is None else tried) else \
                "room" if room is not None and room < MIN_ROOM else None
            opts.append({"id": h, "action": {"skill": "prospect", "params": {"direction": h}}, "score": room,
                         "score_kind": "rule_rank", "feasible": bad is None, **({"rejected_by": bad} if bad else {})})
        st = self.mind.fresh_state() or {}
        state = {"pos": [int(st["pos"][a] // 1) for a in "xyz"] if isinstance(st.get("pos"), dict) else None,
                 "from": [int(at[a] // 1) for a in "xyz"], "tried": list(r["headings"]), "why": why,
                 "tool_uses": self.uses_left()}
        self.pending["prospect"] = self.mind.candidates.row("iron_kit", r["id"], phase, "route_choice", state, opts,
                                                            pick, tick=self.tick())

    def tunnel(self, r, entry):
        """The way her tunnel runs now: the heading from the entry, or where prospect turned it (O85 zone edge), or the
        branch's heading (O86v)."""
        return r.get("dir") or (r.get("branch") or {}).get("heading") or self.cur_heading(r, entry)

    async def prospect(self, r, p, params, source):
        """One prospect, watching where she stands (state at 20 Hz): the farthest cell she reached from where it began
        is that tunnel's farthest safe point - prospect stops before a hazard and walks back, so stop_at cannot say."""
        start = dict((self.mind.fresh_state() or {}).get("pos") or {}) or None
        far = {"far": None, "reach": 0}

        async def watch():
            while True:
                pos = (self.mind.fresh_state() or {}).get("pos")
                if start and pos:
                    d = abs(pos["x"] - start["x"]) + abs(pos["z"] - start["z"])
                    if d > far["reach"]:
                        far.update(far={a: pos[a] for a in "xyz"}, reach=round(d, 1))
                await asyncio.sleep(0.2)
        watcher = asyncio.get_running_loop().create_task(watch())
        try:
            res = await self.act(r, p, "prospect", params, source)
        finally:
            watcher.cancel()
        return res, far

    def branch_heading(self, at, heading):
        """O86v: the perpendicular with most zone room from `at`; never south with under MIN_ROOM (and none with under
        MIN_ROOM when the zone has a box). None when neither side has room."""
        sides = [(h, self.room(at, h)) for h in PERP.get(heading, ())]
        ok = [(h, rm) for h, rm in sides if (rm is None and h != "south") or (rm is not None and rm >= MIN_ROOM)]
        return max(ok, key=lambda x: -1 if x[1] is None else x[1])[0] if ok else None

    async def branch(self, r, p, source, why):
        """O86v (CP-40 a20: north and west no floor, east water after turning north - no heading left, no iron): once, a
        prospect off the longest safe tunnel, from its farthest safe point, at a right angle to it. False when done
        already, no tunnel reached BRANCH_MIN, no side has room, or she cannot get back there."""
        best = max((t for t in r["tunnels"] if t.get("far") and t["reach"] >= BRANCH_MIN), key=lambda t: t["reach"],
                   default=None)
        if r["branch"] or not best:
            return False
        h = self.branch_heading(best["far"], best["heading"])
        if not h:
            r["notes"].append(f"branch: no side of the {best['heading']} tunnel has room")
            return False
        back = await self.act(r, p, "walk_to", {"pos": best["far"], "tolerance": 1.0}, source)
        if back["status"] != "ok":
            r["notes"].append(f"branch: could not get back to {best['far']} ({back['code']})")
            return False
        self.route_row(r, "branch", best["far"], [(d, self.room(best["far"], d)) for d in PERP.get(best["heading"], ())],
                       h, f"branch off the {best['heading']} tunnel ({why})", tried=())
        r["branch"], r["dir"] = {"at": best["far"], "heading": h, "off": best["heading"], "reach": best["reach"]}, None
        at = " ".join(str(int(best["far"][a] // 1)) for a in "xyz")
        r["notes"].append(f"branch: {h} off the {best['heading']} tunnel at {at} (reach {best['reach']}, {why})")
        return True

    @staticmethod
    def ores(res):
        """Ore actually found. `mined` lists every drop: CP-40 a15's west heading dug 9 cobblestone, 0 ores, and was
        counted as a find, so east (room 19) was never tried. Skills' `ores` decides; filler never counts."""
        ev = res.get("evidence") or {}
        if "ores" in ev:
            return int(ev.get("ores") or 0)
        mined = ev.get("mined") if isinstance(ev.get("mined"), dict) else {}
        return sum(int(n or 0) for k, n in mined.items() if str(k).split(":")[-1] not in FILLER)

    async def next_heading(self, r, p, entry, source, why="hazard"):
        """O41l/O74: a prospect that ended (hazard, no floor, unreachable, ...) before finding a single ore tries the
        other headings with room, best room first, as fresh prospects from the entry; each heading's stop reason is
        noted. False when every heading with room is spent or she cannot get back to the entry."""
        left = self.rooms(r, entry)
        left = [h for h in HEADINGS if h not in r["headings"]] if left is None else left    # by room (O41n)
        if not left:
            r["notes"].append(f"headings: {r['headings'][-1]} stopped ({why}) with no ore; no untried heading with "
                              f"room left")
            return await self.branch(r, p, source, why)      # O86v: one perpendicular branch, then the run ends
        back = await self.act(r, p, "walk_to", {"pos": entry}, source)
        if back["status"] != "ok":
            r["notes"].append(f"headings: could not walk back to the entry ({back['code']})")
            return False
        r["notes"].append(f"headings: {r['headings'][-1]} stopped ({why}) with no ore; trying {left[0]} from the entry")
        self.route_row(r, "mine_raw_iron", entry, [(d, self.room(entry, d)) for d in HEADINGS], left[0], why)
        r["headings"].append(left[0])
        r["dir"] = None                                 # a fresh heading from the entry, not the old tunnel's turn
        return True

    async def fight_back(self, r, p, res, source):
        """O58: prospect stopped `hurt` naming a hostile (skills: evidence.threat {kind, health, entity_id, type,
        distance}, evidence.stop_at): with a weapon and health >= 8, fight_melee it, eat once nothing threatens, walk
        back to stop_at if the fight moved her and prospect again (same heading, same budget). False = go home."""
        ev = res.get("evidence") or {}
        th = ev.get("threat") if isinstance(ev.get("threat"), dict) else {}
        inv, st = self.inv()
        hp = th.get("health", st.get("health", 0))
        if th.get("entity_id") is None or len(r["fights"]) >= FIGHTS or hp < FIGHT_HEALTH or                 not any(i.endswith(WEAPONS) for i in inv if inv[i]):
            r["notes"].append(f"hurt: going home ({th.get('kind')} {th.get('type') or ''}, health {hp})".replace("  ", " "))
            return False
        res2 = await self.act(r, p, "fight_melee", {"entity_id": th["entity_id"], "radius": 8}, source)
        r["fights"].append({"entity_id": th["entity_id"], "type": th.get("type"), "status": res2["status"],
                            "code": res2["code"], "health_before": hp})
        if res2["status"] == "refused" and res2["code"] != "bad_target":    # bad_target: it is gone already
            r["notes"].append(f"fight: refused ({res2['code']}); going home")
            return False
        if res2["code"] == "health_floor" or (self.mind.fresh_state() or {}).get("health", 0) < FIGHT_HEALTH:
            r["notes"].append("fight: health too low afterwards; going home")
            return False
        await self.eat(r, p, source)
        stop_at, st = ev.get("stop_at"), self.mind.fresh_state() or {}
        if isinstance(stop_at, dict) and st.get("pos") and max(abs(st["pos"][a] - stop_at[a]) for a in "xyz") > 1.5:
            back = await self.act(r, p, "walk_to", {"pos": stop_at, "tolerance": 1.0}, source)
            if back["status"] != "ok":
                r["notes"].append(f"fight: could not get back to the tunnel ({back['code']})")
                return False
        r["notes"].append(f"fight {len(r['fights'])}: {th.get('type') or 'hostile'} {res2['status']}; resuming")
        return True

    async def eat(self, r, p, source):
        """Once no threat is listed (10 s at most): hotbar food + use_item, if she is not full."""
        for _ in range(20):
            st = self.mind.fresh_state() or {}
            if not st.get("threats"):
                break
            await asyncio.sleep(0.5)
        else:
            return
        if st.get("food", 20) >= 20:
            return
        slot = next((s["slot"] for s in st.get("inventory", []) if isinstance(s.get("slot"), int) and s["slot"] <= 8
                     and any(f in str(s.get("item")) for f in FOODS)), None)
        if slot is not None:
            await self.act(r, p, "select_hotbar", {"slot": slot}, source)
            await self.act(r, p, "use_item", {"hold_ms": 2000}, source)

    def uses_left(self):
        """Uses left on the best pickaxe she carries (max_damage - damage); 0 with none, a large number if the
        body does not report durability."""
        picks = [s for s in (self.mind.fresh_state() or {}).get("inventory", []) if "pickaxe" in s["item"]]
        if not picks:
            return 0
        return max((s["max_damage"] - s.get("damage", 0)) if s.get("max_damage") else 10 ** 6 for s in picks)

    async def retool(self, r, p, entry, source):
        """A new stone pickaxe in the tunnel: sticks from planks if needed (2x2, no table), her crafting table placed
        on the staircase step behind her (air with the step below as support), the pickaxe crafted there, the table
        mined back. Returns True when she holds a working pickaxe again."""
        inv, st = self.inv()
        if sum(inv[x] for x in STONE) < 3:
            r["notes"].append("retool: fewer than 3 cobblestone")
            return False
        if not inv["minecraft:crafting_table"]:
            r["notes"].append("retool: no crafting table carried")
            return False
        if inv["minecraft:stick"] < 2:
            if sum(n for i, n in inv.items() if i.endswith("_planks")) < 2:
                r["notes"].append("retool: no sticks and fewer than 2 planks")
                return False
            res = await self.act(r, p, "craft", {"item": "minecraft:stick", "count": 4}, source)
            if res["status"] != "ok":
                r["notes"].append(f"retool: sticks ({res['code']})")
                return False
        here = {a: int(st["pos"][a] // 1) for a in "xyz"}
        every, refused = self.table_options(r, entry, here), {}
        for table in [c for c, ok in every if ok][:TABLE_TRIES]:   # O93: inside the zone; a refusal tries the next
            self.pending["place_block"] = self.mind.candidates.row(   # O94f: every cell weighed, refused ones marked
                "iron_kit", r["id"], p["name"], "retool", {"pos": [here[a] for a in "xyz"], "tunnel": self.tunnel(r, entry)},
                [{"id": key, "action": {"skill": "place_block", "params": {"pos": c, "item": "minecraft:crafting_table"}},
                  "score": len(every) - i if ok and key not in refused else None, "score_kind": "rule_rank",
                  "feasible": ok and key not in refused,
                  **({} if ok and key not in refused else {"rejected_by": refused.get(key, "outside_zone")})}
                 for i, (c, ok) in enumerate(every) for key in [f"{c['x']} {c['y']} {c['z']}"]],
                f"{table['x']} {table['y']} {table['z']}", tick=self.tick())
            res = await self.act(r, p, "place_block", {"pos": table, "item": "minecraft:crafting_table"}, source)
            if res["status"] == "ok":
                break
            refused[f"{table['x']} {table['y']} {table['z']}"] = res["code"] or res["status"]
            r["notes"].append(f"retool: table at {table} ({res['code']})")
        else:
            r["notes"].append("retool: no cell for the table inside the mine zone took it")
            return False
        made = await self.act(r, p, "craft", {"item": "minecraft:stone_pickaxe", "count": 1, "pos": table}, source)
        back = await self.act(r, p, "mine_block", {"pos": table, "expect_block": "minecraft:crafting_table"}, source)
        if back["status"] != "ok":
            r["notes"].append(f"retool: table left at {table} ({back['code']})")
        if made["status"] != "ok":
            r["notes"].append(f"retool: stone pickaxe ({made['code']})")
            return False
        r["retools"] += 1
        r["notes"].append(f"retool {r['retools']}: stone pickaxe crafted in the tunnel")
        return True

    def table_cells(self, r, entry, here):
        """O93 (CP-40 a21: the table went twice to x 64 - the step behind her on the west branch, while fresh_mine ends
        at x 63 - zone_denied both times, no pickaxe, the run ended no_tool): where the retool table may go, the
        staircase step she came down from first, then the tunnel cells beside her (along the tunnel, then across it,
        feet before head height) - only cells inside the mine zone's box, which sense enforces."""
        return [c for c, ok in self.table_options(r, entry, here) if ok]

    def table_options(self, r, entry, here):
        """Every cell table_cells() weighs, in order, with whether it is inside the zone (O94f lists them all)."""
        dx, dz = STEP[self.tunnel(r, entry)]
        out = []
        for ox, oy, oz in ((-dx, 1, -dz), (-dx, 0, -dz), (dx, 0, dz), (dx, 1, dz), (dz, 0, dx), (-dz, 0, -dx),
                           (dz, 1, dx), (-dz, 1, -dx)):
            c = {"x": here["x"] + ox, "y": here["y"] + oy, "z": here["z"] + oz}
            if c not in [x for x, _ in out]:
                out.append((c, self.in_zone(c)))
        return out

    def in_zone(self, c):
        """Inside fresh_mine's box (inclusive); True when the zone has no box (sense then has none to enforce)."""
        z = self.zone(MINE_ZONE) or {}
        lo, hi = z.get("min"), z.get("max")
        if not (isinstance(lo, dict) and isinstance(hi, dict)):
            return True
        return all(lo[a] <= c[a] <= hi[a] for a in "xyz" if a in lo and a in hi)

    def room(self, entry, h):
        """Blocks of fresh_mine ahead of the entry on heading h (its box from the envelope = what sense enforces);
        None without a box."""
        z = self.zone(MINE_ZONE) or {}
        lo, hi = z.get("min"), z.get("max")
        if not (isinstance(lo, dict) and isinstance(hi, dict)):
            return None
        return {"east": hi["x"] - entry["x"], "west": entry["x"] - lo["x"], "south": hi["z"] - entry["z"],
                "north": entry["z"] - lo["z"]}[h]

    def rooms(self, r, entry):
        """O41n/O67: untried headings with at least MIN_ROOM blocks of the zone ahead, most room first; None when
        the zone has no box (then the old choice stands)."""
        if self.room(entry, "east") is None:
            return None
        return sorted((h for h in HEADINGS if self.room(entry, h) >= MIN_ROOM and h not in r["headings"]),
                      key=lambda h: -self.room(entry, h))

    def descend_to(self, entry, h):
        """O67 (a11 refused: entry y 67, best room 46): the staircase drops one block per block of tunnel, so it
        stops where the zone still has MIN_ROOM blocks ahead - never below DESCEND_TO (iron level)."""
        room = self.room(entry, h) if h else None
        if room is None:
            return DESCEND_TO
        return max(DESCEND_TO, math.ceil(entry["y"] - room + MIN_ROOM))

    def heading(self, r, entry):
        """The tunnel heading: the one the harness named if it has room in the zone, else the one with most room
        (O41n); without a zone box, the harness's or the compass direction from home's centre to the mine entry."""
        ok = self.rooms(r, entry)
        if ok is not None:
            return ok[0] if ok else None                         # O67: the most room, whatever was asked
        if r.get("direction") in ("north", "south", "east", "west"):
            return r["direction"]
        c = self.mind.env.get("home", {}).get("center") or {"x": 0, "z": 0}
        dx, dz = entry["x"] - c["x"], entry["z"] - c["z"]
        return ("east" if dx > 0 else "west") if abs(dx) >= abs(dz) else ("south" if dz > 0 else "north")

    async def go_home(self, r, source):
        p = self.phase(r, "home")
        res = await self.act(r, p, "go_home", {}, source)
        if res["code"] == "home_blocked" and await self.dig_up(r, p, source):
            res = await self.act(r, p, "go_home", {}, source)
        self.close(r, p, res["status"], res["code"] or "")
        return res["status"] == "ok"

    def below(self, entry):
        """In the mine: feet more than a block under the mine entry, inside the zone's x/z box when it has one (a18's
        shelter at y 63 is 8 under its entry, outside the box)."""
        pos = (self.mind.fresh_state() or {}).get("pos")
        try:
            if pos["y"] >= float(entry["y"]) - 1:
                return False
        except (KeyError, TypeError, ValueError):
            return False
        z = self.zone(MINE_ZONE) or {}
        lo, hi = z.get("min"), z.get("max")
        return not (isinstance(lo, dict) and isinstance(hi, dict)) or all(lo[a] <= pos[a] < hi[a] + 1 for a in "xz")

    async def dig_up(self, r, p, source):
        """O86/O86a (CP-40 a18: the flooded staircase left go_home no route, and she was stranded underground at
        nightfall): in the mine, a pickaxe first (made from cobblestone, sticks and her table if hers broke, as in a18),
        then skills' dig_up up to the entry's level - a staircase with a pickaxe, a pillar of her filler without one.
        True when she got there; else her stop_reason is `stranded` and the owner is called."""
        if not self.below(r.get("entry")):
            return False
        to = {a: int(float(r["entry"][a]) // 1) for a in "xyz"}
        if "dig_up" not in self.mind.registry.skills:
            return await self.stranded(r, "no dig_up skill")
        if not self.uses_left() and r["retools"] < RETOOLS:
            await self.retool(r, p, to, source)            # without one, dig_up pillars with her filler (O86a, skills)
        await self.act(r, p, "equip_best", {"for_block": "minecraft:stone", "slots": []}, source)
        res = await self.act(r, p, "dig_up", {"toward": to}, source)
        if res["status"] != "ok":
            bare = "" if self.uses_left() else "; no pickaxe"
            return await self.stranded(r, f"dig_up {res['status']} ({res['code']}{bare})")
        r["notes"].append("home_blocked underground: dig_up ok, up to the entry's level")
        return True

    async def stranded(self, r, why):
        """O86a: stuck underground with no way up - an emergency for the owner, never a quiet wait in the hole (the
        night runner reads this stop_reason)."""
        pos = (self.mind.fresh_state() or {}).get("pos") or {}
        at = " ".join(str(int(pos[a] // 1)) for a in "xyz") if all(a in pos for a in "xyz") else "?"
        r["stop_reason"] = "stranded"
        r["notes"].append(f"stranded underground at {at}: {why}")
        self.save(r)
        self.mind.event("mind", "emergency", {"kind": "stranded", "objective_id": r["id"], "pos": pos, "why": why})
        await self.mind.say(f"I'm stuck underground at {at} ({why}) and can't get home. Please come and get me.")
        return False
