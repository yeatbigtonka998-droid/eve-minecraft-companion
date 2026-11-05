"""Link v1.9 (CP-54): /cli survey|resources|storage are read-only passthroughs - chunked query.blocks in order,
query.resources forwarded, the storage snapshot from the index; nothing dispatched, no memory written, refused while
STOPPED, every result names the server tick."""
from conftest import mind_env, run, until


def test_survey_chunks_resources_storage_and_stop(tmp_path):
    async def go():
        async with mind_env(tmp_path) as e:
            m = e.mind
            e.sense.blocks = {(0, 64, 0): "minecraft:stone", (40, 64, 0): "minecraft:oak_planks"}
            await e.sense.send_storage([])
            await until(lambda: m.sense_tick is not None)
            facts0 = len(m.memory.all(m.world_id))
            r = await m.cli("survey", {"min": {"x": 0, "y": 64, "z": 0}, "max": {"x": 40, "y": 65, "z": 3}})
            assert r["ok"] and r["tick"] is not None and len(r["chunks"]) == 2, r      # 41 wide: two 32-wide queries
            assert [c["min"]["x"] for c in r["chunks"]] == [0, 32] and r["chunks"][1]["max"]["x"] == 40
            got = {(d["pos"]["x"], c["palette"][d["i"]]) for c in r["chunks"] for d in c["data"]}
            assert got == {(0, "minecraft:stone"), (40, "minecraft:oak_planks")}
            assert all((c["max"]["x"] - c["min"]["x"] + 1) * (c["max"]["y"] - c["min"]["y"] + 1) *
                       (c["max"]["z"] - c["min"]["z"] + 1) <= 32768 for c in r["chunks"])
            e.sense.crops = [{"pos": {"x": 1, "y": 64, "z": 1}, "block": "minecraft:wheat", "state": "age=7"}]
            res = await m.cli("resources", {"kind": "crops", "center": {"x": 0, "y": 64, "z": 0}, "radius": 16})
            assert res["ok"] and res["items"][0]["block"] == "minecraft:wheat" and e.sense.resource_asks[-1]["kind"] == "crops"
            st = await m.cli("storage", {})
            assert st["ok"] and isinstance(st["containers"], list) and "tick" in st
            assert (await m.cli("survey", {"min": {"x": 0, "y": 0, "z": 0}, "max": {"x": 9999, "y": 300, "z": 9999}})
                    )["error"] == "bad_args"                                       # too many queries
            assert m.db.execute("SELECT count(*) FROM actions").fetchone()[0] == 0   # nothing moved
            assert len(m.memory.all(m.world_id)) == facts0                          # no memory written
            await m.stop("cli:test", "test")
            assert (await m.cli("survey", {"min": {"x": 0, "y": 64, "z": 0}, "max": {"x": 1, "y": 64, "z": 1}})
                    )["error"] == "stopped"
            assert (await m.cli("storage", {}))["error"] == "stopped"
    run(go())
