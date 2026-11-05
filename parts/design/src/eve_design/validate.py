"""Independent design checks (CP-60). Every check reads the compiled grid and the pinned 26.3 data (block tags,
legal states); labels only say where rooms are claimed to be, and the checks test those claims against blocks.

Assumptions (recorded in each result's "approximations"): the design sits on flat solid ground just below y=0
unless a brief supplies terrain; collision, light opacity and luminance are classified by game tags plus a short
name list, not by the game's shape code; reach is 4.5 blocks from the eye (1.62 above the feet) to the nearest
point of the block; a player can jump 1 block and drop up to 3.

    validate(design, gd, brief=None, bom=None, scaffold=None, policy=None) -> validation.json dict
"""
from __future__ import annotations

from collections import deque
from functools import lru_cache

import numpy as np

from .gamedata import GameData, parse_state
from .grid import AIR, Design

VALIDATOR_VERSION = "eve-validate/3"   # 2: buildable replays the build order (eve_design.placecheck); 3: stand_audit
MAX_DROP = 3
MIN_FREE_FLOOR = 3         # a room needs at least this many standable cells with 2 blocks of headroom
SIDE_STEP = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
DOOR_SIDES = {"north": ("north", "south"), "south": ("north", "south"), "east": ("east", "west"), "west": ("east", "west")}
FLUIDS = ("minecraft:water", "minecraft:lava")
PAD = 6                    # walkable ground ring around the design
DEFAULT_POLICY = {"approval_limit_blocks": None,     # S16: Eve's own designs need no owner approval (None = no limit)
                  "max_blocks": 50_000, "phase_blocks": 2000}
LUMINANCE = {  # ponytail: hand table of common emitters (game wiki values), PROVISIONAL; unknown emitters count as 0
    "lantern": 15, "soul_lantern": 10, "torch": 14, "wall_torch": 14, "soul_torch": 10, "soul_wall_torch": 10,
    "redstone_torch": 7, "redstone_wall_torch": 7, "copper_torch": 14, "copper_wall_torch": 14, "glowstone": 15,
    "sea_lantern": 15, "jack_o_lantern": 15, "shroomlight": 15, "end_rod": 14, "lava": 15, "fire": 15, "soul_fire": 10,
    "beacon": 15, "ochre_froglight": 15, "verdant_froglight": 15, "pearlescent_froglight": 15, "crying_obsidian": 10,
    "glow_lichen": 7, "magma_block": 3}
LIT_ONLY = {"campfire": 15, "soul_campfire": 10, "redstone_lamp": 15, "copper_bulb": 15}
GRAVITY = ("sand", "red_sand", "gravel", "suspicious_sand", "suspicious_gravel", "anvil", "chipped_anvil",
           "damaged_anvil", "dragon_egg")
WOODS = ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry", "pale_oak", "bamboo", "poplar")
FIRE_SOURCES = ("lava", "fire", "soul_fire")
ROOM_NEEDS = {"bedroom": [("bed", ("_bed",))], "kitchen": [("furnace or smoker", ("furnace", "smoker"))],
              "storage": [("chest or barrel", ("chest", "barrel"))], "workshop": [("crafting table", ("crafting_table",))],
              "living": []}


