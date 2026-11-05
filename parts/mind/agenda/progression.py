r"""O175 PROGRESSION MODE: milestones from a fresh survival world to a diamond and on to a nether portal, as DATA, plus
a pure evaluator. No I/O, no model. Mind evaluates it against the live frame and runs the commands (agenda owns the table).

    next_step(inventory, placed=()) -> {"milestone", "say", "commands", "done", "blocked"}
inventory: the link-protocol state.inventory [{item, count}] (equipment entries may be appended by the caller);
placed: block ids Eve knows she has placed or found standing (crafting_table, furnace, nether_portal ...), from mind's
block memory. A milestone is done when its `have` predicate holds on the LIVE frame, or - for consumables (logs,
planks, sticks, cobblestone, fuel, iron, wool, food, torches, diamonds, obsidian) - when a milestone that needs it is done
(the logs became planks). KEEP milestones (tools, shield, bucket, bed, the placed table/furnace) count only while held or
standing: a lost pickaxe is next again.
"""
from collections import Counter

REGISTRY = "0.15.2"     # 0.15.0 + harvest_animal (combat, from the 0.16.0 draft) + drop_item/select_item/mount/dismount
SKILLS = set("""stop look_at walk_to go_home select_hotbar sneak jump use_item attack_entity mine_block place_block
open_container chat_say craft smelt withdraw deposit mine_resource fight_melee build_schematic fight_ranged
harvest_replant repair_zone gather bridge prospect equip_best dig_up harvest_animal drop_item select_item mount
dismount""".split())

# 1.18+ worldgen (MC 26.3 keeps it). NOTE: sense must confirm the dimension's min_y before a descend_to below 0 is used
# (DIAMOND-WALKTHROUGH 3.3); mind takes descend_to from here, never from command text.
ORES = {
    "diamond": {"y_min": -64, "y_max": 16, "best_y": -58, "note": "densest near the bottom; confirm min_y with sense"},
    "iron": {"y_min": -64, "y_max": 320, "best_y": 16, "also": 232, "note": "also common in mountains around y 232"},
    "coal": {"y_min": 0, "y_max": 192, "best_y": 96},
    "gold": {"y_min": -64, "y_max": 32, "best_y": -16},
    "redstone": {"y_min": -64, "y_max": 16, "best_y": -58},
    "lapis": {"y_min": -64, "y_max": 64, "best_y": 0},
}

TAGS = {
    "logs": lambda i: i.endswith(("_log", "_stem")) and "stripped" not in i,
    "planks": lambda i: i.endswith("_planks"),
    "wool": lambda i: i.endswith("_wool"),
    "bed": lambda i: i.endswith("_bed"),
    "cobble": lambda i: i in ("minecraft:cobblestone", "minecraft:cobbled_deepslate", "minecraft:blackstone"),
    "fuel": lambda i: i in ("minecraft:charcoal", "minecraft:coal"),
    "food": lambda i: i.split(":")[-1] in FOODS,
}
FOODS = {"bread", "apple", "golden_apple", "carrot", "golden_carrot", "baked_potato", "cooked_beef", "cooked_porkchop",
         "cooked_mutton", "cooked_chicken", "cooked_rabbit", "cooked_cod", "cooked_salmon", "cookie", "pumpkin_pie",
         "melon_slice", "sweet_berries", "beef", "porkchop", "mutton", "chicken"}

PORTAL_FRAME = "eve:portal_frame"    # placed-block memory key for a built frame at the build site (mind sets it)
PORTAL_DESIGN, PORTAL_OBSIDIAN, PORTAL_DIRT = "nether_portal v1", 10, 23   # builder 2bf74ebb; v2 ee96350c = 14 + 4

M = lambda id, have, needs, commands, skills, say, note="": dict(  # noqa: E731
    id=id, have=have, needs=needs, commands=commands, skills=skills, say=say, note=note)
