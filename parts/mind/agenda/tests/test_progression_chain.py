"""S93: the whole progression chain, fresh world -> lit portal, run as a simulation against mind's recipe solver
(eve_mind.recipes over data/game/26.3, the kinds craftkit uses) - every craft/smelt command must be feasible from the
inventory the earlier milestones left, with fuel, stations and tools; gathers yield their WORST natural drop (cows 1
beef, sheep 1 wool; gravel's flint is chance, counted at 1 per 10). Plus "progress to X" for every milestone id and a
registry 0.15.2 check of every milestone's skills."""
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))
sys.path.append(str(HERE.parents[2] / "src"))      # mind's solver; a PYTHONPATH copy (mind's live branch) wins
from agenda import adapter, progression as P  # noqa: E402

ROOT = Path(os.environ.get("EVE_ROOT") or HERE.parents[4])     # mind's EVE_ROOT: the checkout with data/ + contracts/
GAME = Path(os.environ.get("EVE_GAME_ROOT") or ROOT / "data" / "game" / "26.3")
recipes = pytest.importorskip("eve_mind.recipes")
try:
    from eve_mind.craftkit import KINDS
except ImportError:                                 # an older mind: craftkit's O131 kinds
    KINDS = recipes.CRAFTING + ("smelting",)
if not (GAME / "recipe").is_dir():
    pytest.skip(f"pinned game data not at {GAME} (set EVE_GAME_ROOT)", allow_module_level=True)

MC = "minecraft:"
NAMES = {"planks": "oak_planks", "sticks": "stick", "torches": "torch", "bed": "white_bed", "raw iron": "iron_ingot",
         "beef": "cooked_beef", "shields": "shield"}
ORE = {"coal": "coal", "iron": "raw_iron", "diamond": "diamond"}
TOOL = {"coal": P.TIERS, "iron": P.TIERS[1:], "diamond": P.TIERS[2:], "obsidian": P.TIERS[3:]}


@pytest.fixture(scope="module")
def book():
    return recipes.RecipeBook(GAME)


