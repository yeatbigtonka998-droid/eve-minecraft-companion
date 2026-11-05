"""O123/O124 (45L session 3): crafting, placing beside her and handing over, by rule - no planner.

- craft: the recipe solver (crafting recipes only) gives the chain from her inventory; a 3x3 step needs a crafting table
  within REACH (sense's query.blocks around her), else her own table placed in the first free cell beside her and
  picked back up after (H-table: she keeps one), else one crafted from her planks/logs first. Missing ingredients are
  said ("Not yet: I need 3 cobblestone"), never attempted.
- place: the first free cell among the 8 around her, the one she faces first, with a solid floor.
- give: walk to him, then body's drop_item (registry 0.16.0); before that skill exists she says she can't yet.
"""
import math
import re

from .commands import item_id
from .recipes import CRAFTING, solve

TABLE = "minecraft:crafting_table"
KINDS = CRAFTING + ("crafting_transmute", "smelting")   # O131: what her craft and smelt skills can do
STATIONS = {"crafting_table": TABLE, "furnace": "minecraft:furnace"}
AIR = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
NO_FLOOR = AIR | {"minecraft:water", "minecraft:lava", "unloaded"}
REACH = 4.5
LEAD = r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:please\s+)?"
# O205 step 2: CAN_MAKE / WHAT_CRAFT / PLACE / GIVE / CRAFT_ANY / CLEAR_TREES are the one owner grammar's readings
# (grammar.py), with the same .match(line) -> m[1], m[2] its callers read; no phrase regex of their own here
from .grammar import CAN_MAKE, CLEAR_TREES, CRAFT_ANY, GIVE, PLACE, WHAT_CRAFT  # noqa: E402,F401
COMMON = ("crafting_table", "stick", "oak_planks", "torch", "chest", "furnace", "wooden_pickaxe", "stone_pickaxe",
          "wooden_axe", "stone_axe", "stone_sword", "wooden_shovel", "stone_shovel", "ladder", "bread")


def thing(item):
    return item.split(":")[-1].replace("_", " ")


def needs(plan):
    """'3 cobblestone and 1 stick' from a solve() result's missing items."""
    parts = [f"{n} {thing(i)}" for i, n in sorted(plan["missing"].items())] + \
        [f"{thing(x.split(' ')[0].rstrip(':'))} (I can't make or find it)" for x in plan["impossible"][:1]]
    return " and ".join(parts) if len(parts) < 3 else ", ".join(parts[:-1]) + " and " + parts[-1]


def resolve(book, name, inv):
    """O131: a spoken item or family ("bed", "planks") -> the recipe output she can make best from her inventory
    (exact id first; else every output named <x>_bed / <x>_planks, feasible first, then fewest missing); None."""
    iid = item_id(name)
    if book.by_output.get(iid):
        return iid
    words = {name.strip().lower().replace(" ", "_")}
    words |= {w[:-1] for w in words if w.endswith("s")} | {w[:-2] for w in words if w.endswith("es")}
    cands = [k for k in book.by_output if any(k.split(":")[1] == w or k.split(":")[1].endswith("_" + w) for w in words)]
    if not cands:
        return None

    def cost(k):
        p = solve(book, {k: 1}, inv, kinds=KINDS)
        return (not p["feasible"], not k.split(":")[1].startswith(("white_", "oak_")), sum(p["missing"].values()), k)
    return min(cands, key=cost)


def recipe_words(book, plan, item):
    """'3 white wool and 3 oak planks': the direct ingredients of the recipe the plan uses for `item`."""
    c = next((c for c in reversed(plan["crafts"]) if item in c["output"]), None)
    r = next((r for r in book.by_output.get(item, []) if c and r["id"] == c["recipe"]), None)
    if r is None:
        return ""
    parts = [f"{n} {thing(o[0]) if len(o) == 1 else ' or '.join(thing(x) for x in o) if len(o) <= 3 else 'any ' + thing(o[0]).split()[-1]}"
             for o, n in r["inputs"]]
    return " and ".join(parts) if len(parts) < 3 else ", ".join(parts[:-1]) + " and " + parts[-1]


def can_make(book, item, inv, count=1):
    """The answer to "can you make X?" from her inventory and the recipes."""
    if item is None or not book.by_output.get(item):
        return f"I don't know a recipe for {thing(item) if item else 'that'}."
    plan = solve(book, {item: count}, inv, kinds=KINDS)
    st = [s.replace("_", " ") for s in plan["stations"]]
    if plan["feasible"]:
        return f"Yes - I have what I need for {thing(item)}" + (f", using a {' and a '.join(st)}." if st else ".")
    w = recipe_words(book, plan, item)
    return f"Not yet: {thing(item)} needs {w}; I'm missing {needs(plan)}." if w else f"Not yet: I need {needs(plan)}."


def what_i_can_make(book, inv):
    ok = [thing(f"minecraft:{i}") for i in COMMON
          if book.by_output.get(f"minecraft:{i}") and solve(book, {f"minecraft:{i}": 1}, inv, kinds=KINDS)["feasible"]]
    return ("Right now I can make: " + ", ".join(ok) + ".") if ok else         "Nothing yet - I have no materials to craft with."


def decode(ans, lo, hi):
    """sense query.blocks.result (data: palette indices, x fastest, then z, then y) -> {(x, y, z): block id}."""
    pal, data, out, i = ans["palette"], ans["data"], {}, 0
    if len(data) != (hi["x"] - lo["x"] + 1) * (hi["y"] - lo["y"] + 1) * (hi["z"] - lo["z"] + 1) or \
            not all(isinstance(d, int) and 0 <= d < len(pal) for d in data):
        return {}                                       # not sense's flat answer: nothing known around her
    for y in range(lo["y"], hi["y"] + 1):
        for z in range(lo["z"], hi["z"] + 1):
            for x in range(lo["x"], hi["x"] + 1):
                out[(x, y, z)] = str(pal[data[i]]).split("[")[0]
                i += 1
    return out


