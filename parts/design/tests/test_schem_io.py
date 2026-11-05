"""CP-47: schematic read/round-trip against the hand-written fixture manifest."""
import json
import os
from pathlib import Path

import pytest
Schematic = pytest.importorskip("litemapy").Schematic   # optional GPL-3.0 extra

from eve_design import gamedata
from eve_design.grid import AIR, rotate_state, rotated
from eve_design.schem_io import (SchematicError, UnsupportedFeatures, load, load_litematic, require_supported,
                                  save_litematic, save_schem)

FIX = Path(__file__).parent / "fixtures" / "schematics"
CASES = json.loads((FIX / "manifest.json").read_text())["cases"]
GD = gamedata.load()


def fixture_path(case, tmp_path):
    if "generate" in case:
        p = tmp_path / case["file"]
        p.write_bytes(os.urandom(case["generate"]["random_bytes"]))
        return p
    return FIX / case["file"]


def check(g, issues, exp):
    assert list(g.size) == exp["size"]
    if "offset" in exp:
        assert g.meta["source"]["offset"] == exp["offset"]
    assert sum(g.counts().values()) == exp["nonair"]
    if "distinct" in exp:
        assert len(g.counts()) == exp["distinct"]
    assert sorted({i["feature"] for i in issues}) == exp["issues"]
    assert len(g.block_entities) == exp.get("block_entities", 0)
    for k, state in exp["probes"].items():
        x, y, z = map(int, k.split(","))
        assert g.get(x, y, z) == state, k


def same(a, b):
    assert a.size == b.size and a.same_blocks(b)
    key = lambda be: (be["pos"]["x"], be["pos"]["y"], be["pos"]["z"])
    assert sorted(a.block_entities, key=key) == sorted(b.block_entities, key=key)
    assert a.meta["source"]["offset"] == b.meta["source"]["offset"]


def test_manifest_coverage():
    classes = [c["class"] for c in CASES]
    assert classes.count("normal") >= 10
    assert len(classes) - classes.count("normal") >= 10
    assert len({c["file"] for c in CASES}) == len(CASES)


@pytest.mark.parametrize("case", CASES, ids=[c["file"] for c in CASES])
def test_fixture(case, tmp_path):
    path, exp = fixture_path(case, tmp_path), case["expect"]
    if "error" in exp:
        with pytest.raises(SchematicError, match=None) as ei:
            load(path, GD)
        assert exp["error"] in str(ei.value)
        return
    g, issues = load(path, GD)
    check(g, issues, exp)
    assert g.meta["import_issues"] == issues
    if issues:
        with pytest.raises(UnsupportedFeatures):
            require_supported(issues)

    # round trip through every writer, re-read with our reader
    outs = {"litematic": tmp_path / "rt.litematic", "schem3": tmp_path / "rt3.schem", "schem2": tmp_path / "rt2.schem"}
    save_litematic(g, outs["litematic"], GD)
    save_schem(g, outs["schem3"], GD, version=3)
    save_schem(g, outs["schem2"], GD, version=2)
    persistent = {"invalid_block_state", "container_contents", "unsupported_block_entity"}  # entities/ticks/overlap are not written
    for p in outs.values():
        g2, issues2 = load(p, GD)
        same(g, g2)
        check(g2, issues2, exp | {"issues": sorted(set(exp["issues"]) & persistent)})

    # independent reader (litemapy) agrees with what we wrote
    sch = Schematic.load(str(outs["litematic"]))
    assert sch.mc_version == GD.data_version and sch.lm_version == 7
    reg = next(iter(sch.regions.values()))
    assert reg.count_blocks() == exp["nonair"]
    ox, oy, oz = g.meta["source"]["offset"]
    for k, state in exp["probes"].items():
        x, y, z = map(int, k.split(","))
        b = reg[x, y, z]
        got = b.id + ("[" + ",".join(f"{a}={v}" for a, v in sorted(b.properties())) + "]" if len(b) else "")
        assert got == state, k
    assert (reg.x, reg.y, reg.z) == (ox, oy, oz)


def test_rotation_hand_expectations():
    g, _ = load(FIX / "n02_mixed_states.litematic", GD)
    r = rotated(g, 1, GD)  # clockwise: (x, z) -> (depth-1-z, x); size 4x3x2 -> 2x3x4
    assert r.size == (2, 3, 4)
    assert r.get(1, 0, 0) == "minecraft:oak_stairs[facing=south,half=top,shape=straight,waterlogged=false]"
    assert r.get(1, 0, 2) == "minecraft:oak_door[facing=west,half=lower,hinge=right,open=false,powered=false]"
    assert r.get(0, 0, 3) == "minecraft:oak_log[axis=z]"
    assert rotate_state("minecraft:oak_sign[rotation=14,waterlogged=false]", 1, GD) == "minecraft:oak_sign[rotation=2,waterlogged=false]"
    assert rotate_state("minecraft:rail[shape=south_east,waterlogged=false]", 1, GD) == "minecraft:rail[shape=south_west,waterlogged=false]"
    assert rotate_state("minecraft:oak_fence[east=false,north=true,south=false,waterlogged=false,west=false]", 1, GD) \
        == "minecraft:oak_fence[east=true,north=false,south=false,waterlogged=false,west=false]"
    four = rotated(rotated(rotated(r, 1, GD), 1, GD), 1, GD)
    assert four.same_blocks(g)


def test_unknown_block_never_substituted(tmp_path):
    g, issues = load(FIX / "m15_unknown_block.schem", GD)
    save_litematic(g, tmp_path / "x.litematic", GD)
    g2, issues2 = load_litematic(tmp_path / "x.litematic", GD)
    assert g2.get(0, 0, 0) == "minecraft:unobtainium" and AIR not in g2.counts()
    assert [i["feature"] for i in issues2] == ["invalid_block_state"]