class Classes:
    """Per-block-id classification from 26.3 tags + short name lists."""

    def __init__(self, gd: GameData):
        t = lambda n: set(gd.tag("block", f"minecraft:{n}"))
        self.passable = (t("replaceable") - {"minecraft:water", "minecraft:lava", "minecraft:bubble_column"}) | t("flowers") \
            | t("saplings") | t("all_signs") | t("buttons") | t("pressure_plates") | t("rails") | t("wool_carpets") \
            | t("doors") | t("fence_gates") | t("banners") | t("climbable") | t("crops") | {
                f"minecraft:{n}" for n in ("torch", "wall_torch", "soul_torch", "soul_wall_torch", "redstone_torch",
                                           "redstone_wall_torch", "copper_torch", "copper_wall_torch", "redstone_wire",
                                           "tripwire", "lever", "moss_carpet", "pale_moss_carpet", "pink_petals",
                                           "wildflowers", "cobweb", "nether_portal")}
        self.doors = t("doors") | t("fence_gates")          # openable: walked through, but closed for enclosure
        self.two_high_doors = t("doors")
        self.trapdoors, self.stairs = t("trapdoors"), t("stairs")
        self.leaves, self.impermeable = t("leaves"), t("impermeable")       # full collision, though partial above
        self.tall = t("fences") | t("walls") | t("fence_gates")                  # cannot be stood on (1.5 tall)
        self.partial = t("slabs") | t("stairs") | t("fences") | t("walls") | t("fence_gates") | t("doors") | t("trapdoors") \
            | t("leaves") | t("beds") | t("impermeable") | t("campfires") | t("candles") | t("all_signs") | t("banners")
        self.flammable = {b for b in t("logs_that_burn") | t("planks") | t("wooden_slabs") | t("wooden_stairs")
                          | t("wooden_fences") | t("fence_gates") | t("wooden_doors") | t("wooden_trapdoors")
                          if any(w in b for w in WOODS)} | t("wool") | t("wool_carpets") | t("leaves") | {
            f"minecraft:{n}" for n in ("bookshelf", "chiseled_bookshelf", "hay_block", "tnt", "vine", "scaffolding",
                                       "lectern", "composter", "beehive", "bee_nest", "target", "dried_kelp_block")}
        self.wall_signs, self.standing_signs = t("wall_signs"), t("standing_signs")
        self.soil = t("dirt") | {"minecraft:farmland"}
        self.plants = t("flowers") | t("saplings")

    def walk_pass(self, bid):
        return bid in self.passable

    def opaque(self, bid):
        """Blocks light fully. ponytail: full solid cubes by exclusion; stairs/slabs count as transparent."""
        name = bid.split(":")[1]
        return not (bid in self.passable or bid in self.partial or bid == AIR
                    or any(p in name for p in ("glass", "pane", "bars", "lantern", "chain", "chest", "ladder", "lantern",
                                               "torch", "rod", "pot", "head", "skull", "cake", "carpet", "anvil",
                                               "table", "lectern", "bell", "stonecutter", "grindstone", "brewing",
                                               "cauldron", "hopper", "composter", "honey", "slime", "ice", "scaffolding",
                                               "dripstone", "amethyst_cluster", "bud", "kelp", "coral", "barrier")))


@lru_cache(maxsize=4)
def classes(gd: GameData) -> Classes:
    return Classes(gd)


def _lum(state: str) -> int:
    bid, props = parse_state(state)
    n = bid.split(":")[1]
    if n in LIT_ONLY:
        return LIT_ONLY[n] if props.get("lit") == "true" else 0
    if n.endswith("candle") or n.endswith("_candle"):
        return 3 * int(props.get("candles", 1)) if props.get("lit") == "true" else 0
    return LUMINANCE.get(n, 0)


def _p(x, y, z):
    return {"x": int(x), "y": int(y), "z": int(z)}


class Grid:
    """The design padded with a walkable ground ring; arrays indexed [y, z, x] in padded coordinates."""

    def __init__(self, g: Design, gd: GameData, scaffold: Design | None = None):
        c = classes(gd)
        self.g, self.c, self.gd = g, c, gd
        sx, sy, sz = g.size
        P = PAD
        self.shape = (sy + 3, sz + 2 * P, sx + 2 * P)
        ids = [parse_state(s)[0] for s in g.palette]
        lut = lambda f: np.array([f(i) for i in ids], bool)
        place = lambda a, fill: _pad(a, self.shape, P, fill)
        self.air = place(g.blocks == 0, True)
        self.passable = place(lut(c.walk_pass)[g.blocks], True)
        self.door = place(lut(lambda b: b in c.doors)[g.blocks], False)
        self.tall = place(lut(lambda b: b in c.tall)[g.blocks], False)
        self.step = place(np.array([parse_state(s)[0] in c.stairs or s.endswith("type=bottom,waterlogged=false]")
                                    and "_slab" in s for s in g.palette], bool)[g.blocks], False)
        self.opaque = place(lut(c.opaque)[g.blocks], False)
        self.light_src = place(np.array([_lum(s) for s in g.palette], np.int8)[g.blocks], 0)
        self.tops = []            # standing spots on top of scaffold pillars (the builder pillars up to them)
        if scaffold is not None:  # scaffold blocks: solid, standable (placed relative to the design origin)
            off = [scaffold.origin[i] - g.origin[i] for i in range(3)]
            top = {}
            for x, y, z, s, _ in scaffold.cells():
                X, Y, Z = x + off[0] + P, y + off[1], z + off[2] + P
                if 0 <= Y < self.shape[0] and 0 <= Z < self.shape[1] and 0 <= X < self.shape[2]:
                    self.passable[Y, Z, X] = self.air[Y, Z, X] = False
                    top[(X, Z)] = max(top.get((X, Z), -1), Y)
            self.tops = [(X, Y + 1, Z) for (X, Z), Y in sorted(top.items())]
        self.P = P

    def to_design(self, X, Y, Z):
        return X - self.P, Y, Z - self.P

    def inb(self, X, Y, Z):
        return 0 <= Y < self.shape[0] and 0 <= Z < self.shape[1] and 0 <= X < self.shape[2]

    def standable(self, X, Y, Z):
        if not (self.inb(X, Y, Z) and Y + 1 < self.shape[0]):
            return False
        if not (self.passable[Y, Z, X] and self.passable[Y + 1, Z, X]):
            return False
        if Y == 0:
            return True                                   # flat ground below the design
        return not self.passable[Y - 1, Z, X] and not self.tall[Y - 1, Z, X]

    def walk(self, seeds=None, seen=None):
        """BFS over feet positions from the outer ground ring (or extra seeds). Returns the reachable feet cells."""
        H, D, W = self.shape
        start = seeds if seeds is not None else self.tops +             [(X, 0, Z) for Z in range(D) for X in range(W) if (X in (0, W - 1) or Z in (0, D - 1)) and self.standable(X, 0, Z)]
        seen = set() if seen is None else seen
        start = [s for s in start if s not in seen]
        seen.update(start)
        q = deque(start)
        while q:
            X, Y, Z = q.popleft()
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, nz = X + dx, Z + dz
                cands = [Y]
                # up 1: a jump needs clearance over the current cell; stepping onto stairs/bottom slabs does not
                if Y + 2 < H and (self.passable[Y + 2, Z, X] or self.inb(nx, Y, nz) and self.step[Y, nz, nx]):
                    cands.append(Y + 1)
                for k in range(1, MAX_DROP + 1):
                    if Y - k < 0:
                        break
                    cands.append(Y - k)
                for ny in cands:
                    if (nx, ny, nz) in seen or not self.standable(nx, ny, nz):
                        continue
                    if ny < Y and not all(self.inb(nx, yy, nz) and self.passable[yy, nz, nx] for yy in range(ny, Y + 2)):
                        continue
                    seen.add((nx, ny, nz))
                    q.append((nx, ny, nz))
        return seen

    def light(self):
        """Block light: multi-source BFS, -1 per step through non-opaque cells."""
        L = self.light_src.astype(np.int16).copy()
        q = deque(zip(*np.nonzero(L > 0)))
        H, D, W = self.shape
        while q:
            y, z, x = q.popleft()
            lv = L[y, z, x] - 1
            if lv <= 0:
                continue
            for dy, dz, dx in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                ny, nz, nx = y + dy, z + dz, x + dx
                if 0 <= ny < H and 0 <= nz < D and 0 <= nx < W and not self.opaque[ny, nz, nx] and L[ny, nz, nx] < lv:
                    L[ny, nz, nx] = lv
                    q.append((ny, nz, nx))
        return L


