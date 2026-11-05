"""O132 (45L session 3: "build a small wood house" -> "I couldn't make a safe plan for that: step 3: mine_block target
102,70,31 was not given in the request"): an owner's building brief, by rule (no planner, no model).

  brief ("build a small wood house")
  -> a design from the catalogue (designs/<project>/v<N>/brief.json names), its gate re-read
  -> a site near him: flat within 1 block, no water, nothing built within MARGIN blocks, inside the envelope (sense
     query.blocks; nothing guessed)
  -> ONE question naming the corner, the floor height and what she would clear ("... yes / no")
  -> his "yes" writes the approval row (decide, channel ingame_confirm: kickoff O132 ruling 2), then: re-read the
     site, clear it (mine_block,
     drops kept), and hand the build to the CP-51 supply loop (withdraw / recipes / mine_resource / build_schematic),
     saying each phase and the end. Every refusal is said. Design note: parts/mind/DESIGN-O132-owner-build-flow.md.

Hooked from Mind._owner_text by one call (owner_line); every dispatch goes through Mind.do() via supply.step.
"""
import math
import re
import time
from collections import Counter

from . import craftkit, owner_phrases
from .grammar import BRIEF  # noqa: F401  O205 step 4: the brief phrase is the one grammar's, unchanged
from .planner import bounds, envelope_allows
from .recipes import solve

RADIUS = 6            # corners tried up to this many blocks past him (each side)
MARGIN = 3            # nothing built within this many blocks of the footprint
MAX_CLEAR = 120       # more than this is no site for a small house
REACH_DY = 3          # she clears cells up to floor + 3 from the ground; higher ones the builder breaks itself
PROGRESS_EVERY = 10
TTL_S = 120
SCAN_DOWN, SCAN_UP = 4, 9

AIR = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
FLUID = {"minecraft:water", "minecraft:lava", "minecraft:bubble_column", "minecraft:seagrass", "minecraft:tall_seagrass",
         "minecraft:kelp", "minecraft:kelp_plant"}
GROUND = {f"minecraft:{b}" for b in (
    "grass_block", "dirt", "coarse_dirt", "podzol", "rooted_dirt", "mycelium", "mud", "moss_block", "clay", "sand",
    "red_sand", "gravel", "stone", "granite", "diorite", "andesite", "deepslate", "tuff", "calcite", "sandstone",
    "red_sandstone", "terracotta", "snow_block", "packed_ice", "coal_ore", "iron_ore", "copper_ore")}
SOFT = {f"minecraft:{b}" for b in (
    "short_grass", "tall_grass", "grass", "fern", "large_fern", "dead_bush", "bush", "firefly_bush", "dandelion", "poppy",
    "blue_orchid", "allium", "azure_bluet", "oxeye_daisy", "cornflower", "lily_of_the_valley", "sunflower", "lilac",
    "rose_bush", "peony", "pink_petals", "wildflowers", "leaf_litter", "snow", "vine", "moss_carpet", "sweet_berry_bush",
    "pumpkin", "melon", "sugar_cane", "bamboo", "cactus", "short_dry_grass", "tall_dry_grass", "torchflower")}
SOFT_END = ("_log", "_wood", "_leaves", "_sapling", "_tulip", "_mushroom", "_stem")
STOP_CODES = {"zone_denied", "cancelled", "interrupted", "not_approved", "mode_denied", "stopped"}
DROP = {"a", "an", "the", "me", "us", "little", "nice", "new", "please", "for", "of", "my", "our"}
SAME = {"wooden": "wood", "planks": "wood", "plank": "wood", "home": "house", "shack": "hut"}


def kind(b):
    """air | ground | soft (cleared, drops kept) | fluid | built (someone's work: never touched) | unknown."""
    if b is None:
        return "unknown"
    if b in AIR:
        return "air"
    if b in FLUID:
        return "fluid"
    if b in GROUND:
        return "ground"
    if b in SOFT or b.endswith(SOFT_END) and "stripped" not in b:
        return "soft"
    return "built"


def words(s):
    return {SAME.get(w, w) for w in re.findall(r"[a-z0-9]+", s.lower().replace("_", " "))} - DROP


def survey(blocks):
    """Once per scan: each column's top solid cell {(x, z): y} (not air, not a plant or tree) and every built cell."""
    tops, built = {}, []
    for (x, y, z), b in blocks.items():
        k = kind(b)
        if k == "built":
            built.append((x, y, z))
        if k in ("ground", "fluid", "built") and y > tops.get((x, z), -10**6):
            tops[(x, z)] = y
    return tops, built


