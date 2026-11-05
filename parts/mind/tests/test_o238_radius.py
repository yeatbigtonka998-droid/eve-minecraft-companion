"""O238: his mine / gather orders reach 100 blocks once the served registry allows it (0.15.3: radius max 128), 32 on
0.15.2 and earlier; a radius he names is kept."""
import json
from types import SimpleNamespace

from conftest import ROOT

from eve_mind import workstations
from eve_mind.registry import Registry


def served(tmp_path, radius_max=None):
    raw = json.loads((ROOT / "contracts" / "skills" / "registry.json").read_text("utf-8"))
    if radius_max:                                       # the 0.15.3 draft (86ad4858): mine_resource / gather max 128
        raw["registry_version"] = "0.15.3"
        for s in raw["skills"]:
            if s["name"] in ("mine_resource", "gather"):
                s["params"]["properties"]["radius"]["maximum"] = radius_max
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(raw), "utf-8")
    return SimpleNamespace(registry=Registry(p))


def test_o238_owner_radius_follows_the_served_registry(tmp_path):
    (tmp_path / "a").mkdir()
    old = served(tmp_path / "a")
    (tmp_path / "b").mkdir()
    new = served(tmp_path / "b", 128)
    for skill in ("mine_resource", "gather"):
        assert workstations.owner_radius(old, skill) == 32
        assert workstations.owner_radius(new, skill) == 100
        assert workstations.widest(new, skill, {"count": 12})["radius"] == 100
        assert workstations.widest(new, skill, {"count": 12, "radius": 20})["radius"] == 20     # his own radius
    assert workstations.owner_radius(new, "walk_to") == 32                                      # no radius: fallback


def test_o238_a_mine_order_on_0153_reaches_100(tmp_path):
    (tmp_path / "a").mkdir()
    m = served(tmp_path / "a", 128)
    assert workstations.widest(m, "mine_resource", {"block": "minecraft:stone", "count": 12}) == \
        {"block": "minecraft:stone", "count": 12, "radius": 100}
