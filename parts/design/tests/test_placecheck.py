"""Placement simulation with eve-builder's standing/clicking rules (placecheck), on fixtures with known answers."""
import runpy

import pytest
from pathlib import Path

from eve_design import buildorder, gamedata
from eve_design.grid import Design
from eve_design.placecheck import simulate

GD = gamedata.load()
D = Path(__file__).parents[1]
T3 = runpy.run_path(str(D / "examples/test3.py"))


def _fails(g, s=None):
    return {(f["pos"]["x"], f["pos"]["y"], f["pos"]["z"]) for f in buildorder.plan_order(g, GD, s)[1]}


def test_test3_builds_with_and_without_its_dirt_step():
    g = T3["build"](GD)
    # since builder 56cd732 Eve may stand on the bottom slab and the stair, so the step is no longer needed
    assert _fails(g) == set()
    assert _fails(g, T3["step"](g, GD)) == set()


def test_a_log_needs_a_face_along_its_axis():
    g = T3["build"](GD)                      # the axis=z log at (2,0,0) only goes against a block north or south of it
    steps = buildorder.order(g, GD)
    log = next(i for i, st in enumerate(steps) if st["pos"] == [2, 0, 0])
    assert any(st["pos"] in ([2, 0, 1], [2, 0, -1]) for st in steps[:log])


def test_an_early_door_a_step_up_shuts_the_way_in():
    """Baritone opens a door only on a level move (builder, lane C a12/a13). hut5's door stands on its floor, a step up
    from the ground: placed first, it shuts the room once the walls close, and the crafting table after that cannot be
    placed (the chest still goes in over the unbuilt wall)."""
    g = Design.load(D / "examples/out/hut5/design.json")
    early = buildorder.order(g, GD, slots={(2, 1, 4): "first"})
    assert (3, 1, 1) in {(f["pos"]["x"], f["pos"]["y"], f["pos"]["z"]) for f in simulate(g, GD, None, early)[0]}


def test_hut5_with_planned_staircases_builds():
    from eve_design import scaffold
    g = Design.load(D / "examples/out/hut5/design.json")
    s, left = scaffold.plan(g, GD)
    steps, fails, slots = buildorder.plan_order(g, GD, s)
    assert not left and not fails and sum(s.counts().values()) <= 40   # 27 with the O80h aim margin


def test_a_half_sensitive_click_aims_into_its_half():
    from eve_design.placecheck import _aims, _half
    N = (5, 4, 3)
    assert _half("minecraft:oak_stairs[facing=west,half=bottom,shape=straight]") == "bottom"
    assert _half("minecraft:oak_slab[type=top]") == "top" and _half("minecraft:oak_slab[type=double]") is None
    assert _half("minecraft:oak_door[facing=north,half=lower]") is None
    low, high = _aims(N, (1, 0, 0), "bottom"), _aims(N, (1, 0, 0), "top")     # side face: 5 x 3 points in its half
    assert len(low) == 15 and all(4.0 < p[1] < 4.5 for p in low) and all(4.5 < p[1] < 5.0 for p in high)
    assert {round(p[2] - 3.5, 2) for p in low} == {0.0, 0.2, -0.2, 0.4, -0.4} and {round(p[0], 2) for p in low} == {5.01}
    grid = _aims(N, (1, 0, 0), None)                                      # anything else: the body's 5 x 5 grid,
    assert len(grid) == 25 and list(grid[0]) == [5.01, 4.5, 3.5]          # centre first (O80h)
    assert [list(p) for p in _aims(N, (0, -1, 0), None)] == [[5.5, 4.99, 3.5]]   # a top face: its centre
    assert _aims(N, (0, -1, 0), "bottom") and not _aims(N, (0, -1, 0), "top")            # top face gives bottom
    assert _aims(N, (0, 1, 0), "top") and not _aims(N, (0, 1, 0), "bottom")              # underside gives top


def test_builder_a18_roof_stair_stand_is_no_longer_offered(monkeypatch):
    # CP-50 a18/a19: on 279f2e8's hut5_sim scaffold the roof stair (4,4,3) (bottom, facing west) was given stand (6,2,2);
    # from there only rays into the face's upper half are clear, so vanilla made it half=top and builder refused it
    from eve_design import placecheck
    g = Design.load(D / "examples/out/hut5/design.json")
    s = Design.load(D / "tests/fixtures/placecheck/hut5_sim_279f2e8_scaffold.json")
    # the half rule alone: with O80h's aim margin other roof clicks fail first and change what stands there
    monkeypatch.setattr(placecheck, "AIM_TOL", 0.0)
    monkeypatch.setattr(placecheck, "AIM_TOL_SAME", 0.0)              # and the support-stair front check (a30/a31)

    def stand_for_4_4_3():
        got, real = [], placecheck.can_place

        def spy(w, T, state, spots, c):
            r = real(w, T, state, spots, c)
            if (T[0] - w.P, T[1], T[2] - w.P) == (4, 4, 3):
                got.append(r and (r[0] - w.P, r[1], r[2] - w.P))
            return r
        monkeypatch.setattr(placecheck, "can_place", spy)
        buildorder.plan_order(g, GD, s)
        monkeypatch.setattr(placecheck, "can_place", real)
        return got
    assert (6, 2, 2) not in stand_for_4_4_3()
    monkeypatch.setattr(placecheck, "_half", lambda state: None)       # without the rule: the stand builder refused
    assert (6, 2, 2) in stand_for_4_4_3()


