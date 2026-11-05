"""Bill of materials (contracts/design-project.md, CP-48): exact final items from the compiled grid, scaffold
tracked separately, deficit against stock, recipe expansion with real 26.3 recipes (batch yields, leftovers),
carry slots, and labelled estimates for tools, fuel and gathering time."""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter

from .gamedata import GameData, parse_state
from .grid import Design

# Items gathered from the world instead of crafted. ponytail: hand table, PROVISIONAL (not game data);
# extend when a design needs another raw material. Logs come from the game's own #minecraft:logs tag.
RAW = {
    "mine": ["cobblestone", "cobbled_deepslate", "coal", "raw_iron", "raw_copper", "raw_gold", "diamond",
             "redstone", "lapis_lazuli", "emerald", "flint", "andesite", "diorite", "granite",
             "tuff", "calcite", "obsidian", "amethyst_shard", "quartz"],
    "dig": ["dirt", "sand", "red_sand", "gravel", "clay_ball", "mud", "snowball", "coarse_dirt"],
    "farm": ["wheat", "sugar_cane", "bamboo", "cactus", "pumpkin", "melon_slice", "carrot", "potato", "beetroot",
             "poppy", "dandelion", "oxeye_daisy", "cornflower", "kelp", "vine", "moss_block"],
    "hunt": ["white_wool", "string", "leather", "feather", "bone", "slime_ball", "ink_sac", "honeycomb"],
}
VIA = {f"minecraft:{i}": via for via, items in RAW.items() for i in items}
# Estimates, uncalibrated until CP-51 measures real gathering (labelled as such in the output).
MINUTES_PER_ITEM = {"chop": 0.15, "mine": 0.08, "dig": 0.04, "farm": 0.3, "hunt": 1.0, "unknown": None}
TOOL_FOR = {"chop": "axe", "mine": "pickaxe", "dig": "shovel", "strip": "axe"}
ITEMS_PER_COAL = 8          # furnace: one coal smelts 8 items (game rule; fuel item fixed to coal here)
INVENTORY_SLOTS = 36
RECIPE_RANK = {"minecraft:crafting_shaped": 1, "minecraft:crafting_shapeless": 1, "minecraft:smelting": 2,
               "minecraft:stonecutting": 3, "strip": 1, "harden": 1}
SMELT = {"minecraft:smelting"}
WALL_VARIANTS = (("_wall_torch", "_torch"), ("_wall_hanging_sign", "_hanging_sign"), ("_wall_sign", "_sign"),
                 ("_wall_banner", "_banner"), ("_wall_head", "_head"), ("_wall_skull", "_skull"), ("_wall_fan", "_fan"))
COUNT_PROPS = ("candles", "pickles", "eggs", "layers", "flower_amount", "segment_amount")


class BomError(ValueError):
    pass


def items_for_state(state: str, gd: GameData) -> list[tuple[str, int]]:
    """Items consumed to place one block of this state. [] for the second half of a two-block structure."""
    bid, props = parse_state(state)
    if bid == "minecraft:air":
        return []
    if props.get("half") == "upper" and set(gd.blocks.get(bid, {}).get("props", {}).get("half", [])) == {"upper", "lower"}:
        return []                                       # doors, tall plants: one item places both halves
    if props.get("part") == "head" and bid.endswith("_bed"):
        return []
    out: list[tuple[str, int]] = []
    if props.get("waterlogged") == "true":
        out.append(("minecraft:water_bucket", 1))
    if bid == "minecraft:torch" or bid == "minecraft:wall_torch":
        return out + [("minecraft:torch", 1)]
    for a, b in WALL_VARIANTS:
        if bid.endswith(a):
            bid = bid[: -len(a)] + b
            break
    if bid.startswith("minecraft:potted_"):
        return out + [("minecraft:flower_pot", 1), ("minecraft:" + bid[len("minecraft:potted_"):], 1)]
    n = 2 if props.get("type") == "double" and bid.endswith("_slab") else 1
    for p in COUNT_PROPS:
        if p in props:
            n = int(props[p])
    item = {"minecraft:redstone_wire": "minecraft:redstone", "minecraft:tripwire": "minecraft:string",
            "minecraft:water": "minecraft:water_bucket", "minecraft:lava": "minecraft:lava_bucket"}.get(bid, bid)
    if item not in gd.items:
        raise BomError(f"no item places {state}")
    return out + [(item, n)]


def count_items(g: Design, gd: GameData) -> tuple[Counter, list[str]]:
    c, problems = Counter(), []
    for state, n in g.counts().items():
        try:
            for item, k in items_for_state(state, gd):
                c[item] += k * n
        except BomError as e:
            problems.append(str(e))
    return c, problems