def _pad(a, shape, P, fill):
    out = np.full(shape, fill, dtype=np.asarray(a).dtype)
    out[: a.shape[0], P:P + a.shape[1], P:P + a.shape[2]] = a
    return out


def _result(status="pass", failures=None, notes=None, approx=None):
    return {"status": status if not failures else "fail", "failures": failures or [], "notes": notes or [],
            "approximations": approx or []}


def _room_cells(G: Grid, room):
    mn, mx = room["min"], room["max"]
    for y in range(mn["y"], mx["y"] + 1):
        for z in range(mn["z"], mx["z"] + 1):
            for x in range(mn["x"], mx["x"] + 1):
                yield x, y, z


# --- checks ------------------------------------------------------------------------------------------
def check_walkable(G: Grid, reach: set) -> dict:
    fails, notes = [], []
    P = G.P
    # O89i (builder, lane C hut5_tall a12/a13): Eve's Baritone enters or leaves a door cell only on a level move, so
    # each side of a door, along its facing, needs feet room on a floor at the door's own height. Listed first: the
    # generator's repair sees only the first few failures of a check, and one door can make every room unreachable.
    name = lambda x, y, z: G.g.get(x, y, z).split("[")[0].removeprefix("minecraft:") if G.g.in_bounds(x, y, z) else "air"
    why = "Eve enters a door only on a level move"
    for x, y, z, s, _ in G.g.cells():
        bid, p = parse_state(s)
        if bid not in G.c.two_high_doors or p.get("half") != "lower" or p.get("facing") not in DOOR_SIDES:
            continue
        for side in DOOR_SIDES[p["facing"]]:
            dx, dz = SIDE_STEP[side]
            X, Z = x + dx, z + dz
            if G.standable(X + P, y, Z + P):
                continue
            hit = next((h for h in (y, y + 1) if G.inb(X + P, h, Z + P) and not G.passable[h, Z + P, X + P]), None)
            if hit is not None:
                r = (f"door at ({x},{y},{z}) is blocked on its {side} side by the {name(X, hit, Z)} at y {hit}: {why}, "
                     f"so that side needs 2 free blocks")
            elif name(X, y - 1, Z) != "air":
                r = (f"door at ({x},{y},{z}) has no level approach on its {side} side: the {name(X, y - 1, Z)} at "
                     f"y {y - 1} there is not a floor to stand on ({why})")
            elif (rise := y - 1 - next((h for h in range(y - 2, -1, -1) if not G.passable[h, Z + P, X + P]), -1)) >= 2:
                r = (f"door at ({x},{y},{z}) has no level approach on its {side} side: the ground there is {rise} "
                     f"blocks below the door, more than one step ({why}). 45L h10: a stone course at the base is "
                     f'detail(rule="base_band") on a 1-high foundation, not a taller foundation')
            else:
                r = (f"door at ({x},{y},{z}) has no level approach on its {side} side: {why}, so that side needs a "
                     f"floor at y {y - 1} with 2 blocks of headroom, not a step up or down (the compiler adds a "
                     f"doorstep where the doorway is clear and the plot has room)")
            fails.append({"pos": _p(x, y, z), "reason": r})
    # 45L h10 attempt 8: a revision raised the foundation for a "stone course"; its door and porch were then 2 up
    entry = min((y for x, y, z, s, _ in G.g.cells() if parse_state(s)[0] in G.c.two_high_doors
                 and parse_state(s)[1].get("half") == "lower"), default=None)    # the lowest door, not a room opening
    high = (f"; the lowest entrance is {entry} blocks above the ground, more than one step: a stone course at the base "
            f'is detail(rule="base_band") on a 1-high foundation') if entry is not None and entry >= 2 else ""
    for room in G.g.rooms:
        feet = [(x, y, z) for x, y, z in _room_cells(G, room) if G.standable(x + P, y, z + P)]
        if not feet:
            fails.append({"pos": room["min"], "reason": f"room {room['id']!r} has no floor with 2 blocks of headroom"})
            continue
        if not any((x + P, y, z + P) in reach for x, y, z in feet):
            fails.append({"pos": room["min"], "reason": f"room {room['id']!r} cannot be reached on foot from outside "
                                                          f"(no path with 2-block headroom, jumps of 1, drops of {MAX_DROP})"
                                                          + high})
        y0 = room["min"]["y"]
        low = [(x, z) for x in range(room["min"]["x"], room["max"]["x"] + 1) for z in range(room["min"]["z"], room["max"]["z"] + 1)
               if G.passable[y0, z + P, x + P] and not G.passable[y0 - 1, z + P, x + P]
               and not G.passable[y0 + 1, z + P, x + P] and not G.step[y0 + 1, z + P, x + P]]  # under-stair nooks are fine
        for x, z in low:
            fails.append({"pos": _p(x, y0, z), "reason": f"headroom under 2 blocks in room {room['id']!r}"})
    if not G.g.rooms:
        notes.append("no rooms labelled; only checked that the design can be walked around")
    return _result(failures=fails, notes=notes, approx=["flat ground below y=0", "doors count as passable (openable)",
                                                        "a door's approach: the cell beside it along its facing"])


