"""Compile a building-language program (lang.py) into a labelled Design (CP-55/56/57).

Only the commands in COMMANDS exist. Every block write goes through Builder.put, which enforces the time,
write-count and coordinate budgets and the collision rule:

    a write into an occupied cell succeeds iff PRIORITY[new role] >= PRIORITY[old role];
    openings (door, window, doorway, stairwell) carve explicitly; a roof never writes into another
    volume's footprint at or below that volume's wall top. Overwrites and refused writes are counted.

A building is one or more rectangular volumes: "main" (foundation + walls) and wings. Each volume has storeys
of equal height; storey k's walls fill y fh+k*h .. fh+(k+1)*h-1 and its floor is the layer y = fh-1+k*h, so the
top wall row of storey k is level with floor k+1. Setbacks shrink the wall ring every N storeys.

Coordinates while compiling: main footprint x 0..w-1 (east), z 0..d-1 (south), y 0 = foundation's first layer.
The finished grid is shifted so its minimum corner is (0,0,0); the shift is recorded in provenance.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .gamedata import GameData, StateError, format_state, parse_state
from .grid import MAX_VOLUME, Design
from .lang import MAX_SOURCE_BYTES, Call, LangError, parse

COMPILER_VERSION = "eve-build/2"
STYLES = Path(__file__).resolve().parents[2] / "styles"
TIME_BUDGET_S = 2.0
MAX_WRITES = 1_000_000
MAX_COORD = 256            # |x|, |z| while compiling
WORLD_HEIGHT = 384         # y -64..319
PRIORITY = {"none": 0, "ground": 0, "foundation": 1, "floor": 2, "wall": 3, "support": 4, "roof": 5, "trim": 6,
            "window": 7, "stair": 7, "door": 8, "furniture": 8, "light": 8}
SIDES = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}   # outward normal (dx, dz)
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
ROOM_TYPES = ("living", "bedroom", "kitchen", "storage", "workshop")
TEMPLATES = {  # furniture per room type, placed along the room's edge farthest from its entrances
    "living": ["crafting_table", "barrel", "lantern"],
    "bedroom": ["bed", "chest", "lantern"],
    "kitchen": ["furnace", "smoker", "crafting_table", "barrel", "lantern"],
    "storage": ["chest", "barrel", "chest", "barrel", "lantern"],
    "workshop": ["crafting_table", "stonecutter", "furnace", "lantern"],
}
FACING_ITEMS = {"chest", "barrel", "furnace", "smoker"}
LIGHT_MARGIN = 2           # the compiler's light estimate wants level > 2 on every floor cell
# Neighbours that panes/fences do NOT connect to. ponytail: name-based approximation of the game's
# "sturdy face" rule, PROVISIONAL until CP-49 compares placed states in a real world.
PARTIAL = ("air", "_stairs", "_slab", "_door", "_trapdoor", "_fence", "_wall", "torch", "lantern", "_sign", "_button",
           "_pressure_plate", "_carpet", "_bed", "chest", "campfire", "flower_pot", "ladder", "rail", "candle",
           "glass_pane", "_pane", "iron_bars", "_gate")
NAME = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
RIGHT_OF = {"south": "west", "west": "north", "north": "east", "east": "south"}   # a player's right, looking that way


def full_sides(state: str) -> set[str]:
    """Sides of a PARTIAL block that are still a full (sturdy) face, which panes/bars/fences do connect to (game rule:
    connect to any sturdy face). CP-49 attempt 3: a closed door facing south made the pane north of it south=true."""
    bid, p = parse_state(state)
    f = p.get("facing")
    if bid.endswith("_door") and f in OPPOSITE:
        if p.get("open") == "true":   # the panel swings to the hinge side
            return {RIGHT_OF[f] if p.get("hinge") == "right" else OPPOSITE[RIGHT_OF[f]]}
        return {OPPOSITE[f]}          # closed: flush with the side Eve stood on
    if bid.endswith("_trapdoor") and p.get("open") == "true" and f in OPPOSITE:
        return {OPPOSITE[f]}          # ponytail: from the game's open-trapdoor shape, not yet checked in a world
    if bid.endswith("_stairs") and p.get("shape") == "straight" and f in OPPOSITE:
        return {f}                    # the tall back
    if bid.endswith("_slab") and p.get("type") == "double":
        return set(SIDES)
    return set()


class CompileError(ValueError):
    def __init__(self, line: int, msg: str):
        super().__init__(f"line {line}: {msg}")
        self.line, self.msg = line, msg


# --- argument binding -------------------------------------------------------------------------------
def _int(lo, hi):
    return ("int", lo, hi)


def _choice(*opts):
    return ("choice", opts)


BLOCK, BOOL, PART, STOREY = ("block",), ("bool",), ("part",), _int(0, 11)
COMMANDS = {  # name -> [(arg, kind, default)]; default REQ = required
    "foundation": [("w", _int(3, 64), "REQ"), ("d", _int(3, 64), "REQ"), ("h", _int(1, 4), 1), ("block", BLOCK, None)],
    "walls": [("h", _int(2, 16), "REQ"), ("frame", BLOCK, None), ("fill", BLOCK, None), ("frame_every", _int(0, 16), None),
              ("storeys", _int(1, 12), 1), ("setback_every", _int(0, 12), 0), ("setback", _int(0, 3), 1), ("part", PART, "main")],
    "floor": [("block", BLOCK, None), ("part", PART, "main")],
    "windows": [("side", _choice(*SIDES), "REQ"), ("count", _int(1, 16), 1), ("w", _int(1, 9), 1), ("h", _int(1, 6), 2),
                ("sill", _int(0, 3), 1), ("block", BLOCK, None), ("storey", STOREY, 0), ("part", PART, "main")],
    "door": [("side", _choice(*SIDES), "REQ"), ("at", ("at",), "center"), ("block", ("family",), None), ("part", PART, "main")],
    "room": [("type", _choice(*ROOM_TYPES), "REQ"), ("furnish", BOOL, True), ("storey", STOREY, 0), ("part", PART, "main")],
    "stairs": [("storey", STOREY, 0), ("side", _choice(*SIDES), "north"), ("at", _int(1, 60), 1), ("block", ("family",), None),
               ("part", PART, "main"), ("landing", BOOL, False)],
    "roof": [("kind", _choice("gable", "flat"), "REQ"), ("ridge", _choice("east_west", "north_south"), None),
             ("overhang", _int(0, 2), 1), ("block", ("family",), None), ("part", PART, "main")],
    "chimney": [("side", _choice(*SIDES), "REQ"), ("block", BLOCK, None), ("part", PART, "main")],
    "porch": [("side", _choice(*SIDES), "REQ"), ("depth", _int(1, 5), 2), ("block", BLOCK, None), ("part", PART, "main"),
              ("roof", _choice("flat", "gable"), "flat"), ("width", _int(3, 15), None)],
    "dormer": [("side", _choice(*SIDES), "REQ"), ("at", ("at",), "center"), ("part", PART, "main")],
    "courtyard": [("side", _choice(*SIDES), "REQ"), ("depth", _int(3, 16), 5), ("h", _int(1, 4), 2), ("block", BLOCK, None),
                  ("gate", BOOL, True), ("part", PART, "main")],
    "wing": [("name", ("str",), "REQ"), ("side", _choice(*SIDES), "REQ"), ("length", _int(3, 64), "REQ"),
             ("depth", _int(3, 64), "REQ"), ("at", ("at",), "center"), ("h", _int(2, 16), None), ("storeys", _int(1, 12), 1),
             ("frame", BLOCK, None), ("fill", BLOCK, None), ("frame_every", _int(0, 16), None)],
}
COMMANDS["detail"] = [("rule", _choice("style", "pillars", "base_band", "gradient", "shutters", "roof_trim"), "REQ"),
                      ("on", BOOL, True), ("rows", _int(1, 3), None)]
DETAIL_ORDER = ("pillars", "base_band", "gradient", "shutters", "roof_trim")
HEADER = [("style", ("str",), "REQ"), ("seed", _int(0, 99_999), 0)]   # seeds are 5-digit literals


def bind(call: Call, spec) -> dict:
    names = [a for a, _, _ in spec]
    if len(call.args) > len(names):
        raise CompileError(call.line, f"{call.name}() takes at most {len(names)} positional arguments")
    vals = dict(zip(names, call.args))
    for k, v in call.kwargs.items():
        if k not in names:
            raise CompileError(call.line, f"{call.name}() has no argument {k!r}")
        if k in vals:
            raise CompileError(call.line, f"{call.name}() got {k!r} twice")
        vals[k] = v
    out = {}
    for name, kind, default in spec:
        if name not in vals:
            if default == "REQ":
                raise CompileError(call.line, f"{call.name}() needs {name}=")
            out[name] = default
            continue
        v = vals[name]
        k = kind[0]
        if k == "int":
            if type(v) is not int or not kind[1] <= v <= kind[2]:
                raise CompileError(call.line, f"{call.name}({name}=) must be an integer {kind[1]}..{kind[2]}, got {v!r}")
        elif k == "choice":
            if v not in kind[1]:
                raise CompileError(call.line, f"{call.name}({name}=) must be one of {', '.join(kind[1])}, got {v!r}")
        elif k == "bool":
            if type(v) is not bool:
                raise CompileError(call.line, f"{call.name}({name}=) must be True or False")
        elif k == "at":
            if v != "center" and type(v) is not int:
                raise CompileError(call.line, f"{call.name}(at=) must be \"center\" or an integer offset")
        elif type(v) is not str:
            raise CompileError(call.line, f"{call.name}({name}=) must be a string")
        elif k in ("part", "name") or name == "name":
            if not NAME.match(v):
                raise CompileError(call.line, f"{call.name}({name}=) must be a short lowercase name")
        out[name] = v
    return out


@dataclass
class Vol:
    name: str
    x0: int
    z0: int
    w: int
    d: int
    fh: int
    h: int | None = None
    base: str | None = None                   # foundation material (doorsteps are made of it)
    storeys: int = 1
    every: int = 0
    setback: int = 0
    frame: str | None = None
    fill: str | None = None
    floor_block: str | None = None
    floor0: bool = False                      # floor() asked for a storey-0 floor over the foundation
    frame_cols: dict = field(default_factory=dict)
    window_cols: dict = field(default_factory=dict)
    doors: list = field(default_factory=list)
    rooms_req: list = field(default_factory=list)
    stairs: list = field(default_factory=list)
    roof_peak: int | None = None
    windows_list: list = field(default_factory=list)   # (storey, side, cols, y0, y1)
    gable_trim: list = field(default_factory=list)     # (x, y, z) of roof stairs on the gable-end overhangs
    roof_info: dict | None = None

    @property
    def top(self):
        return self.fh + self.storeys * self.h - 1

    def inset(self, k):
        return (k // self.every) * self.setback if self.every else 0

    def ring(self, k):
        s = self.inset(k)
        return self.x0 + s, self.z0 + s, self.x0 + self.w - 1 - s, self.z0 + self.d - 1 - s

    def side_len(self, k, side):
        x0, z0, x1, z1 = self.ring(k)
        return x1 - x0 + 1 if side in ("north", "south") else z1 - z0 + 1

    def wall_xz(self, k, side, a):
        x0, z0, x1, z1 = self.ring(k)
        return {"north": (x0 + a, z0), "south": (x0 + a, z1), "west": (x0, z0 + a), "east": (x1, z0 + a)}[side]

    def wall_y(self, k):
        return self.fh + k * self.h, self.fh + (k + 1) * self.h - 1

    def floor_y(self, k):
        return self.fh - 1 + k * self.h

    def in_footprint(self, x, z):
        return self.x0 <= x < self.x0 + self.w and self.z0 <= z < self.z0 + self.d


# --- the builder --------------------------------------------------------------------------------------
class Builder:
    def __init__(self, gd: GameData, style: dict, seed: int, line: int = 0):
        self.gd, self.style, self.seed, self.line = gd, style, seed, line
        self.cells: dict[tuple[int, int, int], tuple[str, str]] = {}
        self.writes = 0
        self.deadline = time.monotonic() + TIME_BUDGET_S
        self.overwrites, self.refused = Counter(), Counter()
        self.vols: dict[str, Vol] = {}
        self.links: list[dict] = []           # doorways between volumes
        self.details: dict[str, dict] = {}    # detailing rules switched on, with parameters
        self.rooms: list[dict] = []
        self.doorsteps: list[tuple] = []      # O89l: steps the compiler added in front of doors

    def err(self, msg):
        raise CompileError(self.line, msg)

    # cells
    def put(self, x, y, z, state, role):
        self.writes += 1
        if self.writes > MAX_WRITES:
            self.err(f"more than {MAX_WRITES} block writes")
        if self.writes % 1024 == 0 and time.monotonic() > self.deadline:
            self.err(f"compile time budget of {TIME_BUDGET_S} s exceeded")
        if abs(x) > MAX_COORD or abs(z) > MAX_COORD or not 0 <= y < WORLD_HEIGHT:
            self.err(f"block at {(x, y, z)} is outside the allowed volume")
        old = self.cells.get((x, y, z))
        if old:
            if PRIORITY[role] < PRIORITY[old[1]]:
                self.refused[f"{role}->{old[1]}"] += 1
                return
            self.overwrites[f"{old[1]}->{role}"] += 1
        self.cells[(x, y, z)] = (state, role)

    def carve(self, x, y, z):
        if self.cells.pop((x, y, z), None):
            self.overwrites["carved"] += 1

    def get(self, x, y, z):
        return self.cells.get((x, y, z), ("minecraft:air", "none"))[0]

    # vocabulary helpers
    def block(self, name):
        if name is None:
            return None
        if not all(c.isalnum() or c in "_:" for c in name):
            self.err(f"bad block name {name!r}")
        bid = name if ":" in name else "minecraft:" + name
        if bid not in self.gd.blocks:
            self.err(f"unknown block {name!r} in game {self.gd.version}")
        return bid

    def family(self, name, suffix):
        """'dark_oak' + '_stairs' -> minecraft:dark_oak_stairs (also accepts the full block name)."""
        if not all(c.isalnum() or c in "_:" for c in name):
            self.err(f"bad block name {name!r}")
        base = name.replace("minecraft:", "")
        for sfx in ("_stairs", "_slab", "_door", "_planks", "_fence"):
            if base.endswith(sfx):
                base = base[: -len(sfx)]
        for cand in (base + suffix, base.rstrip("s") + suffix):
            if "minecraft:" + cand in self.gd.blocks:
                return "minecraft:" + cand
        self.err(f"no {suffix.strip('_')} block for material {name!r}")

    def full_of(self, fam):
        base = fam.replace("minecraft:", "")
        for cand in (base + "_planks", base + "s", base):
            if "minecraft:" + cand in self.gd.blocks:
                return "minecraft:" + cand
        self.err(f"no full block for material {fam!r}")

    def pal(self, role):
        v = self.style["palette"][role]
        return v[0][0] if isinstance(v, list) and isinstance(v[0], list) else v

    def state(self, bid, **props):
        try:
            return self.gd.canonical(format_state(bid, {k: str(v).lower() for k, v in props.items()}))
        except StateError as e:
            self.err(str(e))

    def vol(self, part, walls=True) -> Vol:
        v = self.vols.get(part)
        if v is None:
            self.err("foundation() must come first" if part == "main" else f"no part named {part!r}")
        if walls and v.h is None:
            self.err("walls() must come first")
        return v

    def storey(self, v, k):
        if not 0 <= k < v.storeys:
            self.err(f"storey {k} does not exist (part {v.name!r} has {v.storeys})")
        return k

    # --- commands --------------------------------------------------------------------------------------
    def foundation(self, w, d, h, block):
        if "main" in self.vols:
            self.err("foundation() given twice")
        self.vols["main"] = Vol("main", 0, 0, w, d, h)
        self._foundation(self.vols["main"], self.block(block) or self.pal("base"))

    def _foundation(self, v, b):
        v.base = b
        for y in range(v.fh):
            for z in range(v.z0, v.z0 + v.d):
                for x in range(v.x0, v.x0 + v.w):
                    self.put(x, y, z, b, "foundation")

    def walls(self, h, frame, fill, frame_every, storeys, setback_every, setback, part):
        v = self.vol(part, walls=False)
        if v.h is not None:
            self.err("walls() given twice")
        self._walls(v, h, frame, fill, frame_every, storeys, setback_every, setback)

    def _walls(self, v, h, frame, fill, frame_every, storeys, every, setback):
        v.h, v.storeys, v.every, v.setback = h, storeys, every, setback if every else 0
        if v.top >= WORLD_HEIGHT:
            self.err(f"{storeys} storeys of {h} exceed the world height")
        for k in range(storeys):
            x0, z0, x1, z1 = v.ring(k)
            if x1 - x0 < 4 or z1 - z0 < 4:
                self.err(f"setbacks leave storey {k} of {v.name!r} narrower than 5 blocks")
        fb = self.block(frame) or self.pal("frame")
        v.frame = self.state(fb, **({"axis": "y"} if "axis" in self.gd.blocks[fb]["props"] else {}))
        v.fill = self.block(fill) or self.pal("wall")
        every_f = self.style.get("rules", {}).get("frame_every", 4) if frame_every is None else frame_every
        for k in range(storeys):
            lo, hi = v.wall_y(k)
            for side in SIDES:
                L = v.side_len(k, side)
                seg = max(1, math.ceil((L - 1) / every_f)) if every_f else 1
                cols = v.frame_cols[(k, side)] = {round(i * (L - 1) / seg) for i in range(seg + 1)}
                v.window_cols[(k, side)] = set()
                for a in range(L):
                    x, z = v.wall_xz(k, side, a)
                    for y in range(lo, hi + 1):
                        self.put(x, y, z, v.frame if a in cols else v.fill, "support" if a in cols else "wall")

    def floor(self, block, part):
        v = self.vol(part, walls=False)
        v.floor_block, v.floor0 = self.block(block) or self.pal("floor"), True

    def door(self, side, at, block, part):
        v = self.vol(part)
        L = v.side_len(0, side)
        a = (L - 1) // 2 if at == "center" else at
        if not 1 <= a <= L - 2:
            self.err(f"door at {a} is not inside the {side} wall (1..{L - 2})")
        if v.h < 3:
            self.err("walls too low for a 2-tall door with a lintel")
        b = self.family(block or self.style["palette"]["door"], "_door")
        x, z = v.wall_xz(0, side, a)
        for half, y in (("lower", v.fh), ("upper", v.fh + 1)):
            self.carve(x, y, z)
            self.put(x, y, z, self.state(b, facing=OPPOSITE[side], half=half, hinge="left"), "door")
        dx, dz = SIDES[side]
        v.doors.append({"side": side, "a": a, "x": x, "z": z, "y": v.fh, "inside": (x - dx, z - dz)})

    def windows(self, side, count, w, h, sill, block, storey, part):
        v = self.vol(part)
        k = self.storey(v, storey)
        L = v.side_len(k, side)
        lo, hi = v.wall_y(k)
        y0, y1 = lo + sill, lo + sill + h - 1
        if y1 >= hi:
            self.err(f"window rows {y0}..{y1} leave no lintel under the wall top ({hi})")
        blocked = v.frame_cols[(k, side)] | ({d["a"] for d in v.doors if d["side"] == side} if k == 0 else set())
        free = [a for a in range(1, L - 1) if a not in blocked and a not in v.window_cols[(k, side)]]
        pane = self.block(block) or self.pal("window")
        for i in range(count):
            target = (i + 0.5) * L / count - 0.5
            starts = [s for s in free if all(s + j in free for j in range(w))]
            if not starts:
                self.err(f"no room for window {i + 1} of {count} on the {side} wall: " + self._wall_full(v, k, side, w, i))
            s = min(starts, key=lambda s: (abs(s + (w - 1) / 2 - target), s))
            v.windows_list.append((k, side, list(range(s, s + w)), y0, y1))
            for j in range(w):
                a = s + j
                free.remove(a)
                v.window_cols[(k, side)].add(a)
                x, z = v.wall_xz(k, side, a)
                for y in range(y0, y1 + 1):
                    self.carve(x, y, z)
                    self.put(x, y, z, pane, "window")

    def _window_room(self, v, k, side, w) -> int:
        """How many windows w wide still fit on this wall (its frame posts, its door, the windows already on it)."""
        L = v.side_len(k, side)
        taken = v.frame_cols[(k, side)] | v.window_cols[(k, side)] | (
            {d["a"] for d in v.doors if d["side"] == side} if k == 0 else set())
        runs, n = [], 0
        for a in range(1, L - 1):
            n = 0 if a in taken else n + 1
            if n and (a + 1 == L - 1 or a + 1 in taken):
                runs.append(n)
        return sum(r // w for r in runs)

    def _wall_full(self, v, k, side, w, placed) -> str:
        """O87 (45L attempt 7, h08: 'no room for window 3 of 3 on the north wall', the bare message): what takes the
        wall, the biggest first, and the edits that fit, in the program's own commands."""
        L = v.side_len(k, side)
        door = {d["a"] for d in v.doors if d["side"] == side} if k == 0 else set()
        by = sorted(((n, w_) for w_, n in (("frame posts", len(v.frame_cols[(k, side)] - {0, L - 1})),
                                         ("windows", len(v.window_cols[(k, side)])), ("the door", len(door))) if n),
                    reverse=True)
        fix = []
        narrow = self._window_room(v, k, side, 1)
        if w > 1 and narrow:
            fix.append(f"w=1 (room for {narrow} more of those here)")
        fix.append(f"count={placed}" if placed else "drop this windows() line")
        others = [(s, self._window_room(v, k, s, w)) for s in ("north", "south", "east", "west") if s != side]
        fix += [f"windows(side={s}, storey={k}) for the rest (room for {n})" for s, n in others if n]
        if v.frame_cols[(k, side)] - {0, L - 1}:
            fix.append("a larger frame_every (fewer posts)")
        return (f"a window {w} wide needs {w} free blocks side by side, and the wall's {L - 2} inner blocks are taken ("
                + ", ".join(f"{what} {n}" for n, what in by) + "). Fix: " + "; or ".join(fix))

    def room(self, type, furnish, storey, part):
        v = self.vol(part)
        v.rooms_req.append((type, furnish, self.storey(v, storey), self.line))

    def stairs(self, storey, side, at, block, part, landing):
        v = self.vol(part)
        k = self.storey(v, storey)
        if k + 1 >= v.storeys:
            self.err(f"stairs from storey {k} need a storey above it")
        if v.h < 3:
            self.err("storeys too low for stairs")
        g = self._stair_geom(v, k, side, at, landing)
        if isinstance(g, str):
            if g == "stairs would block the door":   # 45L h08 (attempts 3-8): the run moved onto the door's wall
                opts = self._stair_options(v, k, landing, {c for s in v.stairs if abs(s["storey"] - k) == 1
                                                           for c in s["cells"]})
                g += f" (these fit, clear of the doors and the stairs given so far: {' or '.join(opts)})" if opts else ""
            self.err(g)
        steps, cells, approach, arrival = g
        fam = block or self.pal("floor")
        v.stairs.append({"storey": k, "steps": steps, "cells": cells, "approach": approach, "arrival": arrival,
                         "stair": self.family(fam, "_stairs"), "fill": None, "line": self.line, "landing": landing})

    def _stair_geom(self, v, k, side, at, landing):
        """The run of storey k along the inside of `side` of the upper ring (it lies inside the lower ring too): h steps,
        the last one in the upper floor layer so its top is level with the upper floor (no full-block rise). Returns
        (steps, cells, approach, arrival), or why it does not fit."""
        x0, z0, x1, z1 = v.ring(k + 1)
        run = v.h
        L = (x1 - x0 + 1) if side in ("north", "south") else (z1 - z0 + 1)
        if at < 1 or at + (run if not landing else run // 2) > L - 2:
            return f"stairs at {at} with {run} steps + landing do not fit inside the {side} wall ({L - 2} free)"
        ux, uz = (1, 0) if side in ("north", "south") else (0, 1)  # ascend toward +x or +z
        row = {"north": (None, z0 + 1), "south": (None, z1 - 1), "west": (x0 + 1, None), "east": (x1 - 1, None)}[side]
        start = (x0 + at, row[1]) if ux else (row[0], z0 + at)
        up = "east" if ux else "south"
        if not landing:
            steps = [(start[0] + ux * i, start[1] + uz * i, i, up) for i in range(run)]
        else:                       # L-turn: n1 steps, a level landing block, then the rest turning into the room
            turn = OPPOSITE[side]
            tx, tz = SIDES[turn]
            n1 = run // 2
            steps = [(start[0] + ux * i, start[1] + uz * i, i, up) for i in range(n1)]
            lx, lz = start[0] + ux * n1, start[1] + uz * n1
            steps.append((lx, lz, n1 - 1, None))
            steps += [(lx + tx * (j + 1), lz + tz * (j + 1), n1 + j, turn) for j in range(run - n1)]
        last = steps[-1]
        fx, fz = SIDES[last[3]]
        arrival = (last[0] + fx, last[1] + fz)
        approach = (start[0] - ux, start[1] - uz)
        cells = [(x, z) for x, z, _, _ in steps] + [arrival]
        rx0, rz0, rx1, rz1 = v.ring(k)
        if not (rx0 < approach[0] < rx1 and rz0 < approach[1] < rz1):
            return "no floor in front of the first step (use a larger at=)"
        if not all(x0 < x < x1 and z0 < z < z1 for x, z in cells):
            return f"stairs at {at} with {run} steps + landing do not fit inside the {side} wall"
        if k == 0 and any(c == d["inside"] for c in cells[:-1] for d in v.doors):   # doors are on storey 0 only:
            return "stairs would block the door"         # a run above it is a storey up (review; 45L h08 x6)
        return steps, cells, approach, arrival

    def _stair_options(self, v, k, landing, taken) -> list[str]:
        """Where storey k's run fits clear of the doors and of `taken` (other runs' cells): the first at= per side."""
        out = []
        for side in SIDES:
            for at in range(1, max(v.w, v.d)):
                g = self._stair_geom(v, k, side, at, landing)
                if not isinstance(g, str) and not set(g[1]) & taken:
                    out.append(f"side=\"{side}\" at={at}")
                    break
        return out

    def roof(self, kind, ridge, overhang, block, part):
        v = self.vol(part)
        if v.roof_peak is not None:
            self.err("roof() given twice")
        fam = block or self.style["palette"]["roof"]
        o, top = overhang, v.top
        rx0, rz0, rx1, rz1 = v.ring(v.storeys - 1)
        W, D = rx1 - rx0 + 1, rz1 - rz0 + 1

        def put_roof(x, y, z, state, role="roof"):
            # never into another volume's walls/interior (at or below its wall top), unless we own the cell and are taller
            if not any(ov is not v and ov.h is not None and ov.in_footprint(x, z) and y <= ov.top
                       and (not v.in_footprint(x, z) or ov.top > v.top) for ov in self.vols.values()):
                self.put(x, y, z, state, role)

        if kind == "flat":
            slab = self.state(self.family(fam, "_slab"), type="bottom")
            for z in range(rz0 - o, rz1 + 1 + o):
                for x in range(rx0 - o, rx1 + 1 + o):
                    put_roof(x, top + 1, z, slab)
            v.roof_peak = top + 1
            return
        ridge = ridge or ("east_west" if W >= D else "north_south")
        stairs, full = self.family(fam, "_stairs"), self.full_of(fam)
        v.roof_info = {"kind": "gable", "ridge": ridge, "o": o, "fam": fam, "W": W, "D": D, "rx0": rx0, "rz0": rz0}
        # Work in (u = along the ridge, v = across it) and map back to (x, z).
        U, V = (W, D) if ridge == "east_west" else (D, W)
        lo_dir, hi_dir = ("south", "north") if ridge == "east_west" else ("east", "west")  # stairs ascend toward the ridge
        xz = (lambda u, w: (rx0 + u, rz0 + w)) if ridge == "east_west" else (lambda u, w: (rx0 + w, rz0 + u))
        k, peak = 0, top
        while True:
            lo, hi = -o + k, V - 1 + o - k
            if lo > hi:
                break
            y = top + 1 - o + k
            for u in range(-o, U + o):
                if lo < hi:
                    put_roof(*_xyz(xz(u, lo), y), self.state(stairs, facing=lo_dir, half="bottom"))
                    put_roof(*_xyz(xz(u, hi), y), self.state(stairs, facing=hi_dir, half="bottom"))
                    if u in (-o, U + o - 1):
                        v.gable_trim += [_xyz(xz(u, lo), y), _xyz(xz(u, hi), y)]
                else:
                    put_roof(*_xyz(xz(u, lo), y), full)
            if y > top:  # gable-end walls under this roof row
                for w in range(max(lo + 1, 0), min(hi - 1, V - 1) + 1):
                    for u in (0, U - 1):
                        put_roof(*_xyz(xz(u, w), y), v.fill, "wall")
            peak = y
            k += 1
        v.roof_peak = peak

    def chimney(self, side, block, part):
        v = self.vol(part)
        if v.roof_peak is None:
            self.err("roof() must come first: put the roof(...) line before chimney(...)")
        L = v.side_len(0, side)
        blocked = v.window_cols[(0, side)] | {d["a"] for d in v.doors if d["side"] == side}
        a = min((a for a in range(1, L - 1) if a not in blocked), key=lambda a: (abs(a - (L - 1) / 2), a), default=None)
        if a is None:
            self.err(f"no room for a chimney on the {side} wall")
        x, z = v.wall_xz(0, side, a)
        dx, dz = SIDES[side]
        b = self.block(block) or self.pal("chimney")
        for y in range(0, v.roof_peak + 2):
            self.put(x + dx, y, z + dz, b, "trim")

    def porch(self, side, depth, block, part, roof, width):
        v = self.vol(part)
        lo, hi = v.wall_y(0)
        if hi - v.fh < 3:
            self.err("walls too low for a porch roof with 2 blocks of headroom")
        L = v.side_len(0, side)
        dx, dz = SIDES[side]
        deck = self.block(block) or self.pal("floor")
        fence = self.family(self.style["palette"].get("fence", "oak"), "_fence")
        fam = self.style["palette"]["roof"]
        slab = self.state(self.family(fam, "_slab"), type="bottom")
        if width is None and roof == "flat":
            a0, a1 = 0, L - 1
        else:                                   # a narrower porch, centred on the door on this side (else the wall)
            w = min(width or 5, L)
            door = next((d["a"] for d in v.doors if d["side"] == side), (L - 1) // 2)
            a0 = max(0, min(door - w // 2, L - w))
            a1 = a0 + w - 1
        for a in range(a0, a1 + 1):
            x, z = v.wall_xz(0, side, a)
            for j in range(1, depth + 1):
                px, pz = x + dx * j, z + dz * j
                self.put(px, v.fh - 1, pz, deck, "floor")
                if roof == "flat":
                    self.put(px, hi - 1, pz, slab, "roof")
                if j == depth and a in (a0, a1):
                    for y in range(v.fh, hi - 1):
                        self.put(px, y, pz, self.state(fence), "support")
        if roof == "gable":                     # ridge runs out from the wall; rows step up toward the middle
            stairs, full = self.family(fam, "_stairs"), self.full_of(fam)
            along = ("east", "west") if side in ("north", "south") else ("south", "north")
            k = 0
            while a0 + k <= a1 - k:
                y = hi - 1 + k
                for j in range(1, depth + 1):
                    for a, facing in ((a0 + k, along[0]), (a1 - k, along[1])):
                        x, z = v.wall_xz(0, side, a)
                        st = full if a0 + k == a1 - k else self.state(stairs, facing=facing, half="bottom")
                        self.put(x + dx * j, y, z + dz * j, st, "roof")
                k += 1

    def dormer(self, side, at, part):
        """A 3-wide gabled dormer with a window, set into an eave-side slope of this part's gable roof."""
        v = self.vol(part)
        r = v.roof_info
        if not r:
            self.err("dormer() needs a gable roof() on this part first")
        eave_sides = ("north", "south") if r["ridge"] == "east_west" else ("west", "east")
        if side not in eave_sides:
            self.err(f"a dormer sits on a sloping side of the roof: {' or '.join(eave_sides)}")
        U, V = (r["W"], r["D"]) if r["ridge"] == "east_west" else (r["D"], r["W"])
        if V < 7:
            self.err("the roof is too small for a dormer (needs 7 blocks across)")
        uc = (U - 1) // 2 if at == "center" else at
        if not 2 <= uc <= U - 3:
            self.err(f"dormer at {uc} is not inside the roof (2..{U - 3})")
        top = v.top
        lo_side = side in ("north", "west")
        rx0, rz0 = r["rx0"], r["rz0"]
        xz = (lambda u, w: (rx0 + u, rz0 + w)) if r["ridge"] == "east_west" else (lambda u, w: (rx0 + w, rz0 + u))
        at_d = lambda u, d, y: _xyz(xz(u, d if lo_side else V - 1 - d), y)       # d = distance in from the eave wall
        u_dirs = ("east", "west") if r["ridge"] == "east_west" else ("south", "north")
        stairs, full = self.family(r["fam"], "_stairs"), self.full_of(r["fam"])
        pane = self.pal("window")
        for u in (uc - 1, uc + 1):                                                # cheeks
            for d, ys in ((0, (top + 1, top + 2)), (1, (top + 2,))):
                for y in ys:
                    self.carve(*at_d(u, d, y))
                    self.put(*at_d(u, d, y), v.fill, "wall")
        self.carve(*at_d(uc, 0, top + 1))
        self.put(*at_d(uc, 0, top + 1), pane, "window")
        self.carve(*at_d(uc, 0, top + 2))
        self.put(*at_d(uc, 0, top + 2), v.fill, "wall")
        self.carve(*at_d(uc, 1, top + 2))                                          # the dormer's inside
        for d in (-1, 0, 1):                                                      # its own small gable roof
            self.carve(*at_d(uc - 1, d, top + 3))
            self.carve(*at_d(uc + 1, d, top + 3))
            self.put(*at_d(uc - 1, d, top + 3), self.state(stairs, facing=u_dirs[0], half="bottom"), "roof")
            self.put(*at_d(uc + 1, d, top + 3), self.state(stairs, facing=u_dirs[1], half="bottom"), "roof")
            self.put(*at_d(uc, d, top + 3), full, "roof")

    def courtyard(self, side, depth, h, block, gate, part):
        """A low wall enclosing ground along one side of the building, with a fence-gate entrance opposite it."""
        v = self.vol(part)
        L = v.side_len(0, side)
        dx, dz = SIDES[side]
        b = self.block(block) or self.pal("base")
        gx = None
        for a in range(L):
            x, z = v.wall_xz(0, side, a)
            for j in range(1, depth + 1):
                if a in (0, L - 1) or j == depth:
                    for y in range(h):
                        self.put(x + dx * j, y, z + dz * j, b, "wall")
            if a == (L - 1) // 2:
                gx = (x + dx * depth, z + dz * depth)
        for y in range(h):
            self.carve(gx[0], y, gx[1])
        if gate:
            fg = self.family(self.style["palette"].get("fence", "oak"), "_fence_gate")
            self.put(gx[0], 0, gx[1], self.state(fg, facing=side), "door")

    def wing(self, name, side, length, depth, at, h, storeys, frame, fill, frame_every):
        m = self.vol("main")
        if name in self.vols:
            self.err(f"part {name!r} exists already")
        if not NAME.match(name):
            self.err("wing name must be a short lowercase name")
        L = m.side_len(0, side)
        off = (L - length) // 2 if at == "center" else at
        if off + length - 1 < 2 or off > L - 3:
            self.err(f"wing at {off} with length {length} shares fewer than 3 blocks of the {side} wall")
        if side in ("north", "south"):
            z0 = m.z0 - depth + 1 if side == "north" else m.z0 + m.d - 1
            v = Vol(name, m.x0 + off, z0, length, depth, m.fh)
        else:
            x0 = m.x0 - depth + 1 if side == "west" else m.x0 + m.w - 1
            v = Vol(name, x0, m.z0 + off, depth, length, m.fh)
        self.vols[name] = v
        self._foundation(v, self.pal("base"))
        self._walls(v, h or m.h, frame, fill, frame_every, storeys, 0, 0)
        # doorway through the shared wall, centred on the shared span, at ground storey
        lo_a, hi_a = max(off, 1), min(off + length - 1, L - 2)
        a = (lo_a + hi_a) // 2
        for blocked in sorted({d["a"] for d in m.doors if d["side"] == side} | m.frame_cols[(0, side)]):
            if a == blocked and lo_a < hi_a:
                a = a + 1 if a + 1 <= hi_a else a - 1
        x, z = m.wall_xz(0, side, a)
        dx, dz = SIDES[side]
        for y in (m.fh, m.fh + 1):
            self.carve(x, y, z)
        self.links.append({"parts": ("main", name), "x": x, "z": z, "y": m.fh,
                           "inside": {"main": (x - dx, z - dz), name: (x + dx, z + dz)}})

    def detail(self, rule, on, rows):
        rules = self.style.get("detailing", {}).get("default", []) if rule == "style" else [rule]
        for r in rules:
            if on:
                self.details[r] = {"rows": rows or self.style.get("detailing", {}).get("base_band_rows", 1)}
            else:
                self.details.pop(r, None)

    def _detailing(self):
        for r in DETAIL_ORDER:
            if r in self.details:
                getattr(self, "_d_" + r)(**({"rows": self.details[r]["rows"]} if r == "base_band" else {}))

    def _axis_y_props(self, bid):
        return {"axis": "y"} if "axis" in self.gd.blocks[bid]["props"] else {}

    def _d_pillars(self):
        """Depth: frame columns stand 1 block proud of the wall (never into another volume or an occupied cell)."""
        for v in self.vols.values():
            if v.h is None:
                continue
            for k in range(v.storeys):
                lo, hi = v.wall_y(k)
                for side, (dx, dz) in SIDES.items():
                    for a in sorted(v.frame_cols[(k, side)]):
                        x, z = v.wall_xz(k, side, a)
                        px, pz = x + dx, z + dz
                        if any(o is not v and o.in_footprint(px, pz) for o in self.vols.values()):
                            continue
                        if any(self.cells.get((x, y, z), ('', 'none'))[1] in ('door', 'window') or (x, y, z) not in self.cells
                               for y in range(lo, hi + 1)):
                            continue                                  # never in front of a door, window or opening
                        if k == 0:
                            lo = 0                                # stand on the ground: an upright log needs a face below
                        elif (px, lo - 1, pz) not in self.cells:
                            continue                              # upper storeys only continue a pillar below
                        for y in range(lo, hi + 1):
                            if (px, y, pz) not in self.cells:
                                self.put(px, y, pz, v.frame, "support")

    def _d_base_band(self, rows):
        """A heavier base material on the lowest wall rows of the ground storey, pillars included."""
        base = self.pal("base")
        for v in self.vols.values():
            if v.h is None:
                continue
            lo = v.wall_y(0)[0]
            x0, z0, x1, z1 = v.ring(0)
            for (x, y, z), (st, role) in list(self.cells.items()):
                on_ring = x0 - 1 <= x <= x1 + 1 and z0 - 1 <= z <= z1 + 1 and not (x0 < x < x1 and z0 < z < z1)
                if lo <= y < lo + rows and role in ("wall", "support") and on_ring:
                    self.cells[(x, y, z)] = (self.state(base, **self._axis_y_props(base)), role)

    def _d_gradient(self):
        """Palette gradients: cells of a role's primary block become a weighted mix from the style card, with
        weathered blocks likelier lower down. Each cell's pick depends only on (seed, x, y, z): a seed reproduces it."""
        import random
        top = max((y for _, y, _ in self.cells), default=1) or 1
        for key, roles in (("base", ("foundation", "wall", "support")), ("wall", ("wall",))):
            pal = self.style["palette"].get(key)
            if not (isinstance(pal, list) and isinstance(pal[0], list) and len(pal) > 1):
                continue
            primary = pal[0][0]
            for (x, y, z), (st, role) in list(self.cells.items()):
                if parse_state(st)[0] != primary or role not in roles:
                    continue
                low = 1 - y / top
                ws = [w * (1 + low) if ("mossy" in b or "cracked" in b) else w for b, w in pal]
                u = random.Random(f"{self.seed}:{x}:{y}:{z}").random() * sum(ws)
                pick = pal[-1][0]
                for (b, _), w in zip(pal, ws):
                    u -= w
                    if u < 0:
                        pick = b
                        break
                self.cells[(x, y, z)] = (self.state(pick, **self._axis_y_props(pick)), role)

    def _d_shutters(self):
        """Window frames: open trapdoors as shutters either side of every window, on the outside face."""
        trap = self.family(self.style["palette"].get("door", "oak"), "_trapdoor")
        for v in self.vols.values():
            for k, side, cols, y0, y1 in v.windows_list:
                dx, dz = SIDES[side]
                for a in (min(cols) - 1, max(cols) + 1):
                    x, z = v.wall_xz(k, side, a)
                    if self.cells.get((x, y0, z), ("", ""))[1] not in ("wall", "support"):
                        continue
                    for y in range(y0, y1 + 1):
                        if (x + dx, y, z + dz) not in self.cells:
                            self.put(x + dx, y, z + dz, self.state(trap, facing=side, open="true", half="bottom"), "trim")

    def _d_roof_trim(self):
        """Gable-end overhang stairs in the trim material (style card palette.trim)."""
        trim = self.family(self.style["palette"].get("trim", self.style["palette"].get("door", "oak")), "_stairs")
        for v in self.vols.values():
            for c in v.gable_trim:
                st, role = self.cells.get(c, (None, None))
                if role == "roof" and "_stairs" in st:
                    props = parse_state(st)[1]
                    self.cells[c] = (self.state(trim, facing=props["facing"], half=props["half"]), role)

    # --- finalize: floors, stairs, rooms, partitions, furniture, connections ---------------------------
    def finalize(self, room=None):
        if "main" not in self.vols:
            self.err("program has no foundation()")
        for v in self.vols.values():
            if v.h is None:
                continue
            fb = v.floor_block or self.pal("floor")
            for k in range(v.storeys):
                if k == 0 and not v.floor0:
                    continue
                y = v.floor_y(k)
                px0, pz0, px1, pz1 = v.ring(k - 1) if k else v.ring(0)
                x0, z0, x1, z1 = v.ring(k)
                for z in range(pz0 + 1, pz1):
                    for x in range(px0 + 1, px1):
                        under = x0 <= x <= x1 and z0 <= z <= z1             # inside or under the upper walls
                        self.put(x, y, z, fb, "floor" if under else "roof")  # outside the upper ring = terrace
            for s in v.stairs:
                self.line = s["line"]
                k = s["storey"]
                base, upper = v.floor_y(k), v.floor_y(k + 1)
                fill = v.floor_block or self.pal("floor")
                for x, z, i, facing in s["steps"]:
                    for y in range(base + 1, base + 1 + i):              # solid support under each step
                        self.put(x, y, z, fill, "support")
                    if facing is None:                                    # landing: a level full block
                        self.put(x, base + 1 + i, z, fill, "stair")
                    else:
                        self.put(x, base + 1 + i, z, self.state(s["stair"], facing=facing, half="bottom"), "stair")
                    if base + 1 + i < upper <= base + 3 + i:              # keep 2 blocks of headroom above each step
                        self.carve(x, upper, z)
            for s in v.stairs:  # a later run must not sit in an earlier run's headroom
                base = v.floor_y(s["storey"])
                for x, z, i, _ in s["steps"]:
                    for y in (base + 2 + i, base + 3 + i):
                        if self.get(x, y, z) != "minecraft:air":
                            self.line = s["line"]
                            nxt = next((t for t in v.stairs if t["storey"] == s["storey"] + 1), None)
                            opts = self._stair_options(v, nxt["storey"], nxt["landing"], {
                                c for t in v.stairs if t is not nxt for c in t["cells"]}) if nxt else []
                            self.err(f"something at {(x, y, z)} blocks the headroom of these stairs (put the next run on "
                                     f"another side{': ' + ' or '.join(opts) + ' for storey ' + str(nxt['storey']) if opts else ''})")
        for v in self.vols.values():
            for k in sorted({r[2] for r in v.rooms_req}):
                self._rooms(v, k, [r for r in v.rooms_req if r[2] == k])
        self._detailing()
        self._doorsteps(room)
        self._connect()

    def _doorsteps(self, room):
        """O89l (builder, lane C hut5_tall a12/a13): Eve's Baritone enters or leaves a door cell only on a level move. A
        door side with no floor at the door's own height and a clear doorway gets one block of its volume's foundation
        material there, if the design still fits `room`: the plot's (x, z) extent from the origin, None when there is
        no plot. Otherwise the validator reports the door. ponytail: a 1-high foundation only; on a taller one a step
        at floor height could not be climbed from the ground (review), so the validator names the door instead."""
        xs, zs = [c[0] for c in self.cells], [c[2] for c in self.cells]
        box = (min(xs), max(xs), min(zs), max(zs))
        for v in self.vols.values():
            for d in v.doors:
                dx, dz, y = *SIDES[d["side"]], d["y"]
                if y != 1:
                    continue
                for sx, sz in ((d["x"] + dx, d["z"] + dz), (d["x"] - dx, d["z"] - dz)):
                    if any((sx, yy, sz) in self.cells for yy in (y - 1, y, y + 1)):
                        continue                  # a floor there already, or the doorway is blocked
                    b = (min(box[0], sx), max(box[1], sx), min(box[2], sz), max(box[3], sz))
                    if room and b != box and (b[1] - b[0] > room[0] or b[3] - b[2] > room[1]):
                        continue                  # it would take the design past the plot
                    self.put(sx, 0, sz, v.base, "foundation")
                    box = b
                    self.doorsteps.append((sx, y - 1, sz))

    def _openings(self, v, k):
        """Interior cells (x, z) where people enter storey k of volume v."""
        out = [d["inside"] for d in v.doors] if k == 0 else []
        if k == 0:
            out += [ln["inside"][v.name] for ln in self.links if v.name in ln["parts"]]
        for s in v.stairs:
            if s["storey"] == k:
                out.append(s["approach"])                        # in front of the first step
            if s["storey"] + 1 == k:
                out.append(s["arrival"])                         # where the stairs arrive upstairs
        return out

    def _crowded(self, v, k, n, lo, hi, along_x, cross) -> str:
        """O87 (45L h02 died on the bare message in attempts 1, 3, 5 and 7): what takes the interior's blocks, the biggest
        first, and the change in the program's own commands. In every saved case no block was free at all."""
        c = (lambda xz: xz[0]) if along_x else (lambda xz: xz[1])
        span, short = set(range(lo, hi + 1)), ("west", "east") if along_x else ("north", "south")
        by = {f"windows on the {s} wall": ({c(v.wall_xz(k, s, a)) for a in v.window_cols[(k, s)]} & span,
                                           f"fewer or narrower windows(side={s}, storey={k})") for s in cross}
        if k == 0:
            by["the door"] = ({d["x"] if along_x else d["z"] for d in v.doors if d["side"] in cross} & span,
                              f"the door on the {short[0]} or {short[1]} wall")
        by["the stairs"] = ({c(xz) + d for xz in self._stair_cells(v, k) for d in (-1, 0, 1)} & span,
                            f"stairs on the {short[0]} or {short[1]} side, or at=1 at one end")
        by = sorted(((w, cs, f) for w, (cs, f) in by.items() if cs), key=lambda t: -len(t[1]))
        taken = set().union(*(cs for _, cs, _ in by))
        return (f"{n} rooms on storey {k} of '{v.name}' share an interior {len(span)} blocks long; a partition needs a "
                f"block with no window, door or stairs beside it and 2 blocks of room each side, and {len(taken)} of "
                f"{len(span)} are taken (" + ", ".join(f"{w} {len(cs)}" for w, cs, _ in by) + "). Fix: " +
                "; or ".join([f for _, _, f in by] + [f"fewer rooms with storey={k}"]))

    def _stair_cells(self, v, k):
        return {c for s in v.stairs if s["storey"] in (k, k - 1) for c in s["cells"]}

    def _rooms(self, v, k, reqs):
        x0, z0, x1, z1 = v.ring(k)
        along_x = (x1 - x0) >= (z1 - z0)
        lo, hi = (x0 + 1, x1 - 1) if along_x else (z0 + 1, z1 - 1)
        n = len(reqs)
        usable = hi - lo + 1 - (n - 1)
        if usable < 2 * n:
            self.line = reqs[-1][3]
            self.err(f"{n} rooms do not fit in an interior {hi - lo + 1} wide")
        sizes = [usable // n + (1 if i < usable % n else 0) for i in range(n)]
        cross = ("north", "south") if along_x else ("west", "east")
        bad = {d["x"] if along_x else d["z"] for d in (v.doors if k == 0 else []) if d["side"] in cross}
        bad |= {(v.wall_xz(k, s, a)[0] if along_x else v.wall_xz(k, s, a)[1]) for s in cross for a in v.window_cols[(k, s)]}
        if k == 0:
            bad |= {ln["x"] if along_x else ln["z"] for ln in self.links if v.name in ln["parts"]}
        for (cx, cz) in self._stair_cells(v, k):
            c = cx if along_x else cz
            bad |= {c - 1, c, c + 1}
        parts, cur = [], lo
        for s in sizes[:-1]:
            p = cur + s
            for shift in (0, 1, -1, 2, -2, 3, -3):  # nudge partitions off doors, windows and stairs
                q = p + shift
                if q not in bad and q - (parts[-1] if parts else lo - 1) > 2 and hi - q >= 2:
                    p = q
                    break
            else:
                self.line = reqs[0][3]
                self.err("cannot place a partition wall clear of doors, windows and stairs: " + self._crowded(
                    v, k, n, lo, hi, along_x, cross))
            parts.append(p)
            cur = p + 1
        bounds = list(zip([lo] + [p + 1 for p in parts], [p - 1 for p in parts] + [hi]))
        clo, chi = (z0 + 1, z1 - 1) if along_x else (x0 + 1, x1 - 1)
        mid = (clo + chi) // 2
        wlo, whi = v.wall_y(k)
        floor_y = v.floor_y(k)
        doorways = []
        for p in parts:
            for c in range(clo, chi + 1):
                for y in range(wlo, whi + 1):
                    self.put(*_xyz((p, c) if along_x else (c, p), y), v.fill, "wall")
            for y in (wlo, wlo + 1):
                self.carve(*_xyz((p, mid) if along_x else (mid, p), y))
            doorways.append((p, mid) if along_x else (mid, p))
        counts = Counter(r["type"] for r in self.rooms)
        ymax = whi if k == v.storeys - 1 else v.floor_y(k + 1) - 1
        for (rtype, furnish, _, line), (a0, a1) in zip(reqs, bounds):
            self.line = line
            counts[rtype] += 1
            rid = rtype if counts[rtype] == 1 else f"{rtype}_{counts[rtype]}"
            (rx0, rx1), (rz0, rz1) = ((a0, a1), (clo, chi)) if along_x else ((clo, chi), (a0, a1))
            inside = lambda x, z: rx0 <= x <= rx1 and rz0 <= z <= rz1
            openings = [o for o in self._openings(v, k) if inside(*o)]
            for wx, wz in doorways:
                for nx, nz in ((wx - 1, wz), (wx + 1, wz), (wx, wz - 1), (wx, wz + 1)):
                    if inside(nx, nz):
                        openings.append((nx, nz))
            room = {"id": rid, "type": rtype, "part": v.name, "storey": k,
                    "min": {"x": rx0, "y": floor_y + 1, "z": rz0}, "max": {"x": rx1, "y": ymax, "z": rz1},
                    "door_pos": None, "openings": [{"x": x, "y": floor_y + 1, "z": z} for x, z in openings]}
            if openings:
                room["door_pos"] = room["openings"][0]
            if furnish:
                self._furnish(rtype, (rx0, rx1, rz0, rz1), openings, floor_y + 1, self._stair_cells(v, k), ymax)
            self.rooms.append(room)

    def _furnish(self, rtype, box, openings, y, avoid, ymax):
        x0, x1, z0, z1 = box
        keep_clear = set(avoid)
        for ox, oz in openings:  # a 3-wide apron in front of every entrance stays empty
            keep_clear |= {(ox + i, oz + j) for i in (-1, 0, 1) for j in (-1, 0, 1) if abs(i) + abs(j) <= 1}
        cx, cz = (x0 + x1) // 2, (z0 + z1) // 2
        for ox, oz in openings:  # a path from every entrance to the room centre stays empty too
            keep_clear |= {(x, oz) for x in range(min(ox, cx), max(ox, cx) + 1)}
            keep_clear |= {(cx, z) for z in range(min(oz, cz), max(oz, cz) + 1)}
        air = "minecraft:air"
        ring = [(x, z) for z in range(z0, z1 + 1) for x in range(x0, x1 + 1)
                if (x in (x0, x1) or z in (z0, z1)) and (x, z) not in keep_clear
                and self.get(x, y, z) == air and self.get(x, y - 1, z) != air]
        far = lambda c: -min((abs(c[0] - ox) + abs(c[1] - oz) for ox, oz in openings), default=0)
        ring.sort(key=lambda c: (far(c), c[1], c[0]))
        used: set = set()

        def inward(x, z):  # direction from the nearest edge toward the room centre
            if x == x0 and x0 != x1: return "east"
            if x == x1 and x0 != x1: return "west"
            return "south" if z == z0 else "north"

        for item in TEMPLATES[rtype]:
            for (x, z) in ring:
                if (x, z) in used or item == "chest" and any(
                        abs(x - ux) + abs(z - uz) == 1 and self.get(ux, y, uz).startswith("minecraft:chest") for ux, uz in used):
                    continue
                if item == "bed":
                    f = inward(x, z)
                    fdx, fdz = SIDES[f]
                    fx, fz = x + fdx, z + fdz
                    if not (x0 <= fx <= x1 and z0 <= fz <= z1) or (fx, fz) in keep_clear or (fx, fz) in used \
                            or self.get(fx, y - 1, fz) == air:
                        continue
                    head_facing = OPPOSITE[f]
                    self.put(x, y, z, self.state("minecraft:white_bed", facing=head_facing, part="head"), "furniture")
                    self.put(fx, y, fz, self.state("minecraft:white_bed", facing=head_facing, part="foot"), "furniture")
                    used |= {(x, z), (fx, fz)}
                elif item == "lantern":
                    self.put(x, y, z, self.state("minecraft:lantern", hanging="false"), "light")
                    used.add((x, z))
                else:
                    props = {"facing": inward(x, z)} if item in FACING_ITEMS else {}
                    self.put(x, y, z, self.state("minecraft:" + item, **props), "furniture")
                    used.add((x, z))
                break
        for _ in range(8):  # ponytail: add <= 8 lanterns where a conservative light flood (all blocks opaque) leaves dark
            lit = self._light_estimate(box, y, ymax)
            floor = [(x, z) for z in range(z0, z1 + 1) for x in range(x0, x1 + 1)
                     if self.get(x, y, z) == air and self.get(x, y - 1, z) != air]
            dark = [c for c in floor if lit.get((c[0], y, c[1]), 0) <= LIGHT_MARGIN]
            free = [c for c in ring if c not in used and self.get(c[0], y, c[1]) == air]
            if not dark or not free:
                break
            spot = min(free, key=lambda c: (min(abs(c[0] - d[0]) + abs(c[1] - d[1]) for d in dark), c[1], c[0]))
            self.put(spot[0], y, spot[1], self.state("minecraft:lantern", hanging="false"), "light")
            used.add(spot)

    def _light_estimate(self, box, y0, y1):
        x0, x1, z0, z1 = box
        lit, q = {}, []
        for y in range(y0, y1 + 1):
            for z in range(z0, z1 + 1):
                for x in range(x0, x1 + 1):
                    if self.get(x, y, z).startswith("minecraft:lantern"):
                        lit[(x, y, z)] = 15
                        q.append((x, y, z))
        while q:
            x, y, z = q.pop(0)
            lv = lit[(x, y, z)] - 1
            for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                n = (x + dx, y + dy, z + dz)
                if lv > 0 and x0 <= n[0] <= x1 and y0 <= n[1] <= y1 and z0 <= n[2] <= z1 and lit.get(n, 0) < lv                         and self.get(*n) == "minecraft:air":
                    lit[n] = lv
                    q.append(n)
        return lit

    def _connect(self):
        """Panes, bars and fences take their side connections from neighbours (approximation, see PARTIAL)."""
        for (x, y, z), (state, role) in list(self.cells.items()):
            bid, props = parse_state(state)
            if not (bid.endswith("_pane") or bid == "minecraft:iron_bars" or bid.endswith("_fence")):
                continue
            new = dict(props)
            for side, (dx, dz) in SIDES.items():
                nbs = self.get(x + dx, y, z + dz)
                nb = parse_state(nbs)[0]
                same = (nb.endswith("_pane") or nb == "minecraft:iron_bars") if not bid.endswith("_fence") else \
                    (nb.endswith("_fence") or nb.endswith("_fence_gate"))
                new[side] = "true" if same or not any(p in nb for p in PARTIAL) or OPPOSITE[side] in full_sides(nbs) \
                    else "false"
            self.cells[(x, y, z)] = (self.gd.canonical(format_state(bid, new)), role)


def _xyz(xz, y):
    return xz[0], y, xz[1]


def load_style(name: str, line: int) -> tuple[dict, str]:
    if not NAME.match(name):
        raise CompileError(line, f"bad style name {name!r}")
    p = STYLES / f"{name}.json"
    if not p.exists():
        raise CompileError(line, f"unknown style {name!r}")
    raw = p.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def compile_program(source: str | bytes, gd: GameData, project_id: str = "design", version: int = 1,
                    origin: tuple[int, int, int] = (0, 64, 0), plot: dict | None = None) -> Design:
    """Parse + compile. Raises LangError (syntax) or CompileError (semantics, limits) for program-caused failures.
    `plot` (the brief's site plot, world x/z) only bounds the doorsteps the compiler adds; the validator checks the fit."""
    prog = parse(source)
    h = bind(prog.header, HEADER)
    style, style_hash = load_style(h["style"], prog.header.line)
    b = Builder(gd, style, h["seed"], prog.header.line)
    for call in prog.calls:
        b.line = call.line
        if call.name not in COMMANDS:
            raise CompileError(call.line, f"unknown command {call.name!r} (known: {', '.join(sorted(COMMANDS))})")
        getattr(b, call.name)(**bind(call, COMMANDS[call.name]))
    b.line = prog.calls[-1].line if prog.calls else prog.header.line
    b.finalize((plot["max"]["x"] - origin[0], plot["max"]["z"] - origin[2]) if plot else None)
    if not b.cells:
        raise CompileError(b.line, "program places no blocks")
    xs, ys, zs = zip(*b.cells)
    mn = (min(xs), min(ys), min(zs))
    size = (max(xs) - mn[0] + 1, max(ys) - mn[1] + 1, max(zs) - mn[2] + 1)
    if size[1] > WORLD_HEIGHT or size[0] * size[1] * size[2] > MAX_VOLUME or max(size) > 384:
        raise CompileError(b.line, f"design size {size} exceeds the limits")
    g = Design(size, origin)
    for (x, y, z), (state, role) in sorted(b.cells.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0])):
        g.set(x - mn[0], y - mn[1], z - mn[2], state, role)
    shift = lambda p: {"x": p["x"] - mn[0], "y": p["y"] - mn[1], "z": p["z"] - mn[2]}
    for r in b.rooms:
        g.rooms.append({**r, "min": shift(r["min"]), "max": shift(r["max"]),
                        "door_pos": shift(r["door_pos"]) if r["door_pos"] else None,
                        "openings": [shift(o) for o in r["openings"]]})
    bad = g.canonicalize(gd)
    if bad:
        raise CompileError(b.line, "; ".join(bad))
    src = (source if isinstance(source, bytes) else source.encode("utf-8")).replace(b"\r\n", b"\n")  # hash the LF form
    entrances = [shift({"x": d["inside"][0], "y": v.fh, "z": d["inside"][1]}) for v in b.vols.values() for d in v.doors]
    g.meta = {"project_id": project_id, "version": version, "world_id": None, "dim": "minecraft:overworld",
              "style": h["style"], "style_card_hash": style_hash,
              "provenance": {"program_hash": hashlib.sha256(src).hexdigest(), "compiler_version": COMPILER_VERSION,
                             "seed": h["seed"], "detailing_rules": [r for r in DETAIL_ORDER if r in b.details], "footprint_offset": [-v for v in mn],
                             "entrances": entrances, "commands": sorted({c.name for c in prog.calls}), "writes": b.writes,
                             "doorsteps": [shift({"x": x, "y": y, "z": z}) for x, y, z in b.doorsteps],
                             "overwrites": dict(sorted(b.overwrites.items())),
                             "refused_writes": dict(sorted(b.refused.items()))}}
    return g


def compile_file(path: Path, gd: GameData, **kw) -> Design:
    p = Path(path)
    if p.stat().st_size > MAX_SOURCE_BYTES:
        raise LangError(1, 1, f"program larger than {MAX_SOURCE_BYTES} bytes")
    return compile_program(p.read_bytes(), gd, project_id=kw.pop("project_id", p.stem), **kw)


__all__ = ["compile_program", "compile_file", "CompileError", "LangError", "COMMANDS"]