def feet(st):
    return tuple(int(st["pos"][a] // 1) for a in "xyz")


def free_cell(blocks, st):
    """The first free cell (air, solid floor) among the 8 around her feet, the one she faces first; None if none."""
    x, y, z = feet(st)
    yaw = math.radians(float(st.get("yaw") or 0))
    fx, fz = -math.sin(yaw), math.cos(yaw)                    # Minecraft yaw 0 = south (+z), 90 = west (-x)
    around = sorted(((dx, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if dx or dz),
                    key=lambda d: -(d[0] * fx + d[1] * fz) / math.hypot(*d))
    for dx, dz in around:
        c = (x + dx, y, z + dz)
        if blocks.get(c) in AIR and blocks.get((c[0], y - 1, c[2]), "unloaded") not in NO_FLOOR:
            return {"x": c[0], "y": y, "z": c[2]}
    return None


def station_in_reach(blocks, st, block):
    me = st["pos"]
    near = [p for p, b in blocks.items() if b == block and math.dist([p[0] + .5, p[1] + .5, p[2] + .5],
                                                                     [me[a] for a in "xyz"]) <= REACH]
    return {"x": min(near)[0], "y": min(near)[1], "z": min(near)[2]} if near else None


def craft_steps(book, item, count, inv, blocks, st):
    """(steps [(skill, params)], line) or (None, line) - the chain for `count` of `item` from her inventory: crafts at
    the crafting table or in hand, smelts at a furnace. A station not in reach is her own one put down beside her and
    picked back up after (H-table: she keeps it), or one made first (a furnace: 8 cobblestone)."""
    want = {item: count}
    plan = solve(book, want, inv, kinds=KINDS)
    if not plan["feasible"]:
        w = recipe_words(book, plan, item)
        return None, (f"I can't make {thing(item)} yet: it needs {w}; I'm missing {needs(plan)}." if w else
                      f"I can't make {thing(item)} yet: I need {needs(plan)}.")
    if not plan["crafts"]:                              # mind-b: on hand already ("Making crafting table: .")
        return [], f"I already have {count} {thing(item)}."
    at, placed, blocks = {}, {}, dict(blocks)
    for _ in range(3):                                  # a new station can need another (a furnace: a table? no)
        for s in plan["stations"]:
            block = STATIONS.get(s)
            if block is None:
                return None, f"I can't use a {s.replace('_', ' ')} yet."
            if s in at:
                continue
            at[s] = station_in_reach(blocks, st, block)
            if at[s] is None:
                if inv.get(block, 0) < 1 and block not in want:
                    want = {block: 1, **want}           # made first, then put down and kept
                    want = {**{b: 1 for b in STATIONS.values() if b in want}, **want}   # a table before a furnace
                    plan = solve(book, want, inv, kinds=KINDS)
                    if not plan["feasible"]:
                        return None, f"I need a {s.replace('_', ' ')} for that, and I'm missing {needs(plan)}."
                cell = free_cell(blocks, st)
                if cell is None:
                    return None, f"There's no free spot beside me to put a {s.replace('_', ' ')} down."
                at[s] = placed[s] = cell
                blocks[(cell["x"], cell["y"], cell["z"])] = block   # the next station takes another cell
        if all(s in at for s in plan["stations"]):
            break
    steps, said, down = [], [], set()
    for c in plan["crafts"]:
        (out, n), = c["output"].items()
        s = c["station"]
        if s in placed and s not in down:
            steps.append(("place_block", {"pos": placed[s], "item": STATIONS[s]}))
            said.append(f"put my {s.replace('_', ' ')} down")
            down.add(s)
        if c["kind"] in ("smelting",):
            r = next(r for r in book.by_output[out] if r["id"] == c["recipe"])
            src = next((o for o in r["inputs"][0][0] if inv.get(o)), r["inputs"][0][0][0])
            steps.append(("smelt", {"pos": at[s], "item": out, "count": min(n, 64)}))   # skills 86b928a0: the OUTPUT
            said.append(f"smelt {c['times']} {thing(src)}")
        else:
            steps.append(("craft", {"item": out, "count": n, **({"pos": at[s]} if s else {})}))
            said.append(f"{n} {thing(out)}")
    for s, cell in placed.items():
        steps.append(("mine_block", {"pos": cell}))     # H-table: her station comes back with her
        said.append(f"pick the {s.replace('_', ' ')} back up")
    return steps, f"Making {thing(item)}: " + ", then ".join(said) + "."


def is_item(name):
    """A spoken name is a 26.3 item (with the game data installed; without it, no name is)."""
    from .memory import known_items
    items = known_items()
    return bool(items) and item_id(name) in items


def held_item(name, inv):
    """The item id in her inventory that `name` names: the exact id first, else one whose name ends with it."""
    want = item_id(name)
    if inv.get(want):
        return want, True
    base = name.strip().lower().replace(" ", "_")
    bases = {base, base[:-1] if base.endswith("s") else base}
    hits = [k for k, n in inv.items() if n > 0 and any(k.split(":")[-1] == b or k.split(":")[-1].endswith("_" + b)
                                                        for b in bases)]
    return (hits[0], False) if len(hits) == 1 else (None, False)
