"""O140 (45L 2026-10-22, mind 69216315): workstations by name, and a placement that does not give up on one cell.

- "the crafting table / furnace / chest / smoker / anvil" (and that / this / my X, the X near you / me) as a place or a
  tool = the nearest such block within RADIUS of her or of him (sense query.blocks; the storage index for containers).
  She asks only when there is none ("Where exactly?" with a table 2 blocks away was the bug).
- "go to <X> and craft / make / smelt ..." = walk_to it, then the craft or smelt with pos = that block, one line;
  "go into / open / use the crafting table" = the same walk, then use it.
- a craft chain with no table in reach and none carried walks to one within RADIUS instead of crafting a new table
  (H-table: one she holds is never crafted again - craftkit checks her inventory first).
- place_block: the cell is checked (air, solid floor, nobody standing in it) from a fresh query.blocks before dispatch;
  occupied / obstructed -> the next free cell of the 8 around her, then the 16 at distance 2, up to TRIES, and the
  steps after it (craft pos, pick-up) follow the cell that worked.
"""
import asyncio
import logging
import math
import re
import time

from . import craftkit, owner_phrases
from .recipes import solve
# O205 step 3: the owner phrases these routes read (GO / OPEN / AT, GO_OUT / USE, TOOL_LEAD / TOOL_TAIL, FILL / POUR /
# OBSIDIAN / LIGHT, INTO ...) are the one grammar's (grammar.py), unchanged; the routes and their order stay here
from .grammar import (AT, CRAFT, FILL, GO, GO_OUT, HANDS, INTO, LIGHT, NATURE, OBSIDIAN, POUR,  # noqa: F401
                      SMELT, THEN, TOOL, TOOL_LEAD, TOOL_TAIL, USE, WS)
from .grammar import WS_OPEN as OPEN, WS_TAIL as TAIL   # grammar has its own OPEN (until-stop) and TAIL (please...)

log = logging.getLogger("eve.mind")

RADIUS = 16
TRIES = 8
RETRY = {"occupied", "obstructed", "no_support"}
KINDS = {"crafting table": ("minecraft:crafting_table",), "workbench": ("minecraft:crafting_table",),
         "furnace": ("minecraft:furnace",), "blast furnace": ("minecraft:blast_furnace",),
         "smoker": ("minecraft:smoker",), "anvil": ("minecraft:anvil", "minecraft:chipped_anvil",
                                                    "minecraft:damaged_anvil"),
         "chest": ("minecraft:chest", "minecraft:trapped_chest"), "barrel": ("minecraft:barrel",)}
CONTAINERS = ("chest", "barrel")


def xyz(p):
    return " ".join(str(int(p[a])) for a in "xyz")


def parse(text):
    """(kind, rest, open_it, at) for a workstation order, else None. at = his x y z answer, if any."""
    t, at = text.strip(), None
    tail = TAIL.search(t)
    if tail:
        t, at = t[:tail.start()], dict(zip("xyz", map(int, tail.groups())))
    m = GO.match(t)
    if m:
        return m[1].lower(), m[2], False, at
    m = OPEN.match(t)
    if m and m[1].lower() not in CONTAINERS:          # "open the chest" stays owner_phrases' (look inside it)
        return m[1].lower(), m[2], True, at
    m = AT.match(t)
    if m:
        return m[2].lower(), m[1], False, at
    return None


async def find(mind, kind, sender=None, sender_pos=None):
    """The nearest `kind` block within RADIUS of her or of him: {x, y, z} or None. Her distance first, then his."""
    st = mind.fresh_state() or {}
    if not st.get("pos"):
        return None
    ids, me = KINDS[kind], st["pos"]
    him = mind.owner_pos(sender, sender_pos) if sender else None
    centers = [me] + ([him] if him and math.dist([him[a] for a in "xyz"], [me[a] for a in "xyz"]) > 4 else [])
    hits, seen = set(), 0
    for c in centers:
        blocks = await mind.blocks_near({"pos": c, "dim": st.get("dim")}, RADIUS, (-4, 4)) or {}
        seen += len(blocks)
        hits |= {p for p, b in blocks.items() if b in ids}
    if not hits:                                      # O140 live doubt: was the table unseen, or not there?
        log.info("O140 %s search: query.blocks radius %d around %d centre(s) gave %d cells, 0 matches",
                 kind, RADIUS, len(centers), seen)
    if kind in CONTAINERS:                            # the storage index knows chests sense has seen
        for c in mind.phrase_view(sender or "", sender_pos)["containers"]:
            if any(str(c.get("kind", "")).endswith(i.split(":")[1]) for i in ids):
                hits.add(tuple(int(c["pos"][a]) for a in "xyz"))

    def d(p, c):
        return math.dist([p[0] + .5, p[1] + .5, p[2] + .5], [c[a] for a in "xyz"])
    near = [p for p in hits if any(d(p, c) <= RADIUS + 1 for c in centers)]
    # ponytail: nearest wins; "newest" would need sense's placed-tick per block, which query.blocks does not carry
    best = min(near, key=lambda p: (d(p, me), d(p, him) if him else 0, p), default=None)
    return dict(zip("xyz", best)) if best else None


def stand(pos):
    """Where she walks to use a block: beside it, within reach (the walk's tolerance keeps her out of the block)."""
    return {"x": pos["x"] + .5, "y": float(pos["y"]), "z": pos["z"] + .5}


