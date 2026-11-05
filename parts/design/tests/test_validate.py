"""CP-60: every planted fault is caught by the right check with a useful location; valid counterexamples pass."""
import json
import runpy
from functools import lru_cache
from pathlib import Path

import pytest

from eve_design import bom, gamedata, scaffold
from eve_design.compiler import compile_file, compile_program
from eve_design.grid import AIR, Design
from eve_design.validate import validate

HERE = Path(__file__).parent
EX = HERE.parent / "examples"
GD = gamedata.load()
CASES = json.loads((HERE / "fixtures/validate/manifest.json").read_text())["cases"]


@lru_cache(maxsize=None)
def base_json(name):
    if name == "hut5":
        # O89i: hut5 as built has a step-up door (its schematic is reported, not gated); the base hut here gets the
        # doorstep the door rule needs, at floor height in front of its door, as hut5_tall has
        h = runpy.run_path(str(EX / "hut5.py"))["build_hut"](GD)[0]
        g = Design((h.size[0], h.size[1], h.size[2] + 1), h.origin)
        for x, y, z, s, r in h.cells():
            g.set(x, y, z, s, r)
        g.rooms, g.meta = h.rooms, h.meta
        g.set(2, 0, 5, "minecraft:cobblestone", "foundation")
    else:
        g = compile_file(EX / f"{name}.eve", GD)
    return json.dumps(g.to_json())


def build(case):
    if "layers" in case:
        L = case["layers"]
        g = Design((len(L[0][0]), len(L), len(L[0])))
        for y, rows in enumerate(L):
            for z, row in enumerate(rows):
                for x, ch in enumerate(row):
                    if ch != ".":
                        g.set(x, y, z, *case["key"][ch])
        assert not g.canonicalize(GD)
        return g, None
    g = Design.from_json(json.loads(base_json(case["base"])))
    planted = None
    for x, y, z, state, role in case.get("set", []):
        g.set(x, y, z, GD.canonical(state), role)
    if "remove_role" in case:
        for x, y, z, s, r in list(g.cells()):
            if r == case["remove_role"]:
                g.set(x, y, z, AIR)
    if case.get("remove_stair_runs"):  # each step and everything under it down to the floor
        for x, y, z, s, r in list(g.cells()):
            if r == "stair":
                for yy in range(y, 0, -1):
                    if g.role_at(x, yy, z) == "floor":
                        break
                    g.set(x, yy, z, AIR)
    if "remove_role_in_room" in case:
        role, rid = case["remove_role_in_room"]
        room = next(r for r in g.rooms if r["id"] == rid)
        for x, y, z, s, r in list(g.cells()):
            if r == role and all(room["min"][k] <= v <= room["max"][k] for k, v in zip("xyz", (x, y, z))):
                g.set(x, y, z, AIR)
    if case.get("plant") == "floating_block":   # an air cell with nothing around it, as high as possible
        sx, sy, sz = g.size
        planted = next((x, y, z) for y in range(sy - 2, 1, -1) for z in range(1, sz - 1) for x in range(1, sx - 1)
                       if all(g.get(x + a, y + b, z + c) == AIR for a, b, c in
                              ((0, 0, 0), (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))))
        g.set(*planted, "minecraft:oak_planks", "trim")
    g.canonicalize(GD)
    return g, planted


def planned(case, g):
    """The base example's committed scaffold (planning a two-storey house takes minutes); planted faults are found by
    their own checks before the placement simulation."""
    base = case.get("base") or ""
    p = EX / "out" / {"hut5": "hut5_sim"}.get(base, f"{base}_v2") / "scaffold.json"   # O243: the compiled bases' v2
    return Design.load(p) if case.get("base") and p.exists() else scaffold.plan(g, GD)[0]


def run(case):
    g, planted = build(case)
    s = planned(case, g) if case.get("scaffold") == "plan" else None
    b = bom.compute(g, GD, s) if case.get("bom") else None
    return g, planted, validate(g, GD, brief=case.get("brief"), bom=b, scaffold=s, policy=case.get("policy"))


