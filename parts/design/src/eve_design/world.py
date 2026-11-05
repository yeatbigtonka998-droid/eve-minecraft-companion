"""CP-62 preparation: put a design into a disposable test world and compare real screenshots with the renderer.

paste(design_dir, out)  -> a data pack (pack.mcmeta + functions) foundation copies into a DISPOSABLE world's datapacks/:
    /function eve:clear_<id>       fill the design box (+1 margin) with air, strict, in chunks under /fill's limit
    /function eve:paste_<id>       setblock every non-air cell, bottom-up, `strict` (no neighbour updates, so door
                                   tops, beds and torches are placed exactly as in design.json)
    /function eve:cam_<id>_<view>  tp the executing player to the camera for SE/NE/NW/SW/top
  plus camera.json. Block-entity data (none in compiled designs) is not pasted; it is listed in the notes.
compare(render_png, screenshot_png) -> aids for the human CP-62 judgement (silhouette overlap, aspect, colour).

The renderer is a true isometric projection: view direction (-1,-1,-1) for SE, i.e. yaw 135, pitch 35.26. The game
camera is a perspective camera, so screenshots are taken from far away with a narrow FOV (30, the game's minimum).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .grid import Design

FILL_LIMIT = 32768            # /fill volume limit
PACK_FORMAT = 121             # data pack format of 26.3 (version.json pack_version.data_major; Mojang's own packs)
FOV = 30.0                    # vertical FOV for screenshots
VIEWS = {"SE": (1, 1), "NE": (1, -1), "NW": (-1, -1), "SW": (-1, 1)}   # camera side in (x, z)


def _ident(s: str) -> str:
    return "".join(c if c.isalnum() or c == "_" else "_" for c in s.lower())[:40] or "design"


def cameras(g: Design) -> dict:
    sx, sy, sz = g.size
    ox, oy, oz = g.origin
    cx, cy, cz = ox + sx / 2, oy + sy / 2, oz + sz / 2
    radius = 0.5 * math.sqrt(sx * sx + sy * sy + sz * sz)
    dist = radius / math.sin(math.radians(FOV / 2)) + 2
    out = {}
    for name, (dx, dz) in VIEWS.items():
        d = np.array([dx, 1.0, dz]) / math.sqrt(3)
        look = -d
        out[name] = {"pos": [round(cx + dist * d[0], 2), round(cy + dist * d[1] - 1.62, 2), round(cz + dist * d[2], 2)],
                     "yaw": round(math.degrees(math.atan2(-look[0], look[2])), 2),
                     "pitch": round(math.degrees(-math.asin(look[1])), 2), "fov": FOV}
    out["top"] = {"pos": [round(cx, 2), round(oy + sy + dist - 1.62, 2), round(cz, 2)], "yaw": 180.0, "pitch": 90.0, "fov": FOV}
    return out


def paste(design_dir: Path, out: Path, origin: tuple[int, int, int] | None = None) -> dict:
    design_dir, out = Path(design_dir), Path(out)
    g = Design.load(design_dir / "design.json")
    if origin is not None:
        g.origin = tuple(origin)
    ident = _ident(f"{g.meta.get('project_id') or 'design'}_v{g.meta.get('version') or 1}")
    fdir = out / "data" / "eve" / "function"
    fdir.mkdir(parents=True, exist_ok=True)
    (out / "pack.mcmeta").write_text(json.dumps({"pack": {"description": f"Eve test paste {ident} {g.hash()[:12]}",
                                                          "min_format": PACK_FORMAT, "max_format": PACK_FORMAT}}, indent=1))
    ox, oy, oz = g.origin
    sx, sy, sz = g.size
    x0, y0, z0, x1, y1, z1 = ox - 1, oy, oz - 1, ox + sx, oy + sy, oz + sz
    clear = []
    step = max(1, FILL_LIMIT // ((x1 - x0 + 1) * (z1 - z0 + 1)))
    for ya in range(y0, y1 + 1, step):
        clear.append(f"fill {x0} {ya} {z0} {x1} {min(y1, ya + step - 1)} {z1} minecraft:air strict")
    (fdir / f"clear_{ident}.mcfunction").write_text("\n".join(clear) + "\n")
    lines = [f"setblock {ox + x} {oy + y} {oz + z} {s} strict" for x, y, z, s, _ in g.cells()]
    (fdir / f"paste_{ident}.mcfunction").write_text("\n".join(lines) + "\n")
    cams = cameras(g)
    for name, c in cams.items():
        (fdir / f"cam_{ident}_{name.lower()}.mcfunction").write_text(
            "gamemode spectator @s\n" f"tp @s {c['pos'][0]} {c['pos'][1]} {c['pos'][2]} {c['yaw']} {c['pitch']}\n")
    meta = {"design_hash": g.hash(), "origin": list(g.origin), "size": list(g.size), "blocks": len(lines),
            "functions": sorted(p.stem for p in fdir.glob("*.mcfunction")), "cameras": cams,
            "notes": ["DISPOSABLE worlds only (server-qual/dev); run clear first, then paste",
                      "screenshots: spectator, F1 (hide HUD), FOV 30 in the client options, render distance >= 8, noon, clear weather",
                      f"{len(g.block_entities)} block entities: data not pasted (compiled designs carry none)"]}
    (out / "camera.json").write_text(json.dumps(meta, indent=1) + "\n")
    return meta


# --- comparison aids --------------------------------------------------------------------------------------
def _foreground(img: np.ndarray, bg=None, thr=40) -> np.ndarray:
    if bg is None:
        h, w = img.shape[:2]
        corners = np.concatenate([img[:8, :8].reshape(-1, 3), img[:8, w - 8:].reshape(-1, 3),
                                  img[h - 8:, :8].reshape(-1, 3), img[h - 8:, w - 8:].reshape(-1, 3)])
        bg = np.median(corners, axis=0)
    return np.abs(img.astype(int) - np.asarray(bg, int)).sum(axis=2) > thr


def _crop(img, mask):
    ys, xs = np.nonzero(mask)
    if not len(ys):
        raise ValueError("no foreground found")
    return img[ys.min():ys.max() + 1, xs.min():xs.max() + 1], mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def compare(render_png: Path, shot_png: Path, out: Path | None = None, size: int = 256) -> dict:
    """Silhouette IoU, aspect ratios and colour-histogram intersection after cropping both to their foreground and
    scaling to a common box. Aids for a person's CP-62 judgement, not a pass/fail on their own."""
    r = np.asarray(Image.open(render_png).convert("RGB"))
    s = np.asarray(Image.open(shot_png).convert("RGB"))
    rc, rm = _crop(r, _foreground(r))
    sc, sm = _crop(s, _foreground(s))
    box = lambda a: np.asarray(Image.fromarray(a.astype(np.uint8) * 255 if a.dtype == bool else a).resize((size, size)))
    rmk, smk = box(rm) > 127, box(sm) > 127
    iou = float((rmk & smk).sum() / max(1, (rmk | smk).sum()))

    def hist(img, mask):
        q = (img[mask] // 32).astype(int)
        h = np.bincount(q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2], minlength=512).astype(float)
        return h / h.sum()
    inter = float(np.minimum(hist(rc, rm), hist(sc, sm)).sum())
    res = {"aspect_render": round(rc.shape[1] / rc.shape[0], 3), "aspect_screenshot": round(sc.shape[1] / sc.shape[0], 3),
           "silhouette_iou": round(iou, 3), "colour_intersection": round(inter, 3),
           "note": "aids for a human judgement (CP-62 J); perspective vs isometric differences are expected"}
    if out is not None:
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        a, b = Image.fromarray(rc).resize((size, size)), Image.fromarray(sc).resize((size, size))
        canvas = Image.new("RGB", (2 * size + 30, size + 24), (255, 255, 255))
        canvas.paste(a, (10, 20))
        canvas.paste(b, (size + 20, 20))
        d = ImageDraw.Draw(canvas)
        d.text((10, 4), "renderer", fill=(0, 0, 0))
        d.text((size + 20, 4), f"screenshot  IoU {res['silhouette_iou']}  colour {res['colour_intersection']}", fill=(0, 0, 0))
        canvas.save(out / "side_by_side.png")
        (out / "compare.json").write_text(json.dumps(res, indent=1) + "\n")
    return res
