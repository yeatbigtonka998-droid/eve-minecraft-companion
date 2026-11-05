"""CP-55 (language is constrained) and CP-56 (deterministic, roles and intent preserved)."""
import os
import socket
import subprocess
from pathlib import Path

import pytest

from eve_design import compiler, gamedata
from eve_design.compiler import CompileError, compile_file, compile_program
from eve_design.grid import Design
from eve_design.lang import LangError, parse
from eve_design.schem_io import load_litematic, save_litematic

GD = gamedata.load()
EX = Path(__file__).parents[1] / "examples"
CABIN = (EX / "cabin.eve").read_text()
H = 'c = Build(style="cozy_cabin")\n'
BASE = H + 'c.foundation(w=7, d=7)\nc.walls(h=4)\n'

# (program, error class, message fragment). Every one must fail cleanly with that error.
HOSTILE = [
    # invalid syntax
    (H + "c.walls(h=4", LangError, "expected"),
    (H + 'c.walls(h="4)', LangError, "unexpected character"),
    (H + "c.walls(h=4); c.walls(h=4)", LangError, "unexpected character ';'"),
    (H + "c.walls(4, h=4)", CompileError, "twice"),
    (H + "c.walls(h=4, 5)", LangError, "positional argument after keyword"),
    (H + "c.walls(h=4.5)", LangError, "expected ',' or ')', got '.'"),
    (H + "c.walls(h=-)", LangError, "unexpected character '-'"),
    ("cabin.walls(h=4)", LangError, "'='"),
    ("", LangError, "empty program"),
    (H + "c.walls(h=4)\n\tc.walls(h=4) extra", LangError, "after statement"),
    (H + "c.walls(h=\u0034\u0660)", LangError, "ASCII"),
    # unknown commands / names / arguments
    (H + "c.teleport(x=1)", CompileError, "unknown command 'teleport'"),
    (H + "c.finalize()", CompileError, "unknown command 'finalize'"),
    (H + "c._rooms()", CompileError, "unknown command '_rooms'"),
    (H + "c.put(x=0, y=0, z=0)", CompileError, "unknown command 'put'"),
    (H + "c.foundation(w=7, d=7, colour=1)", CompileError, "no argument 'colour'"),
    (H + "d.foundation(w=7, d=7)", LangError, "unknown name 'd'"),
    ('c = Build(style="nope")', CompileError, "unknown style"),
    (H + 'c.foundation(w=7, d=7, block="unobtainium")', CompileError, "unknown block"),
    (BASE + 'c.roof("dome")', CompileError, "must be one of"),
    (BASE + "c.room(\"bedroom\")\nc.room(\"bedroom\")\nc.room(\"bedroom\")", CompileError, "do not fit"),
    (H + "c.walls(h=4)", CompileError, "foundation() must come first"),
    # enormous dimensions / output
    (H + "c.foundation(w=99999, d=9)", CompileError, "3..64"),
    (H + "c.foundation(w=999999, d=9)", LangError, "integer literal too long"),
    (H + "c.foundation(w=9, d=9)\nc.walls(h=400)", CompileError, "2..16"),
    (H + "c.foundation(w=9, d=9)\n" + "c.walls(h=4)\n" * 2, CompileError, "walls() given twice"),
    (H + "c.foundation(w=9, d=9)\n" + "c.floor()\n" * 300, LangError, "more than 200 statements"),
    (H + "c.foundation(w=9, d=9)  #" + "x" * 500, LangError, "line longer"),
    # endless computation: there are no loops; attempts are syntax errors
    (H + "while True:\n    c.floor()", LangError, "expected"),
    (H + "for i in range(10**9): c.floor()", LangError, "expected"),
    (H + "c.floor() if True else c.floor()", LangError, "after statement"),
    # host access / Python escapes
    ("import os", LangError, "'='"),
    (H + "import os", LangError, "unknown name 'import'"),
    (H + '__import__("os").system("calc")', LangError, "unknown name '__import__'"),
    (H + 'open("C:/Windows/win.ini")', LangError, "unknown name 'open'"),
    (H + 'c.walls(h=open("x"))', LangError, "expected a number, string or True/False"),
    (H + 'c.walls(h=__import__("os"))', LangError, "expected a number, string or True/False"),
    (H + "c.__class__.__bases__", LangError, "expected ("),
    (H + "c.__init__()", CompileError, "unknown command '__init__'"),
    (H + 'c.eval("1+1")', CompileError, "unknown command 'eval'"),
    (H + 'c.exec("import os")', CompileError, "unknown command 'exec'"),
    (H + "().__class__.__mro__[1].__subclasses__()", LangError, "unexpected character '['"),
    (H + 'c.walls(h=f"{1}")', LangError, "expected a number, string or True/False, got 'f'"),
    (H + "c.walls(h=lambda: 1)", LangError, "expected"),
    ('c = Build(style="../../../../etc/passwd")', CompileError, "bad style name"),
    (H + 'c.foundation(w=7, d=7, block="../../secrets")', CompileError, "bad block name"),
    (H + 'c.foundation(w=7, d=7, block="minecraft:stone[foo=bar]")', CompileError, "bad block name"),
    ('c = Build(style="cozy_cabin", seed=1)\nc.roof("gable")', CompileError, "foundation() must come first"),
]