def check_enclosed(G: Grid) -> dict:
    """Flood air from every room through non-solid cells with doors closed; reaching the open edge is a leak."""
    P = G.P
    H, D, W = G.shape
    open_ = G.passable & ~G.door
    fails = []
    done: set = set()
    for room in G.g.rooms:
        seeds = [(y, z + P, x + P) for x, y, z in _room_cells(G, room) if open_[y, z + P, x + P]]
        if not seeds or seeds[0] in done:
            continue
        parent = {s: None for s in seeds}
        q = deque(seeds)
        leak = None
        while q and leak is None:
            y, z, x = q.popleft()
            for dy, dz, dx in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                ny, nz, nx = y + dy, z + dz, x + dx
                if not (0 <= ny < H and 0 <= nz < D and 0 <= nx < W) or ny >= H - 2 or nz in (0, D - 1) or nx in (0, W - 1):
                    leak = (y, z, x)
                    break
                if ny < 0 or (ny, nz, nx) in parent or not open_[ny, nz, nx]:
                    continue
                parent[(ny, nz, nx)] = (y, z, x)
                q.append((ny, nz, nx))
        done |= set(parent)
        if leak:
            # walk back from the open edge to the first cell touching the building: that is where the envelope breaks
            path = [leak]
            while parent[path[-1]] is not None:
                path.append(parent[path[-1]])
            solid = lambda y, z, x: 0 <= y < H and 0 <= z < D and 0 <= x < W and not G.passable[y, z, x]
            # a gap is an open cell with solid blocks on two opposite sides (a hole in a wall, roof or floor)
            gap_at = lambda c: any(solid(c[0] + a, c[1] + b, c[2] + d) and solid(c[0] - a, c[1] - b, c[2] - d)
                                   for a, b, d in ((1, 0, 0), (0, 1, 0), (0, 0, 1)))
            gap = next((c for c in path if gap_at(c)), path[-1])
            fails.append({"pos": _p(gap[2] - P, gap[0], gap[1] - P),
                          "reason": f"room {room['id']!r} is open to the outside near here (doors and glass count as closed)"})
    return _result(failures=fails, approx=["passable blocks (torches, signs, carpets...) count as gaps"])