async def route(mind, text, sender, source, sender_pos=None):
    """O140: a workstation order by rule. None = not one."""
    r = await logs_into_planks(mind, text, source)  # O161 (c): "craft the 8 logs into planks"
    if r is not None:
        return r
    r = await buckets(mind, text, sender, source, sender_pos)     # O151: buckets, obsidian, the portal
    if r is not None:
        return r
    r = await outdoors(mind, text, sender, source, sender_pos)    # O140-b: the water, a tree, a boat
    if r is not None:
        return r
    p = parse(text)
    if p is None:
        return None
    kind, rest, open_it, at = p
    mind.asked = None                                 # a new order of his closes her old question
    pos = at or await find(mind, kind, sender, sender_pos)
    if pos is None:
        q = f"I don't see a {kind} within {RADIUS} blocks of us. Where is it? Give me the x y z."
        await mind.say(q)
        mind.asked = {"kind": "rerun", "text": text, "sender": sender, "at": time.monotonic()}
        return {"ok": True, "question": q}
    rest = (rest or "").strip()
    if kind in CONTAINERS and rest:                   # "go to the chest and put the cobble in it"
        rest = re.sub(r"\b(?:it|there)\b", f"the {kind}", rest)
    walk = ("walk_to", {"pos": stand(pos), "tolerance": 2.0})
    head = f"Walking to the {kind} at {xyz(pos)}"
    c = CRAFT.match(rest)
    if c and kind in ("crafting table", "workbench"):
        return await craft_there(mind, pos, c[2], c[1], walk, head, source)
    if SMELT.match(rest) or c and kind in ("furnace", "smoker", "blast furnace"):
        view = mind.phrase_view(sender, sender_pos)
        view["st"] = {**(view["st"] or {}), "pos": stand(pos)}
        view["containers"] = view["containers"] + [{"pos": pos, "kind": KINDS[kind][0], "items": {}, "shared": True}]
        v = owner_phrases.smelt(rest, view)
        if v is None or "do" not in v:
            line = (v or {}).get("say") or (v or {}).get("ask") or f"I don't know how to {rest}."
            await mind.say(line)
            return {"ok": False, "error": "cannot_smelt", "detail": line}
        return await go(mind, [walk] + v["do"], f"{head}, then {v['say'][0].lower()}{v['say'][1:].rstrip('.')}.",
                        f"going to the {kind}", source)
    if rest:                                          # anything else: walk there, then his order as he said it
        await mind.say(f"{head}, then: {rest}.")
        r = await mind.move_to(walk[1], source, label=f"going to the {kind}")
        mind.spawn(_after_walk(mind, r, rest, sender, source))
        return r
    use = ("use_item", {"target_pos": dict(pos)}) if open_it and kind not in CONTAINERS else None
    return await go(mind, [walk] + ([use] if use else []), f"{head}" + (", then opening it." if use else "."),
                    f"going to the {kind}", source)


async def _after_walk(mind, r, rest, sender, source):
    aid, epoch = (r or {}).get("action_id"), mind.epoch
    if not aid:
        return
    ev = mind.done.get(aid)
    if ev is not None:
        await ev.wait()
    if mind.status_of(aid) == "ok" and mind.epoch == epoch and not mind.stopped:
        await mind.owner_text(rest, sender, source)


async def go(mind, steps, line, label, source):
    await mind.say(line)
    await mind.preempt(label, source)
    mind.spawn_steps(steps, source)
    return {"ok": True, "steps": len(steps)}


async def craft_there(mind, pos, name, n, walk, head, source):
    """Walk to the table at pos, then the chain from her inventory with that table as its station."""
    from . import dialogue
    inv, st = mind.ironkit.inv()
    book = dialogue.recipe_book(mind)
    if book is None or not st:
        await mind.say("I can't check recipes right now.")
        return {"ok": False, "error": "no_recipes"}
    item = craftkit.resolve(book, name, inv) or craftkit.item_id(name)
    count = int(n) if n and n.isdigit() else 1
    there = {**st, "pos": stand(pos)}
    blocks = dict(await mind.blocks_near(st, 4, (-2, 2)) or {})
    blocks[(pos["x"], pos["y"], pos["z"])] = craftkit.TABLE
    steps, line = craftkit.craft_steps(book, item, count, inv, blocks, there)
    if steps is None:
        await mind.say(line)
        return {"ok": False, "error": "cannot_craft", "detail": line}
    mind.last_item = item
    w = craftkit.thing(item)
    w = f"{count} {w}" if count > 1 else ("an " if w[0] in "aeiou" else "a ") + w
    return await go(mind, [walk] + steps, f"{head}, then making {w}.",
                    f"making {craftkit.thing(item)}", source)


async def table_elsewhere(mind, book, item, count, inv, blocks, st, source):
    """O140 (3), called by craft_order: the chain needs a crafting table, none is in reach and she carries none ->
    the nearest one within RADIUS (walk there and craft), rather than crafting a second table. None = not the case."""
    plan = solve(book, {item: count}, inv, kinds=craftkit.KINDS)
    if not plan["feasible"] or "crafting_table" not in plan["stations"] or inv.get(craftkit.TABLE, 0) > 0 or \
            craftkit.station_in_reach(blocks, st, craftkit.TABLE):
        return None
    pos = await find(mind, "crafting table")
    if pos is None:
        return None
    walk = ("walk_to", {"pos": stand(pos), "tolerance": 2.0})
    return await craft_there(mind, pos, item, str(count), walk, f"Walking to the crafting table at {xyz(pos)}", source)


def ring(st):
    """The 8 cells around her feet, then the 16 at distance 2, each ring the side she faces first."""
    x, y, z = craftkit.feet(st)
    yaw = math.radians(float(st.get("yaw") or 0))
    fx, fz = -math.sin(yaw), math.cos(yaw)
    out = []
    for r in (1, 2):
        cells = [(dx, dz) for dx in range(-r, r + 1) for dz in range(-r, r + 1) if max(abs(dx), abs(dz)) == r]
        out += [(x + dx, y, z + dz) for dx, dz in sorted(cells, key=lambda d: -(d[0] * fx + d[1] * fz) / math.hypot(*d))]
    return out