@pytest.fixture
def no_host(monkeypatch):
    """Any attempt to reach the host during compilation fails the test loudly."""
    def boom(*a, **k):
        raise AssertionError("host access attempted")
    for mod, name in ((os, "system"), (os, "popen"), (subprocess, "Popen"), (socket, "socket")):
        monkeypatch.setattr(mod, name, boom)


@pytest.mark.parametrize("src,err,frag", HOSTILE, ids=[f"h{i:02d}" for i in range(len(HOSTILE))])
def test_hostile_program_fails_cleanly(src, err, frag, no_host):
    with pytest.raises(err) as ei:
        compile_program(src, GD)
    assert frag in str(ei.value)
    assert type(ei.value) is err


def test_size_limit_on_file(tmp_path):
    p = tmp_path / "big.eve"
    p.write_bytes(H.encode() + b"#" * 70_000)
    with pytest.raises(LangError, match="larger than"):
        compile_file(p, GD)


def test_time_budget(monkeypatch):
    monkeypatch.setattr(compiler, "TIME_BUDGET_S", -1.0)   # already past the deadline at the first check
    with pytest.raises(CompileError, match="time budget"):
        compile_program(H + "c.foundation(w=64, d=64, h=4)", GD)


def test_write_budget(monkeypatch):
    monkeypatch.setattr(compiler, "MAX_WRITES", 1000)
    with pytest.raises(CompileError, match="more than 1000 block writes"):
        compile_program(H + "c.foundation(w=64, d=64, h=4)", GD)


def test_parse_shape():
    p = parse(CABIN)
    assert p.var == "cabin" and p.header.kwargs == {"style": "cozy_cabin", "seed": 42}
    assert [c.name for c in p.calls][:3] == ["foundation", "walls", "floor"]
    roof = next(c for c in p.calls if c.name == "roof")
    assert roof.args == ["gable"] and roof.kwargs["overhang"] == 1 and roof.line == 12


def test_cabin_compiles_and_exports(tmp_path):
    g = compile_file(EX / "cabin.eve", GD)
    assert g.size == (13, 11, 13)
    assert {r["type"] for r in g.rooms} == {"living", "bedroom"}
    roles = {g.role_at(x, y, z) for x, y, z, _, _ in g.cells()}
    assert {"foundation", "floor", "wall", "support", "window", "door", "roof", "furniture", "light", "trim"} <= roles
    save_litematic(g, tmp_path / "cabin.litematic", GD)
    g2, issues = load_litematic(tmp_path / "cabin.litematic", GD)
    assert not issues and g2.same_blocks(g)
    from litemapy import Schematic
    reg = next(iter(Schematic.load(str(tmp_path / "cabin.litematic")).regions.values()))
    assert reg.count_blocks() == sum(g.counts().values())


def test_deterministic_and_seed_recorded():
    a, b = compile_program(CABIN, GD), compile_program(CABIN, GD)
    assert a.hash() == b.hash() and a.to_json() == b.to_json()
    assert a.meta["provenance"]["seed"] == 42 and a.meta["provenance"]["compiler_version"] == compiler.COMPILER_VERSION
    assert Design.from_json(a.to_json()).hash() == a.hash()


def _diff(a, b):
    assert a.size == b.size
    return {(x, y, z) for y in range(a.size[1]) for z in range(a.size[2]) for x in range(a.size[0])
            if a.get(x, y, z) != b.get(x, y, z) or a.role_at(x, y, z) != b.role_at(x, y, z)}


def test_edits_are_local_and_roles_follow():
    base = compile_program(CABIN, GD)
    # roof material edit: only roof cells change
    r = compile_program(CABIN.replace('block="dark_oak")', 'block="spruce")'), GD)
    changed = _diff(base, r)
    assert changed and all(base.role_at(*c) == "roof" == r.role_at(*c) for c in changed)
    # window edit: only cells on the west wall change, and they swap wall/window roles
    w = compile_program(CABIN.replace('side="west", count=1, w=2, h=2', 'side="west", count=1, w=1, h=2'), GD)
    changed = _diff(base, w)
    assert changed and all(x == 1 for x, _, _ in changed)
    assert {base.role_at(*c) for c in changed} | {w.role_at(*c) for c in changed} <= {"window", "wall", "support"}
    # room edit: changing the bedroom to storage changes furniture inside that room only
    s = compile_program(CABIN.replace('room("bedroom"', 'room("storage"'), GD)
    room = next(r for r in base.rooms if r["type"] == "bedroom")
    for x, y, z in _diff(base, s):
        assert room["min"]["x"] <= x <= room["max"]["x"] and room["min"]["z"] <= z <= room["max"]["z"]
    assert [r["type"] for r in s.rooms] == ["living", "storage"]


