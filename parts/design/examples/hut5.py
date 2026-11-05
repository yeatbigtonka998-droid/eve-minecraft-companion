"""The original 5x5 hut (build plan, CP-48/CP-50), built by hand layer by layer: no building language.

    python examples/hut5.py   -> examples/out/hut5/ and hut5_sim/ (design, litematic, planned scaffold, bom, validation,
                                 previews, build_order with stands, gate.json category "schematic")

CP-50 treats this hut as an owner-provided schematic (O61): gate category "schematic", decision n/a, no critic. Both
folders carry the scaffold the placement replay needs; the CP-48 hand plan (2 dirt) stays in build_hut() for the hand
BOM count and as the progress-vector fixture tests/fixtures/progress/hut5_hand_scaffold.json.

Layers are drawn north (z=0) at the top, west (x=0) on the left; y=0 is the foundation on the ground.

v1 (hash 90a0175d..., hut5 and hut5_sim) is frozen: the proved CP-50 checkpoint, the contract's hash vector and the lane
pins. It is NOT ENTERABLE (no doorstep): its door stands on the floor, a step up from the ground, and Eve's Baritone
enters a door only on a level move (O89i). It is a v1 checkpoint only. O94x: anything meant to be lived in builds from v2,
`python examples/hut5.py v2` -> examples/out/hut5_sim_v2: the same hut with a doorstep at (2,0,5), version 2, its own
hash, not a schematic, so the gate holds it until an owner approval.json (O94q) lets it build.
"""
import json
import sys
from pathlib import Path

from eve_design import bom, buildorder, gamedata, gate, preview, scaffold
from eve_design.grid import Design
from eve_design.schem_io import save_litematic
from eve_design.validate import validate

STAIR = "minecraft:oak_stairs[facing={},half=bottom,shape=straight]"
KEY = {  # char: (state, role)
    "C": ("minecraft:cobblestone", "foundation"),
    "L": ("minecraft:oak_log[axis=y]", "support"),
    "P": ("minecraft:oak_planks", "wall"),
    "R": ("minecraft:oak_planks", "roof"),
    "H": ("minecraft:white_bed[facing=north,part=head,occupied=false]", "furniture"),
    "F": ("minecraft:white_bed[facing=north,part=foot,occupied=false]", "furniture"),
    "T": ("minecraft:crafting_table", "furniture"),
    "K": ("minecraft:chest[facing=west,type=single]", "furniture"),
    "D": ("minecraft:oak_door[facing=north,half=lower,hinge=left,open=false,powered=false]", "door"),
    "U": ("minecraft:oak_door[facing=north,half=upper,hinge=left,open=false,powered=false]", "door"),
    "G": ("minecraft:glass_pane[east=true,west=true,north=false,south=false]", "window"),
    "g": ("minecraft:glass_pane[east=false,west=false,north=true,south=true]", "window"),
    "t": ("minecraft:wall_torch[facing=west]", "light"),
    "n": (STAIR.format("south"), "roof"), "s": (STAIR.format("north"), "roof"),
    "w": (STAIR.format("east"), "roof"), "e": (STAIR.format("west"), "roof"),
    "d": ("minecraft:oak_slab[type=double]", "roof"),
    "b": ("minecraft:oak_slab[type=bottom]", "roof"),
}
LAYERS = [
    ["CCCCC", "CCCCC", "CCCCC", "CCCCC", "CCCCC"],   # y0 foundation / floor
    ["LPPPL", "PH.TP", "PF.KP", "P...P", "LPDPL"],   # y1 bed, table, chest, door
    ["LPGPL", "P...P", "g...g", "P..tP", "LPUPL"],   # y2 windows, torch, door top
    ["LPPPL", "P...P", "P...P", "P...P", "LPPPL"],   # y3
    ["dnnnd", "wRRRe", "wRRRe", "wRRRe", "dsssd"],   # y4 stair ring, double-slab corners, plank ceiling
    [".....", ".bbb.", ".bbb.", ".bbb.", "....."],   # y5 slab cap
]
ORIGIN = (0, 64, 0)   # placeholder until a site is chosen (the mind binds the real origin at approval)


