"""O175 progression table: five synthetic inventory frames -> the right next milestone, plus structure checks."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agenda import progression as P  # noqa: E402


def inv(**items):
    return [{"slot": n, "item": "minecraft:" + k, "count": c} for n, (k, c) in enumerate(items.items())]


TABLE, FURNACE = "minecraft:crafting_table", "minecraft:furnace"


@pytest.mark.parametrize("frame,placed,want", [
    (inv(), (), "wood"),                                                                         # a fresh world
    (inv(oak_log=8), (), "planks"),
    (inv(stick=8, wooden_pickaxe=1, oak_planks=2), (TABLE,), "cobblestone"),                     # logs used up: fine
    (inv(stone_pickaxe=1, charcoal=8, raw_iron=11, cobblestone=3), (TABLE, FURNACE), "iron_ingots"),
    (inv(iron_pickaxe=1, shield=2, white_bed=1, bread=8, torch=16), (TABLE, FURNACE), "diamond"),
])
def test_five_frames(frame, placed, want):
    r = P.next_step(frame, placed)
    assert r["milestone"] == want, r
    assert r["say"].startswith("Next:") and r["commands"]


def test_a_lost_tool_is_next_again_and_consumables_are_not():
    r = P.next_step(inv(stick=4, cobblestone=20), (TABLE,))      # had a wooden pickaxe, it broke
    assert r["milestone"] == "stone_pickaxe" and "sticks" in r["done"] and "planks" not in r["done"]   # best tier she can make; the bed still owes planks
    r = P.next_step(inv(stick=4, oak_planks=8), (TABLE,))        # no cobblestone: the wooden pickaxe is next again
    assert r["milestone"] == "wooden_pickaxe"
    r = P.next_step(inv(diamond=3, bread=8, torch=16, shield=2, white_bed=1, stick=2), (TABLE, FURNACE))   # iron pick lost
    assert r["milestone"] == "diamond_pickaxe"                   # diamonds in hand: the diamond pickaxe comes next


def test_live_45l_state_cp86_needs_cobblestone_and_iron_first():
    """mind's live 45L inventory 16:55:04: a held shield/stone pickaxe must not mark cobblestone or iron as used up
    while the furnace and the iron pickaxe still consume them."""
    from agenda import adapter
    state = {"inventory": inv(stone_sword=1, bread=16, crafting_table=1, dirt=2, stone_pickaxe=1, shield=1)}
    r = adapter.progression_next(state, set(), portal_light=True)
    assert r["milestone"] == "cobblestone" and r["commands"] == ["mine 12 stone"], r
    assert "wooden_pickaxe" in r["done"] and "raw_iron" not in r["done"] and "iron_ingots" not in r["done"]
    state["inventory"] += inv(cobblestone=12)
    assert adapter.progression_next(state, set())["milestone"] == "furnace"
    r = adapter.progression_next(state, {FURNACE})
    assert r["milestone"] == "raw_iron", r                    # never 'craft an iron pickaxe' at 0 iron
    assert adapter.progression_next({"inventory": inv(stone_pickaxe=1, shield=1, bread=16, charcoal=8)},
                                    {TABLE, FURNACE})["milestone"] == "raw_iron"


def test_everything_done():
    frame = inv(diamond_pickaxe=1, shield=2, white_bed=1, bucket=1, flint_and_steel=1, bread=8, torch=16, obsidian=10)
    r = P.next_step(frame, (TABLE, FURNACE, P.PORTAL_FRAME, "minecraft:nether_portal"))
    assert r["milestone"] is None and "nether portal" in r["say"]


def test_structure():
    ids = [m["id"] for m in P.MILESTONES]
    assert len(ids) == len(set(ids)) == 26
    seen = set()
    for m in P.MILESTONES:                                        # prerequisites come earlier: acyclic, in order
        assert set(m["needs"]) <= seen, m["id"]
        seen.add(m["id"])
        assert set(m["skills"]) <= P.SKILLS, (m["id"], set(m["skills"]) - P.SKILLS)
        assert m["commands"] and m["say"].startswith("Next:")
    assert P.KEEP <= set(ids)
    assert P.ORES["diamond"]["best_y"] == -58 and "min_y" in P.ORES["diamond"]["note"]


def test_registry_set_matches_the_contract_plus_0_15_2_patches():
    import json
    root = Path(__file__).resolve().parents[4]
    base = {s["name"] for s in json.loads((root / "contracts" / "skills" / "registry.json").read_text("utf-8"))["skills"]}
    assert P.SKILLS - base == {"harvest_animal", "drop_item", "select_item", "mount", "dismount"}


def test_iron_quantities_agree():
    raw = next(m for m in P.MILESTONES if m["id"] == "raw_iron")["have"]["any"][0]["n"]
    ing = next(m for m in P.MILESTONES if m["id"] == "iron_ingots")["have"]["n"]
    assert raw == ing == 11                     # pick 3, shield 1, spare 1, shears 2, bucket 3, flint and steel 1 (S93)
    assert P.next_step(inv(stone_pickaxe=1, charcoal=8, raw_iron=10), (TABLE, FURNACE))["milestone"] == "raw_iron"


def test_adapter_progression_next():
    from agenda import adapter
    state = {"inventory": inv(oak_log=8), "equipment": {"mainhand": "minecraft:stone_sword", "offhand": None}}
    r = adapter.progression_next(state, ())
    assert r["milestone"] == "planks"
    r = adapter.progression_next({"inventory": [], "equipment": {"mainhand": "minecraft:iron_pickaxe"}},
                                 ("minecraft:crafting_table",))
    assert "iron_pickaxe" in r["done"]                            # a held tool counts via equipment


def test_held_item_is_not_counted_twice():
    from agenda import adapter
    state = {"inventory": inv(torch=15, iron_pickaxe=1, shield=2, white_bed=1, bread=8, stick=4, charcoal=4),
             "equipment": {"mainhand": "minecraft:torch", "offhand": "minecraft:shield"}}
    r = adapter.progression_next(state, (TABLE, FURNACE))
    assert r["milestone"] == "torches"                            # 15 torches, the held one is among them


def test_portal_stage_build_then_light():
    kit = dict(diamond_pickaxe=1, shield=2, white_bed=1, bucket=1, bread=8, torch=16)
    assert P.next_step(inv(**kit, obsidian=10, iron_ingot=7), (TABLE, FURNACE))["milestone"] == "flint_and_steel"
    assert P.next_step(inv(**kit, obsidian=10), (TABLE, FURNACE))["milestone"] == "raw_iron"   # no iron: mine it first
    r = P.next_step(inv(**kit, obsidian=10, flint_and_steel=1), (TABLE, FURNACE))
    assert r["milestone"] == "scaffold_dirt" and r["commands"] == ["mine 23 dirt"]          # builder v1: 23 dirt
    r = P.next_step(inv(**kit, obsidian=10, flint_and_steel=1, dirt=23), (TABLE, FURNACE))
    assert r["milestone"] == "portal_frame" and r["commands"] == ["build the nether portal"]
    r = P.next_step(inv(**kit, flint_and_steel=1), (TABLE, FURNACE, P.PORTAL_FRAME), portal_light=True)   # frame stands
    assert r["milestone"] == "nether_portal" and r["commands"] == ["light the portal"]
    assert P.next_step(inv(**kit), (TABLE, FURNACE, P.PORTAL_FRAME))["milestone"] == "raw_iron"   # flint lost, no iron


def test_portal_numbers_are_builders_v1():
    assert (P.PORTAL_OBSIDIAN, P.PORTAL_DIRT) == (10, 23)
    r = P.next_step(inv(diamond_pickaxe=1, shield=2, white_bed=1, bucket=1, bread=8, torch=16, flint_and_steel=1),
                    (TABLE, FURNACE, P.PORTAL_FRAME))
    assert "scaffold_dirt" in r["done"] and r["milestone"] == "nether_portal"   # the dirt came back; not needed again


def test_lighting_waits_until_sense_advertises_portal_light():
    kit = dict(diamond_pickaxe=1, shield=2, white_bed=1, bucket=1, bread=8, torch=16, flint_and_steel=1)
    r = P.next_step(inv(**kit), (TABLE, FURNACE, P.PORTAL_FRAME))                   # O193: not advertised -> waits
    assert r["milestone"] == "nether_portal" and r["commands"] == [] and "portal_light" in r["waiting"]
    assert r["say"] == "The frame is built; I can light it once the referee allows portal lighting."
    r = P.next_step(inv(**kit), (TABLE, FURNACE, P.PORTAL_FRAME), portal_light=True)
    assert r["commands"] == ["light the portal"] and "waiting" not in r
    assert P.next_step(inv(**kit), (TABLE, FURNACE))["milestone"] == "obsidian"     # the rest is unaffected
    from agenda import adapter
    assert adapter.progression_next({"inventory": inv(**kit)}, (TABLE, FURNACE, P.PORTAL_FRAME))["commands"] == []
    assert adapter.progression_next({"inventory": inv(**kit)}, (TABLE, FURNACE, P.PORTAL_FRAME),
                                    portal_light=True)["commands"] == ["light the portal"]


@pytest.mark.parametrize("version,want", [
    ("0.1.27", True), ("0.1.28", True), ("0.2.0", True), ("1.0.0", True),
    ("0.1.26", False), ("0.1.9", False), ("0.1.3", False), ("", False), (None, False), ("abc", False),
    ("0.1.27-dev", True),
])
def test_sense_portal_light_from_hello(version, want):
    from agenda import adapter
    assert adapter.sense_portal_light({"mod_version": version}) is want
    assert adapter.sense_portal_light(None) is False


def test_spare_shield_o220e():
    """O220e: a broken shield killed her; a second shield (offhand counts) comes right after the first."""
    from agenda import adapter
    base = dict(iron_pickaxe=1, iron_ingot=1, oak_planks=16, bread=8)
    r = P.next_step(inv(**base, shield=1), (TABLE, FURNACE))
    assert r["milestone"] == "spare_shield" and r["commands"] == ["craft 2 shields"] and r["say"] == "Next: a spare shield."
    assert P.next_step(inv(**base, shield=2), (TABLE, FURNACE))["milestone"] != "spare_shield"
    off = {"inventory": inv(**base, shield=1), "equipment": {"mainhand": "minecraft:iron_pickaxe", "offhand": "minecraft:shield"}}
    assert "spare_shield" in adapter.progression_next(off, (TABLE, FURNACE))["done"]           # bag 1 + offhand 1
    one = {"inventory": inv(**base), "equipment": {"offhand": "minecraft:shield"}}
    assert adapter.progression_next(one, (TABLE, FURNACE))["milestone"] == "spare_shield"      # offhand alone = 1
    slot40 = {"inventory": inv(**base) + [{"slot": 40, "item": "minecraft:shield", "count": 1}],
              "equipment": {"offhand": "minecraft:shield"}}                                    # a sense that lists 40
    assert adapter.progression_next(slot40, (TABLE, FURNACE))["milestone"] == "spare_shield"   # never counted twice
    r = P.next_step(inv(iron_pickaxe=1, shield=1, bread=8), (TABLE, FURNACE))                  # no iron left
    assert r["milestone"] == "raw_iron", r


def test_bed_needs_its_recipe_not_16_planks():
    """kickoff ec921b6b: a bed is 3 wool + 3 planks; holding exactly that is enough (no flat 16 planks)."""
    kit = inv(iron_pickaxe=1, shield=2, bread=8, white_wool=3, oak_planks=3)
    r = P.next_step(kit, (TABLE, FURNACE))
    assert r["milestone"] == "bed" and r["commands"] == ["craft a bed"], r
    assert P.next_step(inv(iron_pickaxe=1, shield=2, bread=8, white_wool=3, oak_planks=2), (TABLE, FURNACE))["milestone"] != "bed"
