"""CP-48: the hut's bom.json equals the independent hand count; grid, scaffold and BOM share one identity."""
import json
import runpy
from collections import Counter
from pathlib import Path

from eve_design import bom, gamedata
from eve_design.grid import Design
from eve_design.schem_io import load_litematic, save_litematic

HERE = Path(__file__).parent
GD = gamedata.load()
EXP = json.loads((HERE / "fixtures/hut/expected_bom.json").read_text())
build_hut = runpy.run_path(str(HERE.parent / "examples/hut5.py"))["build_hut"]


def compute():
    g, s = build_hut(GD)
    stock, ref = bom.stock_from_storage(json.loads((HERE / "fixtures/hut/storage_index.json").read_text()))
    return g, s, bom.compute(g, GD, s, stock, ref)


def as_map(rows):
    return {r["item"]: r["count"] for r in rows}


def test_bom_matches_hand_count():
    g, s, b = compute()
    assert as_map(b["final"]) == EXP["final"]
    assert as_map(b["scaffold"]) == EXP["scaffold"]
    assert as_map(b["deficit"]["missing"]) == EXP["missing"]
    assert {c["item"]: [c["batches"], c["produced"], c["leftover"]] for c in b["crafting"]} == EXP["crafting"]
    assert as_map(b["leftovers"]) == EXP["leftovers"]
    assert {p["item"]: [p["count"], p["via"]] for p in b["deficit"]["gather_plan"]} == EXP["gather"]
    assert b["fuel"][0]["count"] == EXP["fuel_coal"] and b["fuel"][0]["smelted_items"] == EXP["smelted"]
    assert {t["item"].rsplit("_", 1)[1]: t["uses"] for t in b["tools"]} == EXP["tool_uses"]
    assert all(t["estimate"] for t in b["tools"]) and b["fuel"][0]["estimate"]
    assert b["carry"]["slots"] == EXP["carry_slots"] and b["carry"]["trips"] == EXP["trips"]
    assert b["totals"]["final_blocks"] == EXP["final_blocks"] and b["totals"]["scaffold_blocks"] == EXP["scaffold_blocks"]


def test_shared_identity_and_scaffold_separate(tmp_path):
    g, s, b = compute()
    assert b["design_hash"] == g.hash() == build_hut(GD)[0].hash()        # deterministic
    assert s.meta["provenance"]["scaffold_for"] == g.hash() and b["scaffold_hash"] == s.hash()
    assert "minecraft:dirt" not in Counter(as_map(b["final"]))             # temporary never mixed into final
    g.save(tmp_path / "design.json")
    g2 = Design.load(tmp_path / "design.json")                              # hash re-verified on load
    assert g2.same_blocks(g) and (g2.roles == g.roles).all() and g2.rooms == g.rooms
    save_litematic(g, tmp_path / "hut.litematic", GD)
    g3, issues = load_litematic(tmp_path / "hut.litematic", GD)
    assert not issues and g3.same_blocks(g)
    g3.set(2, 1, 4, "minecraft:air")                                        # any block change -> new identity
    assert g3.hash() != g.hash()


def test_item_mapping_rules():
    f = lambda s: bom.items_for_state(GD.canonical(s), GD)
    assert f("minecraft:oak_door[half=upper]") == [] and f("minecraft:oak_door[half=lower]") == [("minecraft:oak_door", 1)]
    assert f("minecraft:red_bed[part=head]") == [] and f("minecraft:red_bed[part=foot]") == [("minecraft:red_bed", 1)]
    assert f("minecraft:oak_slab[type=double]") == [("minecraft:oak_slab", 2)]
    assert f("minecraft:wall_torch[facing=east]") == [("minecraft:torch", 1)]
    assert f("minecraft:soul_wall_torch") == [("minecraft:soul_torch", 1)]
    assert f("minecraft:spruce_wall_sign") == [("minecraft:spruce_sign", 1)]
    assert f("minecraft:candle[candles=3]") == [("minecraft:candle", 3)]
    assert f("minecraft:potted_poppy") == [("minecraft:flower_pot", 1), ("minecraft:poppy", 1)]
    assert f("minecraft:oak_stairs[waterlogged=true]") == [("minecraft:water_bucket", 1), ("minecraft:oak_stairs", 1)]
    assert f("minecraft:tall_grass[half=upper]") == [] and f("minecraft:redstone_wire") == [("minecraft:redstone", 1)]
