"""CP-51: build an approved design, gathering what is missing - deterministic, bounded, honest.

1. Dispatch build_schematic. Builder refuses `no_item` BEFORE placing anything (or ends `partial` mid-build), with
   evidence.deficit = {want, inventory, missing} in registry item ids (builder 83e92eb).
2. Plan the missing items from what the records hold, in this order:
   - withdraw from `shared` containers in the storage index, minus other tasks' open reservations (reserved here);
   - what is still missing: recipes.solve over the surplus inventory (held minus what the build wants) plus the
     shared storage; inputs it uses are withdrawn too, base materials it lacks are mined (mine_resource) only when
     they have a known block source, smelts need a known furnace, table crafts a known crafting table;
   - anything left is a SHORTAGE: the run stops and reports exactly what is short and why, before any destructive
     gathering, and asks for an approved alternative. It never substitutes on its own and never claims completion.
3. Run the plan (walk next to each container/workstation first; every step through Mind.do()), then re-dispatch
   build_schematic, which skips cells already right. A full inventory is handled once per withdraw by depositing
   surplus (never tools, weapons, armour or food) into that container.
Bounded: MAX_ROUNDS supply rounds, a step budget, and a round that gathered nothing ends the run.
"""
import asyncio
import json
import time
import uuid
from collections import Counter

from .recipes import RecipeBook, solve