def test_manifest_coverage():
    cats = [c["category"] for c in CASES]
    for cat in ("site_fit", "walkable", "enclosed", "lit", "functional", "stable", "fire_safe", "buildable",
                "affordable", "within_limits"):
        assert cats.count(cat) >= 2, cat
    assert cats.count("valid") >= 2


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case(case):
    g, planted, v = run(case)
    exp = case["expect"]
    assert v["design_hash"] == g.hash()
    if exp.get("pass"):
        fails = {k: c["failures"][:3] for k, c in v["checks"].items() if c["status"] == "fail"}
        assert v["passed"], fails
        if "needs_approval" in exp:
            assert v["needs_approval"] is exp["needs_approval"]
        return
    check = v["checks"][exp["fail"]]
    assert check["status"] == "fail" and not v["passed"]
    hits = [f for f in check["failures"] if exp["reason"] in f["reason"]]
    assert hits, check["failures"][:5]
    pos = exp["pos"]
    if pos is None:
        assert any(f["pos"] is None for f in hits)
    elif pos == "any":
        assert all(f["pos"] is not None for f in hits)
    elif pos == "planted":
        assert any(f["pos"] == dict(zip("xyz", planted)) for f in hits)
    elif isinstance(pos, str) and pos.startswith("in_room:"):
        room = next(r for r in g.rooms if r["id"] == pos.split(":")[1])
        assert all(all(room["min"][k] <= f["pos"][k] <= room["max"][k] for k in "xyz") for f in hits)
    elif "room" in pos:
        room = next(r for r in g.rooms if r["id"] == pos["room"])
        assert any(f["pos"] == room["min"] for f in hits)
    else:
        assert any(f["pos"] == pos for f in hits), [f["pos"] for f in hits]
    # the planted fault is the only reason for this check to fail on a base that is otherwise valid
    if "base" in case and case.get("scaffold") != "none":
        base = {**case, "set": [], "remove_role": None, "remove_role_in_room": None, "remove_stair_runs": None, "plant": None,
                "brief": None, "policy": None}
        base = {k: v for k, v in base.items() if v is not None}
        assert run(base)[2]["checks"][exp["fail"]]["status"] != "fail"


def test_a_door_needs_a_level_approach_on_both_sides():
    """O89i (builder, lane C hut5_tall a12/a13): Eve's Baritone enters or leaves a door cell only on a level move.
    hut5_tall's doorstep (2,0,5) is the level approach on its door's south side; without it the door is a step up."""
    from eve_design.validate import Grid, check_walkable
    g = runpy.run_path(str(EX / "hut5_tall.py"))["build"](GD)
    door_fails = lambda G: [f for f in check_walkable(G, G.walk())["failures"] if f["reason"].startswith("door at")]
    assert not door_fails(Grid(g, GD))
    g.set(2, 0, 5, AIR)
    f = door_fails(Grid(g, GD))
    assert len(f) == 1 and f[0]["pos"] == {"x": 2, "y": 1, "z": 4} and "on its south side" in f[0]["reason"]


def test_a_dark_or_raised_room_says_why():
    """45L h10 (attempts 7-8): revisions lost a tiny kitchen's lantern (furnish=False; a second door leaving no free
    edge cell) and raised its foundation for a "stone course", putting the door out of reach. The failures say so."""
    prog = 'c = Build(style="cozy_cabin")\nc.foundation(w=7, d=7{h})\nc.walls(h=4)\nc.door(side="south")\n' \
           'c.room("kitchen", furnish={f})\nc.roof("flat")\n'
    v = validate(compile_program(prog.format(h="", f="False"), GD), GD, skip_buildable=True)
    assert "no light source in the room" in v["checks"]["lit"]["failures"][0]["reason"]
    v = validate(compile_program(prog.format(h=", h=2", f="True"), GD), GD, skip_buildable=True)
    walk = [f["reason"] for f in v["checks"]["walkable"]["failures"]]
    assert walk[0].startswith("door at (4,2,7) has no level approach on its south side: the ground there is 2 blocks")
    assert any("cannot be reached" in r and 'detail(rule="base_band")' in r for r in walk)