class Sim:
    def __init__(self, book, inv=None, placed=(), worst=True):
        self.book, self.inv, self.placed, self.worst = book, Counter(inv or {}), set(placed), worst
        self.steps = []                                 # (milestone, inventory before it, placed before it)

    def has_tool(self, ore):
        return any(self.inv[MC + t] for t in TOOL[ore])

    def solve(self, cmd, item, n):
        plan = recipes.solve(self.book, {item: n}, dict(+self.inv), kinds=KINDS)
        assert plan["feasible"], (cmd, plan["missing"], plan["impossible"], dict(+self.inv))
        for s in plan["stations"]:
            assert MC + s in self.placed or self.inv[MC + s], (cmd, "no station", s)
        self.inv -= Counter(plan["consumed"])
        self.inv += Counter({item: n}) + Counter(plan["leftover"])     # the batch remainder is added, not a replacement

    def do(self, cmd):
        inv = self.inv
        if m := re.fullmatch(r"chop (\d+) logs", cmd):
            inv[MC + "oak_log"] += int(m[1])
        elif m := re.fullmatch(r"mine (\d+) stone", cmd):
            assert self.has_tool("coal"), cmd
            inv[MC + "cobblestone"] += int(m[1])
        elif m := re.fullmatch(r"mine (\d+) (coal|iron|diamond) ore", cmd):
            assert self.has_tool(m[2]), (cmd, "pickaxe tier")
            inv[MC + ORE[m[2]]] += int(m[1])
        elif re.fullmatch(r"prospect (coal|iron|diamond) ore down to y -?\d+( for \d+ minutes)?", cmd):
            pass                                        # the fallback: the mine line before it already got them
        elif re.fullmatch(r"prospect obsidian down to y -?\d+ for \d+ minutes", cmd):
            assert self.has_tool("obsidian"), cmd
            inv[MC + "obsidian"] += P.PORTAL_OBSIDIAN
        elif m := re.fullmatch(r"mine (\d+) (dirt|gravel)", cmd):
            n = int(m[1])
            if m[2] == "gravel":
                inv[MC + "flint"] += n // 10           # chance: 10% per gravel (the one non-worst-case drop)
            inv[MC + m[2]] += n
        elif m := re.fullmatch(r"kill (\d+) cows", cmd):
            inv[MC + "beef"] += int(m[1]) * (1 if self.worst else 2)
        elif m := re.fullmatch(r"shear (\d+) sheep", cmd):
            assert inv[MC + "shears"], (cmd, "no shears")
            inv[MC + "white_wool"] += int(m[1]) * (1 if self.worst else 2)
        elif m := re.fullmatch(r"smelt (\d+) (beef|raw iron)", cmd):
            assert MC + "furnace" in self.placed, cmd
            self.solve(cmd, MC + NAMES[m[2]], int(m[1]))
        elif m := re.fullmatch(r"craft (a|an|\d+) (.+)", cmd) or re.fullmatch(r"craft ()(shears)", cmd):
            name = m[2]
            self.solve(cmd, MC + NAMES.get(name, name.replace(" ", "_")), int(m[1]) if m[1].isdigit() else 1)
        elif m := re.fullmatch(r"place the (.+)", cmd):
            item = MC + m[1].replace(" ", "_")
            assert inv[item], cmd
            inv[item] -= 1
            self.placed.add(item)
        elif cmd == "build the nether portal":
            assert inv[MC + "obsidian"] >= P.PORTAL_OBSIDIAN and inv[MC + "dirt"] >= P.PORTAL_DIRT, dict(+inv)
            inv[MC + "obsidian"] -= P.PORTAL_OBSIDIAN   # the scaffold dirt comes back
            self.placed.add(P.PORTAL_FRAME)
        elif cmd == "light the portal":
            assert inv[MC + "flint_and_steel"] and P.PORTAL_FRAME in self.placed, cmd
            self.placed.add(MC + "nether_portal")
        else:
            raise AssertionError(f"command not modelled (and maybe not mapped by mind): {cmd!r}")

    def run(self, target=None, limit=80):
        for _ in range(limit):
            frame = [{"slot": i, "item": k, "count": v} for i, (k, v) in enumerate(sorted((+self.inv).items()))]
            r = P.next_step(frame, self.placed, portal_light=True, target=target)
            if r["milestone"] is None:
                return r
            assert r["commands"] and r["say"].startswith("Next:"), r
            assert not self.steps or self.steps[-1][0] != r["milestone"], (
                f"{r['milestone']} came back unfinished - mind's run stops there", dict(+self.inv))
            self.steps.append((r["milestone"], Counter(self.inv), set(self.placed)))
            for cmd in r["commands"]:
                self.do(cmd)
        raise AssertionError("no end in sight")


@pytest.fixture(scope="module")
def full(book):
    s = Sim(book)
    end = s.run()
    return s, end


def test_fresh_world_to_a_lit_portal_with_worst_drops(full):
    s, end = full
    assert end["milestone"] is None and "nether portal" in end["say"]
    assert MC + "nether_portal" in s.placed and s.inv[MC + "shield"] == 2
    ids = [m for m, _, _ in s.steps]
    assert len(ids) == len(set(ids)), " ".join(ids)     # every milestone once: its quantities sufficed