MILESTONES = [
    M("wood", {"tag": "logs", "n": 8}, [], ["chop 8 logs"], ["gather"],              # mind: "chop N logs" = gather wood
      "Next: wood - I'll chop 8 logs."),
    M("planks", {"tag": "planks", "n": 16}, ["wood"],
      ["craft 32 planks"], ["craft"], "Next: planks - I'll turn the 8 logs into 32 planks.",
      "32 = table 4, 12 sticks 6, wooden pickaxe 3, shield 6, spare 6, bed 3 (28) + 4 spare"),
    M("crafting_table", {"any": [{"item": "minecraft:crafting_table", "n": 1}, {"placed": "minecraft:crafting_table"}]},
      ["planks"], ["craft a crafting table", "place the crafting table"], ["craft", "place_block"],
      "Next: a crafting table - I'll make one and set it down."),
    M("sticks", {"item": "minecraft:stick", "n": 2},
      ["planks"], ["craft 12 sticks"], ["craft"], "Next: sticks - I'll craft 12.", "12 = four pickaxes 8 + 16 torches 4; have 2 is enough for the next tool, later recipes ask via USES"),
    M("wooden_pickaxe", {"any": [{"item": "minecraft:wooden_pickaxe", "n": 1}, {"item": "minecraft:stone_pickaxe", "n": 1},
                                 {"item": "minecraft:iron_pickaxe", "n": 1}, {"item": "minecraft:diamond_pickaxe", "n": 1}]},
      ["crafting_table", "sticks", "planks"], ["craft a wooden pickaxe"], ["craft"], "Next: a wooden pickaxe, so I can mine stone."),
    M("cobblestone", {"tag": "cobble", "n": 12}, ["wooden_pickaxe"], ["mine 12 stone"], ["mine_resource"],
      "Next: cobblestone - I'll mine 12 stone."),
    M("stone_pickaxe", {"any": [{"item": "minecraft:stone_pickaxe", "n": 1}, {"item": "minecraft:iron_pickaxe", "n": 1},
                                {"item": "minecraft:diamond_pickaxe", "n": 1}]},
      ["cobblestone", "sticks"], ["craft a stone pickaxe"], ["craft"], "Next: a stone pickaxe - iron needs one."),
    M("furnace", {"any": [{"item": "minecraft:furnace", "n": 1}, {"placed": "minecraft:furnace"}]}, ["cobblestone"],
      ["craft a furnace", "place the furnace"], ["craft", "place_block"], "Next: a furnace - 8 cobblestone, then I'll place it."),
    # S93: smelting logs into charcoal needs fuel already (the smelt skill burns coal/charcoal only), so a fresh world
    # starts on coal ore. 8 = 11 raw iron (2) + 6 beef (1) + 16 torches (4), one spare
    M("fuel", {"tag": "fuel", "n": 8}, ["furnace"], ["mine 8 coal ore", "prospect coal ore down to y 48"],
      ["mine_resource", "prospect"], "Next: fuel - I'll mine 8 coal.", "the prospect line is the fallback"),
    M("raw_iron", {"any": [{"item": "minecraft:raw_iron", "n": 11}, {"item": "minecraft:iron_ingot", "n": 11}]},
      ["stone_pickaxe"], ["mine 11 iron ore", "prospect iron ore down to y 16"], ["mine_resource", "prospect"],
      "Next: iron - I'll look for 11 iron ore, digging down toward y 16 if none is in sight.",
      "ORES.iron.best_y; the prospect line is the fallback"),
    M("iron_ingots", {"item": "minecraft:iron_ingot", "n": 11}, ["raw_iron", "furnace", "fuel"], ["smelt 11 raw iron"],
      ["smelt"], "Next: iron ingots - I'll smelt 11 raw iron (pickaxe, shields, shears, bucket, flint and steel)."),
    M("iron_pickaxe", {"any": [{"item": "minecraft:iron_pickaxe", "n": 1}, {"item": "minecraft:diamond_pickaxe", "n": 1}]},
      ["iron_ingots", "sticks"], ["craft an iron pickaxe"], ["craft"], "Next: an iron pickaxe - diamonds need it."),
    M("shield", {"item": "minecraft:shield", "n": 1}, ["iron_ingots", "planks"], ["craft a shield"], ["craft", "equip_best"],
      "Next: a shield, for the night and the caves."),
    # O220e: her shield broke after a 53 s hold and she died shieldless; the count includes the offhand one. S93 (mind):
    # the solver counts held items toward a craft's target, so "kept, held, want one more" says the TOTAL (2)
    M("spare_shield", {"item": "minecraft:shield", "n": 2}, ["shield", "iron_ingots", "planks"], ["craft 2 shields"],
      ["craft"], "Next: a spare shield."),
    # S93: mind's run stops when a milestone comes back unfinished, so drops are counted at their worst
    M("food", {"tag": "food", "n": 6}, ["furnace", "fuel"], ["kill 6 cows", "smelt 6 beef"], ["harvest_animal", "smelt"],
      "Next: food - 6 cooked steaks before going deep.", "a cow drops 1-3 beef: 6 cows give at least 6"),
    M("wool", {"any": [{"tag": "wool", "n": 3}, {"tag": "bed", "n": 1}]}, ["iron_ingots"], ["craft shears", "shear 3 sheep"],
      ["craft", "gather"], "Next: wool - I'll make shears and shear 3 sheep, for a bed.",
      "gather wool = shears on an adult sheep (skills Gather: tool minecraft:shears); a sheep gives 1-3"),
    M("bed", {"tag": "bed", "n": 1}, ["wool", "planks"], ["craft a bed"], ["craft"], "Next: a bed, so we can skip nights."),
    M("torches", {"item": "minecraft:torch", "n": 16}, ["fuel", "sticks"], ["craft 16 torches"], ["craft"],
      "Next: 16 torches for the mine."),
    M("diamond", {"any": [{"item": "minecraft:diamond", "n": 3}, {"item": "minecraft:diamond_pickaxe", "n": 1}]},
      ["iron_pickaxe", "food", "torches", "shield"], ["mine 3 diamond ore", "prospect diamond ore down to y -58 for 20 minutes"], ["mine_resource", "prospect"],
      "Next: diamonds - I'll branch-mine near y -58 for 20 minutes.", "ORES.diamond.best_y; confirm min_y with sense"),
    M("diamond_pickaxe", {"item": "minecraft:diamond_pickaxe", "n": 1}, ["diamond", "sticks"], ["craft a diamond pickaxe"],
      ["craft"], "Next: a diamond pickaxe - only it can mine obsidian."),
    M("bucket", {"any": [{"item": "minecraft:bucket", "n": 1}, {"item": "minecraft:water_bucket", "n": 1},
                         {"item": "minecraft:lava_bucket", "n": 1}]},
      ["iron_ingots"], ["craft a bucket"], ["craft"], "Next: a bucket - water keeps me safe around lava."),
    M("flint_and_steel", {"item": "minecraft:flint_and_steel", "n": 1}, ["iron_ingots"],
      ["mine 30 gravel", "craft a flint and steel"], ["mine_resource", "craft"],
      "Next: flint and steel - I'll dig gravel for flint, plus one iron ingot.", "flint drops from gravel 10% each: 30 gravel miss it 4% of the time (0.9^30); then mind's run stops on the repeat"),
    M("obsidian", {"any": [{"item": "minecraft:obsidian", "n": PORTAL_OBSIDIAN}, {"placed": PORTAL_FRAME}]}, ["diamond_pickaxe", "bucket"],
      ["prospect obsidian down to y -54 for 20 minutes"], ["prospect"],
      "Next: 10 obsidian - lava pools near the bottom, mined with the diamond pickaxe.",
      "10 = builder's v1 frame (no corners), 14 = v2 (corners) - builder confirms after the 45L run; or cast it: "
      "water bucket onto still lava, mine with the diamond pickaxe only"),
    # builder's design files: v1 2bf74ebb = 10 obsidian + 23 dirt scaffold (default here); v2 ee96350c = 14 obsidian +
    # 4 dirt. The dirt is broken and picked up again at the end: a have-on-hand need, done once the frame stands.
    M("scaffold_dirt", {"item": "minecraft:dirt", "n": PORTAL_DIRT}, ["obsidian"], [f"mine {PORTAL_DIRT} dirt"],
      ["mine_resource"], f"Next: {PORTAL_DIRT} dirt for the portal scaffold - I get it back afterwards.",
      "any full block would do, but the design names dirt; v2 needs only 4"),
    # builder (O175 wording): build, then light, as two milestones; both computed from the frame, never stored
    M("portal_frame", {"any": [{"placed": PORTAL_FRAME}, {"placed": "minecraft:nether_portal"}]},
      ["obsidian", "scaffold_dirt"],
      ["build the nether portal"], ["build_schematic"],
      "Next: the portal frame - I'll build it from the obsidian.",
      "build_schematic nether_portal v1/v2; a few dirt for scaffold (v1 more; it comes back when the scaffold is broken) "
      "- count from builder after the 45L run; she then says: Frame done; say 'light the portal' when you have flint "
      "and steel"),
    M("nether_portal", {"placed": "minecraft:nether_portal"}, ["portal_frame", "flint_and_steel"],
      ["light the portal"], ["select_hotbar", "use_item"],
      "Next: light the portal with flint and steel.",
      "lit = minecraft:nether_portal blocks inside the frame; mind maps 'light the portal' (select_hotbar "
      "flint_and_steel + use_item on the bottom inside obsidian, face up) - builder lit v1 6/6 that way on the live 45L (CP-49 attempt 5, sense 0.1.27); the phrase is mind's / "
      "build-flow's mapping"),
]