MAX_ROUNDS = 3
MAX_STEPS = 80
RESERVE_S = 1800
# ponytail: base materials Eve may mine, item -> block for mine_resource. Anything else is a shortage to report.
MINE_SOURCE = {"minecraft:cobblestone": "minecraft:stone", "minecraft:raw_iron": "minecraft:iron_ore",
               "minecraft:coal": "minecraft:coal_ore", "minecraft:raw_copper": "minecraft:copper_ore",
               "minecraft:sand": "minecraft:sand", "minecraft:dirt": "minecraft:dirt",
               "minecraft:gravel": "minecraft:gravel", "minecraft:clay_ball": "minecraft:clay",
               "minecraft:obsidian": "minecraft:obsidian",
               **{f"minecraft:{w}_log": f"minecraft:{w}_log" for w in
                  ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry")}}
# O150: a block that drops nothing without the right pickaxe is mined only when she holds one; else this is the reason
NEEDS_TOOL = {"minecraft:obsidian": (("minecraft:diamond_pickaxe", "minecraft:netherite_pickaxe"),
                                     "I need a diamond pickaxe to mine obsidian, or make it with water on lava")}
ESSENTIAL = ("sword", "pickaxe", "axe", "shovel", "hoe", "bow", "shield", "helmet", "chestplate", "leggings", "boots",
             "bread", "apple", "beef", "pork", "chicken", "mutton", "carrot", "potato", "cod", "salmon", "stew")


def near(pos):
    """A standing spot next to a block: one block south of it, same level (ponytail: one approach; a failed walk
    ends the round and the next round re-plans)."""
    return {"x": pos["x"] + 0.5, "y": pos["y"], "z": pos["z"] + 1.5}


class Run:
    def __init__(self, params, rid=None):
        self.id = rid or uuid.uuid4().hex[:12]
        self.params, self.status, self.reason = params, "running", ""
        self.rounds, self.steps, self.ledger, self.short, self.notes = 0, 0, [], {}, []
        self.last_code = None
        self.gathered = []          # items gathered in the last round (a re-dispatch short of one of them counts)

    def as_dict(self):
        return {k: getattr(self, k) for k in ("id", "params", "status", "reason", "rounds", "steps", "ledger",
                                              "short", "notes")}

    def report(self):
        if self.status == "done":
            got = "; ".join(x["done"] for x in self.ledger if x["skill"] != "walk_to") or "nothing needed"
            return f"done: the design is built (gathered: {got})"
        if self.status == "short":
            return ("short: " + "; ".join(f"{n} {i} ({why})" for i, (n, why) in self.short.items()) +
                    ". Nothing was placed for the missing part; give me an approved alternative or the items.")
        return f"{self.status}: {self.reason}"


class Supply:
    def __init__(self, mind):
        self.mind, self.current, self.task, self._book = mind, None, None, None
        self.pending = {}         # O94f: eve-candidates/1 rows waiting for the dispatch that carries out their pick

    @property
    def book(self):
        if self._book is None:
            self._book = RecipeBook(self.mind.cfg.root / "data" / "game" / "26.3")
        return self._book

    # ---- control
    def start(self, params, source="cli"):
        if self.task and not self.task.done():
            return {"ok": False, "error": "busy", "detail": f"supply build {self.current.id} is running"}
        self.current = r = Run(params)
        self.save(r)
        self.task = asyncio.get_running_loop().create_task(self.run(r, source))
        return {"ok": True, "supply_id": r.id, "status": r.status}

    def save(self, r):
        try:
            self.mind.set_meta({f"supply.{r.id}": json.dumps(r.as_dict())})
        except Exception:
            self.mind.metrics["db_errors"] += 1

    def status(self, rid=None):
        r = self.current if not rid or (self.current and self.current.id == rid) else None
        if r is None and rid:
            raw = self.mind.meta(f"supply.{rid}")
            if raw:
                d = json.loads(raw)
                r = Run(d["params"], d["id"])
                r.__dict__.update(d)
        if r is None:
            return {"ok": False, "error": "unknown_supply", "detail": rid}
        return {"ok": True, **r.as_dict(), "report": r.report()}

    def finish(self, r, status, reason=""):
        r.status, r.reason = status, reason
        self.mind.release(f"supply:{r.id}", f"supply {status}")
        self.save(r)
        self.mind.event("mind", "supply", {"id": r.id, "status": status, "report": r.report()})

    # ---- records
    @staticmethod
    def _inv_pairs(st):
        c = Counter()
        for s in st.get("inventory", []):
            c[s["item"]] += int(s.get("count") or 0)
        return c.items()

    def storage(self, rid):
        """{container_id: (pos, Counter)} of shared containers, minus every other task's open reservations."""
        m, now = self.mind, int(time.time() * 1000)
        out = {}
        for c in m.db.execute("SELECT container_id, x, y, z FROM containers WHERE world_id=? AND gone=0 AND "
                              "tag='shared'", (m.world_id,)):
            items = Counter({r["item"]: r["count"] for r in m.db.execute(
                "SELECT item, count FROM container_items WHERE container_id=?", (c["container_id"],))})
            for r in m.db.execute("SELECT item, count FROM reservations WHERE container_id=? AND released_ts IS NULL "
                                  "AND (expires_ts IS NULL OR expires_ts > ?) AND task_id != ?",
                                  (c["container_id"], now, f"supply:{rid}")):
                items[r["item"]] -= r["count"]
            out[c["container_id"]] = ({"x": c["x"], "y": c["y"], "z": c["z"]}, +items)
        return out

    def workstation(self, kind):
        """A known workstation of this kind, from the records: storage.index entries (furnaces are containers; a
        crafting table appears there once sense indexes workstations), else a block Eve saw placed and not since
        broken, else the resources index. Furnaces must be shared (Eve's own input goes in); a table is a non-container
        interaction, allowed in any zone."""
        m, block = self.mind, f"minecraft:{kind}"
        shared = " AND tag='shared'" if kind == "furnace" else ""
        r = m.db.execute(f"SELECT x, y, z FROM containers WHERE world_id=? AND gone=0 AND kind=?{shared} LIMIT 1",
                         (m.world_id, block)).fetchone()
        if r is None:
            for b in m.db.execute("SELECT x, y, z, id FROM block_events WHERE world_id=? AND kind='place' AND after "
                                  "LIKE ? ORDER BY id DESC", (m.world_id, f"%{block}%")):
                if not m.db.execute("SELECT 1 FROM block_events WHERE world_id=? AND x=? AND y=? AND z=? AND id>? AND "
                                    "kind IN ('break','explode','burn')", (m.world_id, b["x"], b["y"], b["z"],
                                                                         b["id"])).fetchone():
                    r = b
                    break
        if r is None:
            r = m.db.execute("SELECT x, y, z FROM resources WHERE world_id=? AND stale=0 AND block=? LIMIT 1",
                             (m.world_id, block)).fetchone()
        return {"x": r["x"], "y": r["y"], "z": r["z"]} if r else None

    def private_holders(self, item):
        """Positions of containers Eve may not open (not shared) that the index says hold this item."""
        return [f"{r['x']} {r['y']} {r['z']}" for r in self.mind.db.execute(
            "SELECT c.x, c.y, c.z FROM containers c JOIN container_items i ON i.container_id=c.container_id WHERE "
            "c.world_id=? AND c.gone=0 AND c.tag!='shared' AND i.item=? AND i.count>0", (self.mind.world_id, item))]

    # ---- the plan
    def plan(self, r, deficit, st):
        """(steps, short): steps = [(skill, params, why)] in order; short = {item: (count, why)}."""
        want = Counter(deficit.get("want") or {})
        missing = Counter({k: int(v) for k, v in (deficit.get("missing") or {}).items() if int(v) > 0})
        surplus = Counter(dict(self._inv_pairs(st))) - want
        store = self.storage(r.id)
        withdraws, short = [], {}

        def take(item, n):
            got = 0
            for cid, (pos, items) in sorted(store.items(), key=lambda kv: -kv[1][1][item]):
                k = min(n - got, items[item])
                if k > 0:
                    opts = [{"id": c, "action": {"skill": "withdraw", "params": {"pos": ps, "item": item}},
                             "score": its[item], "score_kind": "rule_rank", "feasible": its[item] > 0,
                             **({} if its[item] > 0 else {"rejected_by": "none_left"})} for c, (ps, its) in store.items()]
                    items[item] -= k
                    params = {"pos": pos, "item": item, "count": k}
                    withdraws.append(("withdraw", params, cid))
                    self.pending[("withdraw", json.dumps(params, sort_keys=True))] = self.mind.candidates.row(
                        "supply", r.id, "plan", "supply_source", {"item": item, "need": n - got}, opts, cid)   # O94f
                    got += k
                if got >= n:
                    break
            return got

        for item in list(missing):
            missing[item] -= take(item, missing[item])
        rest = +missing
        steps = []
        if rest:
            pool = surplus + sum((items for _, items in store.values()), Counter())
            sol = solve(self.book, dict(rest), dict(pool))
            for item, n in sol["consumed"].items():
                n -= min(n, surplus[item])
                if n and take(item, n) < n:
                    short[item] = (n, "the recipe needs it and storage no longer has it")
            held = dict(self._inv_pairs(st))
            for item, n in sol["missing"].items():
                tools, why = NEEDS_TOOL.get(item, ((), ""))
                if tools and not any(held.get(t) for t in tools):
                    short[item] = (n, why)
                elif item in MINE_SOURCE:
                    steps.append(("mine_resource", {"block": MINE_SOURCE[item], "count": n}, None))
                else:
                    short[item] = (n, "not in shared storage, no recipe from what I can reach, no known place to "
                                      "mine it")
            for line in sol["impossible"]:
                short[line.split(" ")[0]] = (rest.get(line.split(" ")[0], 0), "no approved recipe makes it")
            for c in sol["crafts"]:
                item, count = next(iter(c["output"].items()))
                if c["kind"] == "smelting":
                    furnace = self.workstation("furnace")
                    rec = next(x for x in self.book.by_output[item] if x["id"] == c["recipe"])
                    if furnace is None:
                        short[item] = (count, "it needs smelting and I know no shared furnace")
                        continue
                    steps.append(("smelt", {"pos": furnace, "item": rec["inputs"][0][0][0], "count": c["times"]},
                                  None))
                else:
                    p = {"item": item, "count": count}
                    if c["station"]:
                        table = self.workstation(c["station"])
                        if table is None:
                            short[item] = (count, f"it needs a {c['station']} and I know none")
                            continue
                        p["pos"] = table
                    steps.append(("craft", p, None))
        for item, (n, why) in list(short.items()):   # the reason the owner can act on comes first (CP-51 a2 d)
            where = self.private_holders(item)
            if where:
                why = f"only in a private container at {', '.join(where)}; I never open private containers"
            short[item] = (rest.get(item, n), why)  # the count the build is short, not a recipe batch
        return withdraws + steps, short

    # ---- running
    async def step(self, skill, params, source):
        m = self.mind
        res = await m.do(skill, params, source=source, grant=True)
        if res.get("code") == "stale_state" and not m.stopped:       # CP-51 a1: a state frame was late; once more
            await asyncio.sleep(1.0)
            res = await m.do(skill, params, source=source, grant=True)
        aid = res.get("action_id")
        if not aid or not res.get("ok"):
            return {"status": "refused", "code": res.get("code") or res.get("error"), "evidence": {}}
        timeout = m.registry.skills[skill]["timeout_ms"] / 1000 + m.cfg.result_grace_s + 1
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not m.stopped:
            if m.status_of(aid) not in ("pending", "sent", "accepted", "running"):
                break
            await asyncio.sleep(0.1)
        row = m.action(aid)
        ev = json.loads(row["evidence"]) if row["evidence"] else {}
        return {"status": row["status"], "code": row["code"], "evidence": ev if isinstance(ev, dict) else {},
                "action_id": aid}

    async def go(self, r, skill, params, source):
        """One gathering step with its approach walk. True when it succeeded."""
        if r.steps >= MAX_STEPS:
            return False
        if "pos" in params and skill in ("withdraw", "deposit", "smelt", "craft"):
            w = await self.step("walk_to", {"pos": near(params["pos"])}, source)
            r.steps += 1
            if w["status"] != "ok":
                r.notes.append(f"walk next to {params['pos']} failed ({w['code']})")
                return False
        res = await self.step(skill, params, source)
        r.steps += 1
        pend = self.pending.pop((skill, json.dumps(params, sort_keys=True)), None)
        if pend:                                              # O94f: the row joins its outcome by this action_id
            self.mind.candidates.write(pend, res.get("action_id"))
        if res["status"] == "ok":
            r.ledger.append({"skill": skill, "params": params,
                             "done": f"{skill} {params.get('count', '')} {params.get('item') or params.get('block')}"})
            return True
        r.notes.append(f"{skill} {params.get('item') or params.get('block')} failed ({res['code']})")
        r.last_code = res["code"]
        return False

    def reserve(self, r, cid, item, n):
        now = int(time.time() * 1000)
        self.mind.db.execute("INSERT INTO reservations (reservation_id, task_id, container_id, item, count, "
                             "created_ts, expires_ts) VALUES (?,?,?,?,?,?,?)",
                             (uuid.uuid4().hex, f"supply:{r.id}", cid, item, n, now, now + RESERVE_S * 1000))

    async def make_room(self, r, st, want, pos, source):
        """Inventory full: put surplus (never essentials, never what the build wants) into that container."""
        inv = Counter(dict(self._inv_pairs(st))) - want
        spare = [(i, n) for i, n in inv.items() if n > 0 and not any(e in i for e in ESSENTIAL)]
        if not spare:
            return False
        i, n = max(spare, key=lambda x: x[1])
        return await self.go(r, "deposit", {"pos": pos, "item": i, "count": n}, source)

    async def run(self, r, source):
        m = self.mind
        try:
            while True:
                if m.stopped:
                    return self.finish(r, "stopped", "owner stop; run the build again to resume (done cells are kept)")
                res = await self.step("build_schematic", r.params, source)
                r.steps += 1
                if res["status"] == "ok":
                    return self.finish(r, "done")
                deficit = res["evidence"].get("deficit")
                missing = (deficit or {}).get("missing") or {} if isinstance(deficit, dict) else {}
                again = sorted(set(missing) & set(r.gathered))
                if res.get("action_id") and res["code"] in ("no_item", "partial") and not again:
                    # ruling (CP-51 a1): the opening no_item is a deliberate probe for the deficit and a re-dispatch
                    # after gathering is not a retry; only a dispatch short of an item the loop just supplied counts
                    m.db.execute("UPDATE actions SET reconcile_note=? WHERE action_id=?",
                                 ("retry_exempt: supply probe" if r.rounds == 0 else "retry_exempt: supply re-dispatch",
                                  res["action_id"]))
                if res["code"] not in ("no_item", "partial") or not isinstance(deficit, dict) or \
                        not deficit.get("missing"):
                    return self.finish(r, "failed", f"build_schematic {res['status']} ({res['code']})")
                if r.rounds >= MAX_ROUNDS:
                    return self.finish(r, "partial", f"still short after {MAX_ROUNDS} supply rounds: "
                                                     f"{deficit['missing']}")
                r.rounds += 1
                r.gathered = []
                st = m.fresh_state() or {}
                steps, short = self.plan(r, deficit, st)
                if short:
                    r.short = short
                    return self.finish(r, "short", "")
                for skill, params, cid in steps:
                    if cid:
                        self.reserve(r, cid, params["item"], params["count"])
                gathered = 0
                for skill, params, cid in steps:
                    if m.stopped:
                        break
                    ok = await self.go(r, skill, params, source)
                    if not ok and skill == "withdraw" and r.last_code == "inventory_full":
                        if await self.make_room(r, m.fresh_state() or st, Counter(deficit.get("want") or {}),
                                                params["pos"], source):
                            ok = await self.go(r, skill, params, source)
                    if not ok:
                        break                   # re-dispatch the build: it recomputes the deficit from the world
                    gathered += 1
                    if params.get("item") and skill in ("withdraw", "craft"):
                        r.gathered.append(params["item"])
                self.save(r)
                if not gathered and not m.stopped:
                    return self.finish(r, "partial", "a supply round gathered nothing: " + "; ".join(r.notes[-2:]))
        except asyncio.CancelledError:
            self.finish(r, "stopped", "cancelled")
            raise


# ---- CP-52: restore a zone to an approved snapshot (link v1.3 zone.repair -> repair_zone) -----------------------
CROP_SKIP = "crop_cycle"


def only_allowed(m, field, rows):
    """O49: link messages may carry extra fields, skill params are strict - keep only the keys repair_zone's registry
    entry allows for each row of `field` (steps: op/pos/block; replant: pos/block)."""
    keys = m.registry.skills["repair_zone"]["params"]["properties"][field]["items"]["properties"]
    return [{k: v for k, v in x.items() if k in keys} for x in rows]


class Repair(Supply):
    """`repair <zone> <snapshot>`: ask sense for the restore diff, grant exactly its box (grant_regions, from sense's
    own answer), dispatch repair_zone with those steps (+ replant for harvested crops), gather supplies like CP-51 on
    no_item, re-ask until sense has no steps left. Owner edits after the snapshot are sense's `skipped` and are never
    touched; containers are `manual` (O17) and are reported to the owner, not changed."""

    @staticmethod
    def site_box(box):
        """The repair site {min, max} with integer x y z (normalised so min <= max), or None."""
        try:
            lo = {a: int(min(box["min"][a], box["max"][a])) for a in "xyz"}
            hi = {a: int(max(box["min"][a], box["max"][a])) for a in "xyz"}
        except (KeyError, TypeError, ValueError):
            return None
        return {"min": lo, "max": hi}

    @staticmethod
    def outside_box(ans, box):
        """Why sense's zone.repair answer is not limited to the site box, or None. eve-sense <= 0.1.13 ignores
        `box` and answers for the whole zone; 0.1.15 echoes the box it used, clamped to the snapshot."""
        inside = lambda p: all(box["min"][a] <= p[a] <= box["max"][a] for a in "xyz")  # noqa: E731
        got = ans.get("box")
        if not (isinstance(got, dict) and isinstance(got.get("min"), dict) and isinstance(got.get("max"), dict)):
            return "it does not echo the site box (eve-sense older than 0.1.15 answers for the whole zone)"
        if not (inside(got["min"]) and inside(got["max"])):
            return f"its box {got} is not inside the site box {box}"
        cells = [x["pos"] for x in ans["steps"]] + [x["pos"] for x in ans.get("skipped", [])
                                                     if x.get("reason") == CROP_SKIP]
        bad = [p for p in cells if not inside(p)]
        return f"{len(bad)} step(s) outside the site box, e.g. {bad[0]}" if bad else None

    def start(self, zone_id, snapshot_id, box=None, source="cli"):
        """A repair is always limited to the repair site's box: a snapshot covers its whole zone, and a whole-zone
        repair would undo other work built there after the snapshot (sense, lane B: eve-skills' base)."""
        box = self.site_box(box) if box is not None else None
        if box is None:
            return {"ok": False, "error": "bad_args",
                    "detail": "give the repair site's box (min and max x y z); I never repair a whole zone"}
        if self.task and not self.task.done():
            return {"ok": False, "error": "busy", "detail": f"repair {self.current.id} is running"}
        if self.mind.supply.task and not self.mind.supply.task.done():
            return {"ok": False, "error": "busy", "detail": "a supply build is running"}
        self.current = r = Run({"zone_id": zone_id, "snapshot_id": snapshot_id, "box": box})
        r.manual, r.skipped = [], []
        self.save(r)
        self.task = asyncio.get_running_loop().create_task(self.run(r, source))
        return {"ok": True, "repair_id": r.id, "status": r.status}

    def save(self, r):
        try:
            self.mind.set_meta({f"repair.{r.id}": json.dumps({**r.as_dict(), "manual": getattr(r, "manual", []),
                                                              "skipped": getattr(r, "skipped", [])})})
        except Exception:
            self.mind.metrics["db_errors"] += 1

    def report(self, r):
        if r.status != "done":
            return r.report()
        return ("done: the zone matches the snapshot" +
                (f"; {len(r.manual)} container(s) to restore from the backup by hand (O17)" if r.manual else "") +
                (f"; {len(r.skipped)} later change(s) of yours kept" if r.skipped else ""))

    def finish(self, r, status, reason=""):
        r.status, r.reason = status, reason
        self.mind.release(f"supply:{r.id}", f"repair {status}")
        self.save(r)
        self.mind.event("mind", "repair", {"id": r.id, "status": status, "report": self.report(r),
                                           "manual": r.manual[:20], "skipped": len(r.skipped)})

    def status(self, rid=None):
        r = self.current if self.current and (not rid or self.current.id == rid) else None
        if r is None:
            raw = self.mind.meta(f"repair.{rid}") if rid else None
            if not raw:
                return {"ok": False, "error": "unknown_repair", "detail": rid}
            return {"ok": True, **json.loads(raw)}
        return {"ok": True, **r.as_dict(), "manual": r.manual, "skipped": r.skipped, "report": self.report(r)}

    async def run(self, r, source):
        m, z, snap = self.mind, r.params["zone_id"], r.params["snapshot_id"]
        try:
            stalls = 0
            while True:
                if m.stopped:
                    return self.finish(r, "stopped", "owner stop; run the repair again to resume (it re-asks sense)")
                ans = await m.hub.request("sense", "zone.repair", {"zone_id": z, "snapshot_id": snap,
                                                                   "box": r.params["box"]}, timeout=15)
                if not isinstance(ans, dict) or "steps" not in ans:
                    why = (ans or {}).get("message") or (ans or {}).get("code") or "no answer within 15 s"
                    return self.finish(r, "failed", f"sense zone.repair: {why}")
                outside = self.outside_box(ans, r.params["box"])
                if outside:                            # fail safe: an old sense answers for the whole zone
                    return self.finish(r, "failed", f"refused sense's answer: {outside}; nothing was changed")
                r.manual = list(ans.get("manual", []))
                r.skipped = [x for x in ans.get("skipped", []) if x.get("reason") != CROP_SKIP]
                steps = ans["steps"]
                replant = [{"pos": x["pos"], "block": x["block"]} for x in ans.get("skipped", [])
                           if x.get("reason") == CROP_SKIP and x.get("block") and
                           str(x.get("actual", "")).split("[")[0] in ("minecraft:air", "air")]
                m.repair_answers[(z, snap)] = (
                    {(x["op"], x["pos"]["x"], x["pos"]["y"], x["pos"]["z"], x["block"]) for x in steps} |
                    {("replant", x["pos"]["x"], x["pos"]["y"], x["pos"]["z"], x["block"]) for x in replant})
                if not steps and not replant:
                    return self.finish(r, "done")
                if r.rounds >= MAX_ROUNDS + 2 or stalls >= 2:
                    return self.finish(r, "partial", f"{len(steps)} step(s) still differ after {r.rounds} rounds: "
                                                     + "; ".join(r.notes[-2:]))
                r.rounds += 1
                actors = sorted({x["actor"] for x in steps if x.get("actor")})
                if actors:                             # O49: sense's actor stays in our record, not in the params
                    r.notes.append(f"round {r.rounds}: step actors {actors}")
                params = {"zone_id": z, "snapshot_id": snap, "steps": only_allowed(m, "steps", steps)}
                if replant:
                    params["replant"] = only_allowed(m, "replant", replant)
                res = await self.step("repair_zone", params, source)
                r.steps += 1
                if res["status"] == "ok":
                    stalls = 0
                    continue                           # re-ask: sense says what (if anything) still differs
                deficit = res["evidence"].get("deficit")
                missing = deficit.get("missing") if isinstance(deficit, dict) else None
                if res["code"] in ("no_item", "partial") and missing:
                    if res.get("action_id"):           # the opening no_item is a probe, not a retry (ruling, CP-51)
                        m.db.execute("UPDATE actions SET reconcile_note=? WHERE action_id=?",
                                     ("retry_exempt: repair supply probe", res["action_id"]))
                    plan, short = self.plan(r, deficit, m.fresh_state() or {})
                    if short:
                        r.short = short
                        return self.finish(r, "short", "")
                    for skill, p_, cid in plan:
                        if cid:
                            self.reserve(r, cid, p_["item"], p_["count"])
                    gathered = 0
                    for skill, p_, cid in plan:
                        if m.stopped or not await self.go(r, skill, p_, source):
                            break
                        gathered += 1
                    stalls = 0 if gathered else stalls + 1
                    continue
                r.notes.append(f"repair_zone {res['status']} ({res['code']})")
                stalls += 1
        except asyncio.CancelledError:
            self.finish(r, "stopped", "cancelled")
            raise