def test_o80h_sliver_clicks_are_not_offered_and_roof_stairs_get_raised_stands():
    # CP-50 lane C (b29ba34, both bodies, 147-148/150; body reproduced it on dev): from ground stands only a sliver of
    # the support stair showed above the wall top and the aim sat on its edge; the crosshair's ~0.5 deg tolerance hit
    # air or the neighbour. A click must stay on the face under +-0.5 deg (O80h).
    g = Design.load(D / "examples/out/hut5/design.json")

    def plan(s):
        steps, fails, _ = buildorder.plan_order(g, GD, s)
        key = lambda x: tuple(x["pos"]) if isinstance(x["pos"], list) else tuple(x["pos"][k] for k in "xyz")
        return {key(x): x.get("stand") for x in steps}, {key(f) for f in fails}
    st, fails = plan(Design.load(D / "tests/fixtures/placecheck/hut5_sim_b29ba34_scaffold.json"))
    roof = [(3, 4, 0), (4, 4, 2), (4, 4, 3)]
    assert all(t in fails or st[t][1] >= 1 for t in roof)          # never again from the ground ([4,0,-3], [6,0,2])
    st, fails = plan(Design.load(D / "examples/out/hut5/scaffold.json"))         # the committed (deployed) plan
    assert not fails and st[(3, 4, 0)] == [3, 3, -1]                # the stand that passed live 3/3 (6c6a106)
    from eve_design import scaffold
    s, left = scaffold.plan(g, GD)                                  # a fresh plan (O87h: ranked first, may differ)
    st, fails = plan(s)
    assert not left and not fails and sum(s.counts().values()) <= 40
    assert all(st[t][1] >= 1 for t in roof + [(0, 4, 1), (0, 4, 2), (0, 4, 3)])   # roof stairs from raised stands


def test_o80h_a_steady_click_survives_half_a_degree():
    import numpy as np
    from eve_design import placecheck
    g = Design.load(D / "examples/out/hut5/design.json")
    w = placecheck.World(g, GD, None)
    w.placed[:] = False
    N = (w.P + 2, 1, w.P + 2)
    w.placed[N[1], N[2], N[0]] = True                                # one block standing alone
    d = (-1, 0, 0)                                                    # its east face, seen from the east
    e = np.array([N[0] + 3.5, 1.77, N[2] + 0.5])
    centre = np.array([N[0] + 1.0 - 0.01, 1.5, N[2] + 0.5])
    edge = np.array([N[0] + 1.0 - 0.01, 1.5, N[2] + 0.995])             # on the face, a hair from its edge
    assert placecheck._steady(w, e, centre, N, d, None)
    assert not placecheck._steady(w, e, edge, N, d, None)            # half a degree off it is air


def test_scaffold_planning_stops_at_its_work_budget_and_says_so(monkeypatch):
    # 45L attempt 6, h06 (2200-block walled farmhouse): the planner ran for hours; now it stops and names the reason
    from eve_design import generate, scaffold
    g = Design.load(D / "examples/out/hut5/design.json")
    monkeypatch.setattr(scaffold, "WORK", 3 * 160)          # three simulations of the 160-step hut
    s, left = scaffold.plan(g, GD)
    assert left and left[0]["budget"] and "work budget" in left[0]["reason"]
    rec = {"_built": (g, None, None, None)}
    assert not generate._finish(rec, GD, {})
    assert rec["errors"][0] == f"buildable: scaffold planning stopped at its work budget ({3 * 160} simulated steps)"


def test_scaffold_planning_stops_at_its_time_budget_with_the_best_partial_plan(monkeypatch):
    # O80 (h06 looped again on the 45L): the planner returns within its wall clock, best partial plan + reason
    from eve_design import generate, scaffold
    g = Design.load(D / "examples/out/hut5/design.json")
    monkeypatch.setattr(scaffold, "TIME", 0.0)
    s, left = scaffold.plan(g, GD)
    assert left and left[0]["budget"] and left[0]["reason"] == "scaffold planning stopped at its time budget (0 s)"
    rec = {"_built": (g, None, None, None)}
    assert not generate._finish(rec, GD, {}) and "time budget" in rec["errors"][0]


