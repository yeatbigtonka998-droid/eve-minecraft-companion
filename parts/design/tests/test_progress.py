"""CP-48 identity clause, design side: the shared progress test vector."""
import json
from pathlib import Path

from eve_design import bom, gamedata, progress
from eve_design.grid import Design

GD = gamedata.load()
D = Path(__file__).parents[1]
VEC = json.loads((D / "tests/fixtures/progress/hut5_half.json").read_text())


def test_remaining_items_after_half_the_hut():
    g, s = Design.load(D / VEC["design"]), Design.load(D / VEC["scaffold"])
    assert g.hash() == VEC["design_hash"] and s.meta["provenance"]["scaffold_for"] == g.hash()
    ips = bom.compute(g, GD, s)["items_per_state"]
    status = {("final", i): "placed" for i in progress.cells(g) if progress.cell_pos(g, i)[1] <= 1}
    assert dict(progress.remaining_want(g, s, status, ips)) == VEC["expected_remaining"]
    # nothing placed -> the full bill; everything placed and scaffold removed -> nothing
    full = progress.remaining_want(g, s, {}, ips)
    b = bom.compute(g, GD, s)
    assert dict(full) == {r["item"]: r["count"] for r in b["final"] + b["scaffold"]}
    done = {("final", i): "verified" for i in progress.cells(g)} | {("scaffold", i): "removed" for i in progress.cells(s)}
    assert not progress.remaining_want(g, s, done, ips)


def test_index_round_trip():
    g = Design.load(D / VEC["design"])
    for i in progress.cells(g):
        assert progress.cell_index(g, *progress.cell_pos(g, i)) == i