def stock_from_storage(index: dict, inventory: dict | None = None) -> tuple[Counter, dict]:
    """storage.index payload (contracts/link-protocol.md). Only loaded 'shared' containers count."""
    stock = Counter(inventory or {})
    for c in index.get("containers", []):
        if c.get("tag") == "shared" and c.get("loaded", True):
            for it in c.get("items", []):
                stock[it["item"]] += int(it["count"])
    h = hashlib.sha256(json.dumps({"index": index, "inventory": inventory or {}}, sort_keys=True).encode()).hexdigest()
    ts = max((c.get("observed_tick", 0) for c in index.get("containers", [])), default=0)
    return stock, {"inventory_hash": h, "storage_index_ts": ts}


# --- recipes ----------------------------------------------------------------------------------------------
def _candidates(spec, gd: GameData) -> list[str]:
    out: list[str] = []
    for s in spec if isinstance(spec, list) else [spec]:
        for i in (_tag_ordered(gd, s) if s.startswith("#") else [s]):
            if i not in out:
                out.append(i)
    return out


def _tag_ordered(gd: GameData, name: str) -> list[str]:
    """Tag members in file order (the game lists the primary member first), nested tags expanded in place."""
    path = name.lstrip("#").partition(":")[2]
    out = []
    for v in json.loads((gd.root / "tags" / "item" / f"{path}.json").read_text(encoding="utf-8"))["values"]:
        v = v["id"] if isinstance(v, dict) else v
        out += _tag_ordered(gd, v) if v.startswith("#") else [v]
    return out


def _ingredients(r: dict) -> list[tuple[list[str] | str, int]]:
    t = r["type"]
    if t == "minecraft:crafting_shaped":
        n = Counter(ch for row in r["pattern"] for ch in row if ch != " ")
        return [(r["key"][ch], k) for ch, k in sorted(n.items())]
    if t == "minecraft:crafting_shapeless":
        return [(spec, 1) for spec in r["ingredients"]]
    return [(r["ingredient"], 1)]


class Planner:
    def __init__(self, gd: GameData, stock: Counter, demand: Counter):
        self.gd, self.stock, self.top = gd, stock, demand
        # Choppable = natural logs/stems; stripped variants come from an axe on the unstripped block.
        self.logs = {i for i in gd.tag("item", "minecraft:logs")
                     if i.endswith(("_log", "_stem")) and not i.startswith("minecraft:stripped_")}
        self.choice: dict[str, dict | None] = {}
        self.status: dict[str, int] = {}

    def via(self, item: str) -> str | None:
        return "chop" if item in self.logs else VIA.get(item)

    def pick(self, spec) -> str:
        """Tag/alternative choice: in stock, else gathered directly, else already demanded, else the tag's first."""
        c = _candidates(spec, self.gd)
        return (next((i for i in c if self.stock[i] > 0), None) or next((i for i in c if self.via(i)), None)
                or next((i for i in c if i in self.top), None) or c[0])

    def _recipes(self, item: str) -> list[tuple[str, dict]]:
        out = [(rid, r) for rid, r in self.gd.recipes.get(item, []) if r["type"] in RECIPE_RANK]
        if item.startswith("minecraft:stripped_"):  # axe-stripping, 1 block -> 1 block
            src = "minecraft:" + item[len("minecraft:stripped_"):]
            if src in self.gd.items:
                out.append(("axe:strip", {"type": "strip", "ingredient": src, "result": {"id": item}}))
        if item.endswith("_concrete") and item + "_powder" in self.gd.items:  # powder placed next to water hardens
            out.append(("water:harden", {"type": "harden", "ingredient": item + "_powder", "result": {"id": item}}))
        return sorted(out, key=lambda x: (x[0] != item, RECIPE_RANK.get(x[1]["type"], 0), x[0]))

    def choose(self, item: str, path: tuple = ()) -> int:
        """Pick how to obtain item. Returns 2 = fully resolved, 1 = has an unknown leaf, 0 = only cycles.
        Prefers the first candidate (recipe id == item, crafting, smelting, stonecutting) that resolves fully."""
        if item in self.status:
            return self.status[item]
        if item in path:
            return 0
        if self.via(item):
            self.choice[item], self.status[item] = None, 2
            return 2
        best = None
        for rid, r in self._recipes(item):
            ings = [(self.pick(spec), k) for spec, k in _ingredients(r)]
            st = min((self.choose(i, path + (item,)) for i, _ in ings), default=2)
            if st and (best is None or st > best[0]):
                best = (st, {"recipe": rid, "type": r["type"], "yield": int(r["result"].get("count", 1)),
                             "ingredients": ings})
                if st == 2:
                    break
        if best is None:
            if path and self._recipes(item):
                return 0                      # only cycles from here; let the caller try another recipe
            self.choice[item], self.status[item] = None, 1   # no recipe, not raw: reported as via 'unknown'
            return 1
        self.choice[item], self.status[item] = best[1], best[0]
        return best[0]

    def order(self) -> list[str]:
        """Consumers before producers, so each item's total demand is known before it is expanded."""
        consumers = Counter()
        for c in self.choice.values():
            for i, _ in (c or {}).get("ingredients", []):
                consumers[i] += 1
        ready = sorted(i for i in self.choice if consumers[i] == 0)
        out = []
        while ready:
            i = ready.pop(0)
            out.append(i)
            for j, _ in (self.choice[i] or {}).get("ingredients", []):
                consumers[j] -= 1
                if consumers[j] == 0:
                    ready.append(j)
                    ready.sort()
        if len(out) != len(self.choice):
            raise BomError("recipe cycle")
        return out