@pytest.mark.parametrize("budget,value,why", [("SIM_NODES", 10, "node budget"), ("SIM_SECONDS", 0.0, "time budget")])
def test_o68c_a_replay_stops_at_its_budget_and_reports_what_it_did_not_try(monkeypatch, budget, value, why):
    # 45L h06: 28 min on one core in the walking searches; a replay is bounded and says so
    from eve_design import placecheck, scaffold
    g = Design.load(D / "examples/out/hut5/design.json")
    s = Design.load(D / "examples/out/hut5/scaffold.json")
    monkeypatch.setattr(placecheck, budget, value)
    steps, fails, _ = buildorder.plan_order(g, GD, s)
    assert fails and all(f.get("budget") and why in f["reason"] for f in fails[-3:])
    assert len(fails) >= len(steps) // 2                   # stops early: most of the build was not tried


def test_o87g_h04_inn_north_eave_is_a_planner_gap_not_an_impossible_cell():
    """45L recritique on 4f6a665: h04-inn is no_buildable_design, the planner out of its 600 s budget with the north eave
    stairs (9,8,0) and (10,8,0) unplaced. Both have a stand that passes the O80h margin, with a dirt staircase down from
    it: the greedy planner does not reach them in time (287 placements fail unscaffolded, y 7..16); nothing to reject."""
    import json

    import numpy as np

    from eve_design import scaffold
    from eve_design.compiler import compile_program
    from eve_design.placecheck import World, can_place
    from eve_design.validate import classes
    fx = D / "tests/fixtures/placecheck"
    site = json.loads((fx / "h04_inn_site.json").read_text())
    g = compile_program((fx / "h04_inn.eve").read_text(encoding="utf-8"), GD, project_id="h04-inn", version=1,
                        origin=(0, 64, 0))
    assert g.hash().startswith("10d0c0c43f41")                 # the design the 45L judged
    _, _, early = buildorder.plan_order(g, GD, site=site)
    st, snaps = buildorder.order(g, GD, None, early), {}
    fails, _ = simulate(g, GD, None, st, snaps=snaps, site=site)
    # O94ag-b: no walk over furniture, panes or lanterns any more: 296 fail (was 287), one from y 3 (the (0,3,4) log)
    assert len(fails) > 250 and min(f["pos"]["y"] for f in fails) == 3
    for cell in ((9, 8, 0), (10, 8, 0)):
        f = next(f for f in fails if (f["pos"]["x"], f["pos"]["y"], f["pos"]["z"]) == cell)
        w = World(g, GD, None, site=site)
        w.placed, P = snaps[cell], w.P
        stand = (10 + P, 7, -2 + P)                               # outside the north wall, eye level with the eave
        assert can_place(w, (cell[0] + P, cell[1], cell[2] + P), st[f["step"] - 1]["state"],
                         np.array([[*stand, 0]], float), classes(GD))
        dirt, landing = scaffold._staircase(w, stand, (0, -1), set())
        assert len(dirt) == 28 and landing[1] == 0               # a 7-step staircase north, down to the ground
        assert scaffold._reachable(w, dirt, cell[1])             # O87h pre-check: its stand is walked to in time
        assert not scaffold._reachable(w, dirt + [(stand[0], cell[1] + 1, stand[2])], cell[1])   # dirt after the block
    s = scaffold._design(set(dirt), g, GD, P, 0)                 # that one staircase in a full replay: the eave places,
    after, _ = simulate(g, GD, s, buildorder.order(g, GD, s, early), site=site)   # nothing new fails (the planner's
    key = lambda f: (f["pos"]["x"], f["pos"]["y"], f["pos"]["z"])                  # acceptance rule would take it)
    assert not {(9, 8, 0), (10, 8, 0)} & {key(f) for f in after} and {key(f) for f in after} < {key(f) for f in fails}


@pytest.mark.slow
@pytest.mark.parametrize("name", ["h04_inn", "tower"])
def test_o87h_big_designs_plan_inside_the_planner_budget(name):
    """O87i bar: the 17-high inn (45L h04) and the 30-high setback tower plan with nothing left inside the planner's
    600 s (before O87h: out of budget with 21 left each). Minutes each: EVE_SLOW=1. The tower's top storey and roof need
    steps inside it: with O89's interior scaffold off (the default) it stops at the work budget with 21 left, so its
    bar is checked with interior allowed, as O87i set it."""
    import json

    from eve_design import scaffold
    from eve_design.compiler import compile_file, compile_program
    fx = D / "tests/fixtures/placecheck"
    if name == "tower":
        g, site = compile_file(D / "examples/tower.eve", GD), None
    else:
        site = json.loads((fx / "h04_inn_site.json").read_text())
        g = compile_program((fx / "h04_inn.eve").read_text(encoding="utf-8"), GD, project_id="h04-inn", version=1,
                            origin=(0, 64, 0))
    s, left = scaffold.plan(g, GD, site, interior=name == "tower", seconds=scaffold.TIME)   # the bar as adopted: 600 s,
    assert not left, left[:3]                                                     # not the tower's O87y 1800 s