def test_collision_rule_recorded():
    g = compile_program(CABIN, GD)
    ov = g.meta["provenance"]["overwrites"]
    assert ov["roof->trim"] == 1            # chimney passes through the roof overhang
    assert ov["foundation->floor"] == 63    # 9x7 interior floor replaces the foundation's top layer
    assert g.meta["provenance"]["refused_writes"] == {}
    # lower priority never overwrites: a floor() after furniture would be refused, walls never cover the door
    door = [(x, y, z) for x, y, z, s, r in g.cells() if r == "door"]
    assert len(door) == 2


SNIPPETS = ['__import__("os")', 'open("x")', "exec(", "eval(", ";", "[0]", "lambda: 1", "**", "{", "}", ".__class__",
            "\\", "'", '"', "#", "\n", "()", "=", ",", "99999999", "-", "‮", "\x00", "import sys", "while True:",
            "c.", "c.walls(h=4)\n", "c.foundation(w=64, d=64, h=4)\n", "True", "None", "1e9", "0x10", "@", "$"]


def test_mutation_fuzz(no_host):
    """S-class: 2000 seeded mutations of the cabin program. Each either compiles to a bounded design or fails
    with LangError/CompileError; nothing else, no host access (no_host), each well under the time budget."""
    import random
    import time
    rng = random.Random(55)
    outcomes = {"ok": 0, "LangError": 0, "CompileError": 0}
    slowest = 0.0
    for _ in range(2000):
        s = list(CABIN)
        for _ in range(rng.randint(1, 4)):
            i = rng.randrange(len(s))
            op = rng.random()
            if op < 0.4:
                s[i:i] = list(rng.choice(SNIPPETS))
            elif op < 0.7:
                del s[i:i + rng.randint(1, 8)]
            else:
                s[i] = chr(rng.randrange(1, 0x2FF))
        t = time.perf_counter()
        try:
            g = compile_program("".join(s), GD)
            assert g.size[0] * g.size[1] * g.size[2] <= 2_097_152
            outcomes["ok"] += 1
        except (LangError, CompileError) as e:
            outcomes[type(e).__name__] += 1
        slowest = max(slowest, time.perf_counter() - t)
    assert sum(outcomes.values()) == 2000 and outcomes["LangError"] > 100 and outcomes["CompileError"] > 10
    assert slowest < compiler.TIME_BUDGET_S
    print("fuzz outcomes", outcomes, f"slowest {slowest:.3f}s")


def test_style_defaults_fill_omitted_blocks():
    g = compile_program(BASE + 'c.door(side="south")\nc.roof("gable")', GD)
    by_role = {}
    for x, y, z, s, r in g.cells():
        by_role.setdefault(r, set()).add(s.split("[")[0])
    assert by_role["foundation"] == {"minecraft:cobblestone"}                 # card palette.base[0]
    assert by_role["support"] == {"minecraft:stripped_spruce_log"}            # card palette.frame[0]
    assert "minecraft:spruce_planks" in by_role["wall"]                       # card palette.wall[0]
    assert by_role["door"] == {"minecraft:spruce_door"}                       # card palette.door
    assert {"minecraft:dark_oak_stairs"} <= by_role["roof"]                   # card palette.roof


H02 = """c = Build(style="cozy_cabin", seed=7)
c.foundation(w=10, d=8, h=1, block="cobblestone")
c.walls(h=4, storeys=2)
c.floor()
c.door(side="south", at="center")
c.windows(side="south", count=2, w=2, h=2, storey=0)
c.windows(side="north", count=2, w=2, h=2, storey=0)
c.stairs(storey=0, side="north", at=2)
c.room("living", storey=0)
c.room("kitchen", storey=0)
c.room("bedroom", storey=1)
c.roof("gable", ridge="east_west", overhang=1)
"""


