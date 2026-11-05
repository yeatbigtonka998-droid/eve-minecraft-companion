"""O132 (45L session 3): "build a small wood house" reached the planner, which invented a mine_block target and was
refused. Now: catalogue design -> flat, unbuilt site near him -> one yes/no naming the corner and floor height -> on his
yes the approval row, clearing (mine_block, drops kept), then the CP-51 supply loop and build_schematic at that origin."""
import json
import shutil

import pytest

from conftest import ROOT, mind_env, run, until

from eve_mind import build_flow as bf

HOUSE7 = ROOT / "parts" / "design" / "examples" / "out" / "house7"
SIZE = (7, 7, 8)
HIM = {"dim": "minecraft:overworld", "x": 0.5, "y": 64.0, "z": 0.5}


def world(extra=None, lo=-30, hi=30):
    """Flat grass with its top at y 63, air above, plus `extra` cells."""
    b = {(x, y, z): ("minecraft:grass_block" if y == 63 else "minecraft:dirt" if y < 63 else "minecraft:air")
         for x in range(lo, hi) for z in range(lo, hi) for y in range(59, 76)}
    b.update(extra or {})
    return b


def fake_scan(blocks):
    async def scan(dim, a, b):
        return {(x, y, z): blocks.get((x, y, z), "minecraft:air") for x in range(a["x"], b["x"] + 1)
                for y in range(a["y"], b["y"] + 1) for z in range(a["z"], b["z"] + 1)}
    return scan


def said_by(e):
    out, orig = [], e.mind.say

    async def say(t, *a, **k):
        out.append(t)
        return await orig(t, *a, **k)
    e.mind.say = say
    return out


def actions(e):
    return [(s, json.loads(p)) for s, p in e.mind.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]


def catalogue(tmp_path, hold=False):
    d = tmp_path / "designs" / "house7" / "v1"
    shutil.copytree(HOUSE7, d, ignore=shutil.ignore_patterns("previews", "*.litematic"))
    g = json.loads((d / "gate.json").read_text())
    assert (g["category"], g["decision"]) == ("schematic", "n/a")           # O132 ruling (1a): a catalogue template
    if hold:                                                                 # an Eve-authored design the gate holds
        (d / "gate.json").write_text(json.dumps({**g, "category": "eve_own", "decision": "hold",
                                                 "reasons": ["critiqued: no critique"]}))
    return tmp_path / "designs"


# ---- pure: brief words, site rules

@pytest.mark.parametrize("line,what,where", [
    ("build a small wood house", "small wood house", None),
    ("eve, build me a small wooden house here", "small wooden house", "here"),
    ("please build a small wood house at 104 70 30", "small wood house", "at 104 70 30"),
    ("build a 5x5 cobblestone hut by the lake", "5x5 cobblestone hut", "by the lake"),
])
def test_brief_words(line, what, where):
    m = bf.BRIEF.match(line)
    assert m and m["what"] == what and m["where"] == where, m and m.groupdict()


@pytest.mark.parametrize("line", ["build a bridge to 1 2 3", "build the wall", "make 4 planks", "house"])
def test_not_a_brief(line):
    assert bf.BRIEF.match(line) is None