def test_flat_walking_moves_are_the_cell_rules_in_the_same_order():
    """O87h: the walking search's flat lookups give the moves the cell-by-cell rules (_ok, _free, _step) give, the same
    cells in the same order: a different order would change the stands a replay picks, and committed build orders."""
    import itertools

    import numpy as np

    from eve_design.placecheck import FALL, World
    g = Design.load(D / "examples/out/hut5/design.json")
    w = World(g, GD, Design.load(D / "examples/out/hut5/scaffold.json"), site={"plot": {"min": {"x": -3, "z": -2},
                                                                                         "max": {"x": 9, "z": 9}}})
    H, Dd, W = w.shape

    def ref(X, Y, Z):
        out = []
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, nz = X + dx, Z + dz
            if not (0 <= nz < Dd and 0 <= nx < W):
                continue
            cands = [Y + 1] if w._free(X, Y + 2, Z) or w._step(nx, Y, nz) else []
            cands.append(Y)
            down = w._step(X, Y - 1, Z) or w._step(nx, Y - 2, nz)
            for d in range(1, FALL + 1):
                if Y - d < 0 or not w._free(nx, Y - d + 1, nz) or not w._free(nx, Y, nz) \
                        or not (w._free(nx, Y + 1, nz) or (d == 1 and down)):
                    break
                cands.append(Y - d)
            door = lambda x, y, z: 0 <= y < H and w.placed[y, z, x] and w.door_b[y, z, x]
            out += [(nx, ny, nz) for ny in cands if w._ok(nx, ny, nz)
                    and (ny == Y or not (door(X, Y, Z) or door(nx, ny, nz)))]   # doorways: level moves only
        return out
    checked = 0
    for trial in range(40):                                      # random half-built worlds, from bare to nearly full
        w.placed[:] = np.random.default_rng(trial).random(w.shape) < trial / 40
        moves, ok = w._mover()
        for c in itertools.product(range(-1, W + 1), range(-1, H + 1), range(-1, Dd + 1)):
            assert bool(ok(*c)) == bool(w._ok(*c)), c
            if w._ok(*c):
                assert moves(*c) == ref(*c), c
                checked += 1
    assert checked > 2000


def test_o87h_the_checked_pass_first_then_the_earlier_order_keeps_the_best(monkeypatch):
    """medieval_house: the reachability-checked pass left one block the earlier candidate order places; the planner
    runs the earlier order only when the checked pass leaves something, and keeps the plan with the fewest left."""
    from eve_design import scaffold
    ranked, checked, earlier, straight = (True, True, True), (True, False, True), (False, False, True), (False, False, False)
    calls, script = [], {ranked: ["a"], checked: ["a"], earlier: [], straight: ["b", "c"]}

    def fake(g, gd, site, turns, work=None, check=False, ranked=False, interior=True):
        calls.append((check, ranked, turns))
        return f"plan{calls[-1]}", list(script[calls[-1]])
    monkeypatch.setattr(scaffold, "_plan", fake)
    assert scaffold.plan(None, GD, seconds=scaffold.TIME) == (f"plan{earlier}", []) and calls == [ranked, checked, earlier]
    calls.clear()
    script[ranked] = []
    assert scaffold.plan(None, GD, seconds=scaffold.TIME) == (f"plan{ranked}", []) and calls == [ranked]   # solved
    calls.clear()
    script.update({ranked: ["a", "b"], checked: ["a", "b"], earlier: ["a", "b", "c"], straight: ["a"]})
    assert scaffold.plan(None, GD, seconds=scaffold.TIME) == (f"plan{straight}", ["a"]) and len(calls) == 4


def test_hut5_tall_scaffold_inside_the_room_is_taken_down_from_inside():
    """Builder (O87m): a lane-sized fixture whose plan really puts scaffold inside a room. The committed plan replays
    clean, keeps to lane C's plot (3 west, 2 east of the footprint), stays under the lane's step cap, and every dirt
    block inside the footprint is broken from a stand inside, with the door as the way out."""
    import json
    hut = runpy.run_path(str(D / "examples/hut5_tall.py"))
    out = D / "examples/out/hut5_tall"
    g, s = Design.load(out / "design.json"), Design.load(out / "scaffold.json")
    assert g.hash() == hut["build"](GD).hash() and json.loads((out / "gate.json").read_text())["category"] == "schematic"
    steps, fails, _ = buildorder.plan_order(g, GD, s, site=hut["SITE"])
    assert not fails and len(steps) <= 300 and g.size[1] <= 16
    off = [s.origin[i] - g.origin[i] for i in range(3)]
    cells = [(x + off[0], y + off[1], z + off[2]) for x, y, z, _, _ in s.cells()]
    assert -3 <= min(c[0] for c in cells) and max(c[0] for c in cells) <= g.size[0] - 1 + 2
    inner = lambda p: 0 <= p[0] < g.size[0] and 0 <= p[2] < g.size[2]
    removals = [st for st in steps if st["layer"] == "scaffold_removal" and inner(st["pos"])]
    assert len(removals) >= 5 and all(st["stand"] and inner(st["stand"]) for st in removals)


