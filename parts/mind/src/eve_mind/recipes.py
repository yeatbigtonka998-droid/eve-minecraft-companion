"""Recipe solver (CP-35) over the pinned game data in data/game/26.3/ (recipe/*.json, tags/item/*.json, and the block/
entity/gameplay loot tables inside the pinned client jar).

solve(book, {item: count}, inventory) -> crafts with batch yields, what is consumed from the inventory, what is
missing (base materials to gather), leftovers, smelt operations + fuel, required stations, and anything impossible.

Model:
- base material: a loot table drops it and no approved recipe makes it except unpacking or ore cooking
  (cobblestone, coal, raw_iron, oak_log, diamond, wheat ...). Missing base material is "gather it".
- unpacking a storage block (iron_block -> 9 iron_ingot) and cooking a silk-touched ore block are used only for
  what is already on hand; they are never planned as a way to obtain something.
- a recipe that loops back to an item being made is skipped; an item reachable only that way is infeasible.
- among candidate recipes/substitutes the one with fewest infeasible, then fewest missing units, then fewest crafts
  wins; ties keep data order, preferring a substitute already in use.
"""
import json
import math
import zipfile
from collections import Counter
from pathlib import Path

DEFAULT_KINDS = ("crafting_shaped", "crafting_shapeless", "smelting")
CRAFTING = ("crafting_shaped", "crafting_shapeless")
COOKING = {"smelting": "furnace", "blasting": "blast_furnace", "smoking": "smoker", "campfire_cooking": "campfire"}
FUELS = ("minecraft:coal", "minecraft:charcoal")      # 1 unit smelts 8 items
UNOBTAINABLE = {f"minecraft:{i}" for i in (
    "bedrock", "barrier", "command_block", "chain_command_block", "repeating_command_block", "structure_block",
    "structure_void", "jigsaw", "light", "spawner", "end_portal_frame", "reinforced_deepslate", "budding_amethyst",
    "petrified_oak_slab", "debug_stick", "knowledge_book", "test_block", "test_instance_block")}
MAX_DEPTH = 12
WORK = 20000         # O131: need() calls per solve before the search turns greedy (ponytail: a cap, not a smarter search)