def test_site_rules_flat_unbuilt_dry_and_what_is_cleared():
    tree = {(2, 64 + i, 2): "minecraft:oak_log" for i in range(5)} | {(1, 64, 1): "minecraft:poppy",
                                                                    (3, 64, 1): "minecraft:short_grass"}
    plan, why = bf.site_plan(world(tree), SIZE, 0, 0)
    assert why is None and plan["origin"] == {"x": 0, "y": 64, "z": 0}, why
    assert [b for _p, b in plan["clear"]].count("minecraft:oak_log") == 4 and plan["high"] == 1   # y 68 = floor + 4
    assert {"minecraft:poppy", "minecraft:short_grass"} <= {b for _p, b in plan["clear"]}
    # a one-block bump is dug, two blocks of slope is no site
    plan, why = bf.site_plan(world({(4, 64, 4): "minecraft:dirt"}), SIZE, 0, 0)
    assert plan["origin"]["y"] == 64 and ({"x": 4, "y": 64, "z": 4}, "minecraft:dirt") in plan["clear"]
    hill = {(4, y, 4): "minecraft:dirt" for y in (64, 65)}
    assert "up and down by 2" in bf.site_plan(world(hill), SIZE, 0, 0)[1]
    assert "water" in bf.site_plan(world({(3, 63, 3): "minecraft:water"}), SIZE, 0, 0)[1]
    # someone's planks within MARGIN of the footprint: no site; MARGIN + 1 away: fine
    assert "another build" in bf.site_plan(world({(9, 64, 3): "minecraft:oak_planks"}), SIZE, 0, 0)[1]
    assert bf.site_plan(world({(10, 64, 3): "minecraft:oak_planks"}), SIZE, 0, 0)[1] is None
    # he names the floor height: used as said, no flatness rule
    plan, _ = bf.site_plan(world(hill), SIZE, 0, 0, 66)
    assert plan["origin"]["y"] == 66


def test_pick_site_keeps_off_his_cell_and_away_from_builds():
    shed = {(x, 64, z): "minecraft:cobblestone" for x in range(-8, -4) for z in range(-3, 3)}
    plan, why = bf.pick_site(world(shed), SIZE, HIM, lambda o: None)
    o = plan["origin"]
    assert why is None and o["y"] == 64
    assert not (o["x"] <= 0 < o["x"] + 7 and o["z"] <= 0 < o["z"] + 8)          # not on top of him
    assert o["x"] - bf.MARGIN > -5                                                # clear of the shed
    plan, why = bf.pick_site(world(), SIZE, HIM, lambda o: "I may not place there (no_zone:place=none)")
    assert plan is None and why.startswith("I may not place there")


# ---- the mind, on fakes

def test_brief_question_yes_clear_then_build_in_order(tmp_path):
    async def go():
        async with mind_env(tmp_path, designs_dir=catalogue(tmp_path)) as e:
            if "build_schematic" not in e.mind.registry.skills:
                pytest.skip("registry without build_schematic")
            said = said_by(e)
            e.body.walk_s = 0.01
            blocks = world({(x, 64, z): "minecraft:short_grass" if (x * z) % 3 else "minecraft:poppy"
                            for x in range(-30, 30) for z in range(-30, 30)})          # every site needs clearing
            flow = e.mind.__dict__.setdefault("build_flow", bf.BuildFlow(e.mind))
            flow.scan = fake_scan(blocks)
            r = await e.mind.owner_text("build a small wood house", "Hunter", sender_pos=HIM)
            assert r.get("question") and not actions(e), (r, said)
            o = r["site"]
            q = said[-1]
            assert f"corner at {o['x']} 64 {o['z']}, floor at y 64?" in q and q.endswith("yes / no"), said
            assert any(t.startswith("To build it I'll gather about") for t in said), said
            assert e.db.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
            r = await e.mind.owner_text("yes", "Hunter", sender_pos=HIM)
            assert r["ok"] and r["building"] == "house7", r
            row = e.db.execute("SELECT decided_by, decision, channel FROM approvals").fetchone()
            assert tuple(row) == ("Hunter", "approved", "ingame_confirm")
            await until(lambda: any(t.startswith(("Done:", "The house7 build", "I can't finish")) for t in said),
                        timeout=15, what="the end line")
            sent = actions(e)
            first_build = next(i for i, (s, _p) in enumerate(sent) if s == "build_schematic")
            mined = [p for s, p in sent[:first_build] if s == "mine_block"]
            assert all(s in ("walk_to", "mine_block") for s, _p in sent[:first_build]), sent
            in_box = [p for p in mined if o["x"] <= p["pos"]["x"] < o["x"] + 7 and o["z"] <= p["pos"]["z"] < o["z"] + 8]
            assert len(mined) == 7 * 8 and in_box == mined and all(p["expect_block"] for p in mined), mined
            assert sent[0][0] == "walk_to" and f"Clearing {7 * 8} blocks first" in " ".join(said), said
            assert sent[first_build][1]["origin"] == o and sent[first_build][1]["project_id"] == "house7"
            assert said[-1].startswith("Done: house7 is built at"), said
    run(go())


