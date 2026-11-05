"""CP-53 planner side (O40) on fakes, against the proposed registry 0.11.0 (bridge): pick a void edge that faces
the target, require a recovery item over a deep drop, walk there, dispatch bridge{to}, cross-check its evidence."""
import contextlib
import io
import json
import runpy

import pytest

from conftest import ROOT, mind_env, run, until

TO = {"x": 20, "y": 70, "z": 0}
EDGE = {"pos": {"x": 10, "y": 70, "z": 0}, "block": "minecraft:end_stone", "state": "", "toward": ["east"], "depth": None}
SIDE = {"pos": {"x": 10, "y": 70, "z": 3}, "block": "minecraft:end_stone", "state": "", "toward": ["south"], "depth": None}


@pytest.fixture
def reg11(tmp_path, monkeypatch):
    import fake_body
    from eve_mind import config
    applied = json.loads((ROOT / "contracts" / "skills" / "registry.json").read_text("utf-8"))
    if "bridge" not in {s["name"] for s in applied["skills"]}:
        with contextlib.redirect_stdout(io.StringIO()):
            applied = runpy.run_path(str(ROOT / "parts" / "builder" / "registry" / "builder-0.11.0.py"),
                                     run_name="x")["reg"]
        f = tmp_path / "registry-0.11.0.json"
        f.write_text(json.dumps(applied), encoding="utf-8")
        monkeypatch.setattr(fake_body, "REGISTRY", f)
        monkeypatch.setattr(config.Config, "registry_path", property(lambda self: f))
    return applied


async def bridge(e, to=TO):
    want = {x["item"] for x in e.body.inventory}
    await until(lambda: {x["item"] for x in (e.mind.fresh_state() or {}).get("inventory", [])} == want, timeout=3)
    r = e.mind.bridge.start(to)
    assert r["ok"], r
    await until(lambda: e.mind.bridge.last["status"] != "running", timeout=10)
    return e.mind.bridge.last


def sent(e):
    return [(x["skill"], json.loads(x["params"])) for x in e.mind.db.execute("SELECT skill, params FROM actions "
                                                                            "ORDER BY rowid")]


def test_bridges_from_the_facing_edge_with_a_recovery_item_and_checks_evidence(tmp_path, reg11):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 0.02
            e.sense.void_edges = [SIDE, EDGE]
            e.body.inventory = [{"slot": 0, "item": "minecraft:ender_pearl", "count": 2},
                                {"slot": 1, "item": "minecraft:cobblestone", "count": 64}]
            e.body.evidence_extra = {"bridge": [{"start": EDGE["pos"], "end": TO, "length": 10}]}
            r = await bridge(e)
            assert r["status"] == "done" and r["edge"] == EDGE["pos"] and r["toward"] == "east", r
            assert sent(e) == [("walk_to", {"pos": {"x": 10.5, "y": 71, "z": 0.5}}), ("bridge", {"to": TO})]
            assert e.sense.resource_asks[0]["kind"] == "void_edge"
    run(go())


def test_refuses_without_a_facing_edge_or_without_recovery_and_reports_evidence_mismatch(tmp_path, reg11):
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 0.02
            e.sense.void_edges = [SIDE]                                   # faces south, target is east
            e.body.inventory = [{"slot": 0, "item": "minecraft:ender_pearl", "count": 2}]
            r = await bridge(e)
            assert r["status"] == "refused" and "no edge faces" in r["reason"] and not sent(e), r
            e.sense.void_edges = [EDGE]
            e.body.inventory = [{"slot": 0, "item": "minecraft:cobblestone", "count": 64}]
            r = await bridge(e)
            assert r["status"] == "refused" and "ender pearl" in r["reason"] and not sent(e), r
            e.sense.void_edges = [{**EDGE, "depth": 2}]                  # a safe drop needs no recovery item
            e.body.evidence_extra = {"bridge": [{"start": {"x": 9, "y": 70, "z": 0}, "end": TO, "length": 11}]}
            r = await bridge(e)
            assert r["status"] == "done_with_mismatch" and len(r["problems"]) == 2, r
    run(go())


def test_owner_chat_bridge_command_is_explicit_and_stop_still_wins(tmp_path, reg11):
    async def go():
        async with mind_env(tmp_path) as e:
            e.sense.void_edges = []
            await e.mind.owner_text("bridge to 20 70 0", "Hunter")
            await until(lambda: e.mind.bridge.last and e.mind.bridge.last["status"] != "running", timeout=10)
            assert e.mind.bridge.last["status"] == "refused"
            r = await e.mind.owner_text("stop", "Hunter")      # O120 addendum: chat stop cancels, no latch
            assert r["stopped"] and not e.mind.bridge.running() and not e.mind.stopped
    run(go())


def test_recovery_items_match_the_bridge_skill(tmp_path, reg11):
    """Builder's bridge (after b47803c): a water bucket counts only with ground under the gap; a slow falling potion
    counts by its contents; evidence.depth is cross-checked against sense's void_edge depth."""
    async def go():
        async with mind_env(tmp_path) as e:
            e.body.walk_s = 0.02
            e.body.inventory = [{"slot": 0, "item": "minecraft:water_bucket", "count": 1}]
            e.sense.void_edges = [EDGE]                                   # open void: a bucket is no recovery
            assert (await bridge(e))["status"] == "refused"
            e.sense.void_edges = [{**EDGE, "depth": 12}]                  # ground 12 below: the bucket counts
            e.body.evidence_extra = {"bridge": [{"start": EDGE["pos"], "end": TO, "length": 10, "depth": 12}]}
            r = await bridge(e)
            assert r["status"] == "done" and r["recovery"] == ["minecraft:water_bucket"], r
            e.body.inventory = [{"slot": 0, "item": "minecraft:potion", "count": 1, "potion": "minecraft:slow_falling"}]
            e.sense.void_edges = [EDGE]
            e.body.evidence_extra = {"bridge": [{"start": EDGE["pos"], "end": TO, "length": 10, "depth": 9}]}
            r = await bridge(e)
            assert r["status"] == "done", r                               # sense said open void, skill measured 9
            e.sense.void_edges = [{**EDGE, "depth": 12}]
            e.body.evidence_extra = {"bridge": [{"start": EDGE["pos"], "end": TO, "length": 10, "depth": 9}]}
            r = await bridge(e)
            assert r["status"] == "done_with_mismatch" and "depth" in r["reason"], r
    run(go())
