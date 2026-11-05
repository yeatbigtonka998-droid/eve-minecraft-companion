"""BUILD_SCHEMATIC.md section 4 as data: the 3x3x3 CP-49 fixture's order equals the hand-written order."""
import json
import runpy
from pathlib import Path

from eve_design import buildorder, gamedata
from eve_design.grid import Design
from eve_design.compiler import compile_file
from eve_design.validate import validate

GD = gamedata.load()
D = Path(__file__).parents[1]
EXP = json.loads((D / "tests/fixtures/test3/expected_order.json").read_text())
build = runpy.run_path(str(D / "examples/test3.py"))["build"]


def test_test3_order_matches_hand_order():
    g = build(GD)
    steps = buildorder.plan_order(g, GD)[0]          # the simulation keeps the door in its layer here
    assert [s["pos"] for s in steps] == EXP["order"]
    assert {str(s["step"]): s["after"] for s in steps if s["after"]} == EXP["after"]
    assert {str(s["step"]): s["also_appears"] for s in steps if s["also_appears"]} == EXP["also_appears"]
    assert {str(s["step"]) for s in steps if s.get("note")} == set(EXP["notes"])
    assert sum(len(s["also_appears"]) for s in steps) + len(steps) == sum(g.counts().values())   # every cell covered once


def test_test3_is_a_valid_design():
    g = build(GD)
    v = validate(g, GD)
    assert v["checks"]["stable"]["status"] == "pass" and v["checks"]["buildable"]["status"] == "pass"


def test_every_example_orders_cleanly_with_scaffold_first_and_last():
    for name in ("cabin", "two_storey_landing", "tower"):
        g = compile_file(D / f"examples/{name}.eve", GD)
        s = Design.load(D / f"examples/out/{name}/scaffold.json")
        assert s.meta["provenance"]["scaffold_for"] == g.hash()
        steps = buildorder.order(g, GD, s)
        layers = [st["layer"] for st in steps]
        n = sum(s.counts().values())
        assert layers.count("scaffold") == n and layers[-n:] == ["scaffold_removal"] * n
        for i, st in enumerate(steps):      # a scaffold block comes before every final block of its layer and above
            if st["layer"] == "scaffold":
                assert all(o["pos"][1] < st["pos"][1] for o in steps[:i] if o["layer"] == "final" and o["pos"][1] >= st["pos"][1]) \
                    or all(o["pos"][1] < st["pos"][1] for o in steps[:i] if o["layer"] == "final")
        finals = [st for st in steps if st["layer"] == "final"]
        covered = len(finals) + sum(len(st["also_appears"]) for st in finals)
        assert covered == sum(g.counts().values())
        done = set()
        for st in steps:                    # supports always come first, scaffold included
            if st["layer"] == "scaffold_removal":
                break
            assert st["after"] is None or tuple(st["after"]) in done, st
            done.add(tuple(st["pos"]))
            done.update(tuple(p) for p in st["also_appears"])


def test_a_block_with_nothing_to_place_against_is_listed_last_not_raised():
    """45L attempt 5 crashed on order() raising; the order now lists such blocks last and the simulation fails them."""
    g = Design((3, 3, 1), (0, 64, 0))
    g.set(0, 0, 0, "minecraft:stone", "wall")
    g.set(2, 2, 0, "minecraft:stone", "wall")                # touches nothing
    g.canonicalize(GD)
    steps = buildorder.order(g, GD)
    assert steps[-1]["pos"] == [2, 2, 0] and steps[-1]["note"] == "nothing to place it against"