@pytest.mark.parametrize("mid", [m["id"] for m in P.MILESTONES])
def test_each_milestone_runs_from_the_inventory_before_it(full, book, mid):
    """Every milestone is a step of the fresh-world run; replayed alone from the inventory the run had just before
    it (a realistic prior inventory), its commands succeed and the milestone is then met."""
    s, _ = full
    before = {m: (inv, placed) for m, inv, placed in s.steps}
    assert mid in before, f"{mid} never came up on the fresh-world run"
    one = Sim(book, *before[mid])
    r = P.next_step([{"item": k, "count": v} for k, v in (+one.inv).items()], one.placed, portal_light=True)
    for cmd in P.BY_ID[mid]["commands"]:
        one.do(cmd)
    frame = [{"item": k, "count": v} for k, v in (+one.inv).items()]
    assert mid in P.next_step(frame, one.placed, portal_light=True, target=mid)["done"] or \
        P.holds(P.BY_ID[mid]["have"], P.counts(frame), one.placed), (mid, r["milestone"], dict(+one.inv))


@pytest.mark.parametrize("mid", [m["id"] for m in P.MILESTONES])
def test_progress_to_each_milestone_is_bounded(book, mid):
    """'progress to X' from a fresh world: only X's own chain runs, then X is done and the run ends."""
    s = Sim(book)
    end = s.run(target=mid)
    assert end["milestone"] is None and end["say"] == f"{P.label(mid).capitalize()} is done." and end["target"] == mid
    chain, todo = set(), [mid]
    while todo:
        c = todo.pop()
        if c not in chain:
            chain.add(c)
            todo += P.BY_ID[c]["needs"]
    stray = [m for m, _, _ in s.steps if m not in chain]
    assert not stray, (mid, stray)                      # nothing outside X's prerequisites
    assert s.steps[-1][0] == mid


def test_every_milestone_has_its_spoken_next_line_and_a_target_phrase():
    for m in P.MILESTONES:
        assert m["say"].startswith("Next: ") and len(m["say"]) <= 120, m["id"]
        assert adapter.progress_target(P.label(m["id"])) == m["id"]
        assert adapter.progress_target("the " + P.label(m["id"])) == m["id"]
    for said, mid in [("diamonds", "diamond"), ("an iron pickaxe", "iron_pickaxe"), ("the nether portal", "nether_portal"),
                      ("the portal", "nether_portal"), ("iron", "iron_ingots"), ("a spare shield", "spare_shield"),
                      ("torches", "torches"), ("a bed", "bed"), ("coal", "fuel"), ("Flint and steel", "flint_and_steel")]:
        assert adapter.progress_target(said) == mid, said
    assert adapter.progress_target("the moon") is None


def test_every_milestone_skill_is_in_registry_0_15_2():
    reg = json.loads((ROOT / "contracts" / "skills" / "drafts" / "registry-0.15.2.json").read_text("utf-8"))
    names = {s["name"] for s in reg["skills"]}
    for m in P.MILESTONES:
        assert m["skills"] and set(m["skills"]) <= names, (m["id"], set(m["skills"]) - names)


def test_each_step_carries_its_quantity_and_gather_lines_match_it():
    """O240: the table is the only source of quantities. Every step says what it must end with (want), and a gather
    line's number is that n (a prospect line gathers to want.n - mind's progress.COUNT is retired)."""
    for m in P.MILESTONES:
        w = P.want(m)
        assert w["n"] >= 1 and ("item" in w or "tag" in w or "placed" in w), m["id"]
        for cmd in m["commands"]:
            g = re.fullmatch(r"(?:chop|mine|kill|shear) (\d+) .+", cmd)
            if g and m["id"] not in ("flint_and_steel", "food", "wool"):   # gravel->flint, cows->beef, sheep->wool
                assert int(g[1]) == w["n"], (m["id"], cmd, w)
    assert P.want(P.BY_ID["diamond"])["n"] == 3 and P.want(P.BY_ID["iron_ingots"])["n"] == 11
    assert P.want(P.BY_ID["obsidian"])["n"] == P.PORTAL_OBSIDIAN
    r = P.next_step([])
    assert r["want"] == {"tag": "logs", "n": 8}


def test_fresh_world_run_is_monotone(full):
    """O240: 26 milestones in 26 steps, in table order, none revisited."""
    s, _ = full
    assert [m for m, _, _ in s.steps] == [m["id"] for m in P.MILESTONES]
