r"""CP-18 on the 45L: what a Ledger rollback of Eve's logged changes really restores, measured against server truth.
Needs Ledger 1.3.25 (+ Fabric Language Kotlin) on the server, eve-sense-devtools >= 0.1.5 on the server and on Eve's
client (her actions are real client packets, so Ledger attributes them to her player), Eve online, nobody else acting
as Eve during the run; ledger_filtered.py and ask_player.py next to it. Every Ledger command and its output is recorded.
    C:\eve\mind\parts\mind\.venv\Scripts\python.exe C:\eve\ops\cp18_45l.py [--other-mode manual|driver|skip]
"Another player's subsequent edits": --other-mode manual (default: the first trusted player, i.e. Hunter, follows the
marked, glowing targets; the step stays open until the world shows it, up to 30 min), driver (local), skip (not run; CP-18 stays open).

FROZEN PROCEDURE
Fixture box B of cp15_45l.py (mine side, free zone). Before Eve: oak planks at (66..68, 55, -44), an unprotected NoAI
pig at (68.5, 55, -38.5), and the CP-10 shared chest (-26, 99, -32) whose contents are read first and written back at
the end. Eve, by real packets: places cobblestone on the floor at x 64..69, z -41 (6); breaks the planks at
(66,55,-44), (67,55,-44) (2); takes the chest's first stack; kills the pig with a sword; empties a water bucket on the
floor at (69, 54, -44). Then the other player places a block on (65,55,-41) (on top of Eve's) and replaces Eve's block
at (64,55,-41) with an oak log. Then the console detonates one TNT at (59, 55, -36) (not Eve; out of reach of her work). Then:
(1) the unfiltered `ledger rollback range:16 source:<Eve> after:Nm` must be REFUSED by eve-sense (rule O17); (2) the filtered
rollback (tools/ledger_filtered.py) around the box (radius 16) and the chest (radius 6); (3) `source:@tnt action:block-break`.
Classification (each effect gets exactly one): restored / not_restored / duplicated / overwrote_other_player / kept.
PASS = the O17 refusal; Eve's untouched placements and breaks restored exactly; the chest take NOT rolled back (no
duplication); the other player's block on top AND his replacement log kept; every other
effect explicitly classified in RESULT.txt (that is the supported-scope statement; anything not restored or duplicated
is "unsupported: use the backup/restore procedure (foundation CP-04)").
"""
import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=r"C:\eve\server-dev")
ap.add_argument("--lane", default="45L", choices=["45L", "shark"], help="which test lane this server is (O18): labels the evidence")
ap.add_argument("--ops", default=r"C:\eve\ops")
ap.add_argument("--out", default=r"C:\eve\logs")
ap.add_argument("--park", default="-15.5 99 -47.5")
ap.add_argument("--other-mode", default="manual", choices=["manual", "driver", "skip"])
ap.add_argument("--chest", default="-26 99 -32 @ -24.5 99 -34.5", help="a SHARED chest @ where Eve stands to open it")
a = ap.parse_args()
sys.path.insert(0, a.ops)
import rcon  # noqa: E402
import ledger_filtered  # noqa: E402
from ask_player import ask  # noqa: E402

ROOT = Path(a.root)
ENV = json.loads((ROOT / "config" / "envelope.json").read_text(encoding="utf-8"))
P, OTHER = ENV["owner"]["eve_account"], ENV["owner"]["trusted_players"][0]
LATEST = ROOT / "logs" / "latest.log"
OUT = Path(a.out) / time.strftime(f"cp18-{a.lane.lower()}-%Y%m%d-%H%M")
OUT.mkdir(parents=True, exist_ok=True)
LOG = (OUT / "steps.jsonl").open("a", encoding="utf-8")
BOX = "58 55 -46 70 59 -35"
EVE_SPOT = (66.5, 55, -40.5)
PLACES = [(x, 55, -41) for x in range(64, 70)]
BREAKS = [(66, 55, -44), (67, 55, -44)]
ON_TOP, REPLACED = (65, 56, -41), (64, 55, -41)
WATER_FLOOR = (64, 54, -44)                              # a clear line from Eve's spot (no plank in the way)
TNT = (59, 55, -36)                                      # far end of the box: its blast must not reach Eve's work
LEVER = (66, 55, -37)                                    # block-change: Eve pulls it
CH = tuple(int(v) for v in a.chest.split("@")[0].split())
CH_SPOT = tuple(float(v) for v in a.chest.split("@")[1].split())
results = {}