def check_lit(G: Grid) -> dict:
    L = G.light()
    P = G.P
    fails, dark = [], 0
    for room in G.g.rooms:
        cells = list(_room_cells(G, room))
        # 45L h10: a revision dropped furnish, or a second door left a tiny room no free edge cell for its lantern
        none = "" if any(G.light_src[y, z + P, x + P] > 0 for x, y, z in cells) else (
            "; there is no light source in the room: room(furnish=True) puts a lantern on a free edge cell, and the "
            "cells in front of each entrance stay clear, so a small room with two doors can have none")
        for x, y, z in cells:
            if G.standable(x + P, y, z + P) and L[y, z + P, x + P] == 0:
                dark += 1
                if len(fails) < 20:
                    fails.append({"pos": _p(x, y, z), "reason": f"block light 0 in room {room['id']!r} (hostile mobs can spawn){none}"})
    notes = [f"{dark} dark floor cells in total"] if dark > len(fails) else []
    return _result(failures=fails, notes=notes, approx=["luminance from a hand table", "stairs/slabs/glass treated as transparent",
                                                         "sky light ignored (interiors)"])


OPPOSITE_OFFSET = {"north": (0, 0, 1), "south": (0, 0, -1), "east": (-1, 0, 0), "west": (1, 0, 0)}


def attachment(bid: str, p: dict, c: Classes):
    """((dx, dy, dz), reason) of the block this one rests on or hangs from, or None. Shared by the `stable` check and
    the build order (eve_design.buildorder)."""
    n = bid.split(":")[1]
    if n in GRAVITY or n.endswith("concrete_powder"):
        return (0, -1, 0), "falls: nothing under it"
    if n in ("torch", "soul_torch", "redstone_torch", "copper_torch", "redstone_wire") or bid in c.standing_signs \
            or n.endswith("_carpet") or n.endswith("_pressure_plate") or n.endswith("rail") or n.startswith("candle") \
            or n.endswith("_candle") or (n.endswith("lantern") and p.get("hanging") == "false"):
        return (0, -1, 0), "needs a block under it"
    if n.endswith("lantern") and p.get("hanging") == "true" or n.endswith("_hanging_sign") and "wall" not in n:
        return (0, 1, 0), "needs a block above it"
    if bid in c.two_high_doors and p.get("half") == "lower":
        return (0, -1, 0), "door needs a block under it"
    if (n.endswith("wall_torch") or bid in c.wall_signs or n.endswith("wall_banner") or n == "ladder") and "facing" in p:
        return OPPOSITE_OFFSET[p["facing"]], "needs a block behind it"
    if n.endswith("_button") or n == "lever":
        face = p.get("face")
        return ((0, -1, 0) if face == "floor" else (0, 1, 0) if face == "ceiling" else OPPOSITE_OFFSET[p["facing"]],
                "needs a block to sit on")
    if bid in c.plants and "potted" not in n:
        return (0, -1, 0), "needs dirt or grass under it"
    return None


def check_stable(G: Grid) -> dict:
    g, c = G.g, G.c
    is_solid = lambda x, y, z: y < 0 if not g.in_bounds(x, y, z) else (g.get(x, y, z) != AIR and parse_state(g.get(x, y, z))[0] not in c.passable)
    fails = []
    for x, y, z, s, _ in g.cells():
        bid, p = parse_state(s)
        n = bid.split(":")[1]
        if bid in c.plants and "potted" not in n:
            below = g.get(x, y - 1, z) if y > 0 else "minecraft:dirt"
            if parse_state(below)[0] not in c.soil:
                fails.append({"pos": _p(x, y, z), "reason": f"{n} needs dirt or grass under it"})
            continue
        need = attachment(bid, p, c)
        if need and not is_solid(x + need[0][0], y + need[0][1], z + need[0][2]):
            fails.append({"pos": _p(x, y, z), "reason": f"{n} {need[1]}"})
        if bid in FLUIDS or p.get("waterlogged") == "true":
            for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, -1, 0)):
                q = (x + dx, y + dy, z + dz)
                if y + dy < 0:
                    continue
                qs = g.get(*q) if g.in_bounds(*q) else AIR
                if parse_state(qs)[0] in c.passable and parse_state(qs)[0] not in FLUIDS:
                    fails.append({"pos": _p(x, y, z), "reason": f"{'water' if bid != 'minecraft:lava' else 'lava'} would "
                                                                f"flow out into {_p(*q)}"})
                    break
        # two-block structures must be whole
        if bid in c.two_high_doors:
            other = (x, y + 1, z) if p.get("half") == "lower" else (x, y - 1, z)
            if not g.in_bounds(*other) or parse_state(g.get(*other))[0] != bid:
                fails.append({"pos": _p(x, y, z), "reason": f"{n} is missing its other half"})
        if n.endswith("_bed"):
            dx, dz = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}[p["facing"]]
            o = (x + dx, y, z + dz) if p["part"] == "foot" else (x - dx, y, z - dz)
            if not g.in_bounds(*o) or parse_state(g.get(*o))[0] != bid:
                fails.append({"pos": _p(x, y, z), "reason": f"{n} is missing its other half"})
    return _result(failures=fails, approx=["support = any non-passable neighbour; face sturdiness not modelled"])


