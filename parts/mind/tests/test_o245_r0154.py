"""O245: registry 0.15.4 (contracts/skills/drafts/registry-0.15.4.json, canonical 3a31a20d) adds follow_boat, shear_sheep,
milk_cow and harvest_crop. Served via EVE_REGISTRY the mind validates and dispatches them; the owner phrases map to them
(with spoken refusals when a tool, a boat or the animal is missing); plain "follow me" switches to follow_boat while
he is on the water and back to walking when he lands."""
import asyncio
import json

import pytest

from conftest import ROOT, envelope, mind_env, run, until
from test_o111b_phrases import view

import fake_body
from eve_mind.owner_phrases import match
from eve_mind.registry import Registry

R0154 = ROOT / "contracts" / "skills" / "drafts" / "registry-0.15.4.json"
HUNTER = {"id": 9, "uuid": "u-hunter", "type": "minecraft:player", "name": "Hunter",
          "pos": {"x": 6.5, "y": 64.0, "z": 0.5}, "distance": 6.0}
SHEEP = [{"id": 60 + i, "type": "minecraft:sheep", "pos": {"x": 2.0 + i, "y": 64.0, "z": 3.0}, "distance": 3.0 + i}
         for i in range(4)]
COW = {"id": 70, "type": "minecraft:cow", "pos": {"x": 3.0, "y": 64.0, "z": -2.0}, "distance": 3.5}


def test_o245_the_draft_validates_the_four_skills():
    reg = Registry(R0154)
    assert reg.version == "0.15.4" and len(reg.skills) == 37
    zone = {"min": {"x": -8, "y": 60, "z": -8}, "max": {"x": 8, "y": 70, "z": 8}}
    good = {"follow_boat": {"entity_id": 9, "keep_min": 4, "keep_max": 8},
            "shear_sheep": {"entity_ids": [60, 61], "zone": zone, "count": 2},
            "milk_cow": {"entity_ids": [70], "zone": zone, "count": 1},
            "harvest_crop": {**zone, "crop": "minecraft:wheat", "count": 32, "replant": True}}
    for name, params in good.items():
        assert reg.validate(name, params) == [], (name, reg.validate(name, params))
    assert reg.validate("follow_boat", {"entity_id": 9, "keep_min": 0})          # below its minimum
    assert reg.validate("harvest_crop", {**zone, "crop": "minecraft:beetroots"})  # not in its enum


def v0154(**more):
    v = view()
    v.update({"shear_sheep": True, "milk_cow": True, "harvest_crop": True})
    v["st"] = {**v["st"], **more}
    return v


def test_o245_shear_milk_harvest_phrases():
    with_shears = v0154(nearby_entities=SHEEP + [COW], inventory=[{"slot": 0, "item": "minecraft:shears", "count": 1},
                                                                   {"slot": 1, "item": "minecraft:bucket", "count": 1}])
    p = match("shear the sheep", with_shears)
    assert p["do"][0][0] == "shear_sheep" and p["do"][0][1]["entity_ids"] == [60, 61, 62, 63], p
    p = match("shear 3 sheep", with_shears)
    assert p["do"][0][1]["entity_ids"] == [60, 61, 62] and p["do"][0][1]["count"] == 3, p
    assert match("shear the sheep", v0154(nearby_entities=SHEEP, inventory=[]))["say"] == "I need shears for that."
    p = match("milk the cow", with_shears)
    assert p["do"] == [("milk_cow", {"entity_ids": [70], "zone": p["do"][0][1]["zone"], "count": 1})], p
    assert match("milk the cow", v0154(nearby_entities=[COW], inventory=[]))["say"] == \
        "I need an empty bucket to milk a cow."
    box = {"min": {"x": 0, "y": 63, "z": 0}, "max": {"x": 10, "y": 64, "z": 10}}
    assert match("harvest the wheat", v0154())["do"] == [("harvest_crop", {**box, "replant": True,
                                                                         "crop": "minecraft:wheat"})]
    assert match("harvest the farm", v0154())["do"] == [("harvest_crop", {**box, "replant": True})]
    assert match("harvest the farm", view())["do"][0][0] == "harvest_replant"     # 0.15.2: the old skill


def serve_0154(monkeypatch):
    monkeypatch.setenv("EVE_REGISTRY", str(R0154))
    monkeypatch.setattr(fake_body, "REGISTRY", R0154)


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t, *a, **k):
        out.append(t)
        return await orig(t, *a, **k)
    e.mind.say = say
    return out


def actions(e):
    return [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]


@pytest.mark.parametrize("outcome", ["ok", "no_boat"])
def test_o245_follow_switches_to_the_boat_on_water_and_back(tmp_path, monkeypatch, outcome):
    serve_0154(monkeypatch)

    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            assert e.mind.registry.version == "0.15.4"
            e.body.walk_s = 0.3
            e.body.nearby = [dict(HUNTER)]
            await until(lambda: (e.mind.fresh_state() or {}).get("nearby_entities"))
            await e.mind.owner_text("follow me", "Hunter")
            await until(lambda: any(s == "walk_to" for s, _ in actions(e)), timeout=5, what="the walk")
            if outcome == "no_boat":
                e.body.result_override = {"follow_boat": [("failed", "no_boat")]}
            e.body.nearby = [{**HUNTER, "pos": {"x": 14.5, "y": 63.0, "z": 0.5}, "vehicle": "minecraft:oak_boat",
                              "in_water": True}]
            await until(lambda: any(s == "follow_boat" for s, _ in actions(e)), timeout=5, what="follow_boat")
            assert [p for s, p in actions(e) if s == "follow_boat"][0] == {"entity_id": 9}
            await until(lambda: "Getting my boat out." in said, timeout=3, what="the launch line")
            if outcome == "no_boat":
                await until(lambda: "I don't have a boat - I'll follow along the shore." in said, timeout=5)
                fb = [s for s, _ in actions(e)].index("follow_boat")
                await until(lambda: any(s == "walk_to" for s, _ in actions(e)[fb:]), timeout=5, what="the shore walk")
                await asyncio.sleep(1.0)
                assert sum(s == "follow_boat" for s, _ in actions(e)) == 1          # no second try without a boat
            else:
                e.body.nearby = [{**HUNTER, "pos": {"x": 30.5, "y": 64.0, "z": 0.5}}]   # he lands
                n = len(actions(e))
                await until(lambda: any(s == "walk_to" for s, _ in actions(e)[n:]), timeout=8, what="on foot again")
            e.mind.follow.end("test")
    run(go())


def test_o245_follow_me_by_boat(tmp_path, monkeypatch):
    serve_0154(monkeypatch)

    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.nearby = [dict(HUNTER)]
            await until(lambda: (e.mind.fresh_state() or {}).get("nearby_entities"))
            r = await e.mind.owner_text("follow me by boat", "Hunter")
            assert r.get("following") == "Hunter" and "I'll bring my boat." in said, (r, said)
            e.mind.follow.end("test")
    run(go())