def test_a_door_is_entered_only_on_a_level_move():
    """Builder, lane C (hut5_tall a12/a13): the door stood on the room's floor, a block above the ground; Baritone opens
    a door only on a level move, so the step up into it closed the room. The doorstep (2,0,5) is her level way in."""
    from eve_design.placecheck import World
    hut = runpy.run_path(str(D / "examples/hut5_tall.py"))
    w = World(hut["build"](GD), GD, None, site=hut["SITE"])
    P = w.P
    w.placed[:] = w.design                                       # all built, the door shut
    inside = (2 + P, 1, 2 + P)
    assert inside in w.walk() and inside in w.escape(w.walk())   # over the doorstep, level through the door, and out
    w.placed[0, 5 + P, 2 + P] = False                            # no doorstep: a step up into the door, a drop out
    assert inside not in w.walk() and inside not in w.escape({inside})


def test_a_stair_or_slab_side_counts_only_where_the_block_is():
    """Builder, lane C (CP-50 a30): the side of a support stair is L-shaped. A click in its missing quarter goes on into
    the block and lands on another face; a single slab covers only its half."""
    import numpy as np

    from eve_design.placecheck import _aims, _on_outline
    east = "minecraft:oak_stairs[facing=east,half=bottom,shape=straight,waterlogged=false]"
    south = (0, 0, 1)                                            # the stair's south face (a side of an east stair)
    assert _on_outline(east, south, (0.2, 0.3, 1.0)) and _on_outline(east, south, (0.8, 0.3, 1.0))   # full lower half
    assert _on_outline(east, south, (0.8, 0.7, 1.0)) and not _on_outline(east, south, (0.2, 0.7, 1.0))  # raised: east
    assert _on_outline(east, (1, 0, 0), (1.0, 0.7, 0.5)) and not _on_outline(east, (-1, 0, 0), (0.0, 0.7, 0.5))
    top = east.replace("half=bottom", "half=top")
    assert _on_outline(top, south, (0.2, 0.7, 1.0)) and not _on_outline(top, south, (0.2, 0.3, 1.0))
    slab = "minecraft:oak_slab[type=bottom,waterlogged=false]"
    assert _on_outline(slab, south, (0.5, 0.3, 1.0)) and not _on_outline(slab, south, (0.5, 0.7, 1.0))
    assert _on_outline(slab, (0, 1, 0), (0.5, 1.0, 0.5))       # top and bottom faces unchanged
    N = (5, 4, 3)
    full, stair = _aims(N, (0, 0, -1), None), _aims(N, (0, 0, -1), None, east)   # target south of N: N's south face
    assert len(full) == 25 and 0 < len(stair) < 25
    assert all(_on_outline(east, south, q - np.array(N, float)) for q in stair)


def test_a_ceiling_over_the_top_step_takes_the_stand_away():
    """Builder, lane C (hut5_tall, c2d1312): after the plank over the interior staircase's last step (2,7,1), the stand
    (2,6,2) can neither be climbed to nor left; the replay used it again (steps 194-195) and Eve could not get there."""
    import runpy
    hut = runpy.run_path(str(D / "examples/hut5_tall.py"))
    g = Design.load(D / "examples/out/hut5_tall/design.json")
    s = Design.load(D / "examples/out/hut5_tall/scaffold.json")
    steps, fails, _ = buildorder.plan_order(g, GD, s, site=hut["SITE"])
    i = next(k for k, st in enumerate(steps) if st["pos"] == [2, 7, 1])
    assert not fails and all(st.get("stand") != [2, 6, 2] for st in steps[i + 1:])


def test_generation_writes_a_buildable_design_with_its_scaffold(tmp_path):
    import json
    from eve_design.generate import generate
    from eve_design.models import FakeCritic, FakeGenerator
    briefs = json.loads((D / "tests/fixtures/briefs/manifest.json").read_text())["briefs"]
    r = generate(briefs[1], GD, FakeGenerator(), FakeCritic(), out=tmp_path / "v1")
    v = json.loads((tmp_path / "v1/validation.json").read_text())
    assert r["design_hash"] and v["passed"] and v["checks"]["buildable"]["status"] == "pass"
    assert v["validator_version"] == "eve-validate/3"


def test_pillar_tall_scaffold_stays_on_its_plot_and_off_the_kit_chest():
    """CP-53 fixture: End island A (x 400..409, z -6..6); dirt switchback staircases, all inside, chest cell untouched."""
    pt = runpy.run_path(str(D / "examples/pillar_tall.py"))
    site = pt["SITE"]
    g = Design.load(D / "examples/out/pillar_tall/design.json")
    s = Design.load(D / "examples/out/pillar_tall/scaffold.json")
    cells = {(x + s.origin[0], y + s.origin[1], z + s.origin[2]) for x, y, z, _, _ in s.cells()}
    assert all(400 <= x <= 409 and -6 <= z <= 6 for x, _, z in cells)
    assert tuple(site["obstacles"][0]) not in cells
    steps, fails, _ = buildorder.plan_order(g, GD, s, site=site)
    assert not fails and all(st.get("stand") for st in steps)