def check_fire(G: Grid, policy: dict) -> dict:
    g, c = G.g, G.c
    fails = []
    for x, y, z, s, _ in g.cells():
        bid, p = parse_state(s)
        n = bid.split(":")[1]
        if not (n in FIRE_SOURCES or n in ("campfire", "soul_campfire") and p.get("lit") == "true"):
            continue
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    q = (x + dx, y + dy, z + dz)
                    if (dx or dy or dz) and g.in_bounds(*q) and parse_state(g.get(*q))[0] in c.flammable:
                        fails.append({"pos": _p(*q), "reason": f"flammable {g.get(*q).split('[')[0]} next to {n} at {_p(x, y, z)}"})
    return _result(failures=fails, approx=["flammability from wood/wool/leaf tags + a short list", "26-neighbourhood"])


def check_function(G: Grid, brief: dict | None) -> dict:
    g = G.g
    fails = []
    for room in g.rooms:
        ids = {parse_state(g.get(*c))[0].split(":")[1] for c in _room_cells(G, room) if g.in_bounds(*c)}
        for label, pats in ROOM_NEEDS.get(room["type"], []):
            if not any(i.endswith(pt) or i == pt for i in ids for pt in pats):
                fails.append({"pos": room["min"], "reason": f"{room['type']} {room['id']!r} has no {label}"})
    P = G.P
    for room in g.rooms:
        free = sum(1 for x, y, z in _room_cells(G, room) if y == room["min"]["y"] and G.standable(x + P, y, z + P))
        if free < MIN_FREE_FLOOR:
            fails.append({"pos": room["min"], "reason": f"{room['type']} {room['id']!r} has only {free} free floor cells "
                                                        f"(needs {MIN_FREE_FLOOR}) to stand in"})
    for want in (brief or {}).get("rooms", []):
        if sum(r["type"] == want for r in g.rooms) < (brief or {}).get("rooms", []).count(want):
            fails.append({"pos": None, "reason": f"the brief asks for a {want} and the design has none (or too few)"})
    return _result(failures=fails, notes=[] if brief else ["no brief: checked labelled rooms only"])


def check_site(G: Grid, brief: dict | None) -> dict:
    site = (brief or {}).get("site")
    if not site:
        return {"status": "skipped", "failures": [], "notes": ["no site in the brief"], "approximations": []}
    g = G.g
    ox, oy, oz = g.origin
    fails = []
    plot = site.get("plot")
    foot = {(x, z) for x, y, z, _, _ in g.cells()}
    if plot:
        out = sorted((x, z) for x, z in foot if not (plot["min"]["x"] <= ox + x <= plot["max"]["x"] and plot["min"]["z"] <= oz + z <= plot["max"]["z"]))
        fails += [{"pos": _p(x, 0, z), "reason": "outside the plot boundary"} for x, z in out[:20]]
    top = oy + max(y for _, y, _, _, _ in g.cells())
    if site.get("height_limit") is not None and top > site["height_limit"]:
        fails.append({"pos": None, "reason": f"top at y={top} is above the height limit {site['height_limit']}"})
    if oy < -64 or top > 319:
        fails.append({"pos": None, "reason": f"design spans y {oy}..{top}, outside the world (-64..319)"})
    terrain = site.get("terrain")
    if terrain:
        allow = int(site.get("terrain_change", 0))
        tx, tz = terrain["origin"]["x"], terrain["origin"]["z"]
        base = {(x, z) for x, y, z, _, _ in g.cells() if y == 0}
        for x, z in sorted(base):
            i, j = oz + z - tz, ox + x - tx
            if not (0 <= i < len(terrain["heights"]) and 0 <= j < len(terrain["heights"][0])):
                fails.append({"pos": _p(x, 0, z), "reason": "no terrain data under this column"})
                continue
            ground = terrain["heights"][i][j]               # world y of the top ground block
            change = abs(ground - (oy - 1))
            if change > allow:
                fails.append({"pos": _p(x, 0, z), "reason": f"needs {'fill' if ground < oy - 1 else 'digging'} of {change} "
                                                             f"blocks here (allowed {allow})"})
    return _result(failures=fails[:40])


