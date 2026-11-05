"""CP-62 (offline part): previews draw the exact grid from the game's own models; missing support is visible."""
import json

import numpy as np
from PIL import Image

from eve_design import gamedata, preview
from eve_design.grid import Design

GD = gamedata.load()
BG = np.array(preview.BG)


def one(state, tmp_path, name):
    g = Design((1, 1, 1))
    g.set(0, 0, 0, GD.canonical(state), "wall")
    g.canonicalize(GD)
    meta = preview.render(g, GD, tmp_path / name, scale=32)
    a = np.asarray(Image.open(tmp_path / name / "SE.png").convert("RGB")).astype(int)
    ys, xs = np.nonzero((np.abs(a - BG).sum(axis=2) > 0))
    return meta, a, ys, xs


def test_slab_is_half_height(tmp_path):
    _, _, ys_full, _ = one("minecraft:oak_planks", tmp_path, "full")
    _, _, ys_slab, _ = one("minecraft:oak_slab[type=bottom]", tmp_path, "slab")
    full_h, slab_h = ys_full.max() - ys_full.min(), ys_slab.max() - ys_slab.min()
    assert slab_h < full_h - 10            # 32 px per block: a bottom slab is 16 px less tall


def test_stair_facing_changes_silhouette(tmp_path):
    _, _, ys_e, xs_e = one("minecraft:oak_stairs[facing=east]", tmp_path, "east")
    _, _, ys_w, xs_w = one("minecraft:oak_stairs[facing=west]", tmp_path, "west")
    top_e = xs_e[ys_e == ys_e.min()].mean()
    top_w = xs_w[ys_w == ys_w.min()].mean()
    assert top_e > top_w + 5               # the high step sits to the east (right) for facing=east in the SE view


def test_unsupported_is_magenta_and_listed(tmp_path):
    meta, a, _, _ = one("minecraft:chest", tmp_path, "chest")
    assert meta["unsupported"] == {"minecraft:chest": 1}
    assert ((a[:, :, 0] > 200) & (a[:, :, 1] < 60) & (a[:, :, 2] > 200)).any()


def test_views_bound_to_design(tmp_path):
    g = Design.load("examples/out/hut5/design.json")
    meta = preview.render(g, GD, tmp_path / "hut")
    assert meta["design_hash"] == g.hash()
    names = {p.name for p in (tmp_path / "hut").iterdir()}
    assert {"SE.png", "SW.png", "NE.png", "NW.png", "top.png", "interior-main.png", "render.json"} <= names
    assert json.loads((tmp_path / "hut" / "render.json").read_text())["unsupported"] == {"minecraft:chest": 1}