def natural_drops(jar):
    """Items a block, entity or gameplay (fishing, ...) loot table can drop. Structure-chest loot is excluded."""
    found = set()

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "minecraft:item" and isinstance(node.get("name"), str):
                found.add(node["name"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    with zipfile.ZipFile(jar) as z:
        for n in z.namelist():
            parts = n.split("/")
            if n.startswith("data/minecraft/loot_table/") and n.endswith(".json") and parts[3] in (
                    "blocks", "entities", "gameplay", "shearing", "harvest", "brush"):
                walk(json.loads(z.read(n)))
    return found


class RecipeBook:
    def __init__(self, root: Path):
        root = Path(root)
        self.version = json.loads((root / "MANIFEST.json").read_text("utf-8"))["version"]
        jar = root / "jars" / f"client-{self.version}.jar"
        self.natural = natural_drops(jar) if jar.exists() else None   # None: loot tables unavailable
        self.tags = {f"minecraft:{p.stem}": json.loads(p.read_text("utf-8"))["values"]
                     for p in (root / "tags" / "item").glob("*.json")}
        self.by_output, self.unsolved = {}, {}
        for p in sorted((root / "recipe").glob("*.json")):
            r = json.loads(p.read_text("utf-8"))
            kind = r["type"].split(":")[1]
            res = r.get("result")
            if not isinstance(res, dict) or "id" not in res:
                continue
            if kind == "crafting_shaped":
                cells = Counter(ch for row in r["pattern"] for ch in row if ch != " ")
                ings = [(self.options(r["key"][ch]), n) for ch, n in cells.items()]
                big = len(r["pattern"]) > 2 or max(len(row) for row in r["pattern"]) > 2
                station = "crafting_table" if big else None
            elif kind == "crafting_shapeless":
                ings = [(self.options(i), 1) for i in r["ingredients"]]
                station = "crafting_table" if len(ings) > 4 else None
            elif kind in COOKING or kind == "stonecutting":
                ings = [(self.options(r["ingredient"]), 1)]
                station = COOKING.get(kind, "stonecutter")
            elif kind == "crafting_transmute":         # O131: input + material, same slot count as 2x2
                ings = [(self.options(r["input"]), 1), (self.options(r["material"]), 1)]
                station = None
            elif kind == "smithing_transform":         # O131: template + base + addition at a smithing table
                ings = [(self.options(r[k]), 1) for k in ("template", "base", "addition")]
                station = "smithing_table"
            else:     # smithing, special and transmute recipes are not solved
                self.unsolved.setdefault(res["id"], set()).add(kind)
                continue
            self.by_output.setdefault(res["id"], []).append(
                {"id": p.stem, "kind": kind, "inputs": ings, "out": res.get("count", 1), "item": res["id"],
                 "station": station})
        ins_of = lambda r: {o for opts, _ in r["inputs"] for o in opts}  # noqa: E731

        def reversible(r):    # crafting from one item type that another crafting recipe turns back into it
            ins = ins_of(r)
            return r["kind"] in CRAFTING and len(ins) == 1 and any(
                b["kind"] in CRAFTING and ins_of(b) == {r["item"]} for b in self.by_output.get(next(iter(ins)), []))
        for rs in self.by_output.values():
            for r in rs:
                r["reversible"] = reversible(r)
        for rs in self.by_output.values():
            for r in rs:
                ins = ins_of(r)
                # unpacking a storage block (iron_block -> 9 iron_ingot): the block only ever comes from packing,
                # so plan it only from blocks on hand. iron_ingot -> 9 nuggets is fine: ingots come from smelting.
                unpack = r["reversible"] and r["out"] > 1 and all(
                    b["reversible"] for b in self.by_output.get(next(iter(ins)), []))
                # ponytail: ore blocks recognised by name; they only come from silk touch (Eve gets raw drops)
                ore_cook = r["kind"] in COOKING and all(i.endswith("_ore") for i in ins)
                # stonecutting that undoes a cooking step (26.3: stone -> cobblestone) is a downgrade loop
                undo_cook = r["kind"] == "stonecutting" and any(
                    b["kind"] in COOKING and ins_of(b) == {r["item"]} for i in ins for b in self.by_output.get(i, []))
                # O131: recolouring (any wool/bed/carpet + a dye -> white_wool) only re-dyes what is on hand - as a way
                #   to obtain it, its 16-colour substitute tree made the search explode (white_wool hung > 40 s)
                fam = r["item"].rsplit("_", 1)[-1]
                recolour = any(len(opts) > 1 and all(o.rsplit("_", 1)[-1] == fam for o in opts) for opts, _ in r["inputs"]) \
                    and any(all(o.endswith("_dye") for o in opts) for opts, _ in r["inputs"])
                r["on_hand_only"] = unpack or ore_cook or undo_cook or recolour

    def options(self, ing):
        """An ingredient (item id, '#tag' or list of either) as an ordered tuple of concrete item ids."""
        out = []
        for x in ing if isinstance(ing, list) else [ing]:
            if x.startswith("#"):
                out += [i for i in self.options(self.tags.get(x[1:], [])) if i not in out]
            elif x not in out:
                out.append(x)
        return tuple(out)


class Solver:
    def __init__(self, book, inventory, kinds=DEFAULT_KINDS, avoid=()):
        self.book, self.kinds, self.avoid = book, set(kinds), set(avoid)
        self.inv = Counter({k: v for k, v in inventory.items() if v > 0})
        self.made = Counter()              # produced and not yet used (leftovers)
        self.consumed = Counter()          # taken from the starting inventory
        self.missing = Counter()           # base materials to gather
        self.infeasible = Counter()        # only reachable through a loop or an unapproved recipe
        self.impossible, self.crafts, self.smelts = [], [], 0
        self.work = [WORK]                 # O131: one budget for this solve and all its trial clones

    def clone(self):
        c = Solver.__new__(Solver)
        c.__dict__.update(self.__dict__)
        for k in ("inv", "made", "consumed", "missing", "infeasible"):
            setattr(c, k, Counter(getattr(self, k)))
        c.impossible, c.crafts = list(self.impossible), list(self.crafts)
        return c

    def score(self):
        return (len(self.impossible), sum(self.infeasible.values()),
                sum(self.missing.values()) + self.fuel_missing(), sum(c["times"] for c in self.crafts))

    def fuel_needed(self):
        return math.ceil(self.smelts / 8)

    def fuel_missing(self):
        return max(0, self.fuel_needed() - sum(self.inv[f] for f in FUELS))

    def avail(self, item):
        return self.made[item] + self.inv[item]

    def take(self, item, n):
        a = min(self.made[item], n)
        self.made[item] -= a
        b = min(self.inv[item], n - a)
        self.inv[item] -= b
        self.consumed[item] += b
        return n - a - b

    def allowed(self, opts):
        """Avoided items are excluded from substitutions only; a fixed single-item ingredient is not a substitution."""
        return list(opts) if len(opts) == 1 else [o for o in opts if o not in self.avoid]

    def is_base(self, item):
        if self.book.natural is not None and item not in self.book.natural:
            return False
        return all(r["on_hand_only"] or r["kind"] not in self.kinds for r in self.book.by_output.get(item, []))

    def need(self, item, n, stack=()):
        self.work[0] -= 1                  # O131: shared by clones; past the cap every choice takes its first option
        n = self.take(item, n)
        if n == 0:
            return
        if item in UNOBTAINABLE:
            self.impossible.append(f"{item} cannot be obtained in survival")
            return
        usable = [r for r in self.book.by_output.get(item, []) if r["kind"] in self.kinds and item not in stack
                  and len(stack) < MAX_DEPTH]
        for r in usable:                   # spend what is on hand: storage blocks, silk-touched ores
            if r["on_hand_only"]:
                k = min(self.times_on_hand(r), math.ceil(n / r["out"]))
                if k:
                    self.craft(r, k, stack + (item,))
                    n = self.take(item, n)
                    if n == 0:
                        return
        cands = [r for r in usable if not r["on_hand_only"]]
        if not cands:
            outs = self.book.by_output.get(item, [])
            if self.is_base(item):
                self.missing[item] += n
            elif outs and all(r["kind"] not in self.kinds for r in outs):   # O131: in the book, not allowed here
                kinds = sorted({r["kind"] for r in outs})
                self.impossible.append(f"{item}: {', '.join(kinds)} (not solvable with approved recipes)")
            elif self.book.by_output.get(item) or (self.book.natural and item in self.book.natural):
                self.infeasible[item] += n
            else:
                kinds = sorted(self.book.unsolved.get(item, ())) or ["no recipe and no loot table"]
                self.impossible.append(f"{item}: {', '.join(kinds)} (not solvable with approved recipes)")
            return
        best = None
        for r in (cands if self.work[0] > 0 else cands[:1]):
            trial = self.clone() if len(cands) > 1 else self
            trial.craft(r, math.ceil(n / r["out"]), stack + (item,))
            if best is None or trial.score() < best.score():
                best = trial
        if best is not self:
            self.__dict__.update(best.__dict__)
        self.take(item, n)

    def times_on_hand(self, r):
        return min(sum(self.avail(o) for o in self.allowed(opts)) // c for opts, c in r["inputs"])

    def craft(self, r, times, stack):
        for opts, c in r["inputs"]:
            self.use_any(self.allowed(opts), c * times, stack, r)
        self.made[r["item"]] += r["out"] * times
        self.crafts.append({"recipe": r["id"], "kind": r["kind"], "times": times, "station": r["station"],
                            "output": {r["item"]: r["out"] * times}})
        if r["kind"] in COOKING:
            self.smelts += times

    def use_any(self, opts, n, stack, r):
        """Fill n slots from any allowed option: available units first (mixing allowed), then make the rest."""
        if not opts:
            self.impossible.append(f"{r['id']}: every substitute for an ingredient is avoided")
            return
        for o in opts:
            n = self.take(o, n)
            if n == 0:
                return
        opts = sorted(opts, key=lambda o: not (self.missing[o] or self.consumed[o] or self.made[o]))
        best = None
        for o in (opts if self.work[0] > 0 else opts[:1]):
            trial = self.clone() if len(opts) > 1 else self
            trial.need(o, n, stack)
            if best is None or trial.score() < best.score():
                best = trial
        if best is not self:
            self.__dict__.update(best.__dict__)


def solve(book, want, inventory=None, kinds=DEFAULT_KINDS, avoid=()):
    """want: {item: count}. Items are made in the given order from one shared inventory."""
    if not want or any(not isinstance(c, int) or c < 1 for c in want.values()):
        raise ValueError("want must map items to positive integer counts")
    s = Solver(book, inventory or {}, kinds, avoid)
    for item, count in want.items():
        s.need(item, count)
    fuel, left = {}, s.fuel_needed()
    for f in FUELS:
        k = min(left, s.inv[f])
        if k:
            fuel[f] = k
            s.take(f, k)
            left -= k
    if left:
        s.missing["minecraft:coal"] += left
    impossible = s.impossible + [f"{i} x{n}: only obtainable through a loop or an unapproved recipe"
                                 for i, n in s.infeasible.items()]
    return {"want": dict(want), "feasible": not impossible and not s.missing, "crafts": s.crafts,
            "consumed": dict(+s.consumed), "missing": dict(+s.missing), "leftover": dict(+s.made),
            "smelt_ops": s.smelts, "fuel": fuel, "impossible": impossible,
            "stations": sorted({c["station"] for c in s.crafts if c["station"]}), "game_version": book.version}