def compute(g: Design, gd: GameData, scaffold: Design | None = None, stock: Counter | None = None,
            stock_ref: dict | None = None) -> dict:
    final, problems = count_items(g, gd)
    scaf, p2 = count_items(scaffold, gd) if scaffold else (Counter(), [])
    if problems or p2:
        raise BomError("; ".join(problems + p2))
    stock = Counter(stock or {})
    demand = final + scaf
    pl = Planner(gd, stock, demand)
    for item in sorted(demand):
        pl.choose(item)
    pl.choice = {i: c for i, c in pl.choice.items() if i in pl.status}
    need = Counter(demand)
    left, crafting, gather, smelted, strip_uses = Counter(), [], Counter(), 0, 0
    remaining = Counter(stock)
    for item in pl.order():
        take = min(remaining[item], need[item])
        remaining[item] -= take
        short = need[item] - take
        if short <= 0:
            continue
        c = pl.choice[item]
        if c is None:
            gather[item] += short
            continue
        batches = math.ceil(short / c["yield"])
        produced = batches * c["yield"]
        left[item] += produced - short
        if c["type"] in SMELT:
            smelted += batches
        if c["type"] == "strip":
            strip_uses += batches
        crafting.append({"item": item, "recipe": c["recipe"], "type": c["type"], "batches": batches,
                         "yield_per_batch": c["yield"], "produced": produced, "leftover": produced - short,
                         "consumes": [{"item": i, "count": k * batches} for i, k in c["ingredients"]]})
        for i, k in c["ingredients"]:
            need[i] += k * batches
    fuel_coal = math.ceil(smelted / ITEMS_PER_COAL)
    fuel_from_stock = min(fuel_coal, remaining["minecraft:coal"])
    remaining["minecraft:coal"] -= fuel_from_stock
    if fuel_coal - fuel_from_stock:
        gather["minecraft:coal"] += fuel_coal - fuel_from_stock

    plan, uses = [], Counter({"axe": strip_uses} if strip_uses else {})
    for item in sorted(gather):
        via = pl.via(item) or "unknown"
        per = MINUTES_PER_ITEM[via]
        plan.append({"item": item, "count": gather[item], "via": via,
                     "estimated_minutes": round(per * gather[item], 1) if per is not None else None})
        if via in TOOL_FOR:
            uses[TOOL_FOR[via]] += gather[item]
    if scaffold:
        for item, n in scaf.items():  # removing scaffold breaks each block once
            via = pl.via(item)
            if via in TOOL_FOR:
                uses[TOOL_FOR[via]] += n
    slots = sum(math.ceil(n / gd.max_stack(i)) for i, n in demand.items())
    missing = [{"item": i, "count": demand[i] - min(demand[i], stock[i])} for i in sorted(demand) if demand[i] > stock[i]]
    gather_minutes = [p["estimated_minutes"] for p in plan]
    return {
        "design_hash": g.hash(),
        "scaffold_hash": scaffold.hash() if scaffold else None,
        "final": [{"item": i, "count": final[i]} for i in sorted(final)],
        "items_per_state": {st: [{"item": i, "count": n} for i, n in items_for_state(st, gd)]
                            for st in sorted(set(g.palette[1:]) | set(scaffold.palette[1:] if scaffold else []))},
        "scaffold": [{"item": i, "count": scaf[i]} for i in sorted(scaf)],
        "tools": [{"item": f"minecraft:<any>_{t}", "uses": n, "estimate": True} for t, n in sorted(uses.items())],
        "fuel": [{"item": "minecraft:coal", "count": fuel_coal, "smelted_items": smelted, "estimate": True}],
        "crafting": crafting,
        "leftovers": [{"item": i, "count": n} for i, n in sorted(left.items()) if n],
        "carry": {"slots": slots, "inventory_slots": INVENTORY_SLOTS, "trips": math.ceil(slots / INVENTORY_SLOTS)},
        "deficit": {"computed_against": stock_ref or {"inventory_hash": None, "storage_index_ts": 0},
                    "missing": missing, "gather_plan": plan,
                    "note": "design-side estimate; the mind recomputes from live state before gathering"},
        "totals": {"final_blocks": sum(g.counts().values()),
                   "scaffold_blocks": sum(scaffold.counts().values()) if scaffold else 0,
                   "estimated_gather_minutes": None if None in gather_minutes else round(sum(gather_minutes), 1),
                   "estimated_build_minutes": None},
        "estimates_note": "tools (1 use per block broken), fuel (coal, 8 items each) and minutes are uncalibrated estimates",
        "game_data": {"version": gd.version, "data_version": gd.data_version},
    }