@pytest.mark.parametrize("tgt,stand", [((4, 4, 2), (6, 1, 3)), ((0, 4, 2), (-2, 1, 3))])
def test_a30_a31_click_beside_a_support_stairs_front_is_not_taken(tgt, stand, monkeypatch):
    """Builder lane C (CP-50 a30/a31, both body jars): these roof stairs were clicked on the south face of their support
    stair from these stands, with ~1.6 deg to spare before the support's front; the crosshair landed on the front. The
    1-degree margin took the click; rays turned 2 degrees must not reach a support stair's front, so now it is not taken,
    and the re-planned hut5_sim places the stair from a stand that passes."""
    import numpy as np

    from eve_design import placecheck
    from eve_design.validate import classes
    g = Design.load(D / "examples/out/hut5_sim/design.json")
    old = Design.load(D / "tests/fixtures/placecheck/hut5_sim_53eb23c_scaffold.json")   # the plan builder ran
    steps = buildorder.order(g, GD, old)
    k = next(i for i, st in enumerate(steps) if st["pos"] == list(tgt))
    _, w = placecheck.simulate(g, GD, old, steps[:k])
    T = (tgt[0] + w.P, tgt[1], tgt[2] + w.P)
    S = np.array([[stand[0] + w.P, stand[1], stand[2] + w.P, 0]], float)
    assert placecheck.can_place(w, T, steps[k]["state"], S, classes(GD)) is None              # now: not from there
    monkeypatch.setattr(placecheck, "AIM_TOL_SAME", 0.0)
    assert placecheck.can_place(w, T, steps[k]["state"], S, classes(GD)) is not None          # the old margin took it
    monkeypatch.undo()
    steps, fails, _ = buildorder.plan_order(g, GD, Design.load(D / "examples/out/hut5_sim/scaffold.json"))
    st = {tuple(x["pos"]): x.get("stand") for x in steps}
    assert not fails and st[tgt] and st[tgt] != list(stand)


def test_o87y_the_planning_budget_scales_with_storeys():
    """O87y (45L h08: a 3-storey watch-house's best design ran out of planning at 600 s): 600 s per storey, at most
    1800 s; no rooms or no storey labels count as one storey."""
    from eve_design import scaffold
    from eve_design.compiler import compile_file
    budget = {n: scaffold.budget(compile_file(D / f"examples/{n}.eve", GD)) for n in ("cabin", "two_storey", "tower")}
    assert budget == {"cabin": 600.0, "two_storey": 1200.0, "tower": 1800.0}               # the tower has 6 storeys
    assert scaffold.budget(Design.load(D / "examples/out/hut5_tall/design.json")) == 600.0   # hand-built, no labels


def test_o87y_finishing_plans_share_one_capped_clock(monkeypatch):
    """A brief's finishing plans share scaffold.TIME_CAP, so its worst case stays FINISH_TRIES x 600 s as before: a tall
    design's plan gets up to 1800 s and the next one what is left; each records its planning against the O87i bar."""
    from eve_design import generate, scaffold
    from eve_design.compiler import compile_file
    asked = []

    def plan(g, gd, site=None, interior=None, seconds=None, report=None):
        import time
        time.sleep(0.05)                                                      # it spends some of the allowance
        asked.append(seconds)
        report.update(storeys=2, budget_s=seconds, seconds=seconds, left=1, stopped_at="its time budget",
                      o87i_bar_met=False)
        return None, [{"pos": None, "budget": True, "reason": "scaffold planning stopped at its time budget"}]
    monkeypatch.setattr(scaffold, "plan", plan)
    g = compile_file(D / "examples/two_storey.eve", GD)
    allowance = [1500.0]
    rec = {"_built": (g, None, None, None), "_allowance": allowance}
    assert not generate._finish(rec, GD, {})
    assert asked == [1200.0] and allowance[0] < 1500.0 and rec["planning"]["o87i_bar_met"] is False
    allowance[0] = 300.0
    generate._finish({"_built": (g, None, None, None), "_allowance": allowance}, GD, {})
    assert asked[-1] == 300.0                                                  # what is left, not its own 1200