def site_plan(blocks, size, ox, oz, oy=None, seen=None):
    """One corner (ox, oz), footprint size (sx, sy, sz). oy=None: the floor goes on the ground (flat within 1: the
    higher cells are dug); oy given: that floor height, as he stated it. seen: survey(blocks), reused across corners.
    -> (plan, None) or (None, reason). plan: {origin, clear: [(pos, block)], high: cells left to the builder}."""
    sx, sy, sz = size
    xs, zs = range(ox, ox + sx), range(oz, oz + sz)
    col, built = seen or survey(blocks)
    if oy is None:
        tops = []
        for x in xs:
            for z in zs:
                top = col.get((x, z))
                if top is None:
                    return None, f"no ground under {x} {z}"
                if kind(blocks[(x, top, z)]) == "fluid":
                    return None, f"water at {x} {top} {z}"
                if kind(blocks[(x, top, z)]) == "built":
                    return None, f"something built at {x} {top} {z} ({blocks[(x, top, z)].split(':')[1]})"
                tops.append(top)
        if max(tops) - min(tops) > 1:
            return None, f"the ground there goes up and down by {max(tops) - min(tops)} blocks"
        oy = min(tops) + 1
    clear, high = [], 0
    for y in range(oy, oy + sy):
        for x in xs:
            for z in zs:
                b = blocks.get((x, y, z))
                k = kind(b)
                if k in ("unknown", "fluid", "built"):
                    why = {"unknown": "part of it isn't loaded", "fluid": "water"}.get(k, "something built")
                    return None, f"{why} at {x} {y} {z}" + (f" ({b.split(':')[1]})" if k == "built" else "")
                if k == "air":
                    continue
                if y > oy + REACH_DY:
                    high += 1
                else:
                    clear.append(({"x": x, "y": y, "z": z}, b))
    for x, y, z in built:
        if ox - MARGIN <= x < ox + sx + MARGIN and oz - MARGIN <= z < oz + sz + MARGIN and oy - 1 <= y < oy + sy:
            return None, f"another build within {MARGIN} blocks, at {x} {y} {z} ({blocks[(x, y, z)].split(':')[1]})"
    if len(clear) + high > MAX_CLEAR:
        return None, f"{len(clear) + high} blocks would have to be cleared"
    return {"origin": {"x": ox, "y": oy, "z": oz}, "clear": clear, "high": high}, None


def pick_site(blocks, size, center, allowed):
    """The best corner near him: least clearing, then nearest; his own column stays outside the footprint.
    allowed(origin) -> None or a reason (envelope zones, explore radius). -> (plan, None) or (None, the commonest reason)."""
    sx, _sy, sz = size
    cx, cz = math.floor(center["x"]), math.floor(center["z"])
    best, why, seen = None, Counter(), survey(blocks)
    for ox in range(cx - RADIUS - sx + 1, cx + RADIUS + 1):
        for oz in range(cz - RADIUS - sz + 1, cz + RADIUS + 1):
            if ox <= cx < ox + sx and oz <= cz < oz + sz:
                continue
            plan, bad = site_plan(blocks, size, ox, oz, seen=seen)
            bad = bad or allowed(plan["origin"])
            if bad:
                why[re.sub(r" at -?\d+ -?\d+ -?\d+.*| by \d+ blocks|\d+ blocks would", "", bad)] += 1
                continue
            d = math.hypot(ox + sx / 2 - center["x"], oz + sz / 2 - center["z"])
            score = (len(plan["clear"]) + plan["high"] + 2 * d, d)
            if best is None or score < best[0]:
                best = (score, plan)
    if best:
        return best[1], None
    return None, (why.most_common(1)[0][0] if why else "no spot to try")


def what_clear(plan):
    c = Counter(b.split(":")[1].replace("_", " ") for _p, b in plan["clear"])
    return ", ".join(f"{n} {b}" for b, n in c.most_common(3)) + (", ..." if len(c) > 3 else "")