def free(blocks, c, st):
    """Air, a solid floor under it, and no one standing in it (her nearby_entities, her own feet)."""
    if blocks.get(c) not in craftkit.AIR or blocks.get((c[0], c[1] - 1, c[2]), "unloaded") in craftkit.NO_FLOOR:
        return False
    people = [e.get("pos") for e in st.get("nearby_entities", []) if isinstance(e.get("pos"), dict)] + [st.get("pos")]
    return not any(p and (int(p["x"] // 1), int(p["z"] // 1)) == (c[0], c[2]) and c[1] - 1 <= p["y"] < c[1] + 1
                   for p in people)


def in_her_box(c, st, grow=0.15):
    """O147 (45L: "Eve herself stands in the target space" twice): the cell meets her bounding box (0.6 wide, 1.8 high,
    from her exact state pos) grown by `grow` on every side - 0.3 in all; 0.3 a side would cover all 8 cells even
    when she stands mid-block."""
    p, h = st["pos"], 0.3 + grow
    return c[0] < p["x"] + h and c[0] + 1 > p["x"] - h and c[2] < p["z"] + h and c[2] + 1 > p["z"] - h and \
        c[1] < p["y"] + 1.8 + grow and c[1] + 1 > p["y"] - grow


def order(cells, st):
    """O147: the cell straight ahead at >= 1.0 from her feet centre first, then the rest nearest first."""
    p, ahead = st["pos"], ring(st)[0]                 # the one cell she faces; if it is out, nearest first

    def hd(c):
        return math.hypot(c[0] + .5 - p["x"], c[2] + .5 - p["z"])
    return sorted(cells, key=lambda c: (not (c == ahead and hd(c) >= 1.0), hd(c), c))


def choices(blocks, st, want=None):
    """(ring-1 cells, ring-2 cells) she may place into: free, outside her grown box; without blocks only `want`."""
    ok = [c for c in ring(st) if (free(blocks, c, st) if blocks else c == want) and not in_her_box(c, st)]
    x, y, z = craftkit.feet(st)
    near = [c for c in ok if max(abs(c[0] - x), abs(c[2] - z)) == 1]
    return order(near, st), order([c for c in ok if c not in near], st)


async def place(mind, steps, i, source, epoch, wait):
    """run_steps' place_block: the cell is checked before dispatch (air, floor, nobody in it, outside her own box) and
    retried on the next one; with none of the 8 usable she first steps one block back along her facing (O147). Later
    steps that used the planned cell (craft pos, pick-up) are moved to the one that worked."""
    params = steps[i][1]
    want = tuple(int(params["pos"][a]) for a in "xyz")
    st = mind.fresh_state() or {}
    if not st.get("pos"):
        return await mind.do("place_block", params, source=source, grant=True, epoch=epoch, wait=wait)
    blocks = await mind.blocks_near(st, 2, (-1, 0))
    near, far = choices(blocks, st, want)
    if not near:                                      # O147: back up one block, then look again
        yaw = math.radians(float(st.get("yaw") or 0))
        back = {"x": st["pos"]["x"] + math.sin(yaw), "y": float(st["pos"]["y"]), "z": st["pos"]["z"] - math.cos(yaw)}
        w = await mind.do("walk_to", {"pos": back, "tolerance": 0.5}, source=source, grant=True, epoch=epoch,
                          wait=mind.registry.skills["walk_to"]["timeout_ms"] / 1000 + mind.cfg.result_grace_s)
        if w.get("ok") and w.get("status") == "ok":
            st = mind.fresh_state() or st
            for _ in range(20):                       # the state frame after the walk
                if math.dist([st["pos"][a] for a in "xz"], [back[a] for a in "xz"]) < 0.6:
                    break
                await asyncio.sleep(0.1)
                st = mind.fresh_state() or st
            blocks = await mind.blocks_near(st, 2, (-1, 0))
            near, far = choices(blocks, st, want)
    r = {"ok": False, "error": "no free spot beside me"}
    for c in (near + far)[:TRIES]:
        r = await mind.do("place_block", {**params, "pos": dict(zip("xyz", c))}, source=source, grant=True,
                          epoch=epoch, wait=wait)
        if r.get("ok") and r.get("status") == "ok":
            if c != want:
                old, new = dict(zip("xyz", want)), dict(zip("xyz", c))
                for j in range(i + 1, len(steps)):
                    s, p = steps[j]
                    if p.get("pos") == old:
                        steps[j] = (s, {**p, "pos": dict(new)})
            return r
        if r.get("code") not in RETRY:
            return r
    return r


# --- O140-b (45L: "go to the water nearby and place the boat" -> "Which block, and where?") -------------------------
FAR = 32                                              # sense: radius <= 32, volume <= 32768 -> 7 layers at 65 x 65
XYZ = re.compile(r"(?:\bat\s+)?(-?\d+)[\s,]+(-?\d+)[\s,]+(-?\d+)")
WATER = ("minecraft:water",)
LOG = re.compile(r"^minecraft:[a-z_]+_(?:log|stem)$")
NOT_FLOOR = craftkit.NO_FLOOR | {"minecraft:short_grass", "minecraft:tall_grass", "minecraft:fern", "minecraft:snow"}
NATURE_KIND = {"lake": "lake", "river": "lake", "pond": "lake", "ocean": "lake", "sea": "lake", "water": "water",
               "tree": "tree", "trees": "tree", "forest": "tree", "cave": "cave", "hill": "hill"}


def pull_xyz(text):
    """His x y z anywhere in the line ("55 62 -19 place boat", "place boat 55 62 -19") -> (rest, {x,y,z} or None)."""
    m = XYZ.search(text)
    if not m:
        return text, None
    return re.sub(r"\s+", " ", text[:m.start()] + " " + text[m.end():]).strip(" ,"), dict(zip("xyz", map(int, m.groups())))


async def find_nature(mind, kind, st):
    """The nearest water / lake (>= 3 water neighbours) / tree (lowest log of a column) / cave air (3+ below her
    feet): ((x, y, z) or None, blocks)."""
    if kind == "cave":
        dy = (-12, -3)
        blocks = await mind.blocks_near(st, 24, dy) or {}               # 49 x 49 x 10 < 32768
        hits = [p for p, b in blocks.items() if b == "minecraft:cave_air"]
    else:
        dy = (-3, 3) if kind == "tree" else (-5, 1)
        blocks = await mind.blocks_near(st, FAR, dy) or {}
        if kind == "tree":
            hits = [p for p, b in blocks.items() if LOG.match(b) and blocks.get((p[0], p[1] - 1, p[2])) != b]
        else:
            hits = [p for p, b in blocks.items() if b in WATER and blocks.get((p[0], p[1] + 1, p[2])) not in WATER]
            if kind == "lake":
                hits = [p for p in hits if sum(blocks.get((p[0] + dx, p[1], p[2] + dz)) in WATER
                                               for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))) >= 3]
    me = st["pos"]
    best = min(hits, key=lambda p: (math.dist([p[0] + .5, p[1], p[2] + .5], [me[a] for a in "xyz"]), p), default=None)
    if best is None:                                  # O140 live doubt: unseen, or not there?
        log.info("O140-b %s search: query.blocks dy %s gave %d cells, 0 matches", kind, dy, len(blocks))
    return best, blocks


def shore(target, blocks):
    """A standable cell beside `target`: two air cells over a solid, dry floor."""
    x, y, z = target
    for up in (2, 1, 3):                              # feet one above it first (a shore), then level, then a bank
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            c = (x + dx, y + up - 1, z + dz)            # her feet cell
            if blocks.get(c) in craftkit.AIR and blocks.get((c[0], c[1] + 1, c[2])) in craftkit.AIR and \
                    blocks.get((c[0], c[1] - 1, c[2]), "unloaded") not in NOT_FLOOR:
                return {"x": c[0], "y": c[1], "z": c[2]}
    return None