def test_a_crowded_storey_says_what_takes_it_and_how_to_fix_it():
    """O87 (45L h02 died on the bare partition message in attempts 1, 3, 5 and 7): the stairs on the long wall take most
    of the interior; the message ranks what is in the way and names the change, which compiles."""
    with pytest.raises(CompileError) as e:
        compile_program(H02, GD)
    m = str(e.value)
    assert "8 of 8 are taken (the stairs 7, windows on the north wall 4, windows on the south wall 4, the door 1)" in m
    assert "Fix: stairs on the west or east side, or at=1 at one end; or fewer or narrower windows(side=north" in m
    fixed = H02.replace('side="north", at=2', 'side="west", at=2')
    compile_program(fixed, GD)                                                       # the first fix compiles
    with pytest.raises(CompileError, match=r"put the roof\(\.\.\.\) line before chimney"):
        compile_program(fixed.replace('c.roof("gable", ridge="east_west", overhang=1)\n', 'c.chimney(side="west")\n'), GD)


def test_a_full_wall_says_what_takes_it_and_where_the_windows_fit():
    """O87 (45L attempt 7, h08 died on the bare "no room for window 3 of 3 on the north wall")."""
    head = 'c = Build(style="cozy_cabin")\nc.foundation(w=7, d=7)\nc.walls(h=4, frame_every=3)\nc.door(side="south")\n'
    with pytest.raises(CompileError) as e:
        compile_program(head + 'c.windows(side="north", count=3, w=2)\n', GD)
    m = str(e.value)
    assert "the wall's 5 inner blocks are taken (windows 4, frame posts 1)" in m
    assert "Fix: count=2; or windows(side=south, storey=0) for the rest (room for 2)" in m
    compile_program(head + 'c.windows(side="north", count=2, w=2)\nc.windows(side="south", count=1, w=2)\n', GD)
    with pytest.raises(CompileError, match=r"Fix: drop this windows\(\) line"):
        compile_program(head + 'c.windows(side="north", count=2, w=2)\nc.windows(side="north", count=1, w=2)\n', GD)


def test_a_door_without_a_level_approach_gets_a_doorstep():
    """O89l: Eve's Baritone enters a door only on a level move (builder, lane C hut5_tall a12/a13). A door side with no
    floor at the door's height gets one block of foundation material, only where needed and only inside the plot."""
    from eve_design.validate import Grid, check_walkable
    def door_fails(g):
        G = Grid(g, GD)
        return [f for f in check_walkable(G, G.walk())["failures"] if f["reason"].startswith("door at")]
    prog = BASE + 'c.door(side="south")\n'                      # foundation 7 x 7: the door at (3,1,6)
    g = compile_program(prog, GD)
    assert g.meta["provenance"]["doorsteps"] == [{"x": 3, "y": 0, "z": 7}] and g.get(3, 0, 7) == g.get(3, 0, 6)
    assert g.role_at(3, 0, 7) == "foundation" and not door_fails(g)
    assert compile_program(prog + 'c.porch(side="south")\n', GD).meta["provenance"]["doorsteps"] == []   # a deck there
    tight = compile_program(prog, GD, plot={"min": {"x": 0, "z": 0}, "max": {"x": 6, "z": 6}})          # no room for it
    assert tight.meta["provenance"]["doorsteps"] == [] and tight.size[2] == 7
    assert [f["pos"] for f in door_fails(tight)] == [{"x": 3, "y": 1, "z": 6}]
    tall = compile_program(H + 'c.foundation(w=7, d=7, h=2)\nc.walls(h=4)\nc.door(side="south")\n', GD)
    assert tall.meta["provenance"]["doorsteps"] == [] and door_fails(tall)   # a step 2 up could not be climbed: none
    fence = compile_program(prog + 'c.porch(side="south", block="oak_fence")\n', GD)
    assert "the oak_fence at y 0 there is not a floor" in door_fails(fence)[0]["reason"]


def test_stair_errors_name_where_the_run_fits():
    """45L h08 (attempts 3-8, 15 saved failures): a 3-storey tower's second run went on the first run's side (headroom)
    or onto the door's wall, and "put the next run on another side" never converged. A run above storey 0 no longer
    counts as blocking the ground-floor door (4 of the 15 compile as they are), and the errors name the placements that
    fit, clear of the doors and the other runs (the first suggestion compiles 9 of the other 10 with one)."""
    tower = H + 'c.foundation(w=9, d=9)\nc.walls(h=4, storeys=3)\n'
    south = tower + 'c.door(side="south")\nc.stairs(storey=0, side="north", at=2)\n'
    with pytest.raises(CompileError) as e:
        compile_program(south + 'c.stairs(storey=1, side="north", at=2)\n', GD)
    assert 'put the next run on another side: side="south" at=2 or side="west" at=2 or side="east" at=2 for storey 1' \
        in str(e.value)
    assert compile_program(south + 'c.stairs(storey=1, side="south", at=2)\n', GD)     # a storey above the door
    with pytest.raises(CompileError) as e:
        compile_program(tower + 'c.door(side="north")\nc.stairs(storey=0, side="north", at=2)\n', GD)
    assert 'stairs would block the door (these fit, clear of the doors and the stairs given so far: side="south" at=2' \
        in str(e.value)