def test_o94x_hut5_sim_v1_fails_on_its_door_and_v2_with_the_doorstep_passes():
    """O94x: hut5_sim v1 (90a0175d, the frozen CP-50 checkpoint, not enterable) has no doorstep, so a fresh validate fails
    walkable on its door; v2 (examples/hut5.py build_hut_v2, a doorstep at (2,0,5)) passes, and its committed folder
    is held by the gate until an owner approval (O94q)."""
    v1 = Design.load(EX / "out" / "hut5_sim" / "design.json")
    assert v1.hash().startswith("90a0175d")
    walk = validate(v1, GD, skip_buildable=True)["checks"]["walkable"]
    assert walk["status"] == "fail"
    assert walk["failures"][0]["reason"].startswith("door at (2,1,4) has no level approach on its south side")
    v2 = runpy.run_path(str(EX / "hut5.py"))["build_hut_v2"](GD)
    assert v2.meta["version"] == 2 and validate(v2, GD, skip_buildable=True)["checks"]["walkable"]["status"] == "pass"
    out = EX / "out" / "hut5_sim_v2"
    assert Design.load(out / "design.json").hash() == v2.hash()
    g, v = json.loads((out / "gate.json").read_text()), json.loads((out / "validation.json").read_text())
    assert (g["category"], g["decision"]) == ("eve_own", "hold") and v["passed"] and v["design_hash"] == v2.hash()


def test_o94x_a_compiled_door_gets_its_doorstep_automatically():
    """The generator side of the door rule: the compiler adds the level approach (O89l), so a compiled house passes."""
    g = compile_file(EX / "two_storey.eve", GD)
    assert g.meta["provenance"]["doorsteps"]
    assert validate(g, GD, skip_buildable=True)["checks"]["walkable"]["status"] == "pass"


def test_o94ag_stand_audit_warns_a_compiled_design_and_fails_an_organic_one():
    """O94ag: stand_audit counts planned stands on the design's exterior. hut5_sim (frozen v1, built on lane C with its
    roof stands) is a warning; the same stands on an eve-organic design fail (C2 placed 33 % from such stands)."""
    from eve_design import buildorder
    from eve_design.validate import check_stand_audit
    g, s = Design.load(EX / "out/hut5_sim/design.json"), Design.load(EX / "out/hut5_sim/scaffold.json")
    steps = buildorder.plan_order(g, GD, s)[0]
    r = check_stand_audit(g, steps, s)
    assert r["status"] == "warn" and not r["failures"] and "exterior roof" in r["notes"][0]
    org = Design.from_json(g.to_json())
    org.meta["provenance"] = {**g.meta["provenance"], "pipeline": "eve-organic/1"}
    r = check_stand_audit(org, steps, s)
    assert r["status"] == "fail" and r["failures"][0]["reason"].startswith("step ")
    assert check_stand_audit(g, None, s, ran=False)["status"] == "skipped"
    v = validate(g, GD, scaffold=s)
    assert v["checks"]["stand_audit"]["status"] == "warn" and v["validator_version"] == "eve-validate/3"


def test_o94ag_stand_audit_follows_scaffold_down_and_says_n_a_without_a_build_order():
    """O94ag review: a stand on dirt counts by what the dirt column rests on (a column started on the roof is reached
    over the roof); with buildable skipped the audit is skipped, without a build order it is n/a (not "skipped", which
    validation.json reserves for checks that need a brief)."""
    from eve_design.validate import check_stand_audit
    g = Design.load(EX / "out/hut5_sim/design.json")
    s = Design((1, 1, 1), (g.origin[0], g.origin[1] + 5, g.origin[2]))   # one dirt on the roof corner (0,4,0)
    s.set(0, 0, 0, "minecraft:dirt", "support")
    step = {"step": 1, "layer": "final", "stand": [0, 6, 0]}
    r = check_stand_audit(g, [step], s)
    assert r["status"] == "warn" and "exterior roof (under scaffold) 1" in r["notes"][0]
    assert check_stand_audit(g, None, None, ran=False)["status"] == "skipped"
    assert check_stand_audit(g, None, None, ran=True)["status"] == "n/a"