def eve_alive():
    """A dead Eve sits on the respawn screen: her position and chunks freeze and every case would fail for that reason."""
    m = re.search(r"([\d.]+)f", rcon.command(ROOT, f"data get entity {P} Health"))
    return m is not None and float(m.group(1)) > 0


if not eve_alive():
    sys.exit(f"{P} is not online and alive (dead players wait on the respawn screen): fix that first")




def R(cmd):
    out = rcon.command(ROOT, cmd)
    LOG.write(json.dumps({"ts": time.strftime("%H:%M:%S"), "cmd": cmd, "out": out}) + "\n")
    LOG.flush()
    return out


def block_is(p, b):
    return R(f"execute if block {p[0]} {p[1]} {p[2]} {b}").startswith("Test passed")


def drive(**j):
    out = R(f"evetest drive {P} " + json.dumps(j, separators=(",", ":")))
    if "sent" not in out:
        raise RuntimeError(f"driver not reachable: {out!r}")
    time.sleep(0.5)


def items(p):
    out = R(f"data get block {p[0]} {p[1]} {p[2]} Items")
    counts = {}
    for m in re.finditer(r'count:\s*(\d+)[^}]*?id:\s*"([^"]+)"|id:\s*"([^"]+)"[^}]*?count:\s*(\d+)', out):
        item, n = (m.group(2), m.group(1)) if m.group(2) else (m.group(3), m.group(4))
        counts[item] = counts.get(item, 0) + int(n)
    return counts, out


def inv_count(item):
    m = re.search(r"Found (\d+)", R(f"clear {P} {item} 0"))
    return int(m.group(1)) if m else 0


def classify(name, cls, detail):
    results[name] = {"class": cls, "detail": detail}
    print(f"{cls:<24} {name}  {detail}", flush=True)


def xyz(p):
    return " ".join(str(v) for v in p)


