"""CP-51 (mind side): build an approved design, gathering missing supplies, on fakes. build_schematic is modelled from
the fake inventory: it refuses no_item with builder's deficit (want/inventory/missing) until Eve holds everything."""
import json
from collections import Counter

import pytest

from conftest import design_folder, mind_env, run, until

PLANKS, LOG, GLASS = "minecraft:oak_planks", "minecraft:oak_log", "minecraft:ender_pearl"
SLAB, TABLE = "minecraft:oak_slab", {"x": 14, "y": 64, "z": 10}
CHEST = {"x": 10, "y": 64, "z": 10}


def inv(body):
    c = Counter()
    for s in body.inventory:
        c[s["item"]] += s["count"]
    return c


def set_inv(body, c):
    body.inventory = [{"slot": i, "item": k, "count": v} for i, (k, v) in enumerate(sorted((+c).items()))]


def fake_world(body, want, chests, full_once=False):
    """chests: {container_id: Counter}; withdraw/deposit/craft move items; build needs `want` in the inventory."""
    orig, state = body.execute, {"full": full_once, "builds": 0}

    async def execute(aid, p, req_id):
        skill, params = p["skill"], p["params"]
        body.result_override = None
        have = inv(body)
        if skill == "build_schematic":
            state["builds"] += 1
            missing = +(Counter(want) - have)
            if missing:
                body.result_override = ("failed", "no_item")
                body.evidence_extra = {"build_schematic": [{"deficit": {
                    "want": dict(want), "inventory": {k: min(have[k], n) for k, n in want.items() if have[k]},
                    "missing": dict(missing)}}]}
            else:
                set_inv(body, have - Counter(want))
        elif skill in ("withdraw", "deposit"):
            cid = next(k for k, (pos, _) in chests.items() if pos == params["pos"])
            box = chests[cid][1]
            if skill == "withdraw" and state["full"]:
                state["full"] = False
                body.result_override = ("failed", "inventory_full")
            elif skill == "withdraw" and box[params["item"]] >= params["count"]:
                box[params["item"]] -= params["count"]
                have[params["item"]] += params["count"]
                set_inv(body, have)
            elif skill == "deposit":
                box[params["item"]] += params["count"]
                have[params["item"]] -= params["count"]
                set_inv(body, have)
            else:
                body.result_override = ("failed", "insufficient")
        elif skill == "craft" and params["item"] == SLAB:
            batches = -(-params["count"] // 6)
            if have[PLANKS] >= 3 * batches and params.get("pos"):
                have[PLANKS] -= 3 * batches
                have[SLAB] += 6 * batches
                set_inv(body, have)
            else:
                body.result_override = ("failed", "no_workstation" if not params.get("pos") else "missing_ingredients")
        elif skill == "craft" and params["item"] == PLANKS:
            batches = -(-params["count"] // 4)
            if have[LOG] >= batches:
                have[LOG] -= batches
                have[PLANKS] += 4 * batches
                set_inv(body, have)
            else:
                body.result_override = ("failed", "missing_ingredients")
        return await orig(aid, p, req_id)
    body.execute = execute
    return state


async def setup(e, designs, chests, tag="shared"):
    h = design_folder(designs, "hut", 1)[1]
    assert e.mind.decide("approve", "hut", "v1", None, "cli")["ok"]
    await e.sense.send_storage([{"id": cid, "dim": "minecraft:overworld", "pos": pos, "kind": "minecraft:chest",
                                 "tag": tag, "loaded": True, "observed_tick": 1,
                                 "items": [{"item": k, "count": v} for k, v in items.items()]}
                                for cid, (pos, items) in chests.items()])
    await until(lambda: e.mind.sense_tick is not None and e.mind.db.execute(
        "SELECT count(*) FROM containers").fetchone()[0] == len(chests))
    e.body.walk_s = 0.02
    return {"project_id": "hut", "version": 1, "design_hash": h}


async def build(e, p, timeout=20):
    r = await e.mind.cli("build", p) if hasattr(e.mind, "cli") else e.mind.supply.start(p)
    assert r["ok"], r
    await until(lambda: e.mind.supply.current.status != "running", timeout=timeout, what="supply run ends")
    return e.mind.supply.status(r["supply_id"])


def skills_sent(e):
    return [r["skill"] for r in e.mind.db.execute("SELECT skill FROM actions ORDER BY rowid")]


def needs_build(e):
    if "build_schematic" not in e.mind.registry.skills:
        pytest.skip("registry without build_schematic")


def test_half_the_materials_in_chests_are_withdrawn_then_the_build_finishes(tmp_path):
    designs = tmp_path / "designs"

    async def go():
        async with mind_env(tmp_path, designs_dir=designs) as e:
            needs_build(e)
            chests = {"c1": (CHEST, Counter({PLANKS: 10}))}
            p = await setup(e, designs, chests)
            set_inv(e.body, Counter({PLANKS: 10}))
            world = fake_world(e.body, {PLANKS: 20}, chests)
            r = await build(e, p)
            assert r["status"] == "done", r
            assert world["builds"] == 2 and chests["c1"][1][PLANKS] == 0
            assert skills_sent(e) == ["build_schematic", "walk_to", "withdraw", "build_schematic"]
            assert not e.mind.db.execute("SELECT count(*) FROM reservations WHERE released_ts IS NULL").fetchone()[0]
            rows = [json.loads(x) for x in (tmp_path / "candidates.jsonl").read_text("utf-8").splitlines()]
            wid = e.mind.db.execute("SELECT action_id FROM actions WHERE skill='withdraw'").fetchone()[0]
            assert [(x["decision_type"], x["pick"], x["action_id"]) for x in rows] == [("supply_source", "c1", wid)]
            assert rows[0]["options"][0]["score"] == 10 and rows[0]["runner"] == "supply"     # O94f
    run(go())


def test_a_recipe_covers_the_rest_from_stored_logs(tmp_path):
    designs = tmp_path / "designs"

    async def go():
        async with mind_env(tmp_path, designs_dir=designs) as e:
            needs_build(e)
            chests = {"c1": (CHEST, Counter({PLANKS: 4, LOG: 2}))}
            p = await setup(e, designs, chests)
            world = fake_world(e.body, {PLANKS: 12}, chests)
            r = await build(e, p)
            assert r["status"] == "done", r
            sent = skills_sent(e)
            assert sent.count("withdraw") == 2 and "craft" in sent and sent[-1] == "build_schematic"
            assert world["builds"] == 2
    run(go())


def test_an_inaccessible_item_is_reported_short_without_gathering_or_placing(tmp_path):
    designs = tmp_path / "designs"

    async def go():
        async with mind_env(tmp_path, designs_dir=designs) as e:
            needs_build(e)
            chests = {"c1": (CHEST, Counter({GLASS: 5}))}
            p = await setup(e, designs, chests, tag="private")      # Eve never opens a private chest
            fake_world(e.body, {GLASS: 3}, chests)
            r = await build(e, p)
            assert r["status"] == "short" and GLASS in r["report"] and "approved alternative" in r["report"], r
            assert r["short"][GLASS][0] == 3 and "only in a private container at 10 64 10" in r["short"][GLASS][1], r
            assert skills_sent(e) == ["build_schematic"]           # refused before placing; nothing gathered
    run(go())


def test_a_full_inventory_makes_room_with_surplus_then_withdraws(tmp_path):
    designs = tmp_path / "designs"

    async def go():
        async with mind_env(tmp_path, designs_dir=designs) as e:
            needs_build(e)
            chests = {"c1": (CHEST, Counter({PLANKS: 8}))}
            p = await setup(e, designs, chests)
            set_inv(e.body, Counter({"minecraft:dirt": 64, "minecraft:stone_pickaxe": 1}))
            fake_world(e.body, {PLANKS: 8}, chests, full_once=True)
            r = await build(e, p)
            assert r["status"] == "done", r
            dep = [r for r in e.mind.db.execute("SELECT params FROM actions WHERE skill='deposit'")]
            assert len(dep) == 1 and "minecraft:dirt" in dep[0]["params"] and "pickaxe" not in dep[0]["params"]
    run(go())


def test_supply_probes_and_redispatches_do_not_count_toward_retry_limit(tmp_path):
    """Ruling after CP-51 a1: the opening no_item is a deliberate probe and a re-dispatch after gathering is not a
    retry, so repeated supply builds are never refused retry_limit. A re-dispatch short of an item the loop just
    supplied still counts."""
    designs = tmp_path / "designs"

    async def go():
        async with mind_env(tmp_path, designs_dir=designs, retry_limit=2) as e:
            needs_build(e)
            chests = {"c1": (CHEST, Counter({PLANKS: 30}))}
            p = await setup(e, designs, chests)
            fake_world(e.body, {PLANKS: 8}, chests)
            for _ in range(3):                      # 3 probes in 10 min with identical params
                r = await build(e, p)
                assert r["status"] == "done", r
            # withdraw reports ok but the planks vanish: the re-dispatch is short of what was just gathered -> counts
            orig = e.body.execute

            async def vanishing(aid, msg, req_id):
                out = await orig(aid, msg, req_id)
                if msg["skill"] == "withdraw":
                    e.body.inventory = []
                return out
            e.body.execute = vanishing
            e.body.inventory = []
            chests["c1"][1][PLANKS] = 30           # the fake chest again matches the index
            first = e.mind.db.execute("SELECT max(rowid) FROM actions").fetchone()[0]
            r = await build(e, p)
            assert r["status"] in ("partial", "failed"), r
            notes = [x["reconcile_note"] for x in e.mind.db.execute(
                "SELECT reconcile_note FROM actions WHERE skill='build_schematic' AND rowid > ? ORDER BY rowid",
                (first,))]
            assert notes[0] == "retry_exempt: supply probe" and None in notes[1:], notes
    run(go())


def test_a_table_recipe_uses_a_known_crafting_table_and_says_so_when_none_is_known(tmp_path):
    """CP-51 a2 d: workstations come from the records (storage.index kind, or a block Eve saw placed); with none known
    the short reason says so, counted as the build's shortfall (not the recipe batch)."""
    designs = tmp_path / "designs"

    async def go():
        async with mind_env(tmp_path, designs_dir=designs) as e:
            needs_build(e)
            chests = {"c1": (CHEST, Counter({PLANKS: 3}))}
            p = await setup(e, designs, chests)
            fake_world(e.body, {SLAB: 4}, chests)
            r = await build(e, p)
            assert r["status"] == "short" and tuple(r["short"][SLAB]) == (4, "it needs a crafting_table and I know none"), r
            await e.sense.send("block.event", {"events": [{"kind": "place", "dim": "minecraft:overworld", "pos": TABLE,
                                                           "actor": "player", "after": "minecraft:crafting_table"}]})
            await until(lambda: e.mind.supply.workstation("crafting_table") is not None, timeout=3)
            r = await build(e, p)
            assert r["status"] == "done", r
            import json
            crafts = [json.loads(x["params"]) for x in e.mind.db.execute("SELECT params FROM actions WHERE skill='craft'")]
            assert crafts and crafts[-1]["pos"] == TABLE and crafts[-1]["item"] == SLAB, crafts
    run(go())


def test_a_crafting_table_in_the_storage_index_is_a_known_workstation(tmp_path):
    designs = tmp_path / "designs"

    async def go():
        async with mind_env(tmp_path, designs_dir=designs) as e:
            needs_build(e)
            chests = {"c1": (CHEST, Counter({PLANKS: 3}))}
            p = await setup(e, designs, chests)
            await e.sense.send_storage([{"id": "c1", "dim": "minecraft:overworld", "pos": CHEST, "kind": "minecraft:chest",
                                         "tag": "shared", "loaded": True, "observed_tick": 2,
                                         "items": [{"item": PLANKS, "count": 3}]},
                                        {"id": "t1", "dim": "minecraft:overworld", "pos": TABLE,
                                         "kind": "minecraft:crafting_table", "tag": "shared", "loaded": True,
                                         "observed_tick": 2, "items": []}])
            await until(lambda: e.mind.supply.workstation("crafting_table") == TABLE, timeout=3)
            fake_world(e.body, {SLAB: 4}, chests)
            assert (await build(e, p))["status"] == "done"
    run(go())