KEEP = {"crafting_table", "wooden_pickaxe", "stone_pickaxe", "furnace", "iron_pickaxe", "shield", "spare_shield", "bed",
        "diamond_pickaxe", "bucket", "flint_and_steel", "portal_frame", "nether_portal"}
BY_ID = {m["id"]: m for m in MILESTONES}
IRON, STICK = "minecraft:iron_ingot", "minecraft:stick"
USES = {   # per-recipe input amounts a goal must hold (else the input milestone's own n): O220e spare = 1 ingot + 6 planks
    ("iron_pickaxe", "iron_ingots"): {"item": IRON, "n": 3}, ("shield", "iron_ingots"): {"item": IRON, "n": 1},
    ("spare_shield", "iron_ingots"): {"item": IRON, "n": 1}, ("spare_shield", "planks"): {"tag": "planks", "n": 6},
    ("bucket", "iron_ingots"): {"item": IRON, "n": 3}, ("flint_and_steel", "iron_ingots"): {"item": IRON, "n": 1},
    ("wool", "iron_ingots"): {"item": IRON, "n": 2},                                          # shears
    # S93: wood is counted per recipe too (a held wooden pickaxe no longer stands in for planks and sticks)
    ("planks", "wood"): {"tag": "logs", "n": 8}, ("crafting_table", "planks"): {"tag": "planks", "n": 4},
    ("sticks", "planks"): {"tag": "planks", "n": 6}, ("wooden_pickaxe", "planks"): {"tag": "planks", "n": 3},
    ("wooden_pickaxe", "sticks"): {"item": STICK, "n": 2}, ("stone_pickaxe", "sticks"): {"item": STICK, "n": 2},
    ("stone_pickaxe", "cobblestone"): {"tag": "cobble", "n": 3}, ("furnace", "cobblestone"): {"tag": "cobble", "n": 8},
    ("iron_pickaxe", "sticks"): {"item": STICK, "n": 2}, ("diamond_pickaxe", "sticks"): {"item": STICK, "n": 2},
    ("shield", "planks"): {"tag": "planks", "n": 6}, ("bed", "planks"): {"tag": "planks", "n": 3},
    ("food", "fuel"): {"tag": "fuel", "n": 1}, ("iron_ingots", "fuel"): {"tag": "fuel", "n": 2},
    ("torches", "fuel"): {"tag": "fuel", "n": 4}, ("torches", "sticks"): {"item": STICK, "n": 4},
}
USERS = {m["id"]: {u["id"] for u in MILESTONES if m["id"] in u["needs"]} for m in MILESTONES}
TIERS = ["wooden_pickaxe", "stone_pickaxe", "iron_pickaxe", "diamond_pickaxe"]