def walk_beside(cell, target):
    if cell is not None:
        return ("walk_to", {"pos": {"x": cell["x"] + .5, "y": float(cell["y"]), "z": cell["z"] + .5}, "tolerance": 1.0})
    return ("walk_to", {"pos": {"x": target[0] + .5, "y": float(target[1]), "z": target[2] + .5}, "tolerance": 3.0})


async def outdoors(mind, text, sender, source, sender_pos=None):
    """O140-b by rule: "go to the water / lake / nearest tree / cave [and ...]", "place / put / use the boat [in the
    water | at x y z]" - a boat, raft, minecart or bucket is used (use_item), never placed. None = not one of these."""
    t, at = pull_xyz(text.strip())
    g, u = GO_OUT.match(t), USE.match(t)
    if not (g or u):
        return None
    kind = NATURE_KIND[g[1].lower()] if g else None
    if g and g[2]:
        u = USE.match(g[2].strip())
        if u is None:
            return None                               # "go to the lake and fish": not ours
    mind.asked = None                                 # a new order of his closes her old question
    st = mind.fresh_state() or {}
    if not st.get("pos"):
        await mind.say("I can't see where I am right now.")
        return {"ok": False, "error": "no_state"}
    if kind == "hill" and not at:
        q = "Which hill? Give me its x y z."
        await mind.say(q)
        mind.asked = {"kind": "rerun", "text": text, "sender": sender, "at": time.monotonic()}
        return {"ok": True, "question": q}
    item = re.sub(r"\s+", "_", u[1].strip().lower()) if u else None
    if u and kind is None:
        where = (u[2] or "").strip().lower()
        w = re.match(NATURE + r"$", where, re.I) if where else None
        kind = NATURE_KIND[w[1].lower()] if w else "water" if item.endswith(("boat", "raft")) else None
    target, blocks = (tuple(at[a] for a in "xyz"), None) if at else (None, None)
    if target is None and kind:
        target, blocks = await find_nature(mind, kind, st)
    if target is None:
        none = "any water" if kind in ("water", "lake", None) else "a " + kind
        q = (f"I don't see {none} within {FAR} blocks. " if kind else "") + \
            (f"Where should I put the {item.replace('_', ' ')}? Give me the x y z." if u else
             "Where is it? Give me the x y z.")
        await mind.say(q)
        mind.asked = {"kind": "rerun", "text": text, "sender": sender, "at": time.monotonic()}
        return {"ok": True, "question": q}
    if blocks is None:                                # his x y z: what is there, and where she can stand
        blocks = await mind.blocks_near({"pos": {a: v + .5 for a, v in zip("xyz", target)}, "dim": st.get("dim")},
                                        2, (-2, 2)) or {}
    spot = xyz(dict(zip("xyz", target)))
    name = {"water": "the water", "lake": "the water", "tree": "the tree", "cave": "the cave"}.get(
        kind, "the water" if blocks.get(target) in WATER else "the spot")
    me = st["pos"]
    close = math.dist([target[0] + .5, target[1] + .5, target[2] + .5], [me[a] for a in "xyz"]) <= craftkit.REACH - .5
    steps = [] if close and u else [walk_beside(None if kind == "cave" else shore(target, blocks), target)]
    head = f"Walking to {name} at {spot}"
    if not u:
        return await go(mind, steps, head + ".", f"going to {name}", source)
    thing = item.replace("_", " ")
    bare = item in ("boat", "raft", "minecart", "bucket")   # "the boat" = any oak_boat / spruce_boat ...
    held = [s for s in st.get("inventory", []) if isinstance(s.get("slot"), int) and (
        str(s.get("item", "")).endswith(("_" + item, ":" + item)) if bare else s.get("item") == "minecraft:" + item)]
    if not held:
        line = f"I don't have a {thing}."
        await mind.say(line)
        return {"ok": False, "error": "no_item", "detail": line}
    sel, line = await hold(mind, st, held[0]["item"])
    if sel is None:
        await mind.say(line)
        return {"ok": False, "error": "not_in_hotbar", "detail": line}
    if item.endswith(("boat", "raft")) and blocks.get(target) in WATER:
        # body (O140-b): the crosshair ignores fluids - aim at the water, then use with NO target (a target is obstructed)
        steps += sel + [("look_at", {"pos": {"x": target[0] + .5, "y": target[1] + .9, "z": target[2] + .5}}),
                        ("use_item", {})]
    else:
        steps += sel + [("use_item", {"target_pos": dict(zip("xyz", target)), "face": "up"})]
    on = "on the water" if kind in ("water", "lake") or blocks.get(target) in WATER else f"at {spot}"
    line = f"Putting the {thing} {on}." if close else f"{head}, then putting the {thing} {on}."
    return await go(mind, steps, line, f"putting the {thing} down", source)


def can(mind, skill):
    """The skill is in the registry she serves and body reports the same version (registry 0.15.2: select_item)."""
    link = mind.hub.links.get("body")
    return skill in mind.registry.dispatchable(link.hello["caps"] if link else [])


def holding(st, item):
    """The step that puts `item` (an id, or "*_shovel"-style suffix) in her hand: ([steps], None) or (None, line)."""
    inv = [s for s in st.get("inventory", []) if isinstance(s.get("slot"), int)]
    hits = [s for s in inv if (str(s.get("item")).endswith(item[1:]) if item.startswith("*") else s.get("item") == item)]
    if not hits:
        return None, f"I don't have a {craftkit.thing(item.lstrip('*_'))}."
    return hits, None


async def hold(mind, st, item):
    """([select step], None) or (None, the line she says): select_item when body offers it (0.15.2), else the hotbar."""
    hits, line = holding(st, item)
    if hits is None:
        return None, line
    if can(mind, "select_item"):
        return [("select_item", {"item": hits[0]["item"]})], None
    hot = [s for s in hits if s["slot"] <= 8]
    if not hot:                                       # ponytail: no hotbar-swap skill before select_item (0.15.2)
        return None, f"My {craftkit.thing(hits[0]['item'])} is in my pack, not my hotbar - put it in my hotbar and tell me again."
    return [("select_hotbar", {"slot": hot[0]["slot"]})], None


# --- O148 (45L: "use the wooden shovel to mine 10 grass blocks" -> "Which block? Give me its x y z.") --------------
RANK = ("netherite", "diamond", "iron", "stone", "golden", "wooden")
SHOVEL = {"dirt", "grass_block", "sand", "red_sand", "gravel", "clay", "snow", "snow_block", "soul_sand", "soul_soil",
          "mud", "farmland", "podzol", "mycelium", "coarse_dirt", "rooted_dirt", "dirt_path", "suspicious_sand"}