class BuildFlow:
    def __init__(self, mind):
        self.mind, self.pending, self.task = mind, None, None

    # ---- sense
    async def scan(self, dim, lo, hi):
        """sense query.blocks over the box, chunked by y to <= 32 768 blocks -> {(x, y, z): id} or None (no answer or
        part of it not loaded)."""
        per = (hi["x"] - lo["x"] + 1) * (hi["z"] - lo["z"] + 1)
        step, out = max(1, 32768 // per), {}
        for y0 in range(lo["y"], hi["y"] + 1, step):
            a, b = {**lo, "y": y0}, {**hi, "y": min(hi["y"], y0 + step - 1)}
            ans = await self.mind.hub.request("sense", "query.blocks", {"dim": dim, "min": a, "max": b}, timeout=5)
            if not isinstance(ans, dict) or not isinstance(ans.get("data"), list) or ans.get("unloaded"):
                return None
            out.update(craftkit.decode(ans, a, b))
        return out

    def box(self, center, size, pad):
        return ({"x": math.floor(center["x"]) - pad, "y": math.floor(center["y"]) - SCAN_DOWN,
                 "z": math.floor(center["z"]) - pad},
                {"x": math.floor(center["x"]) + pad, "y": math.floor(center["y"]) + max(SCAN_UP, size[1] + 2),
                 "z": math.floor(center["z"]) + pad})

    def allowed(self, st, dim, size):
        env = self.mind.env

        def check(o):
            sx, sy, sz = size
            for x in (o["x"], o["x"] + sx - 1):
                for z in (o["z"], o["z"] + sz - 1):
                    for y in (o["y"], o["y"] + sy - 1):
                        for eff in ("break", "place"):       # ponytail: corners only; do() checks every cell again
                            ok, rule = envelope_allows(eff, dim, {"x": x, "y": y, "z": z}, env, granted=True)
                            if not ok:
                                return f"I may not {eff} there ({rule})"
            return bounds({"pos": o}, st, env)
        return check

    # ---- the owner's lines
    async def line(self, text, sender, source, sender_pos):
        m = self.mind
        p = self.pending
        if p and m.asked is p["ask"] and p["sender"] == sender and time.monotonic() - p["at"] < TTL_S:
            from .mind import NO, YES                          # (mind imports this module)
            yes, no = YES.match(text), NO.match(text)
            if yes or no:
                m.asked, self.pending = None, None
                if no:
                    await m.say("OK, I won't build it.")
                    return {"ok": True, "answered": "no"}
                return await self.confirmed(p, text, sender, source)
        t = text.strip().lower().rstrip(".!")
        mm = BRIEF.match(t)
        if not mm:
            return None
        view = m.phrase_view(sender, sender_pos)
        if owner_phrases.builder_build(t, view) is not None:
            return None                                      # an approved design by that name: the phrase path
        return await self.offer(mm, text, sender, view)

    async def refuse(self, line, code, **kw):
        await self.mind.say(line)
        return {"ok": False, "error": code, "detail": line, **kw}

    def pick_design(self, what):
        """designs/<project>/v<N>/ whose brief names cover every word he used -> (loaded, None) or (None, line)."""
        m, want, hits = self.mind, words(what), {}
        for d in sorted(m.cfg.designs_dir.glob("*/v*/design.json")):
            brief = m._read(d.parent, "brief.json") or {}
            tags = set().union(words(d.parent.parent.name), words(str(brief.get("purpose", ""))),
                               words(str(brief.get("style", ""))), *(words(n) for n in brief.get("names", [])))
            if want and want <= tags and d.parent.name[1:].isdigit():
                pid, v = d.parent.parent.name, int(d.parent.name[1:])
                exact = any(words(n) == want for n in brief.get("names", []))   # "nether portal" is v1's own name,
                hits[pid] = max(hits.get(pid, (False, 0)), (exact, v))           # v2's "...with corners" is not
        if len(hits) > 1:
            return None, "I have more than one design for that: " + " or ".join(sorted(hits)) + ". Which one?"
        if not hits:
            have = sorted({p.parent.parent.name for p in m.cfg.designs_dir.glob("*/v*/brief.json")
                           if (m._read(p.parent, "brief.json") or {}).get("names")})
            return None, (f"I don't have a design for a {what} yet" +
                          (f"; I can build: {', '.join(have)}." if have else ", and I can't draw new ones yet."))
        pid, (_exact, v) = next(iter(hits.items()))
        loaded = m._load_design(pid, v)
        if isinstance(loaded, dict):
            return None, f"The {pid} design is broken: {loaded.get('detail') or loaded.get('error')}."
        return loaded, None

    async def offer(self, mm, text, sender, view):
        m = self.mind
        if self.task and not self.task.done():
            return await self.refuse("I'm already on a build. Say stop first if you want a different one.", "busy")
        loaded, why = self.pick_design(mm["what"])
        if loaded is None:
            return await self.refuse(why, "no_design")
        d, v, design, brief = loaded
        pid = d.parent.name
        gate = m._read(d, "gate.json") or {}
        if gate and gate.get("category") not in ("owner_supplied", "schematic") and gate.get("decision") != "build":
            reasons = "; ".join(str(r).split(":")[0].replace("_", " ") for r in gate.get("reasons", [])[:2])
            return await self.refuse(f"I have a design for that ({pid} v{v}), but the design gate holds it"
                                     f"{' (' + reasons + ')' if reasons else ''}, so the builder won't build it yet.",
                                     "gate_refused")
        st = view.get("st") or m.fresh_state()
        if not st:
            return await self.refuse("I can't see the world right now, so I can't pick a spot.", "no_state")
        where = (mm["where"] or "").strip()
        if where and where != "here" and not re.match(r"(?:at\s+)?-?\d", where):
            return await self.refuse(f"I don't know where '{where}' is. Stand there and say '{mm['what']} here', "
                                     f"or give me x y z.", "unknown_place")
        shown = (design["size"]["x"], design["size"]["y"], design["size"]["z"])
        size = (*shown[:2], shown[2] + int(brief.get("clearance_front") or 0))   # O150: kept clear south (+z) of it
        dim = st["dim"]
        at = re.findall(r"-?\d+", where) if where[:1] in "a-0123456789" and where != "here" else None
        center = {a: float(c) for a, c in zip("xyz", at)} if at else view.get("owner_pos") or st["pos"]
        if center.get("dim", dim) != dim:
            return await self.refuse("You're in another dimension; I build where I am.", "other_dim")
        blocks = await self.scan(dim, *self.box(center, size, RADIUS + max(size[0], size[2]) + MARGIN))
        if blocks is None:
            return await self.refuse("I can't read the ground there right now (part of it isn't loaded).", "no_scan")
        if at:
            o = {a: int(c) for a, c in zip("xyz", at)}
            plan, bad = site_plan(blocks, size, o["x"], o["z"], o["y"])
            bad = bad or self.allowed(st, dim, size)(o)
        else:
            plan, bad = pick_site(blocks, size, center, self.allowed(st, dim, size))
        if plan is None:
            return await self.refuse(f"I couldn't find a clear, flat spot for {pid} near you: {bad}. Take me to an open, "
                                     f"level spot and ask again, or give me x y z.", "no_site")
        o = plan["origin"]
        need = self.materials(design, d, st)
        if need:
            await m.say(need)
        cl = len(plan["clear"]) + plan["high"]
        q = (f"Build {pid} ({shown[0]} by {shown[2]}) with its north-west corner at {o['x']} {o['y']} {o['z']}, "
             f"{brief.get('level_word') or 'floor'} at y {o['y']}? " + (f"I'd clear {cl} blocks there first ({what_clear(plan)}). " if cl else "") + "yes / no")
        await m.say(q)
        ask = {"kind": "build_site", "sender": sender, "at": time.monotonic()}
        m.asked = ask
        self.pending = {"ask": ask, "sender": sender, "at": ask["at"], "project_id": pid, "version": v,
                        "design_hash": design["hash"], "origin": o, "size": size, "dim": dim, "plan": plan,
                        "done_say": brief.get("done_say")}
        return {"ok": True, "question": q, "site": o}

    def materials(self, design, d, st):
        """What she would have to get, from the bill and the recipes (one line), or None."""
        bom = self.mind._read(d, "bom.json") or {}
        want = Counter({i["item"]: i["count"] for i in bom.get("final", []) + bom.get("scaffold", [])})
        have = Counter(owner_phrases._inv(st))
        short = +(want - have)
        if not short:
            return "I have everything for it."
        try:
            s = solve(self.mind.supply.book, dict(short), dict(+(have - want)))
        except Exception:                                    # no recipe data on this machine: the bill alone
            s = {"missing": dict(short)}
        base = Counter(s["missing"])
        return "To build it I'll gather about " + ", ".join(f"{n} {i.split(':')[1].replace('_', ' ')}"
                                                            for i, n in base.most_common(5)) + \
            (" and more" if len(base) > 5 else "") + "."

    async def confirmed(self, p, text, sender, source):
        m = self.mind
        r = m.decide("approve", p["project_id"], p["version"], sender, "ingame_confirm")   # O132 ruling (2)
        if not r.get("ok"):
            return await self.refuse(f"I can't approve it: {r.get('detail') or r.get('error')}.", r.get("error"))
        if r["design_hash"] != p["design_hash"]:
            return await self.refuse("The design changed since I asked; ask me again.", "design_hash_mismatch")
        await m.preempt(f"building {p['project_id']}", source)
        self.task = m.spawn(self.run(p, source))
        return {"ok": True, "approval_id": r["approval_id"], "building": p["project_id"], "origin": p["origin"]}

    async def run(self, p, source):
        m, o, pid = self.mind, p["origin"], p["project_id"]
        at = f"{o['x']} {o['y']} {o['z']}"
        st = m.fresh_state() or {}
        blocks = await self.scan(p["dim"], *self.box({**o, "x": o["x"] + p["size"][0] // 2, "z": o["z"] + p["size"][2] // 2},
                                                     p["size"], max(p["size"][0], p["size"][2]) // 2 + MARGIN + 1))
        plan, bad = site_plan(blocks, p["size"], o["x"], o["z"], o["y"]) if blocks is not None else \
            (None, "I can't read it right now")
        if plan is None:
            return await m.say(f"The site changed since I asked ({bad}); I stopped before touching it.")
        todo = sorted(plan["clear"], key=lambda c: (c[0]["x"], c[0]["z"], -c[0]["y"]))   # column by column, top down
        if todo:
            await m.say(f"Building {pid} at {at}. Clearing {len(todo)} blocks first; I keep what drops.")
        cleared, left = 0, []
        for pos, block in todo:
            if m.stopped:
                return await m.say(f"Stopped while clearing ({cleared} of {len(todo)} done). Ask again to go on.")
            st = m.fresh_state() or st
            me = st.get("pos") or {}
            if not me or math.dist([me["x"], me["z"]], [pos["x"] + 0.5, pos["z"] + 0.5]) > 3.5:
                w = await m.supply.step("walk_to", {"pos": {"x": pos["x"] + 0.5, "y": float(o["y"]),
                                                            "z": pos["z"] + 0.5}, "tolerance": 2.0}, source)
                if w["status"] != "ok" and w["code"] in STOP_CODES:
                    return await m.say(f"I stopped clearing: {owner_phrases.code_line(w['code'])}")
            res = await m.supply.step("mine_block", {"pos": pos, "expect_block": block}, source)
            if res["status"] == "ok":
                cleared += 1
                if cleared % PROGRESS_EVERY == 0 and cleared < len(todo):
                    await m.say(f"Cleared {cleared} of {len(todo)}.")
                continue
            if res["code"] in STOP_CODES:
                return await m.say(f"I stopped clearing at {pos['x']} {pos['y']} {pos['z']}: "
                                   f"{owner_phrases.code_line(res['code'])}")
            left.append(res["code"])
            if len(left) > 5:
                return await m.say(f"I stopped clearing: {len(left)} blocks failed ({', '.join(sorted(set(left)))}).")
        rest = len(left) + plan["high"]
        await m.say(("Land is clear" if todo else "The site is clear") +
                    (f" ({rest} higher or stuck blocks are left for the builder)" if rest else "") +
                    ". Getting materials, then building.")
        r = m.supply.start({"project_id": pid, "version": p["version"], "design_hash": p["design_hash"],
                            "origin": dict(o)}, source=source)
        if not r.get("ok"):
            return await m.say(f"I can't start the build: {r.get('detail') or r.get('error')}.")
        await self.watch(m.supply.current, pid, at, p.get("done_say"))

    async def watch(self, run, pid, at, done_say=None):
        """Phase lines from the CP-51 run (each supply round), then its end said in one line."""
        import asyncio
        m, rounds = self.mind, 0
        while m.supply.task is not None and not m.supply.task.done():
            if run.rounds != rounds:
                rounds = run.rounds
                await m.say(f"Gathering what I'm missing (round {rounds}).")
            await asyncio.sleep(0.5)
        if run.status == "done":
            got = "; ".join(x["done"] for x in run.ledger if x["skill"] != "walk_to")
            await m.say(f"Done: {pid} is built at {at}." + (f" I gathered: {got[:150]}." if got else ""))
            if done_say:                                       # O150: the brief's next step ("light the portal")
                await m.say(done_say)
        elif run.status == "short":
            needs = sorted({why for _n, why in run.short.values() if why.startswith("I need")})   # O150: a tool
            await m.say(f"I can't finish {pid}: I'm short of " + ", ".join(
                f"{n} {i.split(':')[1].replace('_', ' ')}" for i, (n, _why) in list(run.short.items())[:5]) + ". " +
                (". ".join(needs) + "." if needs else "Bring me some or tell me where to find it, then say build it again."))
        else:
            await m.say(f"The {pid} build stopped ({run.status}): {(run.reason or 'no reason given')[:150]}.")


async def owner_line(mind, text, sender, source, sender_pos=None):
    """The hook: None when the line is not a building brief or her pending site question's answer."""
    flow = mind.__dict__.setdefault("build_flow", BuildFlow(mind))
    return await flow.line(text, sender, source, sender_pos)
