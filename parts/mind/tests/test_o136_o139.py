"""O136 (Hunter: "fight the wandering trader" - she must override her protected rule for the mob he names): with a
registry that declares owner_target, sense's attack.grant (his exact line) comes first, then fight_melee {entity_id,
owner_target}; players never; two-word mobs map ("fight the zombie villager" got no reply live). O139 (S28: shot to
death in daylight, the mind never reacted): hurt to 8 HP or less right after a hit, she drops the task, says so, backs
off home (or to him), eats and says "Ready."; with no bow an archer is rushed, said."""
import json

import fake_body
from conftest import ROOT, mind_env, run, until

TRADER = {"id": 31, "uuid": "u-trader", "type": "minecraft:wandering_trader", "pos": {"x": 4.5, "y": 64.0, "z": 0.5},
          "distance": 4.0, "hostile": False, "protected": True, "line_of_sight": True}
ZVILL = {"id": 32, "uuid": "u-zv", "type": "minecraft:zombie_villager", "pos": {"x": 3.5, "y": 64.0, "z": 0.5},
         "distance": 3.0, "hostile": True, "protected": False, "line_of_sight": True}
SKEL = {"id": 33, "uuid": "u-sk", "type": "minecraft:skeleton", "pos": {"x": 5.5, "y": 64.0, "z": 0.5},
        "distance": 5.0, "hostile": True, "protected": False, "line_of_sight": True}


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t, *a, **k):
        out.append(t)
        return await orig(t, *a, **k)
    e.mind.say = say
    return out


def actions(e):
    return [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]


def registry_with_owner_target(tmp_path):
    reg = json.loads((ROOT / "contracts" / "skills" / "registry.json").read_text("utf-8"))
    reg["registry_version"] = "0.15.2"
    for s in reg["skills"]:
        if s["name"] in ("fight_melee", "fight_ranged", "attack_entity"):
            s["params"]["properties"]["owner_target"] = {"type": "boolean", "default": False}
    path = tmp_path / "registry-0.15.2.json"
    path.write_text(json.dumps(reg), "utf-8")
    return path


def test_o136_his_named_protected_mob_gets_a_grant_then_owner_target(tmp_path, monkeypatch):
    path = registry_with_owner_target(tmp_path)
    monkeypatch.setenv("EVE_REGISTRY", str(path))
    monkeypatch.setattr(fake_body, "REGISTRY", path)

    async def go():
        async with mind_env(tmp_path / "m") as e:
            said = said_by(e)
            e.body.walk_s = 0.05
            e.body.nearby = [TRADER, ZVILL]
            await until(lambda: len((e.mind.fresh_state() or {}).get("nearby_entities", [])) == 2)
            await e.mind.owner_text("fight the wandering trader", "Hunter")
            assert e.sense.attack_grants == [{"entity_uuid": "u-trader", "by": "Hunter",
                                              "line": "fight the wandering trader"}], e.sense.attack_grants
            assert actions(e)[-1] == ("fight_melee", {"entity_id": 31, "owner_target": True}), actions(e)
            assert "OK, going for the wandering trader (you asked)." in said, said
            await until(lambda: e.mind.open_action() is None)
            e.sense.attack_grant_refuse = "the line does not name the mob"
            n = len(actions(e))
            await e.mind.owner_text("kill the wandering trader", "Hunter")
            assert len(actions(e)) == n and said[-1].startswith("The server won't let me attack it"), said
            await e.mind.owner_text("fight the zombie villager", "Hunter")       # two words; hostile: no grant
            assert actions(e)[-1] == ("fight_melee", {"entity_id": 32}) and said[-1] == "Fighting the zombie villager."
    (tmp_path / "m").mkdir()
    run(go())


def test_o136_without_owner_target_in_the_registry_she_still_refuses(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.nearby = [TRADER]
            await until(lambda: (e.mind.fresh_state() or {}).get("nearby_entities"))
            await e.mind.owner_text("fight the wandering trader", "Hunter")
            assert "protected" in said[-1] and not e.sense.attack_grants and not actions(e), said
    run(go())


def test_o139_hurt_she_backs_off_eats_and_says_ready_and_archers_are_rushed(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            said = said_by(e)
            e.body.walk_s = 3.0
            e.body.food = 15
            e.body.inventory = [{"slot": 2, "item": "minecraft:bread", "count": 5}]
            e.body.nearby = [SKEL]
            await until(lambda: (e.mind.fresh_state() or {}).get("nearby_entities"))
            await e.mind.owner_text("attack the skeleton", "Hunter")             # no bow: rush, said
            assert said[-1] == "It's an archer; I'll rush it." and actions(e)[-1] == ("fight_melee", {"entity_id": 33})
            fight = e.mind.open_action()["action_id"]
            e.body.health = 14
            await until(lambda: e.mind._hp == 14)
            e.body.walk_s = 0.05
            e.body.health = 6                                                     # a hit to 6 HP
            await until(lambda: "I'm hurt, backing off." in said, timeout=5, what="the back-off line")
            await until(lambda: said[-1] == "Ready.", timeout=10, what="ready")
            assert e.mind.status_of(fight) == "cancelled", e.mind.action(fight)
            skills = [s for s, p in actions(e)]
            assert skills[-4:] == ["stop", "go_home", "select_hotbar", "use_item"], skills
            n = len(actions(e))
            e.body.health = 5                                                     # no new task starts on its own
            await until(lambda: e.mind._hp == 5)
            assert len(actions(e)) <= n + 4                                       # at most one more back-off round
    run(go())