def counts(inventory):
    c = Counter()
    for s in inventory or []:
        if s and s.get("item"):
            c[s["item"]] += int(s.get("count", 1))
    return c


def holds(have, inv, placed):
    if "any" in have:
        return any(holds(h, inv, placed) for h in have["any"])
    if "placed" in have:
        return have["placed"] in placed
    if "tag" in have:
        return sum(n for i, n in inv.items() if TAGS[have["tag"]](i)) >= have["n"]
    return inv.get(have["item"], 0) >= have["n"]


NEEDS_PORTAL_LIGHT = {"nether_portal"}   # O193: fire stays off; sense 0.1.27's scoped exception allow:portal_light


def want(m):
    """O240: the table is the ONLY source of quantities - what the step must end with ({item|tag|placed, n}); a gather
    or prospect line gathers to this (mind's progress.COUNT is retired)."""
    h = m["have"]["any"][0] if "any" in m["have"] else m["have"]
    return {**h, "n": h.get("n", 1)}


def label(mid):
    return mid.replace("_", " ")


def next_step(inventory, placed=(), portal_light=False, target=None):
    """portal_light: True only while the LIVE sense advertises its portal-lighting exception (mind reads sense's hello
    and passes the bool). Without it the portal milestone is named but WAITS - no commands, the referee decides.
    target (S93 "progress to X"): a milestone id; the step is the next one on X's own chain only (nothing past it, no
    other branch of the table); X done -> milestone None and "<X> is done." - mind stops its run there."""
    if target is not None and target not in BY_ID:
        raise ValueError(f"unknown milestone {target!r}")
    inv, placed = counts(inventory), set(placed or ())
    met = {m["id"] for m in MILESTONES if holds(m["have"], inv, placed)}
    done, stack = set(met), list(met)
    while stack:                                     # consumables used up on the way to a milestone that is met
        for n in BY_ID[stack.pop()]["needs"]:
            if n not in done and n not in KEEP:
                done.add(n)
                stack.append(n)
    for i, low in enumerate(TIERS):                  # a lost pickaxe: skip to the best tier she holds or can make now
        if low not in done and any(h in done or all(n in done for n in BY_ID[h]["needs"]) for h in TIERS[i + 1:]):
            done.add(low)
    def ready(m, n):  # mind CP-86 (live 45L): a goal's consumable inputs must be HELD now; only KEEP items stay implied
        if (m["id"], n) in USES:
            return holds(USES[m["id"], n], inv, placed)
        return n in met or (n in KEEP and n in done)

    def resolve(m):  # demand-driven: the first input the goal lacks, recursively, becomes the step
        lack = next((n for n in m["needs"] if not ready(m, n)), None)
        if lack is None:
            return m
        done.discard(lack)
        return resolve(BY_ID[lack])
    # ponytail: a missing input is gathered to its milestone's n (raw_iron 11 even for one flint and steel); scale
    # the input milestone by USES if that over-gathers in practice
    def step(m, blocked):
        m = resolve(m)
        while owed := {d for d in done if d not in met and d not in KEEP and not USERS[d] <= done}:
            done.difference_update(owed)             # report only what is held, kept, or fully used up
        out = {"milestone": m["id"], "say": m["say"], "commands": m["commands"], "want": want(m), "done": sorted(done),
               "blocked": blocked, **({"target": target} if target else {})}
        if m["id"] in NEEDS_PORTAL_LIGHT and not portal_light:
            out.update(commands=[], waiting="the live sense does not advertise portal_light (O193, sense 0.1.27)",
                       say="The frame is built; I can light it once the referee allows portal lighting.")
        return out

    if target is not None:
        if target in done:
            return {"milestone": None, "say": f"{label(target).capitalize()} is done.", "commands": [],
                    "done": sorted(done), "blocked": [], "target": target}
        return step(BY_ID[target], [])
    blocked = []
    for m in MILESTONES:
        if m["id"] in done:
            continue
        missing = [n for n in m["needs"] if n not in done]
        if not missing:
            return step(m, blocked)
        blocked.append({"id": m["id"], "missing": missing})
    return {"milestone": None, "say": "We have a nether portal. What's next?", "commands": [], "done": sorted(done),
            "blocked": blocked}
