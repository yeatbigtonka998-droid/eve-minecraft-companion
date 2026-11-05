"""O131 (Hunter: "load her with every recipe vanilla has"; 45L: "can you make a bed?" -> "I don't know a recipe for bed"):
every 26.3 recipe type is in the book, families resolve by name against her inventory, the solver reaches raw
materials fast, and a smelting step goes to a furnace (made and put down when none is in reach, then picked back up)."""
import time
from pathlib import Path

import pytest

from conftest import ROOT

from eve_mind import craftkit
from eve_mind.recipes import RecipeBook, solve

DATA = ROOT / "data" / "game" / "26.3"
pytestmark = pytest.mark.skipif(not (DATA / "recipe").exists(), reason="BLOCKED: data/game/26.3 not extracted")
ST = {"pos": {"x": 0.5, "y": 64, "z": 0.5}, "yaw": 0}
GROUND = {(x, y, z): ("minecraft:stone" if y < 64 else "minecraft:air")
          for x in range(-6, 7) for y in range(60, 68) for z in range(-6, 7)}


@pytest.fixture(scope="module")
def book():
    return RecipeBook(Path(DATA))


@pytest.mark.parametrize("item,kind", [
    ("minecraft:chest", "crafting_shaped"), ("minecraft:oak_planks", "crafting_shapeless"),
    ("minecraft:black_bundle", "crafting_transmute"), ("minecraft:iron_ingot", "smelting"),
    ("minecraft:gold_ingot", "blasting"), ("minecraft:cooked_beef", "smoking"),
    ("minecraft:baked_potato", "campfire_cooking"), ("minecraft:stone_slab", "stonecutting"),
    ("minecraft:netherite_axe", "smithing_transform"), ("minecraft:white_bed", "crafting_shaped")])
def test_every_recipe_type_is_in_the_book(book, item, kind):
    assert any(r["kind"] == kind for r in book.by_output.get(item, [])), (item, [r["kind"] for r in book.by_output.get(item, [])])


def test_families_resolve_from_her_inventory_and_say_what_is_missing(book):
    logs = {"minecraft:oak_log": 9}
    assert craftkit.resolve(book, "bed", logs) == "minecraft:white_bed"
    assert craftkit.resolve(book, "bed", {**logs, "minecraft:red_wool": 3}) == "minecraft:red_bed"
    assert craftkit.resolve(book, "planks", {"minecraft:spruce_log": 2}) == "minecraft:spruce_planks"
    assert craftkit.can_make(book, "minecraft:white_bed", logs) == \
        "Not yet: white bed needs 3 white wool and 3 any planks; I'm missing 12 string."
    assert craftkit.can_make(book, craftkit.resolve(book, "bed", {**logs, "minecraft:red_wool": 3}),
                             {**logs, "minecraft:red_wool": 3}).startswith("Yes")
    assert craftkit.can_make(book, None, logs) == "I don't know a recipe for that."


def test_stone_pickaxe_from_logs_and_cobble_and_the_solver_stays_fast(book):
    t0 = time.monotonic()
    plan = solve(book, {"minecraft:stone_pickaxe": 1}, {"minecraft:oak_log": 2, "minecraft:cobblestone": 3},
                 kinds=craftkit.KINDS)
    assert plan["feasible"] and plan["stations"] == ["crafting_table"]
    for item in ("minecraft:white_wool", "minecraft:white_bed", "minecraft:cake", "minecraft:white_carpet"):
        solve(book, {item: 1}, {}, kinds=craftkit.KINDS)          # white_wool alone hung > 40 s before O131
    assert time.monotonic() - t0 < 5


def test_a_smelting_step_goes_to_a_furnace_made_and_put_down_after_the_table(book):
    inv = {"minecraft:raw_iron": 3, "minecraft:oak_log": 3, "minecraft:coal": 1, "minecraft:cobblestone": 8}
    steps, line = craftkit.craft_steps(book, "minecraft:iron_pickaxe", 1, inv, GROUND, ST)
    skills = [s for s, _ in steps]
    assert skills.index("place_block") > [i for i, (s, p) in enumerate(steps) if p.get("item") == craftkit.TABLE][0]
    furnace = [p for s, p in steps if s == "place_block" and p["item"] == "minecraft:furnace"][0]["pos"]
    assert ("smelt", {"pos": furnace, "item": "minecraft:iron_ingot", "count": 3}) in steps   # smelt names the output
    assert skills[-2:] == ["mine_block", "mine_block"] and line.startswith("Making iron pickaxe:")
