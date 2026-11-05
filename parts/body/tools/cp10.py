"""CP-10 ordinary player interactions on the Shark local fixture. Same rules as arena.py (it reuses its Run): fixed commands,
no model, server truth by RCON, every attempt counted into evidence/CP-10/<stamp>-attempt-N/.

    py -3.12 tools/cp10.py [--player EveDev]      (dev server + dev client up, EVE_LINK_TOKEN set, stub not running)
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from arena import FLOOR, Run, nums  # noqa: E402

CRITERIA = """Eve stands at (0.5, -60, 0.5) facing +z on a smooth-stone floor (difficulty easy, no natural spawns, every non-player entity
and item removed before each attempt). Hotbar: 0 stone_sword, 1 wooden_pickaxe, 2 wooden_hoe, 3 golden_apple x3, 4 dirt x16,
5 wooden_shovel, 6 wooden_axe, 7 shears, 8 cobblestone x16. Expected results are written per case below (independent of the body).
- Normal cases (B): 5 variants x 4 per skill (place_block 6: a wall torch since 2026-10-20) for mine_block, place_block, open_container, attack_entity, use_item; 5 chat_say.
  Pass = result `ok` AND the server shows the expected effect (block state by `execute if block`, inventory count by
  `clear <p> <item> 0`, container contents exactly as written into the fixture, target Health below its start or the target dead).