PICK = re.compile(r"stone|ore|deepslate|obsidian|netherrack|brick|terracotta|basalt|andesite|granite|diorite|tuff|"
                  r"calcite|ice|concrete|quartz|prismarine|copper|iron_block|gold_block|furnace|anvil")


def tool_hint(text):
    """O148: (order, hint) - "use the wooden shovel to mine X" -> ("mine X", "wooden shovel"); "with my hands" ->
    (order, "hands"); no tool phrase -> (text, None)."""
    m = TOOL_LEAD.match(text.strip())
    if m:
        return m[2].strip(), "hands" if "hand" in m[1].lower() else m[1].strip().lower()
    m = TOOL_TAIL.match(text.strip())
    if m and m[3]:
        return m[3].strip(), "hands"
    if m:
        return m[1].strip(), "hands" if "hand" in m[2].lower() else m[2].strip().lower()
    return text, None


def tool_kind(name):
    w = name.replace(" ", "_").replace("spade", "shovel")
    w = re.sub(r"\bpick$", "pickaxe", w)
    return next((k for k in ("pickaxe", "shovel", "axe", "hoe", "sword", "shears") if w.endswith(k)), None)


def kind_for(block):
    """The tool kind that suits a block id or tag (None: unknown, body chooses)."""
    b = str(block or "").split(":")[-1]
    if b in SHOVEL:
        return "shovel"
    if re.search(r"(?:_log|_wood|_planks|_stem|_hyphae|logs|crafting_table|chest|barrel|bookshelf|fence|door)$", b):
        return "axe"
    if re.search(r"leaves|hay_block|sculk|wart_block|sponge", b):
        return "hoe"
    return "pickaxe" if PICK.search(b) else None


def best_of(inv, kind):
    tools = [s for s in inv if tool_kind(craftkit.thing(str(s.get("item", "")))) == kind and isinstance(s.get("slot"), int)]
    rank = {m: i for i, m in enumerate(RANK)}
    return min(tools, key=lambda s: rank.get(str(s["item"]).split(":")[-1].split("_")[0], 9), default=None)


async def use_tool(mind, hint, order, source):
    """O148: before the order runs - the named tool selected when she has it and it suits the block, else her best
    tool for it (said in one line); "hands" = an empty hotbar slot. Never stops the order."""
    from .planner import gather_order
    st = mind.fresh_state() or {}
    inv = [s for s in st.get("inventory", []) if isinstance(s.get("slot"), int)]
    if hint == "hands":
        empty = next((i for i in range(9) if i not in {s["slot"] for s in inv}), None)
        if empty is None:
            await mind.say("My hotbar is full, so I'll use what I'm holding.")
            return
        await mind.preempt(order, source)
        await mind.do("select_hotbar", {"slot": empty}, source=source, grant=True,
                      wait=mind.registry.skills["select_hotbar"]["timeout_ms"] / 1000 + mind.cfg.result_grace_s)
        return
    g = gather_order(order) or {}
    target = (g.get("params") or {}).get("block") or (g.get("params") or {}).get("tag")
    want, kind = tool_kind(hint), kind_for(target)
    named = next((s for s in inv if craftkit.thing(str(s["item"])) == hint.replace("_", " ") or
                  (" " not in hint and tool_kind(craftkit.thing(str(s["item"]))) == want)), None)
    pick = named if named is not None and (kind is None or want == kind) else best_of(inv, kind) if kind else None
    if pick is None and named is None:
        await mind.say(f"I don't have a {hint} - using my hands." if kind is None or best_of(inv, kind) is None else
                       f"I don't have a {hint} - using my {craftkit.thing(best_of(inv, kind)['item'])}.")
        return
    if pick is not named:
        best = f"my {craftkit.thing(pick['item'])}" if pick else "my hands"
        await mind.say(f"I don't have a {hint} - using {best}." if named is None else
                       f"A {hint} won't help with {craftkit.thing(str(target))} - using {best}.")
        if pick is None:
            return
    steps, line = await hold(mind, st, pick["item"])
    if steps is None:
        await mind.say(line)
        return
    await mind.preempt(order, source)
    for skill, params in steps:
        await mind.do(skill, params, source=source, grant=True,
                      wait=mind.registry.skills[skill]["timeout_ms"] / 1000 + mind.cfg.result_grace_s)


# --- O151 (Hunter's obsidian + nether portal test): buckets, make obsidian, light the portal ------------------------
LAVA_R = 24
PICKS = ("minecraft:diamond_pickaxe", "minecraft:netherite_pickaxe")


async def fluid_near(mind, st, fluid, r=FAR):
    """The nearest surface water / lava block within r of her (query.blocks), with the blocks seen."""
    dy = (-5, 1) if r == FAR else (-6, 2)            # 65 x 65 x 7 / 49 x 49 x 9 <= 32768
    blocks = await mind.blocks_near(st, r, dy) or {}
    me = st["pos"]
    hits = [p for p, b in blocks.items() if b == f"minecraft:{fluid}" and
            blocks.get((p[0], p[1] + 1, p[2])) != f"minecraft:{fluid}"]
    best = min(hits, key=lambda p: (math.dist([p[0] + .5, p[1], p[2] + .5], [me[a] for a in "xyz"]), p), default=None)
    if best is None:
        log.info("O151 %s search: query.blocks radius %d gave %d cells, 0 matches", fluid, r, len(blocks))
    # ponytail: query.blocks drops block states, so a source and a flowing block look alike; nearest surface wins
    return best, blocks


def centre(p):
    return {"x": p[0] + .5, "y": p[1] + .5, "z": p[2] + .5}


def reach_steps(st, target, blocks):
    """walk beside `target` unless it is already in reach, then look at it."""
    me = st["pos"]
    close = math.dist([target[0] + .5, target[1] + .5, target[2] + .5], [me[a] for a in "xyz"]) <= craftkit.REACH - .5
    return ([] if close else [walk_beside(shore(target, blocks), target)]) + [("look_at", {"pos": centre(target)})]