def test_o94ag_walk_surfaces_keeps_feet_off_the_designs_own_tops():
    """O94ag (lane C C2, eve-organic's C2-STANDS-ANALYSIS): the replay let Eve stand on any sturdy top, so build orders
    stood her on roof slopes, wall tops and a tree crown Baritone could not reach (33 % placed there, 97 % from the
    ground, scaffold or floors). Under walk_surfaces a stand rests only on the ground, scaffold, or the design's floor,
    foundation, stair or ground role; eve-organic designs get it by default, any design can select it."""
    from eve_design.grid import AIR
    from eve_design.placecheck import WALK_ROLES, World, stand_policy
    g = Design.load(D / "examples/out/hut5_sim/design.json")
    s = Design.load(D / "examples/out/hut5_sim/scaffold.json")
    org = Design.from_json(g.to_json())
    org.meta["provenance"] = {**g.meta["provenance"], "pipeline": "eve-organic/1"}
    assert stand_policy(g) == "unbuilt" and stand_policy(org) == "walk_surfaces"
    org.meta["provenance"]["stand_policy"] = "unbuilt"
    assert stand_policy(org) == "unbuilt"                                        # selectable either way

    def stands(policy):
        w = World(g, GD, s, policy)
        w.placed[:] = w.design | w.scaf                                          # everything up, scaffold too
        return {(int(X) - w.P, int(Y), int(Z) - w.P) for X, Y, Z, _ in w.spots(w.walk())}

    def on_top(x, y, z):                                                         # rests on a design cell, not a walk role
        return y > 0 and g.in_bounds(x, y - 1, z) and g.get(x, y - 1, z) != AIR and g.role_at(x, y - 1, z) not in WALK_ROLES
    loose, strict = stands("unbuilt"), stands("walk_surfaces")
    assert any(on_top(*p) for p in loose) and not any(on_top(*p) for p in strict) and strict < loose


def test_o94ag_an_organic_design_resolves_walk_surfaces_and_scaffold_never_rests_on_its_roof():
    """O94ag review: World resolves the policy per design when none is given; an unknown one is refused; under
    walk_surfaces the scaffold planner's floors are the design's walking surfaces only, so no dirt column starts on a
    roof Eve would have to walk onto."""
    from eve_design import scaffold
    from eve_design.placecheck import World
    g = Design.load(D / "examples/out/hut5_sim/design.json")
    org = Design.from_json(g.to_json())
    org.meta["provenance"] = {**g.meta["provenance"], "pipeline": "eve-organic/1"}
    w, wo = World(g, GD, None), World(org, GD, None)
    assert (w.policy, wo.policy) == ("unbuilt", "walk_surfaces")
    with pytest.raises(ValueError):
        World(g, GD, None, "on_the_roof")
    P = w.P
    roof, floor = (0 + P, 4, 0 + P), (2 + P, 0, 2 + P)            # a roof corner (double slab), the foundation floor
    assert g.role_at(0, 4, 0) == "roof" and g.role_at(2, 0, 2) == "foundation"
    for cell, loose, strict in ((roof, True, False), (floor, True, True)):
        X, Y, Z = cell
        assert scaffold._floors(w)[Y, Z, X] == loose and scaffold._floors(wo)[Y, Z, X] == strict


# O94ag-b fixtures: builder's O94ah stand list (notes folder builder-o94ah-stand-list.md, Walk at 3837a84b) was made on
# these organic folders as committed (cp72 b0df91a1, cp69 9dece11b, cp70-horse20 7d3e945c), cut after its last listed
# step; organic re-emits the folders themselves, so the tests keep their own copy.
O94AH = D / "tests/fixtures/placecheck/o94ah"


def _o94ah(name):
    import json
    f = O94AH / name
    g, s = Design.load(f / "design.json"), Design.load(f / "scaffold.json")
    site = json.loads((f / "brief.json").read_text(encoding="utf-8")).get("site")
    bo = json.loads((f / "build_order.json").read_text(encoding="utf-8"))
    return g, s, site, [dict(zip(bo["columns"], r)) for r in bo["rows"]]


def test_o94agb_she_walks_only_on_what_baritone_walks_on():
    """O94ag-b: the replay walked on anything not passable and not a fence. Baritone's canWalkOn (builder read it from
    the body's jar; its run-time Walk now uses the same predicate) takes full-collision blocks, stairs, slabs, chests,
    glass and leaves, and no lantern, pane, trapdoor, pot, bed, magma or honey."""
    from eve_design.placecheck import World
    no = ["glass_pane", "lantern", "oak_trapdoor", "flower_pot", "white_bed", "magma_block", "honey_block", "iron_bars",
          "enchanting_table", "oak_shelf", "mud", "comparator", "cactus"]                    # review: not full cubes
    yes = ["chest", "glass", "oak_leaves", "oak_slab", "oak_stairs", "stone", "snow_block", "dirt_path", "sea_lantern",
           "jack_o_lantern", "budding_amethyst"]                                          # review: full cubes
    g = Design((len(no + yes), 1, 1), (0, 64, 0))
    for x, b in enumerate(no + yes):
        g.set(x, 0, 0, f"minecraft:{b}", "furniture")
    w = World(g, GD, None, "unbuilt")
    w.placed[:] = w.design
    ok = w._mover()[1]
    assert [bool(ok(x + w.P, 1, w.P)) for x in range(len(no + yes))] == [False] * len(no) + [True] * len(yes)


