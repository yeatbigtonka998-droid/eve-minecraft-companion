"""The labelled block grid (design.json, contracts/design-project.md) and its canonical hash."""
from __future__ import annotations

import base64
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np

from .gamedata import GameData, StateError, format_state, parse_state

FORMAT = "eve-design/1"
AIR = "minecraft:air"
ROLES = ["none", "foundation", "wall", "window", "door", "roof", "floor", "stair",
         "furniture", "light", "ground", "support", "trim"]
ROLE = {n: i for i, n in enumerate(ROLES)}
MAX_DIM = 384            # world height -64..319; also the cap per horizontal axis
MAX_VOLUME = 2_097_152   # 128^3 cells, ~6 MB of arrays
DIRS = ["north", "east", "south", "west"]


class Design:
    """Grid indexed [y, z, x] so a C-order flatten is x-fastest, then z, then y (contract order)."""

    def __init__(self, size: tuple[int, int, int], origin: tuple[int, int, int] = (0, 0, 0)):
        sx, sy, sz = (int(v) for v in size)
        if min(sx, sy, sz) < 1 or max(sx, sy, sz) > MAX_DIM or sx * sy * sz > MAX_VOLUME:
            raise ValueError(f"design size {size} outside limits (1..{MAX_DIM} per axis, {MAX_VOLUME} cells)")
        self.size = (sx, sy, sz)
        self.origin = tuple(int(v) for v in origin)
        self.palette = [AIR]
        self._index = {AIR: 0}
        self.blocks = np.zeros((sy, sz, sx), np.uint16)
        self.roles = np.zeros((sy, sz, sx), np.uint8)
        self.rooms: list[dict] = []
        self.block_entities: list[dict] = []   # {"pos": {x,y,z}, "id": str|None, "nbt": snbt}
        self.meta: dict = {}

    # --- cells -------------------------------------------------------------------------------
    def idx(self, state: str) -> int:
        i = self._index.get(state)
        if i is None:
            if len(self.palette) >= 65535:
                raise ValueError("palette full")
            i = self._index[state] = len(self.palette)
            self.palette.append(state)
        return i

    def set(self, x: int, y: int, z: int, state: str, role: str = "none") -> None:
        self.blocks[y, z, x] = self.idx(state)
        self.roles[y, z, x] = ROLE[role] if state != AIR else 0

    def get(self, x: int, y: int, z: int) -> str:
        return self.palette[self.blocks[y, z, x]]

    def role_at(self, x: int, y: int, z: int) -> str:
        return ROLES[self.roles[y, z, x]]

    def in_bounds(self, x: int, y: int, z: int) -> bool:
        return 0 <= x < self.size[0] and 0 <= y < self.size[1] and 0 <= z < self.size[2]

    def counts(self) -> Counter:
        c = Counter()
        ids, n = np.unique(self.blocks, return_counts=True)
        for i, k in zip(ids, n):
            if self.palette[i] != AIR:
                c[self.palette[i]] += int(k)
        return c

    def cells(self):
        """Yield (x, y, z, state, role) for every non-air cell, y-major (bottom-up) order."""
        for y, z, x in zip(*np.nonzero(self.blocks != self._index.get(AIR, -1))):
            yield int(x), int(y), int(z), self.palette[self.blocks[y, z, x]], ROLES[self.roles[y, z, x]]

    # --- canonical form ----------------------------------------------------------------------
    def canonicalize(self, gd: GameData) -> list[str]:
        """Fill default properties, merge equal states, sort palette (air first). Returns illegal states
        (kept verbatim, never substituted)."""
        bad, states = [], []
        for s in self.palette:
            try:
                states.append(gd.canonical(s))
            except StateError as e:
                bad.append(str(e))
                states.append(s)
        self._remap(states)
        return bad

    def _remap(self, states: list[str]) -> None:
        used = np.unique(self.blocks)
        order = [AIR] + sorted({states[i] for i in used} - {AIR})
        pos = {s: i for i, s in enumerate(order)}
        lut = np.array([pos.get(s, 0) for s in states], np.uint16)
        self.blocks = lut[self.blocks]
        self.palette, self._index = order, pos

    def canonical_bytes(self) -> bytes:
        self._remap(list(self.palette))
        doc = {"size": list(self.size), "origin": list(self.origin), "palette": self.palette,
               "blocks": _b64(self.blocks.astype("<u2")), "roles": _b64(self.roles)}
        return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()

    def hash(self) -> str:
        """sha256 of canonical(palette, blocks, roles, size, origin): the identity approvals bind to."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def same_blocks(self, other: "Design") -> bool:
        return (self.size == other.size and
                [self.palette[i] for i in self.blocks.ravel()] == [other.palette[i] for i in other.blocks.ravel()])

    # --- design.json -------------------------------------------------------------------------
    def to_json(self) -> dict:
        h = self.hash()  # also canonicalizes palette order
        m = self.meta
        return {
            "format": FORMAT,
            "project_id": m.get("project_id"), "version": m.get("version"),
            "world_id": m.get("world_id"), "dim": m.get("dim", "minecraft:overworld"),
            "origin": dict(zip("xyz", self.origin)), "size": dict(zip("xyz", self.size)),
            "style": m.get("style"), "style_card_hash": m.get("style_card_hash"),
            "palette": self.palette,
            "blocks": _b64(self.blocks.astype("<u2")),
            "roles": _b64(self.roles),
            "rooms": self.rooms,
            "block_entities": self.block_entities,
            "hash": h,
            "provenance": m.get("provenance", {}),
            "import_issues": m.get("import_issues", []),
        }

    def save(self, path: Path) -> str:
        d = self.to_json()
        Path(path).write_text(json.dumps(d, indent=1) + "\n")
        return d["hash"]

    @classmethod
    def from_json(cls, d: dict) -> "Design":
        if d.get("format") != FORMAT:
            raise ValueError(f"not {FORMAT}")
        size = (d["size"]["x"], d["size"]["y"], d["size"]["z"])
        g = cls(size, (d["origin"]["x"], d["origin"]["y"], d["origin"]["z"]))
        n = size[0] * size[1] * size[2]
        blocks = np.frombuffer(base64.b64decode(d["blocks"], validate=True), "<u2")
        roles = np.frombuffer(base64.b64decode(d["roles"], validate=True), np.uint8)
        pal = list(d["palette"])
        if len(blocks) != n or len(roles) != n or not pal or pal[0] != AIR:
            raise ValueError("design.json arrays do not match size/palette")
        if blocks.max(initial=0) >= len(pal) or roles.max(initial=0) >= len(ROLES):
            raise ValueError("design.json index out of range")
        g.blocks = blocks.astype(np.uint16).reshape(size[1], size[2], size[0])
        g.roles = roles.reshape(size[1], size[2], size[0]).copy()
        g.palette, g._index = pal, {s: i for i, s in enumerate(pal)}
        g.rooms, g.block_entities = list(d.get("rooms", [])), list(d.get("block_entities", []))
        g.meta = {k: d.get(k) for k in ("project_id", "version", "world_id", "dim", "style",
                                        "style_card_hash", "provenance", "import_issues")}
        if d.get("hash") and g.hash() != d["hash"]:
            raise ValueError("design.json hash does not match its content")
        return g

    @classmethod
    def load(cls, path: Path) -> "Design":
        return cls.from_json(json.loads(Path(path).read_text()))


def _b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


# --- rotation (clockwise seen from above: north -> east -> south -> west) --------------------------
def _rot_dir(d: str, turns: int) -> str:
    return DIRS[(DIRS.index(d) + turns) % 4]


def rotate_state(state: str, turns: int, gd: GameData) -> str:
    """Rotate a block state like the game's Block.rotate. Raises StateError if a property can't be rotated."""
    turns %= 4
    bid, props = parse_state(state)
    if not turns or not props:
        return state
    allowed = gd.blocks.get(bid, {}).get("props", {})
    out = {}
    for k, v in props.items():
        if k in DIRS:                                    # fence/wall/pane/vine side flags
            out[_rot_dir(k, turns)] = v
            continue
        if k == "axis" and turns % 2 and v in ("x", "z"):
            v = "z" if v == "x" else "x"
        elif k == "rotation":                            # signs, banners, skulls: 16 steps
            v = str((int(v) + 4 * turns) % 16)
        elif v in DIRS:
            v = _rot_dir(v, turns)
        elif any(w in DIRS for w in v.split("_")):       # rail shapes, orientation=north_up, ...
            words = [_rot_dir(w, turns) if w in DIRS else w for w in v.split("_")]
            v = next((c for c in ("_".join(words), "_".join(reversed(words))) if c in allowed.get(k, [])), None)
            if v is None:
                raise StateError(f"cannot rotate {state}: property {k}")
        out[k] = v
    return format_state(bid, out)


def rotated(g: Design, turns: int, gd: GameData) -> Design:
    turns %= 4
    sx, sy, sz = g.size
    nx, nz = (sz, sx) if turns % 2 else (sx, sz)
    r = Design((nx, sy, nz), g.origin)

    def pos(x, z):
        cx, cz = sx, sz
        for _ in range(turns):
            x, z, cx, cz = cz - 1 - z, x, cz, cx
        return x, z

    for x, y, z, s, role in g.cells():  # ponytail: per-cell loop, fine at design sizes (<= MAX_VOLUME)
        px, pz = pos(x, z)
        r.set(px, y, pz, rotate_state(s, turns, gd), role)
    for be in g.block_entities:
        px, pz = pos(be["pos"]["x"], be["pos"]["z"])
        r.block_entities.append({**be, "pos": {"x": px, "y": be["pos"]["y"], "z": pz}})
    for room in g.rooms:
        a, b = pos(room["min"]["x"], room["min"]["z"]), pos(room["max"]["x"], room["max"]["z"])
        nr = {**room, "min": {"x": min(a[0], b[0]), "y": room["min"]["y"], "z": min(a[1], b[1])},
              "max": {"x": max(a[0], b[0]), "y": room["max"]["y"], "z": max(a[1], b[1])}}
        if room.get("door_pos"):
            d = room["door_pos"]
            dx, dz = pos(d["x"], d["z"])
            nr["door_pos"] = {"x": dx, "y": d["y"], "z": dz}
        r.rooms.append(nr)
    r.meta = dict(g.meta)
    return r
