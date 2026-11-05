"""CP-35 — recipe trees and substitutions against the pinned 26.3 data (data/game/26.3, from the design chat's
extractor). Expected counts were worked out by hand from the recipe files, not from the solver:
  planks: 1 log -> 4; sticks: 2 planks -> 4; wooden pickaxe: 3 planks + 2 sticks; chest/crafting table: 8/4 planks;
  stone bricks: 4 stone -> 4; stairs: 6 -> 4; stone: smelt 1 cobblestone; 1 coal smelts 8; iron armour 5/8/7/4 ingots;
  torch: 1 coal|charcoal + 1 stick -> 4; bread: 3 wheat; iron_block <-> 9 ingots, ingot <-> 9 nuggets.
Skips (BLOCKED) when the game data has not been extracted."""
import pytest

from eve_mind.config import ROOT
from eve_mind.recipes import DEFAULT_KINDS, RecipeBook, solve

DATA = ROOT / "data" / "game" / "26.3"
pytestmark = pytest.mark.skipif(not (DATA / "MANIFEST.json").exists(), reason="BLOCKED: data/game/26.3 not extracted")
STONECUT = DEFAULT_KINDS + ("stonecutting",)
PLANKS = ["oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "pale_oak", "crimson", "warped", "mangrove",
          "bamboo", "cherry", "poplar"]


def m(d):
    return {f"minecraft:{k}": v for k, v in d.items()}


# name: (want, inventory, options, expected {missing, consumed, leftover, smelt, fuel, stations, impossible})
CASES = {
    "wooden_pickaxe_batch_leftovers": ({"wooden_pickaxe": 1}, {}, {},
                                       dict(missing={"oak_log": 2}, consumed={}, leftover={"oak_planks": 3, "stick": 2},
                                            smelt=0, stations=["crafting_table"])),
    "two_pickaxes_exact_batches": ({"wooden_pickaxe": 2}, {}, {}, dict(missing={"oak_log": 2}, leftover={})),
    "mixed_wood_allowed": ({"crafting_table": 1}, {"oak_planks": 2, "spruce_planks": 2}, {},
                           dict(missing={}, consumed={"oak_planks": 2, "spruce_planks": 2}, leftover={}, stations=[])),
    "avoided_substitute": ({"crafting_table": 1}, {"oak_planks": 2, "spruce_planks": 2}, {"avoid": ["spruce_planks"]},
                           dict(missing={"oak_log": 1}, consumed={"oak_planks": 2}, leftover={"oak_planks": 2})),
    "fixed_ingredient_no_substitute": ({"oak_stairs": 4}, {"spruce_planks": 6}, {},
                                       dict(missing={"oak_log": 2}, consumed={}, leftover={"oak_planks": 2})),
    "stone_bricks_smelted": ({"stone_bricks": 10}, {"cobblestone": 12}, {},
                             dict(missing={"coal": 2}, consumed={"cobblestone": 12}, leftover={"stone_bricks": 2},
                                  smelt=12, stations=["furnace"])),
    "stone_bricks_fuel_on_hand": ({"stone_bricks": 10}, {"cobblestone": 12, "coal": 5}, {},
                                  dict(missing={}, consumed={"cobblestone": 12, "coal": 2}, fuel={"coal": 2})),
    "stone_brick_stairs": ({"stone_brick_stairs": 8}, {}, {},
                           dict(missing={"cobblestone": 12, "coal": 2}, leftover={}, smelt=12)),
    "stairs_via_stonecutter": ({"stone_brick_stairs": 8}, {}, {"kinds": STONECUT},
                               dict(missing={"cobblestone": 8, "coal": 1}, smelt=8, stations=["furnace", "stonecutter"])),
    "iron_armour_set": ({"iron_helmet": 1, "iron_chestplate": 1, "iron_leggings": 1, "iron_boots": 1},
                        {"raw_iron": 30}, {},
                        dict(missing={"coal": 3}, consumed={"raw_iron": 24}, leftover={}, smelt=24)),
    "iron_pickaxe_on_hand": ({"iron_pickaxe": 1}, {"iron_ingot": 3, "stick": 2}, {},
                             dict(missing={}, consumed={"iron_ingot": 3, "stick": 2}, stations=["crafting_table"])),
    "cycle_block_to_ingots": ({"iron_ingot": 9}, {"iron_block": 1}, {},
                              dict(missing={}, consumed={"iron_block": 1}, leftover={})),
    "cycle_block_plus_raw": ({"iron_ingot": 10}, {"iron_block": 1}, {},
                             dict(missing={"raw_iron": 1, "coal": 1}, consumed={"iron_block": 1}, smelt=1)),
    "ingots_never_from_imaginary_blocks": ({"iron_ingot": 3}, {}, {}, dict(missing={"raw_iron": 3, "coal": 1})),
    "nuggets_from_an_ingot": ({"iron_nugget": 9}, {}, {}, dict(missing={"raw_iron": 1, "coal": 1}, leftover={})),
    "torch_batch": ({"torch": 5}, {}, {},
                    dict(missing={"coal": 2, "oak_log": 1}, leftover={"torch": 3, "stick": 2, "oak_planks": 2})),
    "torch_alternative_charcoal": ({"torch": 4}, {"charcoal": 1, "stick": 1}, {},
                                   dict(missing={}, consumed={"charcoal": 1, "stick": 1}, leftover={})),
    "furnace_mixed_stone": ({"furnace": 1}, {"cobblestone": 5, "cobbled_deepslate": 3}, {},
                            dict(missing={}, consumed={"cobblestone": 5, "cobbled_deepslate": 3})),
    "chest": ({"chest": 1}, {}, {}, dict(missing={"oak_log": 2}, leftover={})),
    "bread": ({"bread": 2}, {}, {}, dict(missing={"wheat": 6}, leftover={})),
    "coal_block_on_hand": ({"coal": 5}, {"coal_block": 1}, {}, dict(missing={}, consumed={"coal_block": 1},
                                                                    leftover={"coal": 4})),
    "smithing_not_solvable": ({"netherite_pickaxe": 1}, {}, {}, dict(impossible="smithing_transform")),
    "unobtainable": ({"bedrock": 1}, {}, {}, dict(impossible="cannot be obtained in survival")),
    "every_substitute_avoided": ({"crafting_table": 1}, {}, {"avoid": [f"{p}_planks" for p in PLANKS]},
                                 dict(impossible="every substitute for an ingredient is avoided")),
}