def check_buildable(G: Grid, reach_feet: set, scaffold: Design | None, site: dict | None = None,
                    keep: dict | None = None) -> dict:
    """Every block is connected to the ground through placed blocks and within reach of a standing position.
    `keep`, if given, receives the replayed build order's steps (for stand_audit)."""
    g, P = G.g, G.P
    solid = ~G.air
    H, D, W = G.shape
    # connectivity: flood from the bottom layer through non-air cells
    conn = np.zeros_like(solid)
    q = deque((0, z, x) for z, x in zip(*np.nonzero(solid[0])))
    for c in q:
        conn[c] = True
    while q:
        y, z, x = q.popleft()
        for dy, dz, dx in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            n = (y + dy, z + dz, x + dx)
            if 0 <= n[0] < H and 0 <= n[1] < D and 0 <= n[2] < W and solid[n] and not conn[n]:
                conn[n] = True
                q.append(n)
    fails = []
    for y, z, x in zip(*np.nonzero(solid & ~conn)):
        if len(fails) < 20:
            fails.append({"pos": _p(x - P, y, z - P), "reason": "floating: not connected to the ground, cannot be placed without support"})
    if fails:
        return _result(failures=fails)
    from .buildorder import plan_order
    from .placecheck import EYE, REACH as BUILDER_REACH, stand_policy
    try:
        steps, placed_fails, _ = plan_order(g, G.gd, scaffold, site=site)
    except ValueError as e:
        return _result(failures=[{"pos": None, "reason": f"no build order: {e}"}])
    if keep is not None:
        keep["steps"] = steps
    tail = " even with the scaffold" if scaffold else "; needs a scaffold plan"
    fails = [{"pos": f["pos"], "reason": f["reason"] + tail} for f in placed_fails[:20]]
    notes = [f"{len(placed_fails)} blocks cannot be placed"] if len(placed_fails) > 20 else []
    return _result(failures=fails, notes=notes, approx=[
        "build_order.json replayed block by block with eve-builder's rules (eve_design.placecheck): a standing spot "
        f"reachable on foot (sturdy top, bottom slab or stair), a face within {BUILDER_REACH} of the eye (feet + {EYE}), "
        f"hit first by the ray, look matching the facing; stand policy '{stand_policy(g)}'",
        "scaffold blocks are taken as placeable; the click aims at face centres (body's place_block v2 may do better)"])


def check_stand_audit(g: Design, steps: list | None, scaffold: Design | None, ran: bool = True) -> dict:
    """O94ag (lane C C2; eve-organic's C2-STANDS-ANALYSIS): what each planned stand rests on. From the ground, scaffold
    or the design's floors 97 % of C2's blocks were placed; from its roof, wall, trim and support tops 33 %, because
    Baritone found no route there. Any stand on the design's exterior fails an eve-organic design (or one under the
    walk_surfaces policy, which plans none) and is a warning for every other design. A stand on scaffold counts by what
    its dirt column rests on (dirt started on a roof is reached over the roof). Role "none" (imported schematics) is
    unlabelled, not exterior. `ran` False: buildable was skipped, so this is too; buildable that stopped before a build
    order leaves it "n/a" (not in validation.json's skipped list, which means "needs a brief")."""
    from .placecheck import WALK_ROLES, organic, stand_policy
    if steps is None:
        return {"status": "skipped" if not ran else "n/a", "failures": [], "approximations": [],
                "notes": ["buildable was skipped" if not ran else "no build order (buildable stopped before one)"]}
    scaf = set()
    if scaffold is not None:
        off = [scaffold.origin[i] - g.origin[i] for i in range(3)]
        scaf = {(x + off[0], y + off[1], z + off[2]) for x, y, z, _, _ in scaffold.cells()}
    kinds, ext = {}, []
    for st in steps:
        if not st.get("stand"):
            continue
        x, y, z = st["stand"]
        under, via = (x, y - 1, z), ""
        while under in scaf:                     # down the dirt column to what it rests on
            under, via = (x, under[1] - 1, z), " (under scaffold)"
        if under[1] < 0:
            kind = "scaffold" if via else "ground"
        elif g.in_bounds(*under) and g.get(*under) != AIR:
            role = g.role_at(*under)
            kind = ("scaffold" if via else role) if role in WALK_ROLES else "unlabelled" if role == "none" \
                else f"exterior {role}{via}"
        else:
            kind = "other"
        kinds[kind] = kinds.get(kind, 0) + 1
        if kind.startswith("exterior"):
            ext.append((st, kind.split(" ", 1)[1], under))
    n = sum(kinds.values())
    finals = sum(1 for st in steps if st.get("stand") and st["layer"] not in ("scaffold", "scaffold_removal"))
    ext_final = sum(1 for st, _, _ in ext if st["layer"] not in ("scaffold", "scaffold_removal"))
    summary = f"{len(ext)} of {n} planned stands rest on the design's exterior ({ext_final} of {finals} design-block " \
        "steps); " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]))
    if not ext:
        return _result(notes=[summary])
    strict = organic(g) or stand_policy(g) == "walk_surfaces"
    if strict:
        return _result(failures=[{"pos": _p(*st["stand"]), "reason": f"step {st['step']} ({st['layer']}) stands on the "
                                  f"design's {role} at {under}: Eve's Baritone has no route onto design tops (O94ag)"}
                                 for st, role, under in ext[:20]], notes=[summary])
    return {"status": "warn", "failures": [], "approximations": [],
            "notes": [summary + " (a warning for a non-organic design: hut5_sim built with 6 roof stands; lane C C2 "
                                "placed 33 % from such stands)"]}


