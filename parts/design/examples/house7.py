"""O132: a small wood house for the owner's "build a small wood house": 7x7 oak, hand-built layer by layer like hut5.

    python examples/house7.py   -> examples/out/house7/ (design, litematic, planned scaffold, bom, validation, previews,
                                    build_order with stands, gate.json, brief.json for the mind's catalogue match)
    python examples/house7.py v2 -> examples/out/house7_v2/ (O241: the same house, same hash, re-planned: v1's plan
                                    had a stand at step 155 that no walk reaches under the current walking rule)

Oak planks floor and walls, oak log corners, an oak door on the south side with a doorstep in front of it (O94x: Eve's
Baritone enters a door only on a level move), a glass-pane window in the west and east walls, a wall torch inside, and a
stepped hip roof of oak stairs and slabs. No bed or chest: everything comes from logs, sand (glass) and coal (torch), so
the mind's supply loop can gather it in a fresh world. Kickoff O132 ruling (1a): hand-built catalogue templates are
category "schematic" like hut5 v1 (O61), so the gate reports their checks without holding them (no critic, no §8).
Layers are drawn north (z=0) at the top, west (x=0) on the left; y=0 is the floor, placed on the ground.
"""
import json
import runpy
import sys
from pathlib import Path

from eve_design import bom, buildorder, gamedata, gate, preview, scaffold
from eve_design.grid import Design
from eve_design.schem_io import save_litematic
from eve_design.validate import validate

HUT5 = runpy.run_path(str(Path(__file__).with_name("hut5.py")))
KEY = {**HUT5["KEY"], "F": ("minecraft:oak_planks", "floor"), "S": ("minecraft:oak_planks", "foundation"),
       "t": ("minecraft:wall_torch[facing=south]", "light")}
W = "P.....P"
LAYERS = [
    ["FFFFFFF"] * 7 + ["...S..."],                                                       # y0 floor + doorstep
    ["LPPPPPL", W, W, W, W, W, "LPPDPPL", "......."],                                     # y1 door
    ["LPPPPPL", "P..t..P", W, "g.....g", W, W, "LPPUPPL", "......."],                      # y2 windows W/E, torch
    ["LPPPPPL", W, W, W, W, W, "LPPPPPL", "......."],                                     # y3
    ["dnnnnnd"] + ["wRRRRRe"] * 5 + ["dsssssd", "......."],                               # y4 eaves + ceiling
    [".......", ".dnnnd."] + [".wRRRe."] * 3 + [".dsssd.", ".......", "......."],          # y5 second step
    [".......", "......."] + ["..bbb.."] * 3 + [".......", ".......", "......."],          # y6 slab cap
]
ORIGIN = (0, 64, 0)   # placeholder; the mind passes the confirmed site as build_schematic's origin
BRIEF = {"purpose": "small wood house", "names": ["small wood house", "wooden house", "oak house", "small house",
                                                   "wood cabin", "hut", "cabin", "house"],
         "style": "oak", "approval_category": "schematic", "authored_by": "eve-build-flow (O132)",
         "footprint": {"x": 7, "z": 7}, "door_side": "south"}


def build_house(gd, version: int = 1) -> Design:
    g = Design((7, len(LAYERS), 8), ORIGIN)
    for y, rows in enumerate(LAYERS):
        for z, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch != ".":
                    g.set(x, y, z, *KEY[ch])
    bad = g.canonicalize(gd)
    assert not bad, bad
    g.rooms = [{"id": "main", "type": "living", "min": {"x": 1, "y": 1, "z": 1}, "max": {"x": 5, "y": 3, "z": 5},
                "door_pos": {"x": 3, "y": 1, "z": 6}}]
    g.meta = {"project_id": "house7", "version": version, "world_id": None, "dim": "minecraft:overworld", "style": None,
              "provenance": {"source": "hand-built (examples/house7.py, O132)", "compiler_version": None, "seed": None,
                             "schematic": True}}   # O132 ruling (1a): a catalogue template, category schematic (O61)
    return g


def main(out: Path | None = None, version: int = 1) -> None:
    """v1 is frozen in history (O241): run it only to reproduce; v2 is the install version."""
    out = out or Path(__file__).parent / "out" / ("house7" if version == 1 else f"house7_v{version}")
    gd = gamedata.load()
    g = build_house(gd, version)
    s = scaffold.plan(g, gd)[0]
    out.mkdir(parents=True, exist_ok=True)
    g.save(out / "design.json")
    s.save(out / "scaffold.json")
    save_litematic(g, out / "design.litematic", gd, name="house7", description=f"Eve house7 v{version} {g.hash()[:12]}")
    storage = json.loads((Path(__file__).parents[1] / "tests/fixtures/hut/storage_index.json").read_text())
    stock, ref = bom.stock_from_storage(storage)
    b = bom.compute(g, gd, s, stock, ref)
    (out / "bom.json").write_text(json.dumps(b, indent=1) + "\n")
    order = buildorder.compact(g.hash(), buildorder.plan_order(g, gd, s)[0])
    (out / "build_order.json").write_text(json.dumps(order, separators=(",", ":")) + "\n")
    v = validate(g, gd, bom=b, scaffold=s)
    (out / "validation.json").write_text(json.dumps(v, indent=1) + "\n")
    preview.render(g, gd, out / "previews")
    d = gate.decide(out)
    (out / "brief.json").write_text(json.dumps(BRIEF, indent=1) + "\n")   # after the gate: a catalogue tag, no site
    print(f"house7 v{version} {g.hash()} scaffold {sum(s.counts().values())} valid={v['passed']} "
          f"failed={[k for k, c in v['checks'].items() if c['status'] == 'fail']} gate={d['category']}/{d['decision']} "
          f"-> {out}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["v2"]:
        main(*(Path(a) for a in sys.argv[2:]), version=2)
    else:
        main(*(Path(a) for a in sys.argv[1:]))