t0 = time.time()
log_start = LATEST.stat().st_size
chest_before, chest_raw = items(CH)
chest_orig = dict(chest_before)                         # what is written back at the end
aborted = None
try:
    if "Test passed" not in R(f"execute if block {xyz(CH)} minecraft:chest"):
        raise RuntimeError(f"no chest at {CH}")
    if not chest_before:                                # empty on the 45L: stock it for the run (emptied again at the end)
        R(f"item replace block {xyz(CH)} container.0 with minecraft:diamond 10")
        chest_before, _ = items(CH)
    R("forceload add 57 -47 71 -34")
    R("fill 57 54 -47 71 60 -34 minecraft:stone_bricks hollow")
    R(f"fill {BOX} minecraft:air")
    R("kill @e[type=!minecraft:player,x=64,y=56,z=-40,distance=..12]")
    for p in [(66, 55, -44), (67, 55, -44), (68, 55, -44)]:
        R(f"setblock {xyz(p)} minecraft:oak_planks")
    R('summon minecraft:pig 68.5 55 -38.5 {NoAI:1b,PersistenceRequired:1b,Tags:["cp18p"]}')
    R(f"setblock {xyz(LEVER)} minecraft:lever[face=floor,powered=false]")
    R(f"clear {P}")
    for it in ["minecraft:cobblestone 16", "minecraft:iron_sword", "minecraft:water_bucket"]:
        R(f"give {P} {it}")
    R(f"tp {P} {xyz(EVE_SPOT)} -90 30")
    R(f"gamemode survival {P}")
    time.sleep(2)
    t_start = time.time()

    # ---- Eve's actions (real packets: Ledger attributes them to her player) ----
    for p in PLACES:
        drive(do="place", pos=[p[0], 54, p[2]], face="up", item="minecraft:cobblestone")
    R(f"gamemode creative {P}")                          # planks break in one hit, as the driver sends one start packet
    for p in BREAKS:
        drive(do="break", pos=list(p))
    R(f"gamemode survival {P}")
    placed = [p for p in PLACES if block_is(p, "minecraft:cobblestone")]
    drive(do="open", pos=list(LEVER), face="up")       # empty hand: flips the lever (block-change)
    time.sleep(0.5)
    lever_pulled = block_is(LEVER, "minecraft:lever[powered=true]")
    broken = [p for p in BREAKS if block_is(p, "minecraft:air")]
    drive(do="select", item="minecraft:iron_sword")
    time.sleep(1.2)
    for _ in range(6):
        if "Test failed" in R("execute if entity @e[tag=cp18p]"):
            break
        drive(do="attack", near=[68.5, 55.5, -38.5], item="minecraft:iron_sword")
        time.sleep(0.9)
    pig_killed = "Test failed" in R("execute if entity @e[tag=cp18p]")
    dx, dy, dz = WATER_FLOOR[0] + 0.5 - EVE_SPOT[0], WATER_FLOOR[1] + 1 - (EVE_SPOT[1] + 1.62), WATER_FLOOR[2] + 0.5 - EVE_SPOT[2]
    R(f"tp {P} {xyz(EVE_SPOT)} {math.degrees(math.atan2(-dx, dz)):.2f} {math.degrees(-math.atan2(dy, math.hypot(dx, dz))):.2f}")
    time.sleep(0.6)
    drive(do="use", item="minecraft:water_bucket")
    water_cells = [(WATER_FLOOR[0] + dx, 55 + dy, WATER_FLOOR[2] + dz) for dx in (-1, 0, 1) for dy in (0, 1) for dz in (-1, 0, 1)]
    water_src = next((c for c in water_cells if block_is(c, "minecraft:water[level=0]")), None)
    water_placed = water_src is not None
    R(f"tp {P} {xyz(CH_SPOT)}")
    time.sleep(1.5)
    taken_item = next(iter(chest_before))
    inv_before = inv_count(taken_item)
    drive(do="open", pos=list(CH), face="up")
    time.sleep(0.6)
    drive(do="click", slot=0, input="QUICK_MOVE")
    drive(do="close")
    chest_after_take, _ = items(CH)
    inv_after_take = inv_count(taken_item)
    R(f"tp {P} {xyz(EVE_SPOT)}")
    time.sleep(1)

    # ---- the other player's later edits ----
    other_ran = False
    if a.other_mode == "driver":
        R(f"gamemode creative {OTHER}")
        R(f"tp {OTHER} 64.5 55 -38.5")
        R(f"give {OTHER} minecraft:oak_log 4")
        time.sleep(1.5)
        R(f"evetest drive {OTHER} " + json.dumps({"do": "place", "pos": list(placed[1] if len(placed) > 1 else PLACES[1]), "face": "up", "item": "minecraft:oak_log"}))
        time.sleep(0.6)
        R(f"evetest drive {OTHER} " + json.dumps({"do": "break", "pos": list(REPLACED)}))
        time.sleep(0.6)
        R(f"evetest drive {OTHER} " + json.dumps({"do": "place", "pos": [REPLACED[0], 54, REPLACED[2]], "face": "up", "item": "minecraft:oak_log"}))
        time.sleep(0.6)
    elif a.other_mode == "manual" and OTHER in R("list"):
        R(f"give {OTHER} minecraft:oak_log 4")
        ask(rcon, ROOT, OTHER, "[CP-18] Put any block ON TOP of the marked cobblestone, and REPLACE the other marked cobblestone with an oak log.",
            [(PLACES[1], "PUT A BLOCK ON TOP", "minecraft:cobblestone"), (REPLACED, "REPLACE WITH OAK LOG", "minecraft:cobblestone")],
            lambda: block_is(REPLACED, "#minecraft:logs") and not block_is(ON_TOP, "minecraft:air"),
            lambda: ", ".join(m for m, ok in [(f"no block on top of {xyz(PLACES[1])}", not block_is(ON_TOP, "minecraft:air")),
                                               (f"no oak log at {xyz(REPLACED)}", block_is(REPLACED, "#minecraft:logs"))] if not ok))
    other_ran = block_is(REPLACED, "#minecraft:logs") and not block_is(ON_TOP, "minecraft:air")


    # ---- an explosion that is not Eve's ----
    R(f"summon minecraft:tnt {TNT[0]}.5 {TNT[1]} {TNT[2]}.5 {{fuse:1}}")
    time.sleep(2)
    crater = [p for p in [(59, 54, -36), (60, 54, -36), (59, 54, -37), (60, 54, -37), (59, 55, -35)] if block_is(p, "minecraft:air")]

    # ---- rollbacks (Ledger needs a position from the console: execute positioned ... range:N) ----
    minutes = max(1, math.ceil((time.time() - t_start) / 60) + 1)
    BOXPOS = "66 56 -40"
    # 1. rule O17: an unfiltered rollback over Eve must be refused by eve-sense, and change nothing
    watch = list(PLACES) + list(BREAKS) + [ON_TOP, LEVER]

    def states():
        return {str(p): [block_is(p, b) for b in ("minecraft:cobblestone", "minecraft:air", "#minecraft:logs", "minecraft:oak_planks")] for p in watch}
    before_guard = states()                             # a human may have changed blocks earlier: compare against now
    guard_out = R(f"execute positioned {BOXPOS} run ledger rollback range:16 source:{P} after:{minutes}m")
    time.sleep(3)
    guard_kept = states() == before_guard
    # 2. the supported form: the filtered rollback (tools/ledger_filtered.py) from Ledger's own log: Eve's safe actions only,
    #    positions another player edited later are kept, around the fixture and around the chest
    time.sleep(3)                                       # Ledger writes its log from a queue
    rb_out, filtered = [], {}
    for name, center, radius in [("box", (66, 56, -40), 16), ("chest", CH, 6)]:
        todo, kept = ledger_filtered.plan(ledger_filtered.db_path(ROOT), P, center, radius, minutes)
        filtered[name] = {"rolled_back": {k: sorted(v) for k, v in todo.items()}, "kept": kept}
        rb_out += ledger_filtered.rollback(R, P, todo, minutes)
    time.sleep(3)
    (OUT / "filtered-rollback.json").write_text(json.dumps(filtered, default=str, indent=1), encoding="utf-8")
    crater_after_eve = [p for p in crater if block_is(p, "minecraft:air")]
    # 3. an explosion by others is rolled back by its own source (Ledger names TNT "@tnt"), block changes only
    tnt_out = R(f"execute positioned {xyz(TNT)} run ledger rollback range:8 source:@tnt action:block-break after:{minutes}m")
    time.sleep(3)
    with LATEST.open("rb") as f:
        f.seek(log_start)
        ledger_lines = [l for l in f.read().decode("utf-8", "replace").splitlines() if "ledger" in l.lower() or "O17" in l]
    (OUT / "ledger-log-lines.txt").write_text("\n".join(ledger_lines) + "\nRCON guard: " + guard_out + "\nRCON safe: " + " | ".join(rb_out)
                                              + "\nRCON @tnt: " + tnt_out + "\n", encoding="utf-8")
    classify("O17 guard: an unfiltered rollback over Eve", "refused" if "O17" in guard_out and guard_kept else "NOT_REFUSED",
             {"reply": guard_out[:160], "nothing_changed": guard_kept})

    # ---- server truth after the rollback ----
    untouched = [p for p in placed if p != REPLACED]
    # judged as "her cobblestone is gone" (the rollback of her water lets water flow into the emptied cells)
    classify("Eve's placements (not touched by anyone after)", "restored" if all(not block_is(p, "minecraft:cobblestone") for p in untouched) else "not_restored",
             {str(p): not block_is(p, "minecraft:cobblestone") for p in untouched})
    classify("Eve's breaks", "restored" if all(block_is(p, "minecraft:oak_planks") for p in broken) else "not_restored",
             {str(p): block_is(p, "minecraft:oak_planks") for p in broken})
    if other_ran:
        classify("other player's block placed on top of Eve's", "overwrote_other_player" if block_is(ON_TOP, "minecraft:air") else "kept", "")
        classify("Eve's block the other player replaced (same position, later edit)",
                 "kept" if block_is(REPLACED, "#minecraft:logs") else "overwrote_other_player",
                 {"kept_by_filter": filtered["box"]["kept"]})
    chest_now, _ = items(CH)
    inv_now = inv_count(taken_item)
    took = chest_before.get(taken_item, 0) - chest_after_take.get(taken_item, 0)
    back = chest_now.get(taken_item, 0) - chest_after_take.get(taken_item, 0)
    still = inv_now - inv_before
    classify("Eve's chest take (container: excluded from the safe rollback)",
             "excluded" if back == 0 else "duplicated" if still > 0 else "restored" if back == took else "not_restored",
             {"taken": took, "returned_to_chest": back, "still_in_eve_inventory": still})
    pig_back = "Test passed" in R("execute if entity @e[type=minecraft:pig,x=68,y=55,z=-38,distance=..4]")
    classify("Eve's kill of an unprotected pig", "restored" if pig_back else "not_restored", {"killed": pig_killed, "pig_present_after": pig_back})
    source_left = water_placed and block_is(water_src, "minecraft:water[level=0]")
    water_now = R(f"fill {BOX} minecraft:air replace minecraft:water")
    classify("Eve's water bucket (the source block)", "not_restored" if source_left else "restored" if water_placed else "kept",
             {"placed": water_placed, "source": water_src, "source_left": source_left, "flowing_cleanup": water_now})
    classify("Eve's lever pull (block-change)", "restored" if lever_pulled and block_is(LEVER, "minecraft:lever[powered=false]") else
             "not_restored" if lever_pulled else "kept", {"pulled": lever_pulled})
    classify("console TNT crater under Eve's rollbacks", "kept" if crater and len(crater_after_eve) == len(crater) else "touched",
             {"crater": crater, "still_air_after_eve_rollbacks": crater_after_eve})
    classify("console TNT crater rolled back by its own source (@tnt, block-break)",
             "restored" if crater and not any(block_is(p, "minecraft:air") for p in crater) else "not_restored", {"reply": tnt_out[:160]})