def check_affordable(bom: dict | None, brief: dict | None) -> dict:
    if not bom:
        return {"status": "skipped", "failures": [], "notes": ["no bill of materials"], "approximations": []}
    fails = []
    for p in bom["deficit"]["gather_plan"]:
        if p["via"] == "unknown":
            fails.append({"pos": None, "reason": f"{p['item']} cannot be crafted or gathered by any known route"})
    budget = (brief or {}).get("budget", {})
    if budget.get("max_blocks") is not None and bom["totals"]["final_blocks"] > budget["max_blocks"]:
        fails.append({"pos": None, "reason": f"{bom['totals']['final_blocks']} blocks exceed the budget of {budget['max_blocks']}"})
    mins = bom["totals"].get("estimated_gather_minutes")
    if budget.get("gather_minutes") is not None:
        margin = budget.get("uncertainty_margin", 0.5)
        if mins is None:
            fails.append({"pos": None, "reason": "gathering time unknown for some items"})
        elif mins * (1 + margin) > budget["gather_minutes"]:
            fails.append({"pos": None, "reason": f"estimated gathering {mins} min (+{int(margin * 100)}% margin) exceeds "
                                                 f"the budget of {budget['gather_minutes']} min"})
    notes = [] if budget else ["no budget in the brief: only checked that every material is obtainable"]
    return _result(failures=fails, notes=notes, approx=["gathering minutes are uncalibrated estimates (bom.json)"])


def check_limits(g: Design, policy: dict) -> tuple[dict, bool]:
    n = sum(g.counts().values())
    fails, notes = [], []
    if n > policy["max_blocks"]:
        fails.append({"pos": None, "reason": f"{n} blocks exceed the hard limit of {policy['max_blocks']}"})
    if n > policy["phase_blocks"]:
        notes.append(f"{n} blocks: build in phases of about {policy['phase_blocks']}")
    needs = policy["approval_limit_blocks"] is not None and n > policy["approval_limit_blocks"]
    if needs:
        notes.append(f"{n} blocks is over the no-approval limit of {policy['approval_limit_blocks']}: owner approval required")
    return _result(failures=fails, notes=notes), needs


def validate(g: Design, gd: GameData, brief: dict | None = None, bom: dict | None = None,
             scaffold: Design | None = None, policy: dict | None = None, skip_buildable: bool = False) -> dict:
    policy = {**DEFAULT_POLICY, **(policy or {})}
    G = Grid(g, gd)                                   # the finished building (scaffold removed)
    Gs = Grid(g, gd, scaffold) if scaffold is not None else G
    limits, needs_approval = check_limits(g, policy)
    keep: dict = {}
    checks = {
        "site_fit": check_site(G, brief),
        "walkable": check_walkable(G, G.walk()),
        "enclosed": check_enclosed(G),
        "lit": check_lit(G),
        "functional": check_function(G, brief),
        "stable": check_stable(G),
        "fire_safe": check_fire(G, policy),
        "buildable": {"status": "skipped", "failures": [], "notes": ["not run yet (the scaffold is planned last)"],
                      "approximations": []} if skip_buildable else check_buildable(Gs, Gs.walk(), scaffold, (brief or {}).get("site"), keep),
        "affordable": check_affordable(bom, brief),
        "within_limits": limits,
    }
    checks["stand_audit"] = check_stand_audit(g, keep.get("steps"), scaffold, not skip_buildable)   # O94ag
    return {"design_hash": g.hash(), "validator_version": VALIDATOR_VERSION, "game": gd.version,
            "passed": all(c["status"] != "fail" for c in checks.values()),
            "skipped": [k for k, c in checks.items() if c["status"] == "skipped"],
            "needs_approval": needs_approval, "checks": checks}
