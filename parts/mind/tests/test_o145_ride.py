"""O145 (Hunter: "get in the wood boat" -> a coordinates question; riding with him is "extremely important"): mount
the rideable nearest him within 16, dismount on "get out / hop off"; horses only tamed and saddled; before registry
0.15.2 she says riding comes with the next update. "get out of the hole" stays dig_up's."""
from eve_mind.owner_phrases import match

BOAT = {"id": 40, "type": "minecraft:oak_boat", "pos": {"x": 3, "y": 63, "z": 0}, "distance": 3}
CART = {"id": 42, "type": "minecraft:minecart", "pos": {"x": 9, "y": 64, "z": 0}, "distance": 9}
HORSE = {"id": 41, "type": "minecraft:horse", "pos": {"x": 5, "y": 64, "z": 0}, "distance": 5}
TAME = {**HORSE, "id": 43, "saddled": True, "tamed": True}


def view(*ents, mount=True):
    return {"st": {"pos": {"x": 0.5, "y": 64, "z": 0.5}, "nearby_entities": list(ents)}, "mount": mount,
            "owner_pos": {"x": 8, "y": 64, "z": 0}, "home": {"center": {"x": 0, "y": 64, "z": 0}}}


def test_o145_get_in_rides_the_one_nearest_him_and_get_out_dismounts():
    assert match("get in the wood boat", view(BOAT, CART)) == {"do": [("mount", {"entity_id": 40})],
                                                              "say": "Getting in the oak boat."}
    assert match("hop in", view(BOAT, CART))["do"] == [("mount", {"entity_id": 42})]       # nearest him: the cart
    assert match("ride the horse", view(HORSE))["say"].endswith("can't ride it.")
    assert match("ride the horse", view(TAME))["do"] == [("mount", {"entity_id": 43})]
    assert match("get out of the boat", view())["do"] == [("dismount", {})]
    assert match("get out of the hole", view())["do"][0][0] == "dig_up"
    assert match("get in the house", view(BOAT)) is None
    assert match("get in the boat", view(BOAT, mount=False)) == {"say": "I can't ride yet - that comes with the next update."}