@pytest.fixture(scope="module")
def book():
    return RecipeBook(DATA)


@pytest.mark.parametrize("name", list(CASES))
def test_recipe(book, name):
    want, inv, opt, exp = CASES[name]
    r = solve(book, m(want), m(inv), kinds=opt.get("kinds", DEFAULT_KINDS),
              avoid=[f"minecraft:{a}" for a in opt.get("avoid", [])])
    if "impossible" in exp:
        assert not r["feasible"] and any(exp["impossible"] in x for x in r["impossible"]), r["impossible"]
        return
    assert r["impossible"] == [], r["impossible"]
    for key in ("missing", "consumed", "leftover", "fuel"):
        if key in exp:
            assert r[key] == m(exp[key]), (key, r[key])
    if "smelt" in exp:
        assert r["smelt_ops"] == exp["smelt"]
    if "stations" in exp:
        assert r["stations"] == exp["stations"]
    assert r["feasible"] == (not r["missing"])


def test_accounting_balances(book):
    """Every item made is either used, wanted, or left over (no item appears from nowhere)."""
    for name, (want, inv, opt, _) in CASES.items():
        r = solve(book, m(want), m(inv), kinds=opt.get("kinds", DEFAULT_KINDS),
                  avoid=[f"minecraft:{a}" for a in opt.get("avoid", [])])
        made = {}
        for c in r["crafts"]:
            for i, n in c["output"].items():
                made[i] = made.get(i, 0) + n
        for item, left in r["leftover"].items():
            assert left <= made.get(item, 0), (name, item)


def test_bad_requests(book):
    for bad in ({}, {"minecraft:stick": 0}, {"minecraft:stick": -2}, {"minecraft:stick": 1.5}):
        with pytest.raises(ValueError):
            solve(book, bad)


BOUNDARY = {"avoided_substitute", "fixed_ingredient_no_substitute", "cycle_block_to_ingots", "cycle_block_plus_raw",
            "ingots_never_from_imaginary_blocks", "coal_block_on_hand", "torch_alternative_charcoal",
            "smithing_not_solvable", "unobtainable", "every_substitute_avoided"}
BAD_REQUESTS = 4   # test_bad_requests


def test_fixture_coverage():
    """K class: >= 10 normal and >= 10 boundary/unsupported/malformed cases."""
    assert BOUNDARY <= set(CASES)
    assert len(set(CASES) - BOUNDARY) >= 10 and len(BOUNDARY) + BAD_REQUESTS >= 10
