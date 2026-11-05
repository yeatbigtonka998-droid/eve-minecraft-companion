"""CP-62 preparation: the world paste reproduces the exact grid; cameras match the renderer; compare() behaves."""
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image

from eve_design import gamedata, world
from eve_design.grid import Design

GD = gamedata.load()
EX = Path(__file__).parents[1] / "examples" / "out"
LINE = re.compile(r"^setblock (-?\d+) (-?\d+) (-?\d+) (\S+) strict$")


def test_paste_round_trips_to_the_exact_grid(tmp_path):
    g = Design.load(EX / "cabin" / "design.json")
    meta = world.paste(EX / "cabin", tmp_path / "pack", origin=(100, 70, -40))
    assert json.loads((tmp_path / "pack/pack.mcmeta").read_text())["pack"]["min_format"] == world.PACK_FORMAT == 121
    fn = tmp_path / "pack/data/eve/function"
    lines = (fn / "paste_cabin_v1.mcfunction").read_text().splitlines()
    assert len(lines) == meta["blocks"] == sum(g.counts().values())
    back = Design(g.size)
    ys = []
    for ln in lines:
        x, y, z, s = LINE.match(ln).groups()
        back.set(int(x) - 100, int(y) - 70, int(z) + 40, s)
        ys.append(int(y))
    assert ys == sorted(ys)                      # bottom-up
    assert back.same_blocks(g)
    for ln in (fn / "clear_cabin_v1.mcfunction").read_text().splitlines():
        x0, y0, z0, x1, y1, z1 = map(int, ln.split()[1:7])
        assert (x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1) <= world.FILL_LIMIT and ln.endswith("air strict")


def test_cameras_match_the_renderer_angles():
    g = Design.load(EX / "cabin" / "design.json")
    cams = world.cameras(g)
    assert (cams["SE"]["yaw"], cams["NE"]["yaw"], cams["NW"]["yaw"], cams["SW"]["yaw"]) == (135.0, 45.0, -45.0, -135.0)
    assert all(abs(cams[v]["pitch"] - 35.26) < 0.01 for v in ("SE", "NE", "NW", "SW")) and cams["top"]["pitch"] == 90
    centre = np.array(g.origin) + np.array(g.size) / 2
    radius = 0.5 * np.linalg.norm(g.size)
    for v in ("SE", "NE", "NW", "SW"):
        eye = np.array(cams[v]["pos"]) + [0, 1.62, 0]
        assert np.linalg.norm(eye - centre) * np.sin(np.radians(world.FOV / 2)) >= radius   # the whole design in view


def _fake_screenshot(src, dst, scale=1.5, sky=(120, 170, 255)):
    im = Image.open(src).convert("RGB")
    arr = np.asarray(im).copy()
    bg = (np.abs(arr.astype(int) - np.array([236, 240, 244])).sum(axis=2) <= 40)
    arr[bg] = sky
    im = Image.fromarray(arr).resize((int(im.width * scale), int(im.height * scale)))
    canvas = Image.new("RGB", (im.width + 200, im.height + 120), sky)
    canvas.paste(im, (90, 40))
    canvas.save(dst)


def test_compare_aids(tmp_path):
    r = EX / "cabin" / "previews" / "SE.png"
    same = world.compare(r, r)
    assert same["silhouette_iou"] == 1.0 and same["colour_intersection"] == 1.0
    _fake_screenshot(r, tmp_path / "shot.png")
    near = world.compare(r, tmp_path / "shot.png", tmp_path / "cmp")
    assert near["silhouette_iou"] > 0.9 and near["colour_intersection"] > 0.8
    assert (tmp_path / "cmp/side_by_side.png").exists() and (tmp_path / "cmp/compare.json").exists()
    other = world.compare(r, EX / "tower" / "previews" / "SE.png")
    assert other["silhouette_iou"] < near["silhouette_iou"] and other["colour_intersection"] < near["colour_intersection"]