async def buckets(mind, text, sender, source, sender_pos=None):
    """O151 by rule; None = not a bucket / obsidian / portal order."""
    t, at = pull_xyz(text.strip())
    f, p, o, lt = FILL.match(t), POUR.match(t), OBSIDIAN.match(t), LIGHT.match(t)
    if not (f or p or o or lt):
        return None
    mind.asked = None
    st = mind.fresh_state() or {}
    if not st.get("pos"):
        await mind.say("I can't see where I am right now.")
        return {"ok": False, "error": "no_state"}

    async def no(line, **kw):
        await mind.say(line)
        if kw.get("ask"):
            mind.asked = {"kind": "rerun", "text": text, "sender": sender, "at": time.monotonic()}
            return {"ok": True, "question": line}
        return {"ok": False, "error": kw.get("error", "cannot"), "detail": line}

    if f:                                             # (1) fill the bucket
        fluid = (f[1] or f[2] or "water").lower()
        sel, line = await hold(mind, st, "minecraft:bucket")
        if sel is None:
            return await no("I don't have an empty bucket." if "don't have" in line else line)
        src, blocks = (tuple(at[a] for a in "xyz"), await mind.blocks_near(st, 2, (-2, 2)) or {}) if at else \
            await fluid_near(mind, st, fluid)
        if src is None:
            return await no(f"I don't see any {fluid} within {FAR} blocks. Where is it? Give me the x y z.", ask=True)
        steps = sel + reach_steps(st, src, blocks) + [("use_item", {})]
        return await go(mind, steps, f"Filling my bucket with {fluid} at {xyz(dict(zip('xyz', src)))}.",
                        f"filling my bucket", source)
    if p:                                             # (2) pour the water / lava
        fluid, onto = p[1].lower(), (p[2] or "").lower()
        sel, line = await hold(mind, st, f"minecraft:{fluid}_bucket")
        if sel is None:
            return await no(f"I don't have a bucket of {fluid}." if "don't have" in line else line)
        if at:
            tgt, blocks = tuple(at[a] for a in "xyz"), await mind.blocks_near(st, 2, (-2, 2)) or {}
        elif onto in ("lava", "water"):
            tgt, blocks = await fluid_near(mind, st, onto, LAVA_R)
            if tgt is None:
                return await no(f"I don't see any {onto} near me. Where is it? Give me the x y z.", ask=True)
        else:                                         # here / nothing said: the ground in front of her
            c = ring(st)[0]
            tgt, blocks = (c[0], c[1] - 1, c[2]), {}
        steps = sel + reach_steps(st, tgt, blocks) + [("use_item", {})]
        on = f"on the {onto}" if onto in ("lava", "water") else f"at {xyz(dict(zip('xyz', tgt)))}"
        return await go(mind, steps, f"Pouring the {fluid} {on} ({xyz(dict(zip('xyz', tgt)))}).", f"pouring {fluid}",
                        source)
    if o:                                             # (3) make obsidian: water on a lava source, then mine it
        inv = {s.get("item") for s in st.get("inventory", [])}
        pick = next((i for i in PICKS if i in inv), None)
        if pick is None:
            return await no("I need a diamond pickaxe to mine obsidian.", error="no_tool")
        if "minecraft:water_bucket" not in inv and "minecraft:bucket" not in inv:
            return await no("I need a bucket to make obsidian.", error="no_bucket")
        lava, lblocks = (tuple(at[a] for a in "xyz"), await mind.blocks_near(st, 2, (-2, 2)) or {}) if at else \
            await fluid_near(mind, st, "lava", LAVA_R)
        if lava is None:
            return await no(f"I don't see any lava within {LAVA_R} blocks. Where is it? Give me the x y z.", ask=True)
        steps, said = [], []
        if "minecraft:water_bucket" not in inv:
            water, wblocks = await fluid_near(mind, st, "water")
            if water is None:
                return await no(f"I don't see any water within {FAR} blocks to fill my bucket.", error="no_water")
            sel, line = await hold(mind, st, "minecraft:bucket")
            if sel is None:
                return await no(line)
            steps += sel + reach_steps(st, water, wblocks) + [("use_item", {})]
            said.append(f"fill my bucket at {xyz(dict(zip('xyz', water)))}")
            there = {**st, "pos": steps[len(sel)][1]["pos"] if steps[len(sel)][0] == "walk_to" else st["pos"]}
        else:
            there = st
        if "minecraft:water_bucket" in inv:
            sel, line = await hold(mind, st, "minecraft:water_bucket")
            if sel is None:
                return await no(line)
        else:                                         # the bucket she just filled sits where the empty one was
            sel = [("select_item", {"item": "minecraft:water_bucket"})] if can(mind, "select_item") else \
                [s for s in steps if s[0] == "select_hotbar"][:1]
        steps += sel + reach_steps(there, lava, lblocks) + [("use_item", {})]
        said.append(f"pour it on the lava at {xyz(dict(zip('xyz', lava)))}")
        psel, line = await hold(mind, st, pick)
        if psel is None:
            return await no(line)
        steps += psel + [("mine_block", {"pos": dict(zip("xyz", lava)), "expect_block": "minecraft:obsidian"})]
        said.append(f"mine the obsidian with my {craftkit.thing(pick)}")
        return await go(mind, steps, "Making obsidian: " + ", then ".join(said) + ".", "making obsidian", source)
    # (4) light the portal: flint and steel on the frame's inner bottom block
    sel, line = await hold(mind, st, "minecraft:flint_and_steel")
    if sel is None:
        return await no("I don't have a flint and steel." if "don't have" in line else line, error="no_item")
    blocks = await mind.blocks_near(st, 12, (-4, 6)) or {}            # 25 x 25 x 11 < 32768
    ob = "minecraft:obsidian"
    inner = [p for p, b in blocks.items() if b == ob and all(blocks.get((p[0], p[1] + k, p[2])) in craftkit.AIR
                                                             for k in (1, 2, 3)) and blocks.get((p[0], p[1] + 4, p[2])) == ob]
    if at:
        inner = [tuple(at[a] for a in "xyz")]
    if not inner:
        log.info("O151 portal search: query.blocks radius 12 gave %d cells, no frame", len(blocks))
        return await no("I don't see a finished portal frame near me. Where is it? Give me the x y z of its bottom.",
                        ask=True)
    me = st["pos"]
    b = min(inner, key=lambda p: (math.dist([p[0] + .5, p[1], p[2] + .5], [me[a] for a in "xyz"]), p))
    steps = sel + reach_steps(st, b, blocks) + [("use_item", {})]
    return await go(mind, steps, f"Lighting the portal at {xyz(dict(zip('xyz', b)))}.", "lighting the portal", source)


