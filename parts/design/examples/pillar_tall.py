"""A 3x3 stone-brick column 12 high (a chimney / lookout) for builder CP-53's tall-scaffold test, on the End fixture's
island A (lane C): plot x 400..409, z -6..6, floor y 64, void to the east; the kit chest at (402, 65, 0) stays clear.

    python examples/pillar_tall.py   -> examples/out/pillar_tall/{design.json, design.litematic, scaffold.json, bom.json,
                                          build_order.json (stand/interior per step), validation.json, brief.json}

The scaffold comes from the staircase planner restricted to the plot (the site's `obstacles` are treated as blocks).
"""
import json
import sys
from pathlib import Path

from eve_design import bom, buildorder, gamedata, scaffold
from eve_design.grid import Design
from eve_design.schem_io import save_litematic
from eve_design.validate import validate

ORIGIN = (405, 65, -1)
HEIGHT = 12
SITE = {"plot": {"min": {"x": 400, "z": -6}, "max": {"x": 409, "z": 6}}, "height_limit": 120,
        "origin": dict(zip("xyz", ORIGIN)), "terrain_change": 0, "obstacles": [[402, 65, 0]]}
BRIEF = {"project_id": "pillar_tall", "purpose": "tall-scaffold test (builder CP-53), End island A", "site": SITE}


def build(gd) -> Design:
    g = Design((3, HEIGHT, 3), ORIGIN)
    for y in range(HEIGHT):
        for z in range(3):
            for x in range(3):
                g.set(x, y, z, "minecraft:stone_bricks", "wall")
    assert not g.canonicalize(gd)
    g.meta = {"project_id": "pillar_tall", "version": 1,
              "provenance": {"source": "hand-built CP-53 fixture (examples/pillar_tall.py)"}}
    return g


def main(out: Path = Path(__file__).parent / "out" / "pillar_tall") -> None:
    gd = gamedata.load()
    g = build(gd)
    s, left = scaffold.plan(g, gd, SITE)
    assert not left, left
    out.mkdir(parents=True, exist_ok=True)
    g.save(out / "design.json")
    s.save(out / "scaffold.json")
    save_litematic(g, out / "design.litematic", gd, name="pillar_tall")
    (out / "brief.json").write_text(json.dumps(BRIEF, indent=1) + "\n")
    b = bom.compute(g, gd, s)
    (out / "bom.json").write_text(json.dumps(b, indent=1) + "\n")
    order = buildorder.compact(g.hash(), buildorder.plan_order(g, gd, s, site=SITE)[0])
    (out / "build_order.json").write_text(json.dumps(order, separators=(",", ":")) + "\n")
    (out / "validation.json").write_text(json.dumps(validate(g, gd, brief=BRIEF, bom=b, scaffold=s), indent=1) + "\n")
    print(f"pillar_tall {g.hash()} scaffold {sum(s.counts().values())} -> {out}")


if __name__ == "__main__":
    main(*(Path(a) for a in sys.argv[1:]))