- Failure cases (K): out of range, obstructed (a block or glass between eye and target), wrong/missing block, unbreakable, no tool,
  occupied, no support, no item, not a container, protected targets (villager, named pig, iron golem, tamed wolf, a villager inside
  the sweep area), entity gone, nothing to use, a "/" chat line; placing into Eve's own space or a mob's -> occupied;
  mining beside a 3-deep pit: the block goes and her server y stays at the floor (drop collection must not walk her in);
  mining a block the server refuses to break (dev sender denies sponge, like sense's rails): `zone_denied` within 5 s after exactly
  one server denial, block still there (no re-dig); the target removed by RCON 0.3 s into a dig (added 2026-10-20 after CP-22 on
  the 45L): `wrong_block` with a note, nothing collected; a water source behind the block flowing into the dug cell (added
  2026-10-21, skills O71): `ok` and water in the cell (it is a break, not a server restore). Pass = the expected code AND no server-side change
  (target block unchanged, protected Health unchanged, inventory unchanged where relevant).
- `obstructed` is the body's code for "crosshair is on something else"; it is being added to attack_entity, mine_block,
  place_block and open_container in the registry (use_item already has it).
- O114 chat queue (1 line / 2 s kept): a second chat_say inside the gap is `ok`, sent >= 1.5 s after the first and in the
  server log (it used to be refused); five chat.send lines at once: a, then the reply (reply:true), b, c 2 s apart in the
  log, and the fifth refused `rate_limited` once (queue full).
- O130 place from the main inventory: the only crafting table in inventory slot 20; place_block is `ok`, the table placed,
  none left, evidence item_from_inventory (4 reps).
- O134 drop_item (registry 0.15.1 only): 3 of 5 cobblestone from inventory slot 23 toward a block 2 ahead: `ok`, 2 left,
  faced, entity_ids, an item entity near the target (4 reps); the stone sword `protected_kit` (kept), gold_block `no_item`,
  5 golden apples of 3 `partial` (all 3 tossed).
- O136 owner_target (registry 0.15.2 only): a wandering trader with owner_target is hit; with a protected villager in its
  sweep area it is still `protected_entity` (both unhurt).
- select_item (registry 0.15.2 only): the only oak boat in inventory slot 22 is pulled and held (`ok`, mainhand oak_boat,
  item_from_inventory; 4 reps); no boat: `no_item`.
- boat launch: look_at a 3x3 pool 3 ahead, use_item with no target: `ok`, one oak boat on the pool, none left in hand (4 reps).
- O145 mount/dismount (registry with mount only): mount a boat 2 ahead (`ok`, the server has her riding it, vehicle_id),
  dismount (`ok`, on foot) (4 reps); a cow `not_rideable`.
- O149 faster tool: dirt, the kit's wooden shovel in the hotbar, a stone shovel in inventory slot 20: mine_block `ok` with
  tool_used stone_shovel and tool_from_inventory (4 reps)."""

HOTBAR = ["stone_sword", "wooden_pickaxe", "wooden_hoe", "golden_apple 3", "dirt 16", "wooden_shovel", "wooden_axe", "shears", "cobblestone 16"]


class T:
    def __init__(self, r: Run):
        self.r = r

    def setup(self, *cmds):
        r = self.r
        r.clear_entities()
        r.rcon("fill -6 -60 -6 6 -55 8 minecraft:air")
        r.rcon("fill -6 -61 -6 6 -61 8 minecraft:smooth_stone")
        r.rcon(f"clear {r.player}")
        for it in HOTBAR:
            r.rcon(f"give {r.player} minecraft:{it}")
        r.rcon(f"tp {r.player} 0.5 {FLOOR} 0.5 0 0")
        for c in cmds:
            out = r.rcon(c)
            if re.search(r"Unknown|Incorrect|Expected|Invalid|Failed to parse", out):
                raise RuntimeError(f"fixture command failed: {c!r} -> {out!r}")
        time.sleep(0.8)

    def block_is(self, x, y, z, block):
        return self.r.rcon(f"execute if block {x} {y} {z} minecraft:{block}") == "Test passed"

    def count(self, item):
        out = self.r.rcon(f"clear {self.r.player} minecraft:{item} 0")
        m = re.search(r"Found (\d+)", out)
        return int(m.group(1)) if m else 0

    def health(self, tag):
        v = nums(self.r.rcon(f"data get entity @e[tag={tag},limit=1] Health"))
        return v[0] if v else None  # None = no such entity (dead)

    def entity_id(self, etype, timeout=4.0):
        """Network id of the nearest entity of etype in the body's own state (what a mind would use)."""
        end = time.time() + timeout
        while time.time() < end:
            st = self.r.state()
            if st:
                es = sorted((e for e in st["nearby_entities"] if e["type"] == f"minecraft:{etype}"), key=lambda e: e["distance"])
                if es:
                    return es[0]["id"]
            time.sleep(0.25)
        return None

    def select(self, slot):
        self.r.act("select_hotbar", {"slot": slot})

    def check(self, variant, skill, params, res, expect_code, server_ok, detail=""):
        got = res and (res.get("status") if res.get("status") != "failed" else res.get("code"))
        if res and "code" in res and "status" not in res:  # action.rejected
            got = "rejected:" + res["code"]
        want = expect_code or "ok"
        ok = got == want and server_ok
        self.r.record(variant, skill, params, ok, f"want={want} got={got} server_ok={server_ok} {detail} "
                      f"ev={res and res.get('evidence')} note={res and (res.get('note') or res.get('reason'))}", result=res)
        return ok


def run(r: Run):
    r.fixture()
    r.rcon("difficulty easy")
    t = T(r)
    P = lambda x, y, z: {"x": x, "y": y, "z": z}  # noqa: E731

    # ---- mine_block normal: block, tool expected, drop expected
    mines = [("dirt", "dirt"), ("stone", "cobblestone"), ("oak_planks", "oak_planks"), ("sand", "sand"), ("cobblestone", "cobblestone")]
    for rep in range(4):
        for blk, drop in mines:
            t.setup(f"setblock 0 {FLOOR:.0f} 2 minecraft:{blk}")
            before = t.count(drop)
            _, _, res = r.act("mine_block", {"pos": P(0, -60, 2)})
            time.sleep(0.3)
            t.check(f"mine_{blk}#{rep}", "mine_block", {"pos": P(0, -60, 2)}, res, None,
                    t.block_is(0, -60, 2, "air") and t.count(drop) == before + 1, f"{drop} {before}->{t.count(drop)}")
    # ---- mine_block failures
    fails = [
        ("unbreakable", ["setblock 0 -60 2 minecraft:bedrock"], {"pos": P(0, -60, 2)}, "unbreakable", ("bedrock", (0, -60, 2))),
        ("no_tool", ["setblock 0 -60 2 minecraft:stone", "clear EveDev minecraft:wooden_pickaxe"], {"pos": P(0, -60, 2)}, "no_tool", ("stone", (0, -60, 2))),
        ("wrong_block", ["setblock 0 -60 2 minecraft:dirt"], {"pos": P(0, -60, 2), "expect_block": "minecraft:stone"}, "wrong_block", ("dirt", (0, -60, 2))),
        ("air", [], {"pos": P(0, -60, 2)}, "wrong_block", ("air", (0, -60, 2))),
        ("out_of_range", ["setblock 0 -60 7 minecraft:stone"], {"pos": P(0, -60, 7)}, "out_of_range", ("stone", (0, -60, 7))),
        ("obstructed", ["setblock 0 -60 3 minecraft:stone", "setblock 0 -59 2 minecraft:stone"], {"pos": P(0, -60, 3)}, "obstructed", ("stone", (0, -60, 3))),
    ]
    for name, cmds, params, code, (blk, (x, y, z)) in fails:
        t.setup(*[c.replace("EveDev", r.player) for c in cmds])
        _, _, res = r.act("mine_block", params)
        t.check(f"mine_fail_{name}", "mine_block", params, res, code, t.block_is(x, y, z, blk))
    # a server that refuses the break (dev sender's sponge rule, like sense's rails): one dig, then zone_denied, never a re-dig
    deny_log = Path(__file__).resolve().parents[1] / "run-server" / "evetest-log.jsonl"
    for rep in range(3):
        t.setup("setblock 0 -60 2 minecraft:sponge")
        n0 = deny_log.read_text(encoding="utf-8").count("break_denied") if deny_log.exists() else 0
        t0 = time.time()
        _, _, res = r.act("mine_block", {"pos": P(0, -60, 2)})
        took = time.time() - t0
        time.sleep(1.0)
        denials = deny_log.read_text(encoding="utf-8").count("break_denied") - n0
        t.check(f"mine_denied_by_server#{rep}", "mine_block", {"pos": P(0, -60, 2)}, res, "zone_denied",
                t.block_is(0, -60, 2, "sponge") and denials == 1 and took < 5, f"server denials={denials} took={took:.1f}s")
    # the target removed by someone else mid-dig (CP-22 on the 45L): failed wrong_block with a note, never ok; nothing collected
    for rep in range(4):
        t.setup("setblock 0 -60 2 minecraft:oak_log")
        before = t.count("oak_log")
        since = len(r.hub.frames)
        aid = r.hub.request("mine_block", {"pos": P(0, -60, 2)})
        time.sleep(0.3)
        r.rcon("setblock 0 -60 2 minecraft:air")
        res = r.hub.wait_result(aid, 15, since)
        t.check(f"mine_target_removed#{rep}", "mine_block", {"pos": P(0, -60, 2)}, res, "wrong_block",
                t.block_is(0, -60, 2, "air") and t.count("oak_log") == before and bool(res and res.get("note")),
                f"note={res and res.get('note')!r} oak_log {before}->{t.count('oak_log')}")
    # water from a source behind the block flows into the dug cell within the 10-tick check (skills O71, prospect toward water):
    # a break, not a server restore -> ok, the cell holds water (before the fix: zone_denied "the server restored minecraft:water")
    for rep in range(4):
        t.setup("setblock 0 -60 2 minecraft:dirt", "setblock 0 -60 3 minecraft:water")
        _, _, res = r.act("mine_block", {"pos": P(0, -60, 2)})
        time.sleep(0.5)
        t.check(f"mine_water_inflow#{rep}", "mine_block", {"pos": P(0, -60, 2)}, res, None, t.block_is(0, -60, 2, "water"),
                f"note={res and res.get('note')!r}")
    # an older drop lying 3.6 from the block (skills cp40p p_stairdrop: one trapped under a block she had placed) is not hers to
    # fetch: she collects the dirt this dig dropped, and the stick stays where it lies (before: the walk went on to the nearest item)
    for rep in range(4):
        t.setup("setblock 0 -60 2 minecraft:dirt", 'summon minecraft:item 3.5 -60 4.5 {Item:{id:"minecraft:stick",count:1},Tags:["olddrop"]}')
        time.sleep(1.0)
        before = t.count("dirt")
        _, _, res = r.act("mine_block", {"pos": P(0, -60, 2)})
        time.sleep(0.5)
        stick_left = r.rcon("execute if entity @e[type=minecraft:item,tag=olddrop]").startswith("Test passed")
        moved = ((res or {}).get("evidence") or {}).get("collect_moved_blocks")
        t.check(f"mine_old_drop#{rep}", "mine_block", {"pos": P(0, -60, 2)}, res, None,
                t.block_is(0, -60, 2, "air") and t.count("dirt") == before + 1 and t.count("stick") == 0 and stick_left,
                f"dirt {before}->{t.count('dirt')} stick picked={t.count('stick')} old item still there={stick_left} moved={moved}")
    # O101a (the 45L's first night: no pickaxe in the hotbar at dawn, so the unseal failed no_tool): the only pickaxe is in the
    # main inventory; mine_block moves it into the hotbar (the slot it left empty) and digs: ok, cobblestone +1, pickaxe in the hotbar
    for rep in range(4):
        t.setup("setblock 0 -60 2 minecraft:cobblestone", f"clear {r.player} minecraft:wooden_pickaxe",
                f"item replace entity {r.player} inventory.5 with minecraft:wooden_pickaxe")
        before = t.count("cobblestone")
        _, _, res = r.act("mine_block", {"pos": P(0, -60, 2)})
        time.sleep(0.3)
        moved = ((res or {}).get("evidence") or {}).get("tool_from_inventory")
        in_hotbar = [k for k in range(9) if r.rcon(f"execute if items entity {r.player} hotbar.{k} minecraft:wooden_pickaxe").startswith("Test passed")]
        t.check(f"mine_tool_from_inventory#{rep}", "mine_block", {"pos": P(0, -60, 2)}, res, None,
                t.block_is(0, -60, 2, "air") and t.count("cobblestone") == before + 1 and bool(moved) and bool(in_hotbar),
                f"cobblestone {before}->{t.count('cobblestone')} moved={moved} pickaxe in hotbar slots {in_hotbar}")
    # O149 (Hunter: "she should automatically swap to the tool most suited for the block"): dirt, the kit's wooden shovel in the
    # hotbar and a faster stone shovel in inventory slot 20; mine_block fetches the stone one: ok, tool_used stone_shovel, moved
    for rep in range(4):
        t.setup("setblock 0 -60 2 minecraft:dirt", f"item replace entity {r.player} inventory.11 with minecraft:stone_shovel")
        _, _, res = r.act("mine_block", {"pos": P(0, -60, 2)})
        time.sleep(0.3)
        ev = (res or {}).get("evidence") or {}
        t.check(f"mine_faster_tool#{rep}", "mine_block", {"pos": P(0, -60, 2)}, res, None,
                t.block_is(0, -60, 2, "air") and ev.get("tool_used") == "minecraft:stone_shovel" and bool(ev.get("tool_from_inventory")),
                f"tool_used={ev.get('tool_used')} moved={ev.get('tool_from_inventory')}")
    # O101e by_hand (only with a registry that declares it: the dev-0160 build, O101j): cobblestone and no pickaxe anywhere; with
    # mind's params she breaks it by hand (slow, no drop): ok, the cell air, cobblestone count unchanged, evidence by_hand_used
    mb = next(s for s in r.hub.reg["skills"] if s["name"] == "mine_block")
    if "by_hand" in mb["params"]["properties"]:
        for rep in range(4):
            t.setup("setblock 0 -60 2 minecraft:cobblestone", f"clear {r.player} minecraft:wooden_pickaxe")
            before = t.count("cobblestone")
            params = {"pos": P(0, -60, 2), "expect_block": "minecraft:cobblestone", "by_hand": True}
            t0 = time.time()
            _, _, res = r.act("mine_block", params)
            took = time.time() - t0
            time.sleep(0.3)
            ev = (res or {}).get("evidence") or {}
            t.check(f"mine_by_hand#{rep}", "mine_block", params, res, None,
                    t.block_is(0, -60, 2, "air") and t.count("cobblestone") == before and bool(ev.get("by_hand_used")),
                    f"cobblestone {before}->{t.count('cobblestone')} by_hand_used={ev.get('by_hand_used')} tool={ev.get('tool_used')} took={took:.1f}s")
    # collecting the drop must not walk her into a 3-deep pit right behind the block (edge guard)
    for rep in range(3):
        t.setup("setblock 0 -60 2 minecraft:dirt", "fill -2 -63 3 2 -61 6 minecraft:air")
        _, _, res = r.act("mine_block", {"pos": P(0, -60, 2)})
        time.sleep(0.5)
        y = r.pos()[1]
        t.check(f"mine_edge_pit#{rep}", "mine_block", {"pos": P(0, -60, 2)}, res, None, t.block_is(0, -60, 2, "air") and y > -60.01, f"server y={y}")

    # ---- place_block normal: (variant, setup, params, expected block at pos)
    places = [
        ("floor", [], {"pos": P(0, -60, 2), "item": "minecraft:dirt"}, (0, -60, 2, "dirt")),
        ("on_block", ["setblock 0 -60 2 minecraft:stone"], {"pos": P(0, -59, 2), "item": "minecraft:dirt"}, (0, -59, 2, "dirt")),
        ("side_face", ["setblock 0 -60 2 minecraft:stone"], {"pos": P(0, -60, 1), "item": "minecraft:cobblestone", "face": "north"}, (0, -60, 1, "cobblestone")),
        ("cobble_diag", [], {"pos": P(1, -60, 1), "item": "minecraft:cobblestone"}, (1, -60, 1, "cobblestone")),
        ("against_chest", ["setblock -1 -60 2 minecraft:chest"], {"pos": P(-1, -60, 1), "item": "minecraft:dirt", "face": "north"}, (-1, -60, 1, "dirt")),
        # a torch on a block's side is a wall_torch (eve-builder 2026-10-20: v1 called this uncertain); the item, not the block id, decides
        ("wall_torch", ["setblock 0 -60 2 minecraft:stone", f"item replace entity {r.player} hotbar.8 with minecraft:torch 4"],
         {"pos": P(0, -60, 1), "item": "minecraft:torch", "face": "north"}, (0, -60, 1, "wall_torch")),
    ]
    for rep in range(4):
        for name, cmds, params, (x, y, z, blk) in places:
            t.setup(*cmds)
            item = params["item"].split(":")[1]
            before = t.count(item)
            _, _, res = r.act("place_block", params)
            time.sleep(0.3)
            t.check(f"place_{name}#{rep}", "place_block", params, res, None,
                    t.block_is(x, y, z, blk) and t.count(item) == before - 1, f"{item} {before}->{t.count(item)}")
    # O130 (Hunter live: "place it near you" right after crafting a table failed no_item "not a block item in the hotbar"): the
    # only crafting table is in the main inventory; place_block swaps it into the hotbar and places it: ok, the block there, -1
    for rep in range(4):
        t.setup(f"clear {r.player} minecraft:crafting_table", f"item replace entity {r.player} inventory.11 with minecraft:crafting_table")
        params = {"pos": P(0, -60, 2), "item": "minecraft:crafting_table"}
        _, _, res = r.act("place_block", params)
        time.sleep(0.3)
        moved = ((res or {}).get("evidence") or {}).get("item_from_inventory")
        t.check(f"place_from_inventory#{rep}", "place_block", params, res, None,
                t.block_is(0, -60, 2, "crafting_table") and t.count("crafting_table") == 0 and bool(moved), f"moved={moved}")
    # O124/O134 drop_item (only with a registry that declares it: 0.15.1): Hunter's "hand it over". Normal: 3 of 5 cobblestone
    # kept in the main inventory, tossed toward a block 2 ahead: ok, 2 left, the drop lying near it; then the kit refusal, nothing
    # held, and fewer than asked (partial)
    if any(s["name"] == "drop_item" for s in r.hub.reg["skills"]):
        near_target = lambda: r.rcon("execute if entity @e[type=minecraft:item,x=2.5,y=-60,z=0.5,distance=..2.5]").startswith("Test passed")
        for rep in range(4):
            t.setup(f"clear {r.player} minecraft:cobblestone", f"item replace entity {r.player} inventory.14 with minecraft:cobblestone 5")
            params = {"item": "minecraft:cobblestone", "count": 3, "toward": P(2, -61, 0)}
            _, _, res = r.act("drop_item", params)
            time.sleep(1.0)
            ev = (res or {}).get("evidence") or {}
            t.check(f"drop_item#{rep}", "drop_item", params, res, None,
                    t.count("cobblestone") == 2 and ev.get("dropped") == 3 and ev.get("faced") is True and len(ev.get("entity_ids") or []) >= 1
                    and near_target(), f"left={t.count('cobblestone')} near_target={near_target()}")
        dfails = [
            ("protected_kit", {"item": "minecraft:stone_sword"}, "protected_kit", lambda: t.count("stone_sword") == 1),
            ("no_item", {"item": "minecraft:gold_block"}, "no_item", lambda: True),
            ("partial", {"item": "minecraft:golden_apple", "count": 5}, "partial", lambda: t.count("golden_apple") == 0),
        ]
        for name, params, code, after in dfails:
            t.setup()
            _, _, res = r.act("drop_item", params)
            time.sleep(0.5)
            t.check(f"drop_fail_{name}", "drop_item", params, res, code, after())
    pfails = [
        ("occupied", ["setblock 0 -60 2 minecraft:stone"], {"pos": P(0, -60, 2), "item": "minecraft:dirt"}, "occupied", (0, -60, 2, "stone")),
        ("no_item", [], {"pos": P(0, -60, 2), "item": "minecraft:gold_block"}, "no_item", (0, -60, 2, "air")),
        ("not_a_block", [], {"pos": P(0, -60, 2), "item": "minecraft:golden_apple"}, "no_item", (0, -60, 2, "air")),
        ("no_support", [], {"pos": P(0, -57, 2), "item": "minecraft:dirt"}, "no_support", (0, -57, 2, "air")),
        ("out_of_range", [], {"pos": P(0, -60, 7), "item": "minecraft:dirt"}, "out_of_range", (0, -60, 7, "air")),
        ("obstructed", ["setblock 0 -60 2 minecraft:stone"], {"pos": P(0, -60, 3), "item": "minecraft:dirt", "face": "up"}, "obstructed", (0, -60, 3, "air")),
        # joint run 2026-10-19: a target overlapping Eve was reported zone_denied; it is `occupied`, checked before the click
        ("own_body", [], {"pos": P(0, -60, 0), "item": "minecraft:dirt"}, "occupied", (0, -60, 0, "air")),
        ("mob_in_space", ["summon minecraft:pig 0.5 -60 2.5 {NoAI:1b}"], {"pos": P(0, -60, 2), "item": "minecraft:dirt"}, "occupied", (0, -60, 2, "air")),
    ]
    for name, cmds, params, code, (x, y, z, blk) in pfails:
        t.setup(*cmds)
        _, _, res = r.act("place_block", params)
        t.check(f"place_fail_{name}", "place_block", params, res, code, t.block_is(x, y, z, blk) and t.count("dirt") == 16)

    # ---- open_container normal: kind, expected contents, slot count
    conts = [
        ("chest", '{Items:[{Slot:0b,id:"minecraft:apple",count:3},{Slot:5b,id:"minecraft:iron_ingot",count:7}]}', 27),
        ("barrel", '{Items:[{Slot:13b,id:"minecraft:oak_log",count:12}]}', 27),
        ("hopper", '{Items:[{Slot:2b,id:"minecraft:bread",count:5},{Slot:4b,id:"minecraft:coal",count:1}]}', 5),
        ("dispenser", '{Items:[{Slot:8b,id:"minecraft:arrow",count:64}]}', 9),
        ("furnace", '{Items:[{Slot:0b,id:"minecraft:raw_iron",count:2},{Slot:2b,id:"minecraft:iron_ingot",count:4}]}', 3),  # no fuel: nothing smelts
    ]
    for rep in range(4):
        for kind, nbt, nslots in conts:
            t.setup(f"setblock -1 -60 2 minecraft:{kind}{nbt}")
            want = sorted((int(s), i, int(c)) for s, i, c in re.findall(r'Slot:(\d+)b,id:"([^"]+)",count:(\d+)', nbt))
            _, _, res = r.act("open_container", {"pos": P(-1, -60, 2)})
            got = sorted((o["slot"], o["item"], o["count"]) for o in (res or {}).get("evidence", {}).get("items", []))
            ev = (res or {}).get("evidence", {})
            t.check(f"open_{kind}#{rep}", "open_container", {"pos": P(-1, -60, 2)}, res, None,
                    got == want and ev.get("slots") == nslots, f"want={want} got={got} slots={ev.get('slots')}/{nslots}")
    ofails = [
        ("not_a_container", ["setblock -1 -60 2 minecraft:stone"], {"pos": P(-1, -60, 2)}, "not_a_container"),
        ("out_of_range", ["setblock 0 -60 7 minecraft:chest"], {"pos": P(0, -60, 7)}, "out_of_range"),
        ("obstructed", ["setblock 0 -60 3 minecraft:chest", "setblock 0 -59 2 minecraft:glass", "setblock 0 -60 2 minecraft:glass"], {"pos": P(0, -60, 3)}, "obstructed"),
    ]
    for name, cmds, params, code in ofails:
        t.setup(*cmds)
        _, _, res = r.act("open_container", params)
        t.check(f"open_fail_{name}", "open_container", params, res, code, True)

    # ---- attack_entity normal: 5 mob kinds, NoAI, 2 blocks ahead
    for rep in range(4):
        for mob in ["pig", "cow", "sheep", "goat", "wolf"]:
            t.setup(f"summon minecraft:{mob} 0.5 -60 2.5 {{NoAI:1b,Tags:[\"t\"]}}")
            t.select(0)
            h0 = t.health("t")
            eid = t.entity_id(mob)
            _, _, res = r.act("attack_entity", {"entity_id": eid})
            time.sleep(0.3)
            h1 = t.health("t")
            t.check(f"attack_{mob}#{rep}", "attack_entity", {"entity_id": eid}, res, None, h1 is None or (h0 is not None and h1 < h0), f"health {h0}->{h1}")
    afails = [
        ("villager", ["summon minecraft:villager 0.5 -60 2.5 {NoAI:1b,Tags:[\"p\"]}"], "villager", "protected_entity"),
        ("named_pig", ["summon minecraft:pig 0.5 -60 2.5 {NoAI:1b,Tags:[\"p\"],CustomName:\"Bob\"}"], "pig", "protected_entity"),
        ("iron_golem", ["summon minecraft:iron_golem 0.5 -60 2.5 {NoAI:1b,Tags:[\"p\"]}"], "iron_golem", "protected_entity"),
        ("tamed_wolf", ["summon minecraft:wolf 0.5 -60 2.5 {NoAI:1b,Tags:[\"p\"],Owner:[I;1,2,3,4]}"], "wolf", "protected_entity"),
        ("sweep_villager", ["summon minecraft:pig 0.5 -60 2.5 {NoAI:1b,Tags:[\"t\"]}", "summon minecraft:villager 1.3 -60 2.5 {NoAI:1b,Tags:[\"p\"]}"], "pig", "protected_entity"),
        ("out_of_range", ["summon minecraft:pig 0.5 -60 6.5 {NoAI:1b,Tags:[\"t\"]}"], "pig", "out_of_range"),
        ("obstructed", ["summon minecraft:pig 0.5 -60 3.5 {NoAI:1b,Tags:[\"t\"]}", "fill -1 -60 2 1 -58 2 minecraft:glass"], "pig", "obstructed"),
    ]
    for name, cmds, mob, code in afails:
        t.setup(*cmds)
        t.select(0)
        tag = "p" if any("Tags:[\"p\"]" in c for c in cmds) else "t"
        h0, ht0 = t.health(tag), t.health("t")
        eid = t.entity_id(mob)
        _, _, res = r.act("attack_entity", {"entity_id": eid})
        time.sleep(0.5)
        h1, ht1 = t.health(tag), t.health("t")
        t.check(f"attack_fail_{name}", "attack_entity", {"entity_id": eid}, res, code, h1 == h0 and ht1 == ht0, f"health {h0}->{h1}, t {ht0}->{ht1}")
    # O136 owner_target (registry 0.15.2 only; Hunter: "fight the wandering trader" -> protected): the owner's named target is
    # hit despite its type; a protected villager in the sweep still stops it
    ae = next(s for s in r.hub.reg["skills"] if s["name"] == "attack_entity")
    if "owner_target" in ae["params"]["properties"]:
        for name, cmds, code in [
            ("owner_target", ["summon minecraft:wandering_trader 0.5 -60 2.5 {NoAI:1b,Tags:[\"t\"]}"], None),
            ("owner_target_sweep", ["summon minecraft:wandering_trader 0.5 -60 2.5 {NoAI:1b,Tags:[\"t\"]}",
                                    "summon minecraft:villager 1.3 -60 2.5 {NoAI:1b,Tags:[\"p\"]}"], "protected_entity"),
        ]:
            t.setup(*cmds)
            t.select(0)
            h0, hp0 = t.health("t"), t.health("p")
            params = {"entity_id": t.entity_id("wandering_trader"), "owner_target": True}
            _, _, res = r.act("attack_entity", params)
            time.sleep(0.5)
            h1, hp1 = t.health("t"), t.health("p")
            hit = h1 is None or (h0 is not None and h1 < h0)
            t.check(f"attack_{name}", "attack_entity", params, res, code, hit if code is None else (h1 == h0 and hp1 == hp0),
                    f"trader {h0}->{h1} villager {hp0}->{hp1}")
    t.setup()
    _, _, res = r.act("attack_entity", {"entity_id": 999999})
    t.check("attack_fail_entity_gone", "attack_entity", {"entity_id": 999999}, res, "entity_gone", True)

    # select_item (registry 0.15.2 only; mind-c's boat flow): the only oak boat is in inventory slot 22; select_item pulls it
    # into the hotbar and holds it: ok, mainhand oak_boat, evidence item_from_inventory
    if any(s["name"] == "select_item" for s in r.hub.reg["skills"]):
        for rep in range(4):
            t.setup(f"item replace entity {r.player} inventory.13 with minecraft:oak_boat")
            params = {"item": "minecraft:oak_boat"}
            _, _, res = r.act("select_item", params)
            ev = (res or {}).get("evidence") or {}
            t.check(f"select_item#{rep}", "select_item", params, res, None,
                    ev.get("mainhand_item") == "minecraft:oak_boat" and bool(ev.get("item_from_inventory")), f"ev={ev}")
        t.setup()
        _, _, res = r.act("select_item", {"item": "minecraft:oak_boat"})
        t.check("select_item_fail_no_item", "select_item", {"item": "minecraft:oak_boat"}, res, "no_item", True)
    # boat launch (kickoff: mind-c's boat flow): a 3x3 pool 2-4 ahead; look_at the water, then use_item with no target (vanilla
    # right-click in the air: BoatItem ray-traces the fluid she faces): ok, one boat on the pool, the boat gone from her hand
    for rep in range(4):
        t.setup("fill -1 -61 2 1 -61 4 minecraft:water", f"item replace entity {r.player} hotbar.0 with minecraft:oak_boat")
        t.select(0)
        r.act("look_at", {"pos": {"x": 0.5, "y": -60.9, "z": 3.5}})
        _, _, res = r.act("use_item", {})
        time.sleep(0.5)
        boats = r.rcon("execute if entity @e[type=minecraft:oak_boat,x=0.5,y=-61,z=3.5,distance=..3]")
        t.check(f"boat_launch#{rep}", "use_item", {}, res, None, "count" in boats.lower() and t.count("oak_boat") == 0, f"boats={boats!r}")
        r.rcon("kill @e[type=minecraft:oak_boat]")
    # O145 mount / dismount (registry with mount only; Hunter: "get in the wood boat"): a boat placed 2 ahead; mount it (ok, the
    # server has her riding it), then dismount (ok, the server has her on foot); a cow is not_rideable
    if any(s["name"] == "mount" for s in r.hub.reg["skills"]):
        riding = lambda: r.rcon(f"execute as {r.player} on vehicle if entity @s[type=minecraft:oak_boat]").startswith("Test passed")
        for rep in range(4):
            t.setup("summon minecraft:oak_boat 0.5 -60 2.5")
            params = {"entity_id": t.entity_id("oak_boat")}
            _, _, res = r.act("mount", params)
            time.sleep(0.3)
            ev = (res or {}).get("evidence") or {}
            t.check(f"mount_boat#{rep}", "mount", params, res, None, riding() and ev.get("vehicle_id") == params["entity_id"], f"ev={ev}")
            _, _, res = r.act("dismount", {})
            time.sleep(0.3)
            t.check(f"dismount_boat#{rep}", "dismount", {}, res, None, not riding(), f"pos={((res or {}).get('evidence') or {}).get('pos')}")
            r.rcon("kill @e[type=minecraft:oak_boat]")
        t.setup("summon minecraft:cow 0.5 -60 2.5 {NoAI:1b}")
        params = {"entity_id": t.entity_id("cow")}
        _, _, res = r.act("mount", params)
        t.check("mount_fail_not_rideable", "mount", params, res, "not_rideable", True)
    # ---- use_item normal: (variant, setup, hotbar slot, params, server check)
    uses = [
        ("eat_golden_apple", [], 3, {"hold_ms": 2000}, lambda b: t.count("golden_apple") == 2),
        ("hoe_grass", ["setblock 0 -61 2 minecraft:grass_block"], 2, {"target_pos": P(0, -61, 2), "face": "up"}, lambda b: t.block_is(0, -61, 2, "farmland")),
        ("shovel_path", ["setblock 0 -61 2 minecraft:grass_block"], 5, {"target_pos": P(0, -61, 2), "face": "up"}, lambda b: t.block_is(0, -61, 2, "dirt_path")),
        ("axe_strip", ["setblock 0 -60 2 minecraft:oak_log"], 6, {"target_pos": P(0, -60, 2), "face": "north"}, lambda b: t.block_is(0, -60, 2, "stripped_oak_log")),
        ("shear_sheep", ["summon minecraft:sheep 0.5 -60 2.5 {NoAI:1b,Tags:[\"t\"]}"], 7, "sheep", lambda b: nums(r.rcon("data get entity @e[tag=t,limit=1] Sheared")) == [1.0]),
    ]
    for rep in range(4):
        for name, cmds, slot, params, srv in uses:
            t.setup(*cmds)
            t.select(slot)
            if isinstance(params, str):  # entity use: the id comes from the body's own state
                params = {"entity_id": t.entity_id(params)}
            _, _, res = r.act("use_item", params)
            time.sleep(0.3)
            t.check(f"use_{name}#{rep}", "use_item", params, res, None, srv(None))
    ufails = [
        ("nothing_to_use", ["clear EveDev minecraft:stone_sword"], 0, {"hold_ms": 200}, "nothing_to_use", lambda: True),
        ("out_of_range", ["setblock 0 -61 7 minecraft:grass_block"], 2, {"target_pos": P(0, -61, 7), "face": "up"}, "out_of_range", lambda: t.block_is(0, -61, 7, "grass_block")),
        ("obstructed", ["setblock 0 -61 3 minecraft:grass_block", "setblock 0 -60 2 minecraft:stone"], 2, {"target_pos": P(0, -61, 3), "face": "up"}, "obstructed", lambda: t.block_is(0, -61, 3, "grass_block")),
        ("protected_entity", ["summon minecraft:villager 0.5 -60 2.5 {NoAI:1b,Tags:[\"p\"]}"], 4, None, "zone_denied", lambda: t.count("dirt") == 16),
    ]
    for name, cmds, slot, params, code, srv in ufails:
        t.setup(*[c.replace("EveDev", r.player) for c in cmds])
        t.select(slot)
        if params is None:
            params = {"entity_id": t.entity_id("villager")}
        _, _, res = r.act("use_item", params)
        t.check(f"use_fail_{name}", "use_item", params, res, code, srv())

    # ---- chat_say: 5 lines (server log shows them), a "/" line (rejected, nothing sent), rate limit
    log = Path(__file__).resolve().parents[1] / "run-server" / "logs" / "latest.log"  # the server's own log (attempt 8 read a stale console capture)
    for i in range(5):
        text = f"cp10 chat line {i} {int(time.time())}"
        time.sleep(2.2)
        _, _, res = r.act("chat_say", {"text": text})
        time.sleep(0.5)
        t.check(f"chat#{i}", "chat_say", {"text": text}, res, None, log.exists() and text in log.read_text(encoding="utf-8", errors="replace"))
    time.sleep(2.2)
    _, _, res = r.act("chat_say", {"text": "/give EveDev minecraft:diamond 64"})
    t.check("chat_fail_command", "chat_say", {"text": "/give …"}, res, "rejected:precondition_failed", t.count("diamond") == 0)
    # O114: a second line inside the 2 s gap waits in the body's queue and is sent once the gap allows (it used to be refused)
    time.sleep(2.2)
    r.act("chat_say", {"text": "rate limit first"})
    t0 = time.time()
    _, _, res = r.act("chat_say", {"text": "rate limit second"})
    took = time.time() - t0
    time.sleep(0.5)
    t.check("chat_rate_queued", "chat_say", {"text": "rate limit second"}, res, None,
            "rate limit second" in log.read_text(encoding="utf-8", errors="replace") and took >= 1.5, f"second line after {took:.1f} s")
    # O114: four chat.send lines at once - three wait and are sent 2 s apart, an owner reply first; the fourth is refused (queue full)
    time.sleep(2.2)
    since = len(r.hub.frames)
    for k, (txt, reply) in enumerate([("queue a", False), ("queue b", False), ("queue c", False), ("queue reply", True), ("queue e", False)]):
        r.hub.send("chat.send", {"text": f"{txt} {int(time.time())}", "reply": reply})
    time.sleep(9.0)
    text = log.read_text(encoding="utf-8", errors="replace")
    at = {w: text.rfind(w) for w in ("queue a", "queue b", "queue c", "queue reply", "queue e")}
    errs = [f["msg"]["payload"].get("code") for f in r.hub.frames[since:] if f["dir"] == "in" and f["msg"]["type"] == "error"]
    ok = at["queue a"] >= 0 and at["queue reply"] >= 0 and at["queue b"] >= 0 and at["queue c"] >= 0 and at["queue e"] < 0 \
        and at["queue a"] < at["queue reply"] < at["queue b"] and errs.count("rate_limited") == 1
    r.record("chat_queue_reply_first", "chat.send", {}, ok, f"positions={at} errors={errs} (want: a, reply, b, c sent; e refused once)")
    r.finish(CRITERIA)


HANDS_CRITERIA = """CP-12 client side, the cases that need hands (same fixture as CP-10). Stop arrives over the mind link (`control.stop`) or over
eve:control (dev /evetest sender, sense's wire format) while: mining an oak log with the axe (1.5 s break), an attack waiting for the
sword cooldown right after a first hit, eating a golden apple (hold 3 s). Per attempt, all must hold: the action ends `cancelled` (or
is refused `stopped` if the stop overtook the request); client read-back all clear within 2 ticks; body latency <= 50 ms; no effect
after the stop: the log is still there 1.5 s later, the target's Health equals its value when the stop was sent, the apple count is
unchanged and she is not eating; an action.request after the stop is refused `stopped` (no automatic resume). Then a trusted resume.
- I25 (CP-40 a14 lane B: Eve left outside after three cancels): a walk_to cancelled by the `stop` skill ends `cancelled`, the stop's
  own result comes back ok, and a go_home sent right after it is accepted within 5 s of the stop (x5)."""


def run_hands_stop(r: Run):
    from arena import evetest
    r.fixture()
    r.rcon("difficulty easy")
    t = T(r)
    for i in range(48):
        kind, via = ["mine", "attack", "use"][i % 3], "link" if (i // 3) % 2 == 0 else "server"
        if kind == "mine":
            t.setup("setblock 0 -60 2 minecraft:oak_log")
            since = len(r.hub.frames)
            aid = r.hub.request("mine_block", {"pos": {"x": 0, "y": -60, "z": 2}})
            time.sleep(0.6)
        elif kind == "attack":
            t.setup("summon minecraft:pig 0.5 -60 2.5 {NoAI:1b,Tags:[\"t\"]}")
            t.select(0)
            eid = t.entity_id("pig")
            r.act("attack_entity", {"entity_id": eid})
            since = len(r.hub.frames)
            aid = r.hub.request("attack_entity", {"entity_id": eid})  # waits ~12 ticks for the cooldown
            time.sleep(0.15)
        else:
            t.setup()
            t.select(3)
            since = len(r.hub.frames)
            aid = r.hub.request("use_item", {"hold_ms": 3000})
            time.sleep(0.6)
        h_stop = t.health("t") if kind == "attack" else None
        if via == "link":
            r.hub.send("control.stop", {"reason": f"cp12 hands {kind}", "source": "arena"})
            ack = r.hub.wait(lambda m: m["type"] == "control.ack" and m["payload"]["op"] == "stop", 2, since)
            body_ms = ack and ack["msg"]["payload"]["latency_ms"]
        else:
            body_ms = evetest(r, "stop").get("ack", {}).get("client", {}).get("latency_ms")
        res = r.hub.wait_result(aid, 3, since)
        ev = r.hub.wait(lambda m: m["type"] == "event" and m["payload"]["detail"].get("inputs_cleared") is not None, 3, since)
        cleared = ev and ev["msg"]["payload"]["detail"]
        time.sleep(1.5)
        if kind == "mine":
            no_effect = t.block_is(0, -60, 2, "oak_log")
        elif kind == "attack":
            no_effect = t.health("t") == h_stop
        else:
            no_effect = t.count("golden_apple") == 3 and cleared and cleared.get("not_using_item")
        probe = r.hub.request("look_at", {"pos": {"x": 0, "y": -59, "z": 9}})
        rej = r.hub.wait_result(probe, 3, since)
        checks = {
            "cancelled": bool(res) and (res.get("status") == "cancelled" or res.get("code") == "stopped"),
            "cleared<=2ticks": bool(cleared) and cleared["inputs_cleared"] and cleared["ticks_after_clear"] <= 2,
            "body<=50ms": body_ms is not None and body_ms <= 50,
            "no_effect_after_stop": bool(no_effect),
            "no_auto_resume": bool(rej) and rej.get("code") == "stopped",
        }
        r.record(f"hands_{kind}_{via}#{i}", "stop", {}, all(checks.values()),
                 f"action={res and (res.get('status') or res.get('code'))} body_ms={body_ms} cleared={cleared and cleared.get('ticks_after_clear')} "
                 + " ".join(k for k, v in checks.items() if not v), body_ms=body_ms, cleared=cleared, result=res)
        r.hub.send("control.resume", {"source": "arena"})
        time.sleep(0.3)
    cancel_then_home(r)
    r.finish(HANDS_CRITERIA)


def cancel_then_home(r: Run, n=5):
    """I25: the body settles a cancel at once; the next action (go_home) is accepted as soon as the stop's own result is in."""
    for i in range(n):
        r.tp(30.5, FLOOR, 30.5, 0)
        time.sleep(1.0)
        since = len(r.hub.frames)
        walk = r.hub.request("walk_to", {"pos": {"x": 30.5, "y": FLOOR, "z": -25.5}, "tolerance": 1.0})
        r.hub.wait(lambda m: m["type"] in ("action.accepted", "action.rejected") and m["payload"].get("action_id") == walk, 5, since)
        time.sleep(1.5)
        s1, t_stop = len(r.hub.frames), time.time()
        stop = r.hub.request("stop", {})
        w = r.hub.wait_result(walk, 5, s1)
        st = r.hub.wait_result(stop, 5, s1)
        s2 = len(r.hub.frames)
        home = r.hub.request("go_home", {})
        f = r.hub.wait(lambda m: m["type"] in ("action.accepted", "action.rejected") and m["payload"].get("action_id") == home, 10, s2)
        ms = f and round((f["t"] - t_stop) * 1000)
        ok = bool(w) and w.get("status") == "cancelled" and bool(st) and st.get("status") == "ok" and bool(f)             and f["msg"]["type"] == "action.accepted" and ms <= 5000
        r.record(f"cancel_then_go_home#{i}", "go_home", {}, ok, f"walk={w and w.get('status')} stop={st and st.get('status')} "
                 f"go_home={f and f['msg']['type']} {ms} ms after the stop")
        r.hub.wait_result(home, 60, s2)


if __name__ == "__main__":
    player = sys.argv[sys.argv.index("--player") + 1] if "--player" in sys.argv else "EveDev"
    hands = "cp12" in sys.argv
    run_ = Run("CP-12" if hands else "CP-10", player)
    try:
        (run_hands_stop if hands else run)(run_)
    except BaseException as e:
        run_.finish(HANDS_CRITERIA if hands else CRITERIA, f"**ABORTED** after {len(run_.rows)} attempts: {e!r}")
        raise