# --- S87 / O161 (session 4: "craft the 8 logs into planks" -> "step 0: craft target 7,69,-14 was not given") -------
STATION_BLOCKS = {"crafting_table": ("crafting table", craftkit.TABLE), "furnace": ("furnace", "minecraft:furnace"),
                  "smoker": ("smoker", "minecraft:smoker"), "blast_furnace": ("blast furnace", "minecraft:blast_furnace")}


async def logs_into_planks(mind, text, source):
    """O161 (c): "craft the N logs into planks" = planks from N of her logs (4 per log), by rule. None = not one."""
    m = INTO.match(text.strip())
    if not m:
        return None
    from . import dialogue
    inv, _st = mind.ironkit.inv()
    wood = m[2].strip().lower().replace(" ", "_")
    logs = {k: n for k, n in inv.items() if n > 0 and re.match(r"^minecraft:[a-z_]+_(?:log|stem)$", k) and
            (not wood or k.split(":")[1].startswith(wood))}
    if not logs:
        await mind.say(f"I don't have any {wood.replace('_', ' ') + ' ' if wood else ''}logs.")
        return {"ok": False, "error": "no_item"}
    book = dialogue.recipe_book(mind)
    log_id = max(logs, key=logs.get)
    planks = next((o for o, rs in (book.by_output.items() if book else ()) if o.endswith("_planks") and any(
        log_id in opts for r in rs for opts, _ in r["inputs"])), None)
    if planks is None:
        await mind.say("I can't check recipes right now.")
        return {"ok": False, "error": "no_recipes"}
    n = logs[log_id] if m[1] in (None, "all") else int(m[1])
    if n > logs[log_id]:
        await mind.say(f"I only have {logs[log_id]} {craftkit.thing(log_id)} - planking those.")
        n = logs[log_id]
    return await mind.craft_order(planks, 4 * n, source)


async def plan_stations(mind, plan):
    """O161 (a)+(b), before the planner's plan is judged: a craft whose item has a 2x2 recipe carries no target; a
    table / furnace step whose target is not a station she knows gets the station in her reach. Returns the station
    positions (sense query.blocks in reach, then the nearest within RADIUS) the gate may accept as targets."""
    from . import dialogue
    book, st = dialogue.recipe_book(mind), mind.fresh_state() or {}
    found, blocks = [], None
    for step in plan.get("steps", []):
        p = step.get("params") or {}
        if step.get("skill") not in ("craft", "smelt") or not st.get("pos"):
            continue
        rs = (book.by_output.get(p.get("item"), []) if book else [])
        if step["skill"] == "craft" and any(r["station"] is None for r in rs):
            p.pop("pos", None)                        # 2x2: in her inventory grid, nowhere in the world
            continue
        need = next((r["station"] for r in rs if r["station"]), "crafting_table") if step["skill"] == "craft" \
            else "furnace"
        kind, block = STATION_BLOCKS.get(need, (None, None))
        if block is None:
            continue
        if blocks is None:
            blocks = await mind.blocks_near(st, 4, (-2, 2)) or {}
        here = [dict(zip("xyz", q)) for q, b in blocks.items() if b == block]
        far = await find(mind, kind)
        known = here + ([far] if far and far not in here else [])
        found += [k for k in known if k not in found]
        q = p.get("pos")
        if isinstance(q, dict) and any(max(abs(q[a] - k[a]) for a in "xyz") <= 1 for k in known):
            continue                                  # the planner named a station she knows: kept
        reach = craftkit.station_in_reach(blocks, st, block)
        if reach is not None:
            p["pos"] = reach
    return found


# --- O163 step 1 (ledger 4b922176: "mine 3 stone" -> "not_found (no more exposed minecraft:stone within 16)") -------
GROUND = {"minecraft:stone", "minecraft:cobblestone", "minecraft:dirt", "minecraft:gravel", "minecraft:sand"}
DIG_DEPTH = 6                                         # prospect's descend_to: 6 below her (skills, O175)


OWNER_CAP = 100                                       # O238: his area orders reach this far once the registry allows


def owner_radius(mind, skill, fallback=32):
    """O238: the widest radius for his order - the served registry's maximum for `skill`, capped at OWNER_CAP
    (registry 0.15.2: 32; 0.15.3: 128 -> 100)."""
    r = ((mind.registry.skills.get(skill) or {}).get("params") or {}).get("properties", {}).get("radius") or {}
    return min(OWNER_CAP, r["maximum"]) if "maximum" in r else fallback


def widest(mind, skill, params):
    """O163/O238: an owner's area order searches as far as owner_radius lets it, unless he named a radius."""
    r = ((mind.registry.skills[skill].get("params") or {}).get("properties", {})).get("radius")
    return {**params, "radius": owner_radius(mind, skill)} if r and "radius" not in params else params


def built_here(mind, st):
    """O188 guard (3b): she stands in a zone whose place rule is not free (home included), or a cell within 1 of her
    feet was last a placement (sense's block events) - no prospect starts from here. ponytail: the full walk out to
    natural ground is the next rev's."""
    x, y, z = (math.floor(st["pos"][a]) for a in "xyz")
    dim = st.get("dim")
    for zn in mind.env.get("zones", []):
        if zn.get("dim") == dim and zn.get("place", "free") != "free" and all(
                zn["min"][a] <= v <= zn["max"][a] for a, v in zip("xyz", (x, y, z))):
            return True
    rows = mind.db.execute(
        "SELECT x, y, z, kind FROM block_events WHERE world_id IS ? AND dim=? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ? "
        "AND z BETWEEN ? AND ? ORDER BY id", (mind.world_id, dim, x - 1, x + 1, y - 1, y + 1, z - 1, z + 1)).fetchall()
    last = {}
    for r in rows:
        last[(r["x"], r["y"], r["z"])] = r["kind"]
    return any(k == "place" for k in last.values())


AIR = {"minecraft:air", "minecraft:cave_air", "minecraft:short_grass", "minecraft:tall_grass", "minecraft:fern"}
NO_FLOOR = {"minecraft:air", "minecraft:cave_air", "minecraft:water", "minecraft:lava", "hidden", "unloaded"}


ORDER_R = 16                                      # CP-86: the fallback digs within this of where the order began