except Exception as e:  # noqa: BLE001 - reported either way
    aborted = repr(e)
    print(f"ABORTED: {aborted}", flush=True)
finally:
    # write the CP-10 chest back exactly as found
    R(f"data merge block {xyz(CH)} {{Items:[]}}")
    for m in re.finditer(r"\{[^{}]*?Slot:\s*(\d+)b[^{}]*?\}", chest_raw):
        s = m.group(0)
        idm, cm = re.search(r'id:\s*"([^"]+)"', s), re.search(r"count:\s*(\d+)", s)
        if idm and cm:
            R(f"item replace block {xyz(CH)} container.{m.group(1)} with {idm.group(1)} {cm.group(1)}")
    for c in ["kill @e[type=!minecraft:player,x=64,y=56,z=-40,distance=..12]", f"fill {BOX} minecraft:air", "forceload remove 57 -47 71 -34",
              f"clear {P}", f"gamemode survival {P}", f"tp {P} {a.park}"]:
        R(c)
    if a.other_mode == "driver":
        R(f"gamemode survival {OTHER}")
chest_final, _ = items(CH)
own_ok = results.get("Eve's placements (not touched by anyone after)", {}).get("class") == "restored" and \
    results.get("Eve's breaks", {}).get("class") == "restored" and \
    results.get("O17 guard: an unfiltered rollback over Eve", {}).get("class") == "refused" and \
    results.get("Eve's chest take (container: excluded from the safe rollback)", {}).get("class") == "excluded"
