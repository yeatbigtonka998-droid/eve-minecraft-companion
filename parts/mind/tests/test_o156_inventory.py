"""O156 (45L session 4: "check your own inventory right now, tell me which pickaxes you are carrying" -> "Noted:
inventory status cannot check inventory" - a fact write, no answer): inventory questions anywhere in the line answer
from the state frame, filtered by the asked category, with where each item is; never a memory write."""
from conftest import mind_env, run, until

INV = [{"slot": 0, "item": "minecraft:stone_pickaxe", "count": 1}, {"slot": 1, "item": "minecraft:bread", "count": 6},
       {"slot": 12, "item": "minecraft:wooden_pickaxe", "count": 1}, {"slot": 20, "item": "minecraft:oak_log", "count": 9}]


def facts(e):
    return e.mind.db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]


def test_o156_inventory_questions_answer_from_her_inventory_by_category(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.inventory = INV
            await until(lambda: (e.mind.fresh_state() or {}).get("inventory"))
            n0 = facts(e)
            ask = lambda t: e.mind.owner_text(t, "Hunter")              # noqa: E731
            r = await ask("check your own inventory right now, tell me which pickaxes you are carrying")
            assert r["reply"] == "I'm carrying 1 stone pickaxe (hotbar) and 1 wooden pickaxe (pack).", r
            live = ("Eve, check your own inventory right now. Tell me which pickaxes you are carrying. Check directly; do not "
                    "use memories or chest records. If you cannot check your inventory, say so.")   # 45L 14:56:41 verbatim
            assert (await ask(live))["reply"] == "I'm carrying 1 stone pickaxe (hotbar) and 1 wooden pickaxe (pack).", live
            assert (await ask("how many logs do you have?"))["reply"] == "I'm carrying 9 oak log (pack)."
            assert (await ask("which swords do you have"))["reply"] == "I have no sword."
            assert (await ask("check your inventory"))["reply"].startswith("I'm carrying 9 oak log (pack), 6 bread")
            assert (await ask("do you have bread?"))["reply"] == "Yes - I have 6 bread."
            assert facts(e) == n0                                          # never a memory write
    run(go())


def test_o166_mob_targeting_is_accepted_and_kept_not_logged(tmp_path):
    """sense 0.1.25 (link v1.13): mob.targeting frames are accepted (no unknown_type) and kept per entity."""
    async def go():
        async with mind_env(tmp_path) as e:
            await until(lambda: e.mind.fresh_state() is not None)
            await e.sense.send("mob.targeting", {"entity_uuid": "z1", "entity_id": 77, "entity_type": "minecraft:zombie",
                                                 "target": "owner", "target_name": "Hunter", "dim": "minecraft:overworld",
                                                 "pos": {"x": 3, "y": 64, "z": 1}, "tick": 1})
            await until(lambda: 77 in e.mind.mob_targets)
            assert e.mind.db.execute("SELECT COUNT(*) FROM events WHERE kind='mob.targeting'").fetchone()[0] == 0
    run(go())


def test_o173_mob_targeting_is_relayed_only_to_a_body_that_accepts_it(tmp_path, monkeypatch):
    import fake_body
    frame = {"entity_uuid": "z1", "entity_id": 78, "entity_type": "minecraft:zombie", "target": "eve",
             "target_name": "FED_Chairman", "dim": "minecraft:overworld", "pos": {"x": 3, "y": 64, "z": 1}, "tick": 1}

    async def go(accepts):
        monkeypatch.setattr(fake_body.FakeBody, "accepts", accepts)
        async with mind_env(tmp_path / str(bool(accepts))) as e:
            await until(lambda: e.mind.fresh_state() is not None)
            await e.sense.send("mob.targeting", frame)
            await until(lambda: 78 in e.mind.mob_targets)
            if accepts:
                m = await e.body.wait_for(lambda m: m["type"] == "mob.targeting")
                assert m["payload"] == frame
            else:
                import asyncio
                await asyncio.sleep(0.3)
                assert not any(m["type"] == "mob.targeting" for m in e.body.received)
    for a in ((), ("mob.targeting",)):
        (tmp_path / str(bool(a))).mkdir()
        run(go(a))