def test_no_and_refusals_are_said(tmp_path):
    async def go():
        async with mind_env(tmp_path, designs_dir=catalogue(tmp_path, hold=True)) as e:
            said = said_by(e)
            flow = e.mind.__dict__.setdefault("build_flow", bf.BuildFlow(e.mind))
            flow.scan = fake_scan(world())
            r = await e.mind.owner_text("build a small wood house", "Hunter", sender_pos=HIM)
            assert r["error"] == "gate_refused" and "design gate holds it" in said[-1], said
            r = await e.mind.owner_text("build a 5x5 cobblestone hut by the lake", "Hunter", sender_pos=HIM)
            assert r["error"] == "no_design" and said[-1].startswith("I don't have a design for a 5x5 cobblestone hut"), said
            g = tmp_path / "designs" / "house7" / "v1" / "gate.json"
            g.write_text(json.dumps({**json.loads(g.read_text()), "category": "schematic", "decision": "n/a"}))
            r = await e.mind.owner_text("build a small wood house by the lake", "Hunter", sender_pos=HIM)
            assert r["error"] == "unknown_place" and "by the lake" in said[-1], said
            flow.scan = fake_scan(world({(x, 64, z): "minecraft:stone_bricks" for x in range(-20, 20)
                                         for z in range(-20, 20) if (x + z) % 9 == 0}))
            r = await e.mind.owner_text("build a small wood house", "Hunter", sender_pos=HIM)
            assert r["error"] == "no_site" and "built" in said[-1] and "flatter" not in said[-1], said
            flow.scan = fake_scan(world())
            r = await e.mind.owner_text("build a small wood house", "Hunter", sender_pos=HIM)
            assert r.get("question")
            r = await e.mind.owner_text("no", "Hunter", sender_pos=HIM)
            assert said[-1] == "OK, I won't build it." and not actions(e)
            assert e.db.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    run(go())


@pytest.mark.skipif(not (ROOT / "data" / "game" / "26.3" / "recipe").exists(), reason="data/game/26.3 not extracted")
def test_material_chain_names_base_materials(tmp_path):
    async def go():
        async with mind_env(tmp_path, designs_dir=catalogue(tmp_path)) as e:
            flow = bf.BuildFlow(e.mind)
            d = tmp_path / "designs" / "house7" / "v1"
            design = json.loads((d / "design.json").read_text())
            line = flow.materials(design, d, {"inventory": []})
            assert "oak log" in line and "plank" not in line and "stairs" not in line, line
            line = flow.materials(design, d, {"inventory": [{"item": i["item"], "count": i["count"]} for i in
                                  json.loads((d / "bom.json").read_text())["final"] + [{"item": "minecraft:dirt",
                                                                                        "count": 64}]]})
            assert line == "I have everything for it.", line
    run(go())


# ---- O150: the nether portal frame (catalogue template, 2 blocks kept clear in front)

PORTAL = ROOT / "parts" / "design" / "examples" / "out"


def portal_catalogue(tmp_path):
    for v, name in ((1, "nether_portal"), (2, "nether_portal_v2")):
        shutil.copytree(PORTAL / name, tmp_path / "designs" / "nether_portal" / f"v{v}",
                        ignore=shutil.ignore_patterns("previews", "*.litematic"))
    return tmp_path / "designs"