top_ok = results.get("other player's block placed on top of Eve's", {}).get("class") == "kept" and     results.get("Eve's block the other player replaced (same position, later edit)", {}).get("class") == "kept"
verdict = "FAIL" if aborted or not own_ok else "PASS" if top_ok else "INCOMPLETE (other player's edits not run)" if not other_ran else "FAIL"
lines = [f"CP-18 on the {a.lane} (Ledger rollback scope) {verdict}  {time.strftime('%Y-%m-%d %H:%M')}  world={ENV['world_id']}  eve={P}",
         *(f"  {v['class']:<24} {k}  {json.dumps(v['detail'], default=str)}" for k, v in results.items()),
         "supported scope (rule O17): the filtered rollback (tools/ledger_filtered.py: Eve's block-place/block-break/block-change/"
         "entity-kill from Ledger's own log, positions another player edited later kept); explosions by their own "
         "source; containers are never rolled back (an unfiltered rollback over Eve is refused by eve-sense); anything 'not_restored', "
         "'excluded' or 'overwrote_other_player' goes through the backup/restore procedure (foundation CP-04).",
         f"CP-10 chest written back as found: {chest_final == chest_orig} ({chest_final})", *([f"aborted: {aborted}"] if aborted else []),
         f"elapsed {time.time() - t0:.0f}s"]
(OUT / "RESULT.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines), flush=True)
sys.exit(0 if verdict == "PASS" else 1)
