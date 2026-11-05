"""A 3x3x3 placement fixture for body's CP-49: one of each state family whose orientation or support matters.

    python examples/test3.py   -> examples/out/test3/ (design, litematic, hand step scaffold, bom, validation, previews,
                                  build_order with stands, gate.json category "schematic": an owner-provided fixture, O61)

Layers are drawn north (z=0) at the top, west (x=0) on the left; y=0 sits on the ground.
"""
import json
import sys
from pathlib import Path

from eve_design import bom, buildorder, gamedata, gate, preview
from eve_design.grid import Design
from eve_design.schem_io import save_litematic
from eve_design.validate import validate

KEY = {  # char: (state, role)
    "C": ("minecraft:cobblestone", "foundation"),
    "X": ("minecraft:oak_log[axis=x]", "support"),
    "Z": ("minecraft:oak_log[axis=z]", "support"),
    "P": ("minecraft:oak_planks", "wall"),
    "s": ("minecraft:oak_stairs[facing=east,half=bottom]", "stair"),
    "t": ("minecraft:oak_stairs[facing=south,half=top]", "stair"),
    "d": ("minecraft:oak_slab[type=double]", "roof"),
    "b": ("minecraft:oak_slab[type=bottom]", "roof"),
    "u": ("minecraft:oak_slab[type=top]", "roof"),
    "G": ("minecraft:glass_pane[east=true,west=true,north=false,south=true]", "window"),   # south: the door's flat side
    "D": ("minecraft:oak_door[facing=south,half=lower,hinge=left]", "door"),
    "E": ("minecraft:oak_door[facing=south,half=upper,hinge=left]", "door"),
    "T": ("minecraft:oak_trapdoor[facing=north,half=bottom,open=true]", "trim"),
    "w": ("minecraft:wall_torch[facing=south]", "light"),
    "h": ("minecraft:lantern[hanging=true]", "light"),
    "l": ("minecraft:lantern[hanging=false]", "light"),
}
LAYERS = [
    ["CXZ", "sCd", "bPC"],   # y0: logs on two axes, stairs, double and bottom slab
    ["PGP", "TDw", ".hl"],   # y1: pane between planks, open trapdoor, door, wall torch on the plank north of it, lanterns
    ["tPu", ".E.", ".P."],   # y2: upside-down stairs, top slab, door top, the plank the hanging lantern hangs from
]


def build(gd) -> Design:
    g = Design((3, 3, 3), (0, 64, 0))
    for y, rows in enumerate(LAYERS):
        for z, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch != ".":
                    g.set(x, y, z, *KEY[ch])
    bad = g.canonicalize(gd)
    assert not bad, bad
    g.meta = {"project_id": "test3", "version": 1, "provenance": {"source": "hand-built CP-49 fixture (examples/test3.py)",
                                                                  "schematic": True}}
    return g


def step(g: Design, gd) -> Design:
    """Hand scaffold (builder CP-49 attempt 2): a 1-high dirt row one block north of the fixture. Standing on it
    (eye y+2.62) the tops of the y=1 blocks show, so layer y=2 can be placed from outside the footprint."""
    s = Design((3, 1, 1), (g.origin[0], g.origin[1], g.origin[2] - 1))
    for x in range(3):
        s.set(x, 0, 0, "minecraft:dirt", "support")
    s.canonicalize(gd)
    s.meta = {"project_id": "test3", "version": 1, "provenance": {"scaffold_for": g.hash(), "plan": "hand"}}
    return s


def main(out: Path = Path(__file__).parent / "out" / "test3") -> None:
    gd = gamedata.load()
    g = build(gd)
    s = step(g, gd)
    out.mkdir(parents=True, exist_ok=True)
    g.save(out / "design.json")
    s.save(out / "scaffold.json")
    save_litematic(g, out / "design.litematic", gd, name="test3")
    b = bom.compute(g, gd, s)
    (out / "bom.json").write_text(json.dumps(b, indent=1) + "\n")
    order = buildorder.compact(g.hash(), buildorder.plan_order(g, gd, s)[0])
    (out / "build_order.json").write_text(json.dumps(order, separators=(",", ":")) + "\n")
    v = validate(g, gd, bom=b, scaffold=s)
    (out / "validation.json").write_text(json.dumps(v, indent=1) + "\n")
    preview.render(g, gd, out / "previews")
    d = gate.decide(out)
    print(f"test3 {g.hash()} valid={v['passed']} gate={d['category']}/{d['decision']} -> {out}")


if __name__ == "__main__":
    main(*(Path(a) for a in sys.argv[1:]))