def build_hut(gd) -> tuple[Design, Design]:
    g = Design((5, len(LAYERS), 5), ORIGIN)
    for y, rows in enumerate(LAYERS):
        for z, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch != ".":
                    g.set(x, y, z, *KEY[ch])
    bad = g.canonicalize(gd)
    assert not bad, bad
    g.rooms = [{"id": "main", "type": "bedroom", "min": {"x": 1, "y": 1, "z": 1}, "max": {"x": 3, "y": 3, "z": 3},
                "door_pos": {"x": 2, "y": 1, "z": 4}}]
    g.meta = {"project_id": "hut5", "version": 1, "world_id": None, "dim": "minecraft:overworld", "style": None,
              "provenance": {"source": "hand-built (examples/hut5.py)", "compiler_version": None, "seed": None,
                             "schematic": True}}
    # Scaffold: hand plan, 2 dirt as a step south-west of the door (x=1, z=5) to reach the south roof edge
    # from outside. Not computed: the survival-buildability check (CP-60) will derive scaffold plans.
    s = Design((1, 2, 1), (ORIGIN[0] + 1, ORIGIN[1], ORIGIN[2] + 5))
    s.set(0, 0, 0, "minecraft:dirt", "support")
    s.set(0, 1, 0, "minecraft:dirt", "support")
    s.canonicalize(gd)
    s.meta = {"project_id": "hut5", "version": 1, "provenance": {"scaffold_for": g.hash(), "plan": "hand"}}
    return g, s


def build_hut_v2(gd) -> Design:
    """O94x: v1 plus a cobblestone doorstep at floor height in front of the door, (2,0,5): a level way in. Version 2,
    not a schematic checkpoint: Eve's own gate holds it (no brief, no critic) until the owner approves it (O94q)."""
    v1, _ = build_hut(gd)
    g = Design((v1.size[0], v1.size[1], v1.size[2] + 1), ORIGIN)
    for x, y, z, st, r in v1.cells():
        g.set(x, y, z, st, r)
    g.set(2, 0, 5, *KEY["C"])
    bad = g.canonicalize(gd)
    assert not bad, bad
    g.rooms = v1.rooms
    g.meta = {**v1.meta, "version": 2,
              "provenance": {"source": "hand-built (examples/hut5.py, v2: v1 + doorstep at (2,0,5), O94x)",
                             "compiler_version": None, "seed": None}}
    return g


def write(out: Path, g: Design, s: Design, gd) -> None:
    out.mkdir(parents=True, exist_ok=True)
    g.save(out / "design.json")
    s.save(out / "scaffold.json")
    save_litematic(g, out / "design.litematic", gd, name="hut5", description=f"Eve hut5 v{g.meta['version']} {g.hash()[:12]}")
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
    print(f"hut5 {g.hash()} scaffold {sum(s.counts().values())} valid={v['passed']} gate={d['category']}/{d['decision']} "
          f"-> {out}")


def main(out: Path = Path(__file__).parent / "out" / "hut5") -> None:
    gd = gamedata.load()
    g, _hand = build_hut(gd)
    s = scaffold.plan(g, gd)[0]               # the scaffold the placement replay needs (dirt staircases)
    for name in ("hut5", "hut5_sim"):         # the same hut under both names builder's runs use
        write(out.parent / name, g, s, gd)


def main_v2(out: Path = Path(__file__).parent / "out" / "hut5_sim_v2") -> None:
    """Writes only v2 (the frozen v1 folders are never re-emitted from here)."""
    gd = gamedata.load()
    g = build_hut_v2(gd)
    write(out, g, scaffold.plan(g, gd)[0], gd)


if __name__ == "__main__":
    if sys.argv[1:2] == ["v2"]:
        main_v2(*(Path(a) for a in sys.argv[2:]))
    else:
        main(*(Path(a) for a in sys.argv[1:]))
