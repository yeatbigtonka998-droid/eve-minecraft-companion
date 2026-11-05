"""CP-59 (K part): each detailing rule alone, then all together, keeps every check passing and does what the style
card says; a recorded seed reproduces the palette placement; rules can be switched off."""
from collections import Counter
from pathlib import Path

import pytest

from eve_design import gamedata, scaffold
from eve_design.compiler import compile_program
from eve_design.validate import validate

GD = gamedata.load()
EX = Path(__file__).parents[1] / "examples"
BASES = {n: (EX / f"{n}.eve").read_text() for n in ("cabin", "two_storey", "wing_house")}
RULES = ["pillars", "base_band", "gradient", "shutters", "roof_trim", "style"]


def compiled(src, extra):
    return compile_program(src + "\n" + extra, GD)


def interior(g):
    return {(x, y, z): (g.get(x, y, z), g.role_at(x, y, z)) for r in g.rooms
            for y in range(r["min"]["y"], r["max"]["y"] + 1) for z in range(r["min"]["z"], r["max"]["z"] + 1)
            for x in range(r["min"]["x"], r["max"]["x"] + 1)}


@pytest.mark.parametrize("base", BASES)
@pytest.mark.parametrize("rule", RULES)
def test_rule_keeps_design_valid(base, rule):
    plain = compile_program(BASES[base], GD)
    g = compiled(BASES[base], f'x.detail("{rule}")'.replace("x.", BASES[base].split("=")[0].strip() + "."))
    if base == "cabin":                      # the full buildable check (scaffold planning: seconds for the cabin)
        s, left = scaffold.plan(g, GD)
        v = validate(g, GD, scaffold=s)
    else:                                    # minutes per rule on two storeys; their buildability: test_examples
        left, v = [], validate(g, GD, skip_buildable=True)
    assert not left and v["passed"], {k: c["failures"][:3] for k, c in v["checks"].items() if c["status"] == "fail"}
    # rooms, doors and their purpose are unchanged; interiors are untouched
    assert [(r["id"], r["type"]) for r in g.rooms] == [(r["id"], r["type"]) for r in plain.rooms]
    if g.size == plain.size:
        assert interior(g) == interior(plain)
    expect = ["pillars", "base_band", "gradient", "shutters", "roof_trim"] if rule == "style" else [rule]
    assert g.meta["provenance"]["detailing_rules"] == expect


def var(src):
    return src.split("=")[0].strip()


def test_each_rule_does_what_the_card_says():
    src, v = BASES["cabin"], var(BASES["cabin"])
    plain = compile_program(src, GD)
    cnt = lambda g: Counter(s.split("[")[0] for *_, s, _ in ((x, y, z, s, r) for x, y, z, s, r in g.cells()))
    p = compiled(src, f'{v}.detail("pillars")')
    assert sum(1 for *_, r in p.cells() if r == "support") > sum(1 for *_, r in plain.cells() if r == "support")
    b = compiled(src, f'{v}.detail("base_band")')
    off = b.meta["provenance"]["footprint_offset"]          # exterior ring of the 11 x 9 footprint
    band = [s for x, y, z, s, r in b.cells() if y == 1 and r in ("wall", "support")
            and ((x - off[0] in (0, 10) and 0 <= z - off[2] <= 8) or (z - off[2] in (0, 8) and 0 <= x - off[0] <= 10))]
    assert band and all(s == "minecraft:cobblestone" for s in band)
    sh = compiled(src, f'{v}.detail("shutters")')
    assert cnt(sh)["minecraft:spruce_trapdoor"] > 0 and cnt(plain)["minecraft:spruce_trapdoor"] == 0
    t = compiled(src, f'{v}.detail("roof_trim")')
    assert cnt(t)["minecraft:spruce_stairs"] > 0 and cnt(t)["minecraft:dark_oak_stairs"] < cnt(plain)["minecraft:dark_oak_stairs"]


def test_gradient_seeded_and_weighted():
    src, v = BASES["cabin"], var(BASES["cabin"])
    a = compiled(src, f'{v}.detail("gradient")')
    b = compiled(src, f'{v}.detail("gradient")')
    c = compiled(src.replace("seed=42", "seed=43"), f'{v}.detail("gradient")')
    assert a.hash() == b.hash() and a.hash() != c.hash()
    walls = Counter(s.split("[")[0] for x, y, z, s, r in a.cells() if r == "wall")
    n = walls["minecraft:spruce_planks"] + walls["minecraft:stripped_spruce_wood"]
    assert n > 100 and 0.55 < walls["minecraft:spruce_planks"] / n < 0.85          # card: 70 / 30
    found = Counter(s.split("[")[0] for x, y, z, s, r in a.cells() if r == "foundation")
    assert {"minecraft:cobblestone", "minecraft:mossy_cobblestone", "minecraft:stone_bricks"} <= set(found)
    floor = {s for x, y, z, s, r in a.cells() if r == "floor"}
    assert floor == {s for x, y, z, s, r in compile_program(src, GD).cells() if r == "floor"}   # floors keep their block


def test_rules_can_be_switched_off():
    src, v = BASES["cabin"], var(BASES["cabin"])
    g = compiled(src, f'{v}.detail("style")\n{v}.detail("gradient", on=False)\n{v}.detail("pillars", on=False)')
    assert g.meta["provenance"]["detailing_rules"] == ["base_band", "shutters", "roof_trim"]
    names = {s.split("[")[0] for *_, s, _ in g.cells()}
    assert "minecraft:stripped_spruce_wood" not in names and "minecraft:mossy_cobblestone" not in names


def test_panes_connect_to_full_faces_of_partial_blocks():
    """CP-49 attempt 3: a closed door facing south has its flat side on the north; the pane north of it connects."""
    from eve_design.compiler import full_sides
    assert full_sides("minecraft:oak_door[facing=south,half=lower,hinge=left,open=false,powered=false]") == {"north"}
    assert full_sides("minecraft:oak_door[facing=south,half=lower,hinge=right,open=true,powered=false]") == {"west"}
    assert full_sides("minecraft:oak_door[facing=south,half=lower,hinge=left,open=true,powered=false]") == {"east"}
    assert full_sides("minecraft:oak_stairs[facing=east,half=bottom,shape=straight,waterlogged=false]") == {"east"}
    assert full_sides("minecraft:oak_slab[type=double,waterlogged=false]") == {"north", "south", "east", "west"}
    assert full_sides("minecraft:oak_slab[type=bottom,waterlogged=false]") == set()
