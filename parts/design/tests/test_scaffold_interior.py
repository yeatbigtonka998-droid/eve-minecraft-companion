"""O89: no scaffold inside the design's rooms unless asked (hut5_tall opts in for builder's lane proof)."""
import runpy
from pathlib import Path

from eve_design import gamedata, scaffold

GD = gamedata.load()
D = Path(__file__).parents[1]


def _in_rooms(g, s):
    off = [s.origin[i] - g.origin[i] for i in range(3)]
    return [(x + off[0], y + off[1], z + off[2]) for x, y, z, _, _ in s.cells()
            if any(r["min"]["x"] <= x + off[0] <= r["max"]["x"] and r["min"]["y"] <= y + off[1] <= r["max"]["y"]
                   and r["min"]["z"] <= z + off[2] <= r["max"]["z"] for r in g.rooms)]


def test_interior_scaffold_is_off_by_default_and_hut5_tall_opts_in():
    assert scaffold.INTERIOR is False
    hut = runpy.run_path(str(D / "examples/hut5_tall.py"))
    g = hut["build"](GD)
    s, left = scaffold.plan(g, GD, hut["SITE"])                  # its ceiling middle now needs a way from outside
    assert s is not None and not _in_rooms(g, s)
    s, left = scaffold.plan(g, GD, hut["SITE"], interior=True)
    assert not left and len(_in_rooms(g, s)) >= 5
