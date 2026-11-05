"""O150: a nether portal frame for the owner's "build a nether portal": obsidian, 4 wide x 5 tall, upright, one block deep.

    python examples/nether_portal.py   -> examples/out/nether_portal/ (v1: 10 obsidian, corners omitted)
                                          examples/out/nether_portal_v2/ (v2: 14 obsidian, corners filled)
    python examples/nether_portal.py 3 -> examples/out/nether_portal_v3/ (v3: v2's blocks, same hash; planned with
                                          builder's five-eye click check, O193: v2's top corner went manual)

Catalogue template (kickoff O132 ruling 1a): category "schematic" like hut5 v1 (O61), so the gate reports its checks
without holding it. Origin = the bottom-left front block (min x, the bottom row, z = 0); the frame faces south (+z), where
the mind keeps 2 blocks clear (brief.json clearance_front). The 2 x 3 inside stays air; lighting it is the owner's
"light the portal" afterwards (flint and steel), never part of the build.
"""
import json
import sys
from pathlib import Path

from eve_design import bom, buildorder, gamedata, gate, preview, scaffold
from eve_design.grid import Design
from eve_design.schem_io import save_litematic
from eve_design.validate import validate

OBS = ("minecraft:obsidian", "wall")
ORIGIN = (0, 64, 0)   # placeholder; the mind passes the confirmed site as build_schematic's origin
NAMES = {1: ["nether portal", "portal", "obsidian portal", "nether portal frame", "portal frame"],
         2: ["nether portal with corners", "full nether portal", "portal with corners", "corner portal"]}
NAMES[3] = NAMES[2]   # O193: v3 is v2 re-planned


def build_portal(gd, version=1) -> Design:
    g = Design((4, 5, 1), ORIGIN)
    for x in range(4):
        for y in range(5):
            edge_x, edge_y = x in (0, 3), y in (0, 4)
            if (edge_x or edge_y) and (version >= 2 or not (edge_x and edge_y)):
                g.set(x, y, 0, *OBS)
    bad = g.canonicalize(gd)
    assert not bad, bad
    g.rooms = []
    g.meta = {"project_id": "nether_portal", "version": version, "world_id": None, "dim": "minecraft:overworld",
              "style": None, "provenance": {"source": f"hand-built (examples/nether_portal.py v{version}, O150)",
                                            "compiler_version": None, "seed": None, "schematic": True}}
    if version >= 3:     # O193: provenance, outside the design hash; placecheck mirrors builder's clickableAround
        g.meta["provenance"]["click_eyes"] = 5
    return g


def main(version=1) -> None:
    gd = gamedata.load()
    out = Path(__file__).parent / "out" / ("nether_portal" if version == 1 else f"nether_portal_v{version}")
    g = build_portal(gd, version)
    s = scaffold.plan(g, gd)[0]
    out.mkdir(parents=True, exist_ok=True)
    g.save(out / "design.json")
    s.save(out / "scaffold.json")
    save_litematic(g, out / "design.litematic", gd, name="nether_portal",
                   description=f"Eve nether_portal v{version} {g.hash()[:12]}")
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
    brief = {"purpose": "nether portal frame", "names": NAMES[version], "style": "obsidian",
             "approval_category": "schematic", "authored_by": "eve-build-flow (O150)", "clearance_front": 2,
             "level_word": "bottom", "done_say": "Frame done; say 'light the portal' when you have flint and steel."}
    (out / "brief.json").write_text(json.dumps(brief, indent=1) + "\n")   # after the gate: catalogue tags + site rules
    print(f"nether_portal v{version} {g.hash()} scaffold {sum(s.counts().values())} valid={v['passed']} "
          f"failed={[k for k, c in v['checks'].items() if c['status'] == 'fail']} gate={d['category']}/{d['decision']} "
          f"-> {out}")


if __name__ == "__main__":
    for ver in (int(a) for a in (sys.argv[1:] or ["1", "2", "3"])):
        main(ver)