async def open_ground(mind, st, r=16):
    """O188: the nearest cell within r of her that is natural ground - air with air above, a solid floor, outside every
    zone with place rules, and no placed block within 3 (sense's block events). None if there is none."""
    blocks = await mind.blocks_near(st, r, (-1, 1)) or {}
    if not blocks:
        return None
    x0, y0, z0 = (math.floor(st["pos"][a]) for a in "xyz")
    dim = st.get("dim")
    zones = [zn for zn in mind.env.get("zones", []) if zn.get("dim") == dim and zn.get("place", "free") != "free"]
    placed = {(rw["x"], rw["y"], rw["z"]) for rw in mind.db.execute(
        "SELECT x, y, z FROM block_events WHERE world_id IS ? AND dim=? AND kind='place' AND x BETWEEN ? AND ? AND "
        "z BETWEEN ? AND ?", (mind.world_id, dim, x0 - r - 3, x0 + r + 3, z0 - r - 3, z0 + r + 3))}
    cells = sorted(((x, z) for x in range(x0 - r, x0 + r + 1) for z in range(z0 - r, z0 + r + 1)),
                   key=lambda c: (c[0] - x0) ** 2 + (c[1] - z0) ** 2)
    for x, z in cells:
        if blocks.get((x, y0, z)) not in AIR or blocks.get((x, y0 + 1, z)) not in AIR or \
                blocks.get((x, y0 - 1, z), "unloaded") in NO_FLOOR:
            continue
        if any(all(zn["min"][a] <= v <= zn["max"][a] for a, v in zip("xyz", (x, y0, z))) for zn in zones):
            continue
        if any(abs(px - x) <= 3 and abs(pz - z) <= 3 and abs(py - y0) <= 3 for px, py, pz in placed):
            continue
        return {"x": x, "y": y0, "z": z}
    return None


async def prospect_from_open_ground(mind, params, source, line):
    """O175/O188: an owner prospect (progression or his own "prospect iron ore down to y 16") starts from natural ground -
    walked out to first when she stands in a build or a zone with place rules; none in 16 -> she asks to be led out."""
    st = mind.fresh_state() or {}
    if not isinstance(st.get("pos"), dict):
        await mind.say("I can't see where I am right now.")
        return {"ok": False, "error": "stale_state"}
    if built_here(mind, st):
        spot = await open_ground(mind, st)
        if spot is None:
            await mind.say("I need open ground to dig - lead me out.")
            return {"ok": False, "error": "no_open_ground"}
        await mind.say("Stepping out onto open ground to dig.")
        w = await mind.do("walk_to", {"pos": {a: spot[a] + .5 if a != "y" else float(spot[a]) for a in "xyz"},
                                      "tolerance": 1.0}, source=source, grant=True, wait=60)
        if w.get("status") != "ok":
            await mind.say("I couldn't get out to open ground - lead me out.")
            return {"ok": False, "error": "no_open_ground"}
    await mind.say(line)
    return await mind.owner_do("prospect", params, source)


def dig_for(mind, row, status, code):
    """O163/O175: his mine_resource of a ground block found nothing it could mine -> prospect for it (skills: a
    staircase down into the rock, blocks in the tunnel walls count, hazard stops at lava / water / drops; never a
    straight-down dig). CP-86 run 2 (ledger ad0bae41: 8 stone exposed below a drop, unreachable after 17 attempts, 0
    mined): not_found, unreachable, or partial with nothing mined; it digs for what is still missing. The prospect's
    own outcome is said like any order of his. True = dispatched."""
    import json
    if row["skill"] != "mine_resource" or status != "failed" or code not in ("not_found", "unreachable", "partial"):
        return False
    p = json.loads(row["params"])
    try:
        ev = json.loads(row["evidence"] or "{}") or {}
    except ValueError:
        ev = {}
    mined = ev.get("mined") if isinstance(ev, dict) and type(ev.get("mined")) is int else 0   # a bool is no count
    if code == "partial" and mined > 0:               # some came: the usual partial line, no dig
        return False
    left = int(p.get("count") or 1) - mined
    if left < 1:
        return False
    if p.get("block") not in GROUND:
        return False
    t = mind.db.execute("SELECT source FROM tasks WHERE task_id=?", (row["task_id"],)).fetchone() if row["task_id"] else None
    st = mind.fresh_state() or {}
    his = (t and t["source"] == "owner") or row["action_id"] in mind.owner_orders   # CP-86 run 3: eve ask's "cli" too
    if not his or not isinstance(st.get("pos"), dict) or "prospect" not in mind.registry.skills:
        return False
    inside = built_here(mind, st)                     # O188 (live: the fallback dug out through the shelter's wall)
    began = mind.dig_starts.pop(row["action_id"], None)   # CP-86 run 4: the mine wandered 33 m off - dig near the order
    far = isinstance(began, dict) and math.dist([st["pos"][a] for a in "xyz"], [began[a] for a in "xyz"]) > ORDER_R
    block = "minecraft:stone" if p["block"] == "minecraft:cobblestone" else p["block"]   # cobblestone is stone's drop
    params = {"block": block, "count": min(left, 64),
              "descend_to": max(-58, math.floor(st["pos"]["y"]) - DIG_DEPTH), "max_blocks": 64, "minutes": 5,
              "torch_every": 0}
    fut = mind.followups[row["action_id"]] = asyncio.get_running_loop().create_future()   # the run follows the dig

    async def go():
        new = None
        try:
            new = await dig(params)
        finally:
            fut.set_result(new)

    async def dig(params):
        if inside or far:                             # O188: natural ground; CP-86: near where he asked
            spot = await open_ground(mind, {**st, "pos": began} if far else st, ORDER_R)
            if spot is None:
                await mind.say("I need open ground to dig - lead me out.")
                return None
            await mind.say("Going back near where you asked, to dig there." if far else
                           "Stepping out onto open ground to dig.")
            w = await mind.do("walk_to", {"pos": {a: spot[a] + .5 if a != "y" else float(spot[a]) for a in "xyz"},
                                          "tolerance": 1.0}, source="owner", grant=True, wait=60)
            if w.get("status") != "ok":
                await mind.say("I couldn't get out to open ground - lead me out.")
                return None
            st2 = mind.fresh_state() or st
            params["descend_to"] = max(-58, math.floor(st2["pos"]["y"]) - DIG_DEPTH)
        await mind.say(f"No {craftkit.thing(block)} I can reach here - digging a staircase down for it.")
        r = await mind.do("prospect", params, source="owner", grant=True)
        if r.get("action_id"):
            mind.owner_orders.add(r["action_id"])     # partial / not_found / hazard: said by finish()
        elif not r.get("ok"):
            await mind.say(f"I couldn't dig for {craftkit.thing(block)}: {r.get('code') or r.get('error')}.")
        return r.get("action_id")
    mind.spawn(go())
    return True