def test_portal_front_clearance_rule():
    size = (4, 5, 1 + 2)                                       # the frame row + 2 kept clear south of it
    plan, why = bf.site_plan(world({(1, 64, 2): "minecraft:poppy"}), size, 0, 0)
    assert why is None and ({"x": 1, "y": 64, "z": 2}, "minecraft:poppy") in plan["clear"]
    assert "something built" in bf.site_plan(world({(1, 64, 2): "minecraft:cobblestone"}), size, 0, 0)[1]
    assert "up and down" in bf.site_plan(world({(2, y, 2): "minecraft:dirt" for y in (64, 65)}), size, 0, 0)[1]


def test_build_a_nether_portal_v1_question_yes_frame_then_flint_line(tmp_path):
    async def go():
        async with mind_env(tmp_path, designs_dir=portal_catalogue(tmp_path)) as e:
            if "build_schematic" not in e.mind.registry.skills:
                pytest.skip("registry without build_schematic")
            said = said_by(e)
            e.body.walk_s = 0.01
            flow = e.mind.__dict__.setdefault("build_flow", bf.BuildFlow(e.mind))
            flow.scan = fake_scan(world())
            r = await e.mind.owner_text("build a full nether portal", "Hunter", sender_pos=HIM)
            assert flow.pending["version"] == 2, said                        # v2 by its own name
            await e.mind.owner_text("no", "Hunter", sender_pos=HIM)
            r = await e.mind.owner_text("build a nether portal", "Hunter", sender_pos=HIM)
            o = r["site"]
            assert flow.pending["version"] == 1 and flow.pending["size"] == (4, 5, 3), flow.pending
            assert f"Build nether_portal (4 by 1) with its north-west corner at {o['x']} 64 {o['z']}, bottom at y 64?" \
                in said[-1] and said[-1].endswith("yes / no"), said
            assert any("10 obsidian" in t for t in said), said
            await e.mind.owner_text("yes", "Hunter", sender_pos=HIM)
            await until(lambda: said and said[-1].startswith("Frame done"), timeout=15, what="the flint line")
            assert said[-1] == "Frame done; say 'light the portal' when you have flint and steel." and \
                said[-2].startswith("Done: nether_portal is built at"), said
            b = [p for s, p in actions(e) if s == "build_schematic"]
            assert b and b[0]["version"] == 1 and b[0]["origin"] == o, b
    run(go())


@pytest.mark.skipif(not (ROOT / "data" / "game" / "26.3" / "recipe").exists(), reason="data/game/26.3 not extracted")
def test_obsidian_is_mined_only_with_a_diamond_pickaxe_and_the_short_line_says_why(tmp_path):
    from eve_mind.supply import Run

    async def go():
        async with mind_env(tmp_path, designs_dir=portal_catalogue(tmp_path)) as e:
            sup, deficit = e.mind.supply, {"want": {"minecraft:obsidian": 10}, "missing": {"minecraft:obsidian": 10}}
            steps, short = sup.plan(Run({}), deficit, {"inventory": [{"item": "minecraft:diamond_pickaxe", "count": 1}]})
            assert short == {} and ("mine_resource", {"block": "minecraft:obsidian", "count": 10}, None) in steps
            steps, short = sup.plan(Run({}), deficit, {"inventory": [{"item": "minecraft:iron_pickaxe", "count": 1}]})
            assert not [s for s in steps if s[0] == "mine_resource"]
            assert short == {"minecraft:obsidian": (10, "I need a diamond pickaxe to mine obsidian, or make it with "
                                                        "water on lava")}, short
            said = said_by(e)
            run_ = Run({})
            run_.status, run_.short = "short", short
            sup.task = None
            await bf.BuildFlow(e.mind).watch(run_, "nether_portal", "1 64 2")
            assert said[-1] == ("I can't finish nether_portal: I'm short of 10 obsidian. I need a diamond pickaxe to "
                                "mine obsidian, or make it with water on lava."), said
    run(go())
