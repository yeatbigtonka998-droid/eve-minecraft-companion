"""CP-57 (K part): the hand-authored buildings compile, export, and pass every grid-derived check with their
generated scaffold plans. Whether they differ meaningfully to a person is Hunter's judgement (J part)."""
from pathlib import Path

import pytest

from eve_design import gamedata
from eve_design.compiler import compile_file
from eve_design.grid import Design
from eve_design.schem_io import load_litematic, save_litematic
from eve_design.validate import validate

GD = gamedata.load()
EX = Path(__file__).parents[1] / "examples"
NAMES = ["cabin", "two_storey", "wing_house", "modern_asym", "tower"]
VALID = NAMES   # tower: re-planned with the O87h planner (1,110 dirt; was a stale pre-margin plan, an expected failure)


def committed_scaffold(name, g):
    p = EX / "out" / f"{name}_v2" / "scaffold.json"   # O243: v1's plans had stale stands (O94ag-b); v1 kept in history
    s = Design.load(p) if p.exists() else None
    assert s is None or s.meta["provenance"]["scaffold_for"] == g.hash(), "examples/out is stale: regenerate it"
    return s


@pytest.mark.parametrize("name", VALID)
def test_example_valid_and_exports(name, tmp_path):
    g = compile_file(EX / f"{name}.eve", GD)
    s = committed_scaffold(name, g)          # planned when examples/out was regenerated (minutes for big houses)
    v = validate(g, GD, scaffold=s)
    assert v["passed"], {k: c["failures"][:3] for k, c in v["checks"].items() if c["status"] == "fail"}
    assert g.rooms and all(r["door_pos"] for r in g.rooms)
    save_litematic(g, tmp_path / "x.litematic", GD)
    assert load_litematic(tmp_path / "x.litematic", GD)[0].same_blocks(g)


def test_examples_differ_in_shape_not_just_palette():
    gs = {n: compile_file(EX / f"{n}.eve", GD) for n in NAMES}
    storeys = {n: max(r.get("storey", 0) for r in g.rooms) + 1 for n, g in gs.items()}
    assert storeys == {"cabin": 1, "two_storey": 2, "wing_house": 2, "modern_asym": 2, "tower": 6}
    assert {r["part"] for r in gs["wing_house"].rooms} == {"main", "annex"}
    assert {r["part"] for r in gs["modern_asym"].rooms} == {"main", "studio"}
    assert len({g.size for g in gs.values()}) == len(NAMES)
    # the tower steps in: its top storey's room is smaller than the ground storey's
    t = gs["tower"].rooms
    area = lambda r: (r["max"]["x"] - r["min"]["x"] + 1) * (r["max"]["z"] - r["min"]["z"] + 1)
    assert area(t[-1]) < area(t[0])
    # stairs exist wherever there is more than one storey
    for n, g in gs.items():
        has_stairs = any(r == "stair" for *_, r in g.cells())
        assert has_stairs == (storeys[n] > 1), n


ROUND2 = [pytest.param("cabin_dormer", marks=pytest.mark.xfail(strict=True, reason=(
              "O80h aim margin: the dormer-junction stairs (5..7,7,3) where the north dormer meets the main roof have no "
              "click that stays on its face under the crosshair's tolerance from any stand; open (planner or compiler)"))),
          "two_storey_landing", "courtyard_house", "medieval_house"]


@pytest.mark.parametrize("name", ROUND2)
def test_round2_example_valid(name, tmp_path):
    test_example_valid_and_exports(name, tmp_path)


def _states(g):
    return [(x, y, z, s, r) for x, y, z, s, r in g.cells()]


def test_dormer_and_gabled_porch():
    g = compile_file(EX / "cabin_dormer.eve", GD)
    plain = compile_file(EX / "cabin.eve", GD)
    wall_top = max(y for x, y, z, s, r in _states(plain) if r == "wall" and z == 1)    # north wall row of the cabin
    dormer_windows = [(x, y, z) for x, y, z, s, r in _states(g) if r == "window" and y > wall_top]
    assert dormer_windows, "the dormer has a window above the wall top"
    porch_roof = {s.split("[")[0] for x, y, z, s, r in _states(g) if r == "roof" and z > 9}
    assert "minecraft:dark_oak_stairs" in porch_roof and "minecraft:dark_oak_planks" in porch_roof   # slopes + ridge


def test_stairs_turn_at_a_landing():
    g = compile_file(EX / "two_storey_landing.eve", GD)
    steps = [(x, y, z, s) for x, y, z, s, r in _states(g) if r == "stair"]
    facings = {s.split("facing=")[1].split(",")[0] for *_, s in steps if "_stairs" in s}
    assert facings == {"east", "south"}                     # first run east, then turned into the room
    assert any("_stairs" not in s for *_, s in steps)       # the level landing block
    assert {r["storey"] for r in g.rooms} == {0, 1}          # upstairs rooms reachable: validated above


def test_courtyard_gate_is_the_way_in():
    src = (EX / "courtyard_house.eve").read_text()
    g = compile_file(EX / "courtyard_house.eve", GD)
    gate = [(x, y, z) for x, y, z, s, r in _states(g) if "fence_gate" in s]
    assert len(gate) == 1
    x, y, z = gate[0]
    for yy in (y, y + 1):                  # brick the whole gap up (only the gate cell would leave a jumpable step)
        g.set(x, yy, z, GD.canonical("minecraft:stone_bricks"), "wall")
    v = validate(g, GD, skip_buildable=True)
    assert v["checks"]["walkable"]["status"] == "fail"
    assert all("cannot be reached" in f["reason"] for f in v["checks"]["walkable"]["failures"])
