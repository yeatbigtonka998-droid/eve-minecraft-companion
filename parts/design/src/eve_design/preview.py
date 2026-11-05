"""Preview renderer (CP-62): isometric corner views, a top view and room cutaways of the EXACT grid.

Geometry comes from the pinned game's own blockstate and model JSON (element boxes, variant x/y rotations,
multipart conditions); each face is flat-coloured with the alpha-weighted average of its real texture (UV region),
shaded by direction. Blocks the game draws with special renderers (chests, signs, banners, heads...) have no
model elements: they are drawn as MAGENTA cubes and listed in render.json, never silently dropped.
Approximations (listed in render.json): element rotations (torches on walls, crossed plants) are drawn
axis-aligned; biome tints use one fixed green; painter's ordering per box.

    render(design, gd, out_dir) -> render.json dict; writes SE/SW/NE/NW.png, top.png, interior-<room>.png
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw

from .gamedata import GameData, parse_state
from .grid import AIR, Design, rotated

RENDERER_VERSION = "eve-preview/3"   # 2: straight-on elevations per side (O34d); 3: see-into-a-room = pink
MAGENTA = (255, 0, 255)
BG = (236, 240, 244)
SKY = (255, 64, 160)              # elevations: hot pink = open air (glass reads pale blue, so not sky blue)
INTO = (255, 230, 0)              # elevations: yellow = the sight line reaches a room's inside first (a hole)
TINT = (124, 189, 107)            # ponytail: one plains-like foliage/grass tint for tintindex faces
SHADE = {"up": 1.0, "east": 0.80, "south": 0.62, "down": 0.5, "west": 0.8, "north": 0.62}
COS, SIN = math.cos(math.radians(30)), math.sin(math.radians(30))
YROT = {"north": "east", "east": "south", "south": "west", "west": "north", "up": "up", "down": "down"}
XROT = {"up": "north", "north": "down", "down": "south", "south": "up", "east": "east", "west": "west"}


class Models:
    def __init__(self, gd: GameData):
        self.root = gd.root
        self.unsupported: dict[str, int] = {}
        self.approx: set[str] = set()

    @lru_cache(maxsize=None)
    def _model(self, name: str) -> dict:
        path = name.split(":")[-1]
        if not path.startswith("block/"):
            return {}
        p = self.root / "models" / f"{path}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    @lru_cache(maxsize=None)
    def resolved(self, name: str) -> tuple[tuple, dict]:
        """(elements, textures) following the parent chain."""
        chain, n = [], name
        while n and len(chain) < 16:
            m = self._model(n)
            chain.append(m)
            n = m.get("parent")
        tex, elements = {}, None
        for m in reversed(chain):
            tex.update({k: v.get("sprite", "") if isinstance(v, dict) else v for k, v in m.get("textures", {}).items()})
        for m in chain:
            if "elements" in m:
                elements = m["elements"]
                break
        return tuple(elements or ()), tex

    @lru_cache(maxsize=None)
    def tex_color(self, ref: str, tex_items: tuple, uv: tuple | None) -> tuple:
        tex = dict(tex_items)
        for _ in range(8):
            if ref.startswith("#"):
                ref = tex.get(ref[1:], "")
        if not ref:
            return MAGENTA
        p = self.root / "textures" / f"{ref.split(':')[-1]}.png"
        if not p.exists():
            return MAGENTA
        im = Image.open(p).convert("RGBA")
        if im.height > im.width:
            im = im.crop((0, 0, im.width, im.width))    # animated strip: first frame
        if uv:
            k = im.width / 16
            u0, v0, u1, v1 = uv
            box = (int(min(u0, u1) * k), int(min(v0, v1) * k), max(int(max(u0, u1) * k), int(min(u0, u1) * k) + 1),
                   max(int(max(v0, v1) * k), int(min(v0, v1) * k) + 1))
            im = im.crop(box)
        a = np.asarray(im, dtype=np.int64).reshape(-1, 4)
        a = a[a[:, 3] > 8]
        if not len(a):
            return MAGENTA
        return tuple(int(v) for v in (a[:, :3] * a[:, 3:]).sum(axis=0) // a[:, 3].sum())

    @lru_cache(maxsize=None)
    def boxes(self, state: str) -> tuple:
        """((x0,y0,z0,x1,y1,z1) in 0..1, {face: rgb}, full_cube) for one block state."""
        bid, props = parse_state(state)
        bs = self.root / "blockstates" / f"{bid.split(':')[1]}.json"
        applies = []
        if bs.exists():
            d = json.loads(bs.read_text(encoding="utf-8"))
            if "variants" in d:
                for key, v in d["variants"].items():
                    if all(props.get(k) == val for k, _, val in (kv.partition("=") for kv in key.split(",") if kv)):
                        applies.append(v[0] if isinstance(v, list) else v)
                        break
            for part in d.get("multipart", []):
                if _when(part.get("when"), props):
                    a = part["apply"]
                    applies.append(a[0] if isinstance(a, list) else a)
        out = []
        for a in applies:
            elements, tex = self.resolved(a["model"])
            for e in elements:
                if e.get("rotation") and e["rotation"].get("angle"):
                    self.approx.add("element rotation drawn axis-aligned")
                faces = {}
                for fname, f in e.get("faces", {}).items():
                    col = self.tex_color(f.get("texture", ""), tuple(sorted(tex.items())), tuple(f["uv"]) if "uv" in f else None)
                    if "tintindex" in f:
                        col = tuple(c * t // 255 for c, t in zip(col, TINT))
                    faces[fname] = col
                box = [c / 16 for c in e["from"]] + [c / 16 for c in e["to"]]
                box, faces = _rot_box(box, faces, a.get("x", 0), a.get("y", 0))
                out.append((tuple(box), faces))
        if not out:
            return ()
        full = len(out) == 1 and out[0][0] == (0, 0, 0, 1, 1, 1)
        return tuple((b, tuple(sorted(f.items())), full) for b, f in out)


def _when(cond, props) -> bool:
    if not cond:
        return True
    if "OR" in cond:
        return any(_when(c, props) for c in cond["OR"])
    if "AND" in cond:
        return all(_when(c, props) for c in cond["AND"])
    return all(props.get(k) in str(v).split("|") for k, v in cond.items())


def _rot_box(box, faces, xr, yr):
    x0, y0, z0, x1, y1, z1 = box
    for _ in range((xr // 90) % 4):              # around X: up -> north
        y0, z0, y1, z1 = z0, 1 - y1, z1, 1 - y0
        faces = {XROT[k]: v for k, v in faces.items()}
    for _ in range((yr // 90) % 4):              # around Y, clockwise from above: north -> east
        x0, z0, x1, z1 = 1 - z1, x0, 1 - z0, x1
        faces = {YROT[k]: v for k, v in faces.items()}
    return [min(x0, x1), min(y0, y1), min(z0, z1), max(x0, x1), max(y0, y1), max(z0, z1)], faces


def _shade(c, d):
    k = SHADE[d]
    return tuple(int(v * k) for v in c)


def _iso(g: Design, M: Models, scale: int, clip=None) -> Image.Image:
    """South-east view: camera above, toward -x,-z; visible faces up/east/south."""
    sx, sy, sz = g.size
    pts = [(x, y, z) for x in (0, sx) for y in (0, sy) for z in (0, sz)]
    P = lambda x, y, z: ((x - z) * COS * scale, (x + z) * SIN * scale - y * scale)
    xs, ys = zip(*(P(*p) for p in pts))
    ox, oy = -min(xs) + 8, -min(ys) + 8
    img = Image.new("RGB", (int(max(xs) - min(xs)) + 16, int(max(ys) - min(ys)) + 16), BG)
    dr = ImageDraw.Draw(img)
    full_at = {}
    cells = [c for c in g.cells() if clip is None or clip(*c[:3])]
    for x, y, z, s, _ in cells:
        b = M.boxes(s)
        full_at[(x, y, z)] = bool(b) and b[0][2]
    quads = []
    for x, y, z, s, _ in cells:
        b = M.boxes(s)
        if not b:
            b = (((0, 0, 0, 1, 1, 1), (("up", MAGENTA), ("east", MAGENTA), ("south", MAGENTA)), True),)
        for (x0, y0, z0, x1, y1, z1), faces, _ in b:
            f = dict(faces)
            X0, Y0, Z0, X1, Y1, Z1 = x + x0, y + y0, z + z0, x + x1, y + y1, z + z1
            depth = (X0 + X1 + Y0 + Y1 + Z0 + Z1) / 2
            if "up" in f and not (y1 == 1 and full_at.get((x, y + 1, z))):
                quads.append((depth, f["up"], "up", [(X0, Y1, Z0), (X1, Y1, Z0), (X1, Y1, Z1), (X0, Y1, Z1)]))
            if "east" in f and not (x1 == 1 and full_at.get((x + 1, y, z))):
                quads.append((depth, f["east"], "east", [(X1, Y0, Z0), (X1, Y1, Z0), (X1, Y1, Z1), (X1, Y0, Z1)]))
            if "south" in f and not (z1 == 1 and full_at.get((x, y, z + 1))):
                quads.append((depth, f["south"], "south", [(X0, Y0, Z1), (X1, Y0, Z1), (X1, Y1, Z1), (X0, Y1, Z1)]))
    quads.sort(key=lambda q: q[0])
    for _, col, d, poly in quads:
        pp = [(P(*p)[0] + ox, P(*p)[1] + oy) for p in poly]
        c = _shade(col, d)
        dr.polygon(pp, fill=c, outline=tuple(max(0, v - 25) for v in c))
    box = ImageChops.difference(img, Image.new("RGB", img.size, BG)).getbbox()
    return img.crop((max(0, box[0] - 8), max(0, box[1] - 8), min(img.width, box[2] + 8), min(img.height, box[3] + 8))) if box else img


def _top(g: Design, M: Models, scale: int) -> Image.Image:
    sx, sy, sz = g.size
    img = Image.new("RGB", (sx * scale, sz * scale), (236, 240, 244))
    dr = ImageDraw.Draw(img)
    for z in range(sz):
        for x in range(sx):
            for y in range(sy - 1, -1, -1):
                s = g.get(x, y, z)
                if s == AIR:
                    continue
                b = M.boxes(s)
                col = dict(b[0][1]).get("up", MAGENTA) if b else MAGENTA
                k = 0.55 + 0.45 * (y + 1) / sy
                dr.rectangle([x * scale, z * scale, (x + 1) * scale - 1, (z + 1) * scale - 1],
                             fill=tuple(int(v * k) for v in col))
                break
    return img


def _elev(g: Design, M: Models, scale: int) -> Image.Image:
    """Straight-on view of the south side: per (x, y) the first block from the south, darker the farther back, hot
    pink where nothing is (open air, e.g. under a porch roof), yellow where the sight line reaches a room's inside
    before any block (a hole in an outer wall). A floating block shows pink under it."""
    sx, sy, sz = g.size
    img = Image.new("RGB", (sx * scale, sy * scale), SKY)
    dr = ImageDraw.Draw(img)
    inside = lambda x, y, z: any(r["min"]["x"] <= x <= r["max"]["x"] and r["min"]["y"] <= y <= r["max"]["y"]
                                 and r["min"]["z"] <= z <= r["max"]["z"] for r in g.rooms)
    for x in range(sx):
        for y in range(sy):
            for z in range(sz - 1, -1, -1):
                s = g.get(x, y, z)
                if s == AIR:
                    if inside(x, y, z):            # the sight line is already inside a room: a hole in the outer
                        top = (sy - 1 - y) * scale  # wall (45L 945f4fe: the far wall seen through it read as shade)
                        dr.rectangle([x * scale, top, (x + 1) * scale - 1, top + scale - 1], fill=INTO)
                        break
                    continue
                b = M.boxes(s)
                faces = dict(b[0][1]) if b else {}
                col = faces.get("south") or next(iter(faces.values()), MAGENTA)
                k = 1.0 - 0.6 * (sz - 1 - z) / max(sz - 1, 1)
                top = (sy - 1 - y) * scale
                dr.rectangle([x * scale, top, (x + 1) * scale - 1, top + scale - 1], fill=tuple(int(v * k) for v in col),
                             outline=tuple(int(v * k * 0.85) for v in col))
                break
    return img


def render(g: Design, gd: GameData, out: Path, scale: int | None = None) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    M = Models(gd)
    scale = scale or max(6, min(16, 700 // max(g.size[0] + g.size[2], 1)))
    views = {}
    # rotating the world clockwise by k quarter turns moves the SE camera counter-clockwise around it
    for k, name in enumerate(("SE", "NE", "NW", "SW")):
        img = _iso(rotated(g, k, gd) if k else g, M, scale)
        img.save(out / f"{name}.png")
        views[name] = {"camera": f"true isometric from the {name} corner (35.26 deg elevation, yaw 45 deg off the axes)", "px_per_block": scale}
    _top(g, M, max(4, scale)).save(out / "top.png")
    big = max(12, min(28, 900 // max(g.size[0], g.size[2], g.size[1], 1)))
    for k, side in enumerate(("S", "E", "N", "W")):         # quarter turns bring that side to the south
        _elev(rotated(g, k, gd) if k else g, M, big).save(out / f"elev-{side}.png")
        views[f"elev-{side}"] = {"camera": f"straight-on elevation of the {side} side; darker = farther back, hot pink = "
                                           "open air (around the building, under a porch, under a floating block), yellow = you see into a room (a "
                                           "hole in the wall)", "px_per_block": big}
    views["top"] = {"camera": "orthographic from above, darker = lower", "px_per_block": max(4, scale)}
    for r in g.rooms:
        mn, mx = r["min"], r["max"]
        clip = lambda x, y, z, mn=mn, mx=mx: (mn["x"] - 1 <= x <= mx["x"] + 1 and mn["z"] - 1 <= z <= mx["z"] + 1
                                             and mn["y"] - 1 <= y <= mn["y"] + 1)
        _iso(g, M, 20, clip).save(out / f"interior-{r['id']}.png")
        views[f"interior-{r['id']}"] = {"camera": "SE isometric plan cut: the room box +1 block, cut 2 blocks above "
                                                  "its floor so furniture and openings show", "px_per_block": 20}
    for s, n in g.counts().items():
        if not M.boxes(s):
            M.unsupported[s.split("[")[0]] = M.unsupported.get(s.split("[")[0], 0) + n
    meta = {"renderer": RENDERER_VERSION, "design_hash": g.hash(), "game": gd.version, "views": views,
            "unsupported": dict(sorted(M.unsupported.items())),
            "approximations": sorted(M.approx | {"flat colour per face (texture average)", "fixed foliage tint",
                                                 "painter's ordering per box"})}
    (out / "render.json").write_text(json.dumps(meta, indent=1) + "\n")
    return meta
