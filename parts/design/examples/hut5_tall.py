"""A tall 5x5 hut whose ceiling is cheapest to build from inside (builder, O87m: lane-prove interior-scaffold removal).

    python examples/hut5_tall.py   -> examples/out/hut5_tall/ (design, litematic, planned scaffold, bom, validation,
                                      previews, build_order with stands, gate.json category "schematic")

Walls 6 high and a flat plank ceiling at y 7: its middle can only be clicked from inside below (or from on top of the
walls, a long staircase outside), so the O87h planner puts a short dirt staircase on the floor inside and takes it down
from inside, out through the door. An owner-style schematic like hut5 (O61): gate category "schematic", decision n/a.

The door stands on the floor, a block above the ground; Baritone opens a door only on a level move (builder, lane C
a12/a13), so a doorstep at floor height in front of it (2,0,5) is her level way in.

Layers are drawn north (z=0) at the top, west (x=0) on the left; y=0 is the foundation on the ground.
"""
import json
import sys
from pathlib import Path

from eve_design import bom, buildorder, gamedata, gate, preview, scaffold
from eve_design.grid import Design
from eve_design.schem_io import save_litematic
from eve_design.validate import validate

KEY = {  # char: (state, role)
    "C": ("minecraft:cobblestone", "foundation"),
    "L": ("minecraft:oak_log[axis=y]", "support"),
    "P": ("minecraft:oak_planks", "wall"),
    "R": ("minecraft:oak_planks", "roof"),
    "D": ("minecraft:oak_door[facing=north,half=lower,hinge=left,open=false,powered=false]", "door"),
    "U": ("minecraft:oak_door[facing=north,half=upper,hinge=left,open=false,powered=false]", "door"),
    "G": ("minecraft:glass_pane[east=true,west=true,north=false,south=false]", "window"),
    "K": ("minecraft:chest[facing=west,type=single]", "furniture"),
    "t": ("minecraft:wall_torch[facing=west]", "light"),
    "u": ("minecraft:wall_torch[facing=east]", "light"),
}
WALL = ["LPPPL", "P...P", "P...P", "P...P", "LPPPL"]
LAYERS = [
    ["CCCCC", "CCCCC", "CCCCC", "CCCCC", "CCCCC", "..C.."],   # y0 foundation / floor, doorstep
    ["LPPPL", "P..KP", "P...P", "P...P", "LPDPL"],   # y1 chest (a storage room needs one), door
    ["LPGPL", "P...P", "P..tP", "P...P", "LPUPL"],   # y2 window, torch, door top
    WALL, WALL,
    ["LPPPL", "P...P", "Pu..P", "P...P", "LPPPL"],   # y5 torch
    WALL,                                            # y6
    ["RRRRR", "RRRRR", "RRRRR", "RRRRR", "RRRRR"],   # y7 flat ceiling
]
ORIGIN = (0, 64, 0)   # placeholder until a lane binds it
# builder lane C: design plus scaffold within 11 x 11, scaffold at most 3 west and 2 east of the footprint (relative to
# ORIGIN, as the scaffold is)
SITE = {"plot": {"min": {"x": -3, "z": -3}, "max": {"x": 6, "z": 7}}}


def build(gd) -> Design:
    g = Design((5, len(LAYERS), 6), ORIGIN)   # z 5: the doorstep
    for y, rows in enumerate(LAYERS):
        for z, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch != ".":
                    g.set(x, y, z, *KEY[ch])
    bad = g.canonicalize(gd)
    assert not bad, bad
    g.rooms = [{"id": "main", "type": "storage", "min": {"x": 1, "y": 1, "z": 1}, "max": {"x": 3, "y": 6, "z": 3},
                "door_pos": {"x": 2, "y": 1, "z": 4}}]
    g.meta = {"project_id": "hut5_tall", "version": 1, "world_id": None, "dim": "minecraft:overworld", "style": None,
              "provenance": {"source": "hand-built (examples/hut5_tall.py)", "compiler_version": None, "seed": None,
                             "schematic": True}}
    return g


def main(out: Path = Path(__file__).parent / "out" / "hut5_tall") -> None:
    gd = gamedata.load()
    g = build(gd)
    s, left = scaffold.plan(g, gd, SITE, interior=True)   # O89: the one fixture with scaffold inside a room
    assert not left, left
    out.mkdir(parents=True, exist_ok=True)
    g.save(out / "design.json")
    s.save(out / "scaffold.json")
    save_litematic(g, out / "design.litematic", gd, name="hut5_tall", description=f"Eve hut5_tall v1 {g.hash()[:12]}")
    b = bom.compute(g, gd, s)
    (out / "bom.json").write_text(json.dumps(b, indent=1) + "\n")
    order = buildorder.compact(g.hash(), buildorder.plan_order(g, gd, s, site=SITE)[0])
    (out / "build_order.json").write_text(json.dumps(order, separators=(",", ":")) + "\n")
    v = validate(g, gd, bom=b, scaffold=s)
    (out / "validation.json").write_text(json.dumps(v, indent=1) + "\n")
    preview.render(g, gd, out / "previews")
    d = gate.decide(out)
    print(f"hut5_tall {g.hash()} scaffold {sum(s.counts().values())} valid={v['passed']} "
          f"gate={d['category']}/{d['decision']} -> {out}")


if __name__ == "__main__":
    main(*(Path(a) for a in sys.argv[1:]))