def test_o94agb_builders_stand_list_replays_stale_where_it_said():
    """O94ag-b, builder's list replayed with today's rules (stale_stands; steps are the list's order + 1):
    - cp72's loft: builder's 18 stands, all reached only over a lantern, a bed and a window pane, but the two whose way
      climbs the chest (order #387 and #391), which Baritone walks on;
    - cp69's horse, #301: no walk in from the ground after #300, but she is already standing there (#299/#300), with
      a way down: not stale (builder's run time checks the walk from where she is; S93);
    - cp70-horse20, #1002: no walk back down after #1001."""
    from eve_design.placecheck import stale_stands
    loft = [387, 390, 391, 396, 397, 398, 399, 400, 402, 403, 405, 406, 408, 409, 410, 411, 418, 420]
    why = "no walk to it from the ground or her last stand"
    for name, want in (("cp72-cabin-tree", [(k + 1, why) for k in loft if k not in (387, 391)]),
                       ("cp69-horse-triposr", []),
                       ("cp70-horse20-thick", [(1003, "no walk back down")])):
        g, s, site, rows = _o94ah(name)
        assert [(r["step"], r["why"]) for r in stale_stands(g, GD, s, rows, site)] == want, name


@pytest.mark.parametrize("name", ["cp70-horse20-thick"])
def test_o94agb_the_replay_never_keeps_a_stand_that_lost_its_way(name):
    """O94ag-b: when a placed block filled a walked cell the replay only dropped those spots if their neighbours still
    reached one another, but drops are one-way and the way down was not recomputed: replaying the same steps it planned
    cp70-horse20's #1002 again (no walk down). plan_order now finds that with stale_stands and replays the plan
    re-checked (spots that lost their way in or down dropped), which plans no stale stand."""
    from eve_design.placecheck import stale_stands
    g, s, site, rows = _o94ah(name)
    steps = [{"step": i + 1, "layer": r["layer"], "pos": [r["x"], r["y"], r["z"]], "after": r["after"],
              "also_appears": r["also_appears"] or [],
              "state": g.get(r["x"], r["y"], r["z"]) if r["layer"] == "final" else "minecraft:dirt"}
             for i, r in enumerate(rows)]
    simulate(g, GD, s, steps, policy="unbuilt", site=site)        # as they were planned (before O94ag): stale
    assert stale_stands(g, GD, s, steps, site)
    simulate(g, GD, s, steps, policy="unbuilt", site=site, recheck=True)   # what plan_order then replays
    assert stale_stands(g, GD, s, steps, site) == []


def test_o94agb_the_gate_rejects_a_plan_with_a_stale_stand(tmp_path):
    """O94ag-b (orchestrator: a stale plan must never reach a lane unnoticed): the gate replays build_order.json each
    time it runs; a stand nothing walks to is a reject (an owner approval builds only a hold)."""
    import json
    import shutil

    from eve_design import gate
    f = tmp_path / "hut5_sim_v2"
    shutil.copytree(D / "examples/out/hut5_sim_v2", f)
    d = gate.decide(f)
    assert d["checks"]["plan_fresh"] == {"ok": True} and d["decision"] == "hold"      # O94x: waits for the owner
    bo = json.loads((f / "build_order.json").read_text())
    bo["rows"][10][bo["columns"].index("stand")] = [2, 9, 2]    # in the air over the roof
    (f / "build_order.json").write_text(json.dumps(bo))
    d = gate.decide(f)
    assert d["decision"] == "reject" and "step 11, stand [2, 9, 2]: no walk to it" in d["checks"]["plan_fresh"]["reason"]


def test_o193_the_portal_corner_needs_a_click_from_all_five_landings():
    """O193 (builder, 45L attempt 4: nether_portal v2's top-right corner (3,4,0) went manual): from the planned ground
    stand (4,0,-2) the only support face is ~3.8 away; within 4.3 from the stand centre, but builder's clickableAround
    also checks reach from 4 landings 0.35 off along x and z. A design with provenance.click_eyes = 5 is planned so."""
    import numpy as np

    from eve_design.placecheck import World, can_place
    from eve_design.validate import classes
    portal = runpy.run_path(str(D / "examples/nether_portal.py"))

    def corner(version):
        g = portal["build_portal"](GD, version)
        w = World(g, GD, None)
        P = w.P
        w.placed[:] = w.design
        w.placed[4, P, 3 + P] = False                              # all built but the corner
        stand = np.array([[4 + P, 0, -2 + P, 0]], float)
        return w.eyes5, can_place(w, (3 + P, 4, P), g.get(3, 4, 0), stand, classes(GD))
    eyes, spot = corner(2)
    assert not eyes and spot is not None                     # v2: the stand centre's eye reaches it (what was planned)
    assert corner(3) == (True, None)                         # v3: not from every landing, so the planner looks again
