"""eve-skills fixtures on lane B (eve-watch's Shark stack: C:\\eve-shark, real mind on 8955, real eve-sense, dev account EveDev).
Every action goes through the mind's /cli (`eve do`, the same path as an owner command); the judge is the server (RCON
`data get`), never the skill's own evidence. Every trial is counted and written down.

    py -3.12 parts/skills/tools/laneb_skills.py cp37|cp36 [--n 2] [--only case,case]

Fixture: a floating pad at y=150 inside lane B's `home` zone (x -44..-28, z -76..-60), stations in a row at z=-72:
chests A/B/C (tagged shared in lane B's envelope), P (untagged = private), furnace F (shared), crafting table T. Eve is
teleported 2 blocks south of the station a case uses (fixture setup may use commands; the skill itself uses only player
actions). Her inventory is saved first and given back at the end; she is re-parked (O15). Results: evidence/CP-NN/
<stamp>-shark-attempt-N/ (gitignored) and parts/skills/evidence-summaries/ (committed). Development evidence, not
qualification.
"""
import asyncio, json, re, subprocess, sys, threading, time
from datetime import datetime
from pathlib import Path

# (root, token file, mind port, player, client task, pad offset, mine-room offset). The offsets move every fixture coordinate
# from its lane-B place. The 45L has no lane-only zones: both fixtures go into reflex_arena (x/z -26..26, y 190..215; break
# free, containers shared), clear of home's overlap (z <= -16), and since I26d nothing at or below y 210: body's clear box
# and combat's arena (x -22..24, y 204..210, z -14..16) end there.
LANES = {
    "B": (Path(r"C:\eve-shark"), "secrets/eve-link-token.txt", 8955, "EveDev", "Eve Shark Client", (0, 0, 0), (0, 0, 0)),
    "C": (Path(r"C:\eve-shark-c"), "secrets/eve-link-token.txt", 8956, "EveDev2", "Eve Shark Client C", (0, 0, 0), (0, 0, 0)),
    # I26d: pad x -24..-8, y 212..218 (floor 212; the CP-38 water channel's floor at 211), stash (-26, 212..213, 3..4): the
    # lift is +5, not +4, so that water floor stays above y 210. Mine room x -24..-8, z -15..1, floor 213 (zone top 215).
    "45L": (Path(r"C:\eve"), "mind/config/eve-link-token.txt", 8951, "FED_Chairman", None, (20, 62, 78), (-104, 155, 41)),
}
# O104j: a lane from a config file, as body's laneb takes it (lane F on the 45L: tools/lanes/F.json, run there as eveuser). Keys:
# label, root, token_file (under root), cli_port, player, client_task, pad_offset, mine_offset, evidence_root, tag, gate (argv
# of the lane's fire gate, run before a window takes the lock). RCON needs nothing more: rcon.py reads <root>\server-dev's
# server.properties. Never live Eve's tree (C:\eve) or her account.
LC = json.loads(Path(sys.argv[sys.argv.index("--lane-config") + 1]).read_text(encoding="utf-8-sig")) if "--lane-config" in sys.argv else {}
if LC:
    LANES[LC["label"].upper()] = (Path(LC["root"]), LC["token_file"], int(LC["cli_port"]), LC["player"], LC.get("client_task"),
                                  tuple(LC.get("pad_offset") or (0, 0, 0)), tuple(LC.get("mine_offset") or (0, 0, 0)))
    if Path(r"C:\eve") in (Path(LC["root"]), *Path(LC["root"]).parents) or LC["player"] == "FED_Chairman":
        sys.exit(f"O104: a configured lane must never be live Eve's tree or account ({LC['root']}, {LC['player']}); nothing done")
    if "--lane" not in sys.argv:
        sys.argv += ["--lane", LC["label"]]
LANE_ID = sys.argv[sys.argv.index("--lane") + 1].upper() if "--lane" in sys.argv else "B"
LANE, TOKEN_FILE, CLI_PORT, P, CLIENT_TASK, (DX, DY, DZ), (MDX, MDY, MDZ) = LANES[LANE_ID]
TAG = LC.get("tag", f"lane{LANE_ID.lower()}") if LC else "shark"  # evidence folder tag: shark (lanes B..E) or the config lane's
if "--geom" in sys.argv:  # another lane's fixture coordinates on this lane's server (--fixture-check of the 45L boxes)
    (DX, DY, DZ), (MDX, MDY, MDZ) = LANES[sys.argv[sys.argv.index("--geom") + 1].upper()][5:7]
QUAL = LANE_ID == "45L"
LABEL = "45L" if QUAL else f"{TAG}-skills"  # evidence dirs: <stamp>-<LABEL>-attempt-N
SERVER = LANE / "server-dev"
sys.path.insert(0, str(LANE / "ops"))
import rcon  # noqa: E402  foundation's RCON client, lane-B copy
from websockets.asyncio.client import connect  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
EVIDENCE = Path(LC["evidence_root"]) if LC.get("evidence_root") else REPO / "evidence"  # lane F keeps its evidence on the 45L
TOKEN = (LANE / TOKEN_FILE).read_text().strip() if (LANE / TOKEN_FILE).exists() else ""  # missing: every cli call fails cleanly
CLI = f"ws://127.0.0.1:{CLI_PORT}/cli"
Y = 151 + DY
A, B, C, PRIV, F, T = ((x + DX, Y, -72 + DZ) for x in (-40, -38, -36, -34, -32, -30))
PAD = (-44 + DX, 150 + DY, -76 + DZ, -28 + DX, 156 + DY, -60 + DZ)  # x0 y0 z0 x1 y1 z1 (floor y0, air up to y1)


def r(cmd):
    return rcon.command(SERVER, cmd)


def tp(x, y, z, yaw=0, pitch=0):
    """O39: every player teleport goes through the lane's shared safe_tp (solid floor, air at feet and head, inside an
    envelope zone; otherwise SafeTpRefused and she is not moved). A lane without the helper refuses to run."""
    try:
        from safe_tp import safe_tp  # LANE/ops is on sys.path
    except ImportError:
        sys.exit(f"O39: {LANE / 'ops' / 'safe_tp.py'} is missing; no raw teleports - nothing was run")
    safe_tp(SERVER, P, x, y, z, yaw, pitch)


async def _cli(cmd, args, timeout):
    async with connect(CLI, open_timeout=3, max_size=4 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"token": TOKEN, "cmd": cmd, "args": args}))
        return json.loads(await asyncio.wait_for(ws.recv(), timeout))


def eve(cmd, args=None, timeout=60):
    try:
        return asyncio.run(_cli(cmd, args or {}, timeout))
    except Exception as e:  # mind restarting: recorded as the trial's result, never a crash
        return {"ok": False, "error": "cli_exception", "detail": f"{type(e).__name__}: {e}"}


# ---- server truth ----
ITEM = re.compile(r'\{([^{}]*?)\}')


def items(snbt, main_only=False):
    """{id: count} from `data get` output; main_only keeps inventory slots 0..35 (not armour/offhand)."""
    out = {}
    for body in ITEM.findall(snbt):
        i, c, s = re.search(r'id: "([^"]+)"', body), re.search(r'count: (\d+)', body), re.search(r'Slot: (-?\d+)b', body)
        if not i:
            continue
        if main_only and s and not 0 <= int(s.group(1)) <= 35:
            continue
        out[i.group(1)] = out.get(i.group(1), 0) + int(c.group(1) if c else 1)
    return out


def inv():
    return items(r(f"data get entity {P} Inventory"), main_only=True)


def box(pos):
    x, y, z = pos
    return items(r(f"data get block {x} {y} {z} Items"))  # an empty or tag-less container parses as {}


def fill_box(pos, stacks):
    """stacks: [(item, count), ...] into slots 0.. ; [] empties it."""
    x, y, z = pos
    if pos == F:  # a fresh furnace: no leftover burn time from an earlier (cancelled) smelt
        r(f"setblock {x} {y} {z} minecraft:air")
        r(f"setblock {x} {y} {z} minecraft:furnace[facing=south]")
    nbt = ",".join(f'{{Slot:{i}b,id:"{it}",count:{n}}}' for i, (it, n) in enumerate(stacks))
    r(f"data merge block {x} {y} {z} {{Items:[{nbt}]}}")


def give(stacks):
    r(f"clear {P}")
    for it, n in stacks:
        while n > 0:
            k = min(n, 64)
            r(f"give {P} {it} {k}")
            n -= k
    time.sleep(0.3)


def stand(pos):
    x, _, z = pos
    tp(x + 0.5, Y, z + 2.5, 180, 25)
    time.sleep(1.0)  # the client's position update and chunk view


# Full inventory stash (as eve-combat's harness): every slot copied exactly (enchantments, names, damage) into two barrels
# beside the pad and copied back at the end. A crashed run leaves her items there; the next run refuses to start until
# `--restore` gives them back.
SLOTS = [f"hotbar.{i}" for i in range(9)] + [f"inventory.{i}" for i in range(27)] + \
        ["armor.head", "armor.chest", "armor.legs", "armor.feet", "weapon.offhand"]
STASH = [(PAD[0] - 2, PAD[1] + 1, PAD[2] + 1), (PAD[0] - 2, PAD[1] + 1, PAD[2] + 2)]  # outside the pad's rim, 2 x 27 >= 41


def _stash_slot(i):
    x, y, z = STASH[i // 27]
    return f"{x} {y} {z} container.{i % 27}"


def stash_items():
    n = 0
    for x, y, z in STASH:
        out = r(f"data get block {x} {y} {z} Items")
        n += len(re.findall(r'id: "', out))
    return n


def stash_save():
    if stash_items():
        sys.exit("the inventory stash still holds a previous run's items: run with --restore first (nothing was changed)")
    r(f"forceload add {STASH[0][0]} {STASH[0][2]} {STASH[1][0]} {STASH[1][2]}")
    for x, y, z in STASH:
        r(f"setblock {x} {y - 1} {z} minecraft:smooth_stone")
        r(f"setblock {x} {y} {z} minecraft:barrel")
    for i, sl in enumerate(SLOTS):
        r(f"item replace block {_stash_slot(i)} from entity {P} {sl}")


def stash_restore():
    """Every slot back exactly (an empty stash slot leaves hers empty), then the barrels go."""
    r(f"forceload add {STASH[0][0]} {STASH[0][2]} {STASH[1][0]} {STASH[1][2]}")
    r(f"clear {P}")
    for i, sl in enumerate(SLOTS):
        r(f"item replace entity {P} {sl} from block {_stash_slot(i)}")
    for x, y, z in STASH:
        r(f"data merge block {x} {y} {z} {{Items:[]}}")
        r(f"setblock {x} {y} {z} minecraft:air")
        r(f"setblock {x} {y - 1} {z} minecraft:air")


def build_pad():
    x0, y0, z0, x1, y1, z1 = PAD
    r(f"forceload add {x0} {z0} {x1} {z1}")
    r(f"fill {x0} {y0} {z0} {x1} {y0} {z1} minecraft:smooth_stone")
    r(f"fill {x0} {y0 + 1} {z0} {x1} {y1} {z1} minecraft:air")
    for a in (f"{x0} {y0 + 1} {z0} {x1} {y0 + 2} {z0}", f"{x0} {y0 + 1} {z1} {x1} {y0 + 2} {z1}",
              f"{x0} {y0 + 1} {z0} {x0} {y0 + 2} {z1}", f"{x1} {y0 + 1} {z0} {x1} {y0 + 2} {z1}"):
        r(f"fill {a} minecraft:glass")
    for pos, block in ((A, "chest[facing=south]"), (B, "chest[facing=south]"), (C, "chest[facing=south]"),
                       (PRIV, "chest[facing=south]"), (F, "furnace[facing=south]"), (T, "crafting_table")):
        r(f"setblock {pos[0]} {pos[1]} {pos[2]} minecraft:{block}")
    for pos in (A, B, C, PRIV, F):
        fill_box(pos, [])


def do(skill, params, wait):
    t = time.time()
    res = eve("do", {"skill": skill, "params": params, "wait": wait}, wait + 30)
    res["_elapsed_s"] = round(time.time() - t, 2)
    return res


def pos(p):
    return {"x": p[0], "y": p[1], "z": p[2]}


# ---- cases: setup(k) -> (skill, params, wait, judge(res, inv0, inv1, boxes0, boxes1) -> (ok, detail)) ----
CO, ST, I, RAW, COAL, LOG, PL, STICK, BREAD, PICK = ("minecraft:" + n for n in (
    "cobblestone", "stone", "iron_ingot", "raw_iron", "coal", "oak_log", "oak_planks", "stick", "bread", "iron_pickaxe"))


def expect(res, status, code=None):
    got = (res.get("status"), res.get("code") or res.get("error"))
    if res.get("status") == "refused" or res.get("ok") is False:
        got = ("refused", res.get("code") or res.get("error"))
    want_ok = got[0] == status and (code is None or got[1] == code)
    return want_ok, f"result {got[0]}/{got[1]} (want {status}/{code})"


def moved(inv0, inv1, item):
    return inv1.get(item, 0) - inv0.get(item, 0)


def judged(checks):
    bad = [d for ok, d in checks if not ok]
    return not bad, "; ".join(d for _, d in checks) if not bad else "FAIL: " + "; ".join(bad)


def w_partial(k):
    n = 5 + k
    fill_box(A, [(CO, 64)]); give([(BREAD, 16)]); stand(A)
    return "withdraw", {"pos": pos(A), "item": CO, "count": n}, 30, lambda res, i0, i1, b0, b1: judged([
        expect(res, "ok"), (moved(i0, i1, CO) == n, f"inv +{moved(i0, i1, CO)} (want {n})"),
        (b1[A].get(CO, 0) == 64 - n, f"chest {b1[A].get(CO, 0)} (want {64 - n})")])


def w_multi(k):
    n = 40 + k
    fill_box(A, [(CO, 5), (CO, 30), (CO, 64)]); give([(BREAD, 16)]); stand(A)
    return "withdraw", {"pos": pos(A), "item": CO, "count": n}, 30, lambda res, i0, i1, b0, b1: judged([
        expect(res, "ok"), (moved(i0, i1, CO) == n, f"inv +{moved(i0, i1, CO)} (want {n})"),
        (b1[A].get(CO, 0) == 99 - n, f"chest {b1[A].get(CO, 0)} (want {99 - n})")])


def w_short(k):
    have, n = 12 + k, 20 + k
    fill_box(A, [(CO, have)]); give([(BREAD, 16)]); stand(A)
    return "withdraw", {"pos": pos(A), "item": CO, "count": n}, 30, lambda res, i0, i1, b0, b1: judged([
        expect(res, "failed", "insufficient"), (moved(i0, i1, CO) == have, f"inv +{moved(i0, i1, CO)} (want {have})"),
        (b1[A].get(CO, 0) == 0, f"chest {b1[A].get(CO, 0)} (want 0)"),
        ((res.get("evidence") or {}).get("short") == n - have, f"evidence short {(res.get('evidence') or {}).get('short')} (want {n - have})")])


def w_private(k):
    fill_box(PRIV, [(CO, 64)]); give([(BREAD, 16)]); stand(PRIV)
    return "withdraw", {"pos": pos(PRIV), "item": CO, "count": 3 + k}, 30, lambda res, i0, i1, b0, b1: judged([
        (res.get("status") in ("refused", "failed") and (res.get("code") or res.get("error")) in ("zone_denied", "private_container"),
         f"result {res.get('status')}/{res.get('code') or res.get('error')} (want refused or failed, zone_denied/private_container)"),
        (moved(i0, i1, CO) == 0, f"inv +{moved(i0, i1, CO)} (want 0)"), (b1[PRIV].get(CO, 0) == 64, f"chest {b1[PRIV].get(CO, 0)} (want 64)")])


def w_invfull(k):
    fill_box(A, [(CO, 64)]); give([("minecraft:dirt", 36 * 64)]); stand(A)
    return "withdraw", {"pos": pos(A), "item": CO, "count": 10 + k}, 30, lambda res, i0, i1, b0, b1: judged([
        expect(res, "failed", "inventory_full"), (moved(i0, i1, CO) == 0, f"inv +{moved(i0, i1, CO)} (want 0)"),
        (b1[A].get(CO, 0) == 64, f"chest {b1[A].get(CO, 0)} (want 64)")])


def d_basic(k):
    n = 7 + k
    fill_box(B, []); give([(CO, 64), (BREAD, 16)]); stand(B)
    return "deposit", {"pos": pos(B), "item": CO, "count": n}, 30, lambda res, i0, i1, b0, b1: judged([
        expect(res, "ok"), (moved(i0, i1, CO) == -n, f"inv {moved(i0, i1, CO)} (want -{n})"),
        (b1[B].get(CO, 0) == n, f"chest {b1[B].get(CO, 0)} (want {n})")])


def d_topup(k):
    n = 10 + k
    fill_box(B, [(CO, 60)]); give([(CO, 64), (BREAD, 16)]); stand(B)
    return "deposit", {"pos": pos(B), "item": CO, "count": n}, 30, lambda res, i0, i1, b0, b1: judged([
        expect(res, "ok"), (b1[B].get(CO, 0) == 60 + n, f"chest {b1[B].get(CO, 0)} (want {60 + n})"),
        (moved(i0, i1, CO) == -n, f"inv {moved(i0, i1, CO)} (want -{n})")])


def d_full(k):
    fill_box(C, [(ST, 64)] * 27); give([(CO, 64), (BREAD, 16)]); stand(C)
    return "deposit", {"pos": pos(C), "item": CO, "count": 4 + k}, 30, lambda res, i0, i1, b0, b1: judged([
        expect(res, "failed", "container_full"), (moved(i0, i1, CO) == 0, f"inv {moved(i0, i1, CO)} (want 0)"),
        (b1[C] == {ST: 27 * 64}, f"chest {b1[C]} (want stone only)")])


def d_tool(k):
    fill_box(B, []); give([(PICK, 1 + k), (BREAD, 16)]); stand(B)
    return "deposit", {"pos": pos(B), "item": PICK, "count": 1 + k}, 30, lambda res, i0, i1, b0, b1: judged([
        expect(res, "failed", "protected_item"), (moved(i0, i1, PICK) == 0, f"inv {moved(i0, i1, PICK)} (want 0)"), (b1[B] == {}, f"chest {b1[B]}")])


def d_food(k):
    fill_box(B, []); give([(BREAD, 10 + k)]); stand(B)
    return "deposit", {"pos": pos(B), "item": BREAD, "count": 3 + k}, 30, lambda res, i0, i1, b0, b1: judged([
        expect(res, "failed", "protected_item"), (moved(i0, i1, BREAD) == 0, f"inv {moved(i0, i1, BREAD)} (want 0)"), (b1[B] == {}, f"chest {b1[B]}")])


def c_planks(k):
    logs = 1 + k
    give([(LOG, logs), (BREAD, 16)]); stand(T)
    return "craft", {"item": PL, "count": 4 * logs}, 30, lambda res, i0, i1, b0, b1: judged([
        expect(res, "ok"), (moved(i0, i1, PL) == 4 * logs, f"planks +{moved(i0, i1, PL)} (want {4 * logs})"),
        (i1.get(LOG, 0) == 0, f"logs left {i1.get(LOG, 0)} (want 0)")])


def c_sticks(k):
    n = 1 + k
    give([(PL, 2 * n), (BREAD, 16)]); stand(T)
    return "craft", {"item": STICK, "count": 4 * n}, 30, lambda res, i0, i1, b0, b1: judged([
        expect(res, "ok"), (moved(i0, i1, STICK) == 4 * n, f"sticks +{moved(i0, i1, STICK)} (want {4 * n})"), (i1.get(PL, 0) == 0, "planks used up")])


def c_pickaxe(k):
    give([(I, 3 + k), (STICK, 2), (BREAD, 16)]); stand(T)
    return "craft", {"item": PICK, "count": 1, "pos": pos(T)}, 40, lambda res, i0, i1, b0, b1: judged([
        expect(res, "ok"), (i1.get(PICK, 0) == 1, f"pickaxes {i1.get(PICK, 0)} (want 1)"),
        (i1.get(I, 0) == k, f"ingots left {i1.get(I, 0)} (want {k})"), (i1.get(STICK, 0) == 0, "sticks used up")])


def c_missing(k):
    give([(I, 3), (BREAD, 16)]); stand(T)
    return "craft", {"item": "minecraft:iron_helmet", "count": 1 + k, "pos": pos(T)}, 40, lambda res, i0, i1, b0, b1: judged([
        expect(res, "failed", "missing_ingredients"), (i1 == i0, f"inventory unchanged ({Plans_diff(i0, i1)})")])


def c_needs_table(k):
    give([(I, 3), (STICK, 2), (BREAD, 16)]); stand(T)
    return "craft", {"item": PICK, "count": 1 + k}, 30, lambda res, i0, i1, b0, b1: judged([
        expect(res, "failed", "no_workstation"), (i1 == i0, f"inventory unchanged ({Plans_diff(i0, i1)})")])


def c_norecipe(k):
    give([(BREAD, 16)]); stand(T)
    return "craft", {"item": "minecraft:bedrock", "count": 1 + k}, 30, lambda res, i0, i1, b0, b1: judged([
        expect(res, "failed", "no_recipe"), (i1 == i0, "inventory unchanged")])


def s_iron(k):
    n = 1 + k % 3
    fill_box(F, []); give([(RAW, n), (COAL, 2), (BREAD, 16)]); stand(F)
    return "smelt", {"pos": pos(F), "item": I, "count": n}, 20 + 12 * n, lambda res, i0, i1, b0, b1: judged([
        expect(res, "ok"), (moved(i0, i1, I) == n, f"ingots +{moved(i0, i1, I)} (want {n})"), (i1.get(RAW, 0) == 0, "raw iron used"),
        (i1.get(COAL, 0) == 1, f"coal left {i1.get(COAL, 0)} (want 1: one coal smelts 8)"), (b1[F] == {}, f"furnace left {b1[F]} (want empty)")])


def s_busy(k):
    fill_box(F, [(CO, 1 + k)]); give([(RAW, 1), (COAL, 1), (BREAD, 16)]); stand(F)
    return "smelt", {"pos": pos(F), "item": I, "count": 10 + k % 50}, 30, lambda res, i0, i1, b0, b1: judged([  # own counts: a designed failure must not share params with s_iron (retry_limit)
        expect(res, "failed", "workstation_busy"), (b1[F] == {CO: 1 + k}, f"furnace {b1[F]} untouched"), (i1 == i0, "inventory unchanged")])


def s_nofuel(k):
    fill_box(F, []); give([(RAW, 1 + k), (BREAD, 16)]); stand(F)
    return "smelt", {"pos": pos(F), "item": I, "count": 1 + k}, 30, lambda res, i0, i1, b0, b1: judged([
        expect(res, "failed", "no_fuel"), (b1[F] == {}, f"furnace {b1[F]} (want empty)"), (i1 == i0, "inventory unchanged")])


def invalid(res):
    """O46: the trial never tested the skill - the mind refused the command (no action ran), the CLI failed, or the server
    refused for an environment limit (limit:..., e.g. the rolling break cap). Designed refusals are judged PASS before this."""
    if res.get("ok") is False or res.get("status") == "refused":
        return f"not run: {res.get('code') or res.get('error')}"
    if not res.get("action_id"):
        return "no action id"
    if "limit:" in (res.get("note") or ""):
        return "server limit: " + res["note"].split("limit:")[1].split()[0]
    return None


def later(sec, fn):
    def run():
        time.sleep(sec)
        fn()
    return run


def w_compete(k):
    """Another player empties part of the chest while Eve works through it (one click per tick, 10 stacks of 16)."""
    want = 140 + k % 10
    fill_box(A, [(CO, 16)] * 10); give([(BREAD, 16)]); stand(A)
    gone = [0]

    def steal():  # slots 8 and 9 hold 16 each; whatever is still there when the other player arrives is taken
        b = box(A).get(CO, 0)
        r(f"item replace block {A[0]} {A[1]} {A[2]} container.8 with minecraft:air")
        r(f"item replace block {A[0]} {A[1]} {A[2]} container.9 with minecraft:air")
        gone[0] = b - box(A).get(CO, 0)

    def judge(res, i0, i1, b0, b1):
        got, left, ev = moved(i0, i1, CO), b1[A].get(CO, 0), res.get("evidence") or {}
        honest = (res.get("status") == "ok") == (got >= want) and (
            got >= want or (res.get("code") == "insufficient" and ev.get("short") == want - got))
        return judged([(got + left + gone[0] == 160, f"conservation: Eve +{got}, chest {left}, other player took {gone[0]} (= 160)"),
                       (got <= want, f"never more than asked ({got} <= {want})"),
                       (honest, f"result {res.get('status')}/{res.get('code')} matches what arrived (short {ev.get('short')})")])
    return "withdraw", {"pos": pos(A), "item": CO, "count": want}, 30, judge, later(0.25, steal)


ORES = [("minecraft:raw_iron", "minecraft:iron_ingot"), ("minecraft:raw_gold", "minecraft:gold_ingot"),
        ("minecraft:raw_copper", "minecraft:copper_ingot")]


def ore(k, sizes):
    """(raw, ingot, n) rotating ore type and size: cases that fail by design need params the mind has not seen fail 3 times
    in the last 10 min (retry_limit)."""
    raw, ing = ORES[k % 3]
    return raw, ing, sizes[(k // 3) % len(sizes)]


def s_out_full(k):
    raw, ing, n = ore(k, [2, 3, 4, 5, 6])
    fill_box(F, [])
    x, y, z = F
    r(f"item replace block {x} {y} {z} container.2 with {ing} {50 + k % 14}")
    give([(raw, n), (COAL, 1), (BREAD, 16)]); stand(F)
    return "smelt", {"pos": pos(F), "item": ing, "count": n}, 30, lambda res, i0, i1, b0, b1: judged([
        expect(res, "failed", "workstation_busy"), (b1[F] == {ing: 50 + k % 14}, f"furnace {b1[F]} untouched"), (i1 == i0, "inventory unchanged")])


def s_fuel_short(k):
    """One plank burns 300 ticks = 1.5 items: the second item never finishes; she must take back output and input."""
    raw, ing, n = ore(k, [3, 4, 5])
    fill_box(F, []); give([(raw, n), (PL, 1), (BREAD, 16)]); stand(F)
    return "smelt", {"pos": pos(F), "item": ing, "count": n, "fuel": PL}, 60, lambda res, i0, i1, b0, b1: judged([
        expect(res, "failed", "no_fuel"), (moved(i0, i1, ing) == 1, f"{ing} +{moved(i0, i1, ing)} (want 1)"),
        (i1.get(raw, 0) == n - 1, f"{raw} back {i1.get(raw, 0)} (want {n - 1})"), (b1[F] == {}, f"furnace {b1[F]} (want empty)"),
        ((res.get("evidence") or {}).get("smelted") == 1, f"evidence smelted {(res.get('evidence') or {}).get('smelted')}")])


def s_taken(k):
    """Another player takes the ingot(s) from the output slot mid-smelt: Eve reports what she got and takes nothing else."""
    raw, ing, n = ore(k, [3, 4, 5])
    fill_box(F, []); give([(raw, n), (COAL, 1), (BREAD, 16)]); stand(F)
    x, y, z = F
    took = [0]

    def take():
        took[0] = box(F).get(ing, 0)
        r(f"item replace block {x} {y} {z} container.2 with minecraft:air")

    return "smelt", {"pos": pos(F), "item": ing, "count": n, "fuel": COAL}, 20 + 12 * n, lambda res, i0, i1, b0, b1: judged([
        (took[0] >= 1, f"the other player took {took[0]} {ing} at 12.5 s"),
        (moved(i0, i1, ing) + took[0] == n, f"Eve +{moved(i0, i1, ing)} + taken {took[0]} == {n}"),
        (res.get("status") == "failed" and res.get("code") == "partial", f"result {res.get('status')}/{res.get('code')} (want failed/partial)"),
        (b1[F] == {}, f"furnace {b1[F]} (want empty)")]), later(12.5, take)


def s_cancel(k):
    """Owner cancel mid-smelt, then withdraw from the furnace: everything comes back (raw + ingots == n)."""
    raw, ing, n = ore(k, [6, 7, 8, 9, 10])  # disjoint sizes from s_taken
    fill_box(F, []); give([(raw, n), (COAL, 1), (BREAD, 16)]); stand(F)

    def judge(res, i0, i1, b0, b1):
        time.sleep(1)
        left = (res.get("evidence") or {}).get("furnace_left") or {}
        for it, c in left.items():
            if it in (raw, ing):
                do("withdraw", {"pos": pos(F), "item": it, "count": c}, 30)
        i2, f2 = inv(), box(F)
        back = i2.get(raw, 0) + i2.get(ing, 0)
        return judged([(res.get("status") == "cancelled", f"result {res.get('status')}/{res.get('code')} (want cancelled)"),
                       (bool(left), f"furnace_left reported {left}"),
                       (back == n, f"after withdraw: {raw} {i2.get(raw, 0)} + {ing} {i2.get(ing, 0)} == {n}"),
                       (not ({raw, ing} & set(f2)), f"furnace after reclaim {f2}")])
    return "smelt", {"pos": pos(F), "item": ing, "count": n, "fuel": COAL}, 40, judge, later(5 + k % 3, lambda: eve("cancel", {"reason": "fixture: owner cancel mid-smelt"}))


def c_interrupt(k):
    """Owner cancel mid-craft (several batches of sticks in the 2x2): nothing is lost in the grid or cursor; a next craft works."""
    planks = 128
    give([(PL, planks), (BREAD, 16)]); stand(T)

    def judge(res, i0, i1, b0, b1):
        sticks, pl = i1.get(STICK, 0), i1.get(PL, 0)
        want2 = 4 + 4 * (k % 5)
        time.sleep(2.5)  # the cancel thread has fired before the follow-up craft starts
        nxt = do("craft", {"item": STICK, "count": want2}, 30)
        i2 = inv()
        return judged([(res.get("status") == "cancelled", f"result {res.get('status')}/{res.get('code')} (want cancelled)"),
                       (pl * 2 + sticks == planks * 2, f"conservation: planks {pl} + sticks {sticks} (2 planks -> 4 sticks)"),
                       (nxt.get("status") == "ok" and i2.get(STICK, 0) - sticks >= want2,
                        f"next craft {nxt.get('status')}/{nxt.get('code')} +{i2.get(STICK, 0) - sticks}")])
    return "craft", {"item": STICK, "count": 256 - 4 * (k % 8)}, 30, judge, later(0.4 + 0.6 * (k % 3), lambda: eve("cancel", {"reason": "fixture: owner cancel mid-craft"}))


# ---- CP-34 mine_resource: a glass room in the lanes' `mine` zone (break free up to y=60) ----
# Floor y=58 (smooth stone), interior y 59..63, glass walls and ceiling. Eve stands at the centre. Targets sit on the floor at
# 2-6 blocks (some need a walk). The block at y=61 is OUTSIDE the zone (no zone -> sense denies breaking it).
MX0, MX1, MZ0, MZ1, MY = 80 + MDX, 96 + MDX, -56 + MDZ, -40 + MDZ, 58 + MDY
EVE_AT = (88.5 + MDX, 59 + MDY, -47.5 + MDZ)
SPOTS = [(x + MDX, 59 + MDY, z + MDZ) for x, z in ((85, -47), (91, -48), (88, -44), (88, -51), (84, -52), (93, -43))]
HIGH = (90 + MDX, 61 + MDY, -50 + MDZ)  # floor + 3: outside the zone (lanes: mine zone top 60; 45L: reflex_arena top 215)


def build_room():
    r(f"forceload add {MX0} {MZ0} {MX1} {MZ1}")
    r(f"fill {MX0} {MY} {MZ0} {MX1} {MY} {MZ1} minecraft:smooth_stone")
    r(f"fill {MX0} {MY + 1} {MZ0} {MX1} {MY + 6} {MZ1} minecraft:glass hollow")
    r(f"fill {MX0} {MY} {MZ0} {MX1} {MY} {MZ1} minecraft:smooth_stone")


def reset_room(blocks):
    """Empty interior, then {pos: block}."""
    r(f"fill {MX0 + 1} {MY + 1} {MZ0 + 1} {MX1 - 1} {MY + 5} {MZ1 - 1} minecraft:air")
    for (x, y, z), b in blocks.items():
        r(f"setblock {x} {y} {z} {b}")
    tp(EVE_AT[0], EVE_AT[1], EVE_AT[2], 0, 30)
    time.sleep(1.2)


def is_block(p, b):
    return "passed" in r(f"execute if block {p[0]} {p[1]} {p[2]} {b}").lower()


def mine_case(block, n_targets, want, kit, expect_status, expect_code, drop, extra=None, tag=None, params_extra=None):
    """Common shape: n_targets of `block` on SPOTS; judge by drops in the inventory and which target blocks are gone."""
    spots = SPOTS[:n_targets]
    reset_room({**{s: block for s in spots}, **(extra or {})})
    give(kit)
    params = {"tag": tag} if tag else {"block": block}
    params.update({"count": want, **(params_extra or {})})

    def judge(res, i0, i1, b0, b1):
        gone = [s for s in spots if not is_block(s, block)]
        got = moved(i0, i1, drop) if drop else 0
        checks = [expect(res, expect_status, expect_code)]
        if expect_status == "ok":
            checks += [(len(gone) == want, f"{len(gone)} {block} gone (want {want})"),
                       (got >= want, f"{drop} +{got} in inventory (want >= {want})")]
        else:
            ev = res.get("evidence") or {}
            checks += [(got >= len(gone) if drop else True, f"{drop} +{got} for {len(gone)} broken (drops collected)"),
                       (ev.get("mined") == len(gone), f"evidence mined {ev.get('mined')} == broken {len(gone)}")]
        checks.append((is_block((int(EVE_AT[0] // 1), MY, int(EVE_AT[2] // 1)), "minecraft:smooth_stone"), "floor under her intact"))
        return judged(checks)
    return "mine_resource", params, 90, judge


STONE_PICK, IRON_PICK, WOOD_PICK = "minecraft:stone_pickaxe", "minecraft:iron_pickaxe", "minecraft:wooden_pickaxe"


def m_log(k):
    n = 2 + k % 3
    return mine_case("minecraft:oak_log", n, n, [(BREAD, 16)], "ok", None, "minecraft:oak_log")


def m_stone(k):
    n = 2 + k % 3
    return mine_case("minecraft:stone", n, n, [(STONE_PICK, 1), (BREAD, 16)], "ok", None, "minecraft:cobblestone")


def m_coal(k):
    n = 2 + k % 3
    return mine_case("minecraft:coal_ore", n, n, [(STONE_PICK, 1), (BREAD, 16)], "ok", None, "minecraft:coal")


def m_iron(k):
    n = 2 + k % 4
    return mine_case("minecraft:iron_ore", 6, n, [(STONE_PICK, 1), (BREAD, 16)], "ok", None, RAW)


def m_iron_tag(k):
    """Tag #minecraft:iron_ores over iron + deepslate iron."""
    reset_room({SPOTS[0]: "minecraft:iron_ore", SPOTS[1]: "minecraft:deepslate_iron_ore", SPOTS[2]: "minecraft:iron_ore"})
    give([(STONE_PICK, 1), (BREAD, 16)])
    want = 2 + k % 2

    def judge(res, i0, i1, b0, b1):
        gone = sum(1 for s in SPOTS[:3] if is_block(s, "minecraft:air"))
        return judged([expect(res, "ok"), (gone == want, f"{gone} iron ores gone (want {want})"),
                       (moved(i0, i1, RAW) >= want, f"raw iron +{moved(i0, i1, RAW)}")])
    return "mine_resource", {"tag": "minecraft:iron_ores", "count": want}, 90, judge


def m_no_tool(k):
    return mine_case("minecraft:iron_ore", 2, 10 + k % 5, [(BREAD, 16)], "failed", "no_tool", RAW)  # counts disjoint from m_iron (retry_limit)


def m_wrong_tool(k):  # a wooden pickaxe cannot drop iron
    return mine_case("minecraft:iron_ore", 2, 20 + k % 5, [(WOOD_PICK, 1), (BREAD, 16)], "failed", "no_tool", RAW)


def m_not_found(k):
    return mine_case("minecraft:diamond_ore", 0, 1 + k % 5, [(IRON_PICK, 1), (BREAD, 16)], "failed", "not_found", None,
                     extra={SPOTS[0]: "minecraft:iron_ore"})


def m_inv_full(k):
    return mine_case("minecraft:coal_ore", 2, 10 + k % 5, [(STONE_PICK, 1), ("minecraft:dirt", 35 * 64)], "failed", "inventory_full",
                     "minecraft:coal")


def m_forbidden(k):
    """The only target is at y=61, outside every zone: the server refuses the break; it stays."""
    reset_room({(HIGH[0], MY + 1, HIGH[2]): "minecraft:glass", (HIGH[0], MY + 2, HIGH[2]): "minecraft:glass", HIGH: "minecraft:gold_ore"})
    give([(IRON_PICK, 1), (BREAD, 16)])
    want = 1 + k % 5

    def judge(res, i0, i1, b0, b1):
        return judged([expect(res, "failed", "zone_denied"), (is_block(HIGH, "minecraft:gold_ore"), f"gold ore at y={HIGH[1]} (outside the zone) intact"),
                       (moved(i0, i1, "minecraft:raw_gold") == 0, "no raw gold")])
    return "mine_resource", {"block": "minecraft:gold_ore", "count": want}, 60, judge


def m_worn(k):
    """A stone pickaxe with 1 use left: one ore, then no hotbar tool can drop iron -> no_tool after 1."""
    reset_room({s: "minecraft:iron_ore" for s in SPOTS[:3]})
    r(f"clear {P}")
    r(f"give {P} minecraft:stone_pickaxe[damage=130] 1")
    r(f"give {P} {BREAD} 16")
    time.sleep(0.3)
    want = 30 + k % 5  # disjoint from m_iron; she can only ever get 1

    def judge(res, i0, i1, b0, b1):
        gone = sum(1 for s in SPOTS[:3] if is_block(s, "minecraft:air"))
        ev = res.get("evidence") or {}
        return judged([expect(res, "failed", "no_tool"), (gone == 1, f"{gone} ore broken (want 1: the pickaxe breaks)"),
                       (moved(i0, i1, RAW) == 1, f"raw iron +{moved(i0, i1, RAW)}"), (ev.get("mined") == 1, f"evidence mined {ev.get('mined')}")])
    return "mine_resource", {"block": "minecraft:iron_ore", "count": want}, 60, judge


# ---- CP-38 harvest_replant: two rows of 9 plots on the pad (home zone, task_only: needs the mind's grant over the box) ----
# Farmland y=150 at z -62/-63, x -44..-36; water line z=-64 (hydrates both rows); crops at y=151. Eve stands on the stone
# path z=-61. The box given to the skill includes the farmland layer (replanting is a use on the farmland).
FX0, FX1, FZ, FY = -44 + DX, -36 + DX, (-62 + DZ, -63 + DZ), 150 + DY  # FY = farmland
FARM_MIN, FARM_MAX = {"x": FX0, "y": FY, "z": -63 + DZ}, {"x": FX1, "y": FY + 1, "z": -62 + DZ}
CROPS = ["minecraft:wheat", "minecraft:carrots", "minecraft:potatoes"]


def plots():
    return [(x, FY + 1, z) for z in FZ for x in range(FX0, FX1 + 1)]


def layout(k, mature_share=0.66):
    """Deterministic mixed farm for trial k: crop type and age per plot."""
    import random
    rnd = random.Random(1000 + k)
    out = {}
    for p in plots():
        crop = CROPS[rnd.randrange(3)]
        age = 7 if rnd.random() < mature_share else rnd.randrange(0, 7)
        out[p] = (crop, age)
    return out


def build_farm(lay):
    r(f"fill {FX0 - 1} {FY + 1} {-65 + DZ} {FX1 + 1} {FY + 3} {-61 + DZ} minecraft:air")
    r(f"fill {FX0} {FY} {-63 + DZ} {FX1} {FY} {-62 + DZ} minecraft:farmland[moisture=7]")
    r(f"fill {FX0} {FY - 1} {-64 + DZ} {FX1} {FY - 1} {-64 + DZ} minecraft:smooth_stone")  # the pad floats: the water needs a floor
    r(f"fill {FX0} {FY} {-64 + DZ} {FX1} {FY} {-64 + DZ} minecraft:water")
    r(f"fill {FX0 - 1} {FY} {-65 + DZ} {FX1 + 1} {FY} {-65 + DZ} minecraft:smooth_stone")
    for (x, y, z), (crop, age) in lay.items():
        r(f"setblock {x} {y} {z} {crop}[age={age}]")
    tp(-39.5 + DX, FY + 1, -60.5 + DZ, 180, 40)
    time.sleep(1.2)


def block_at(p):
    """(crop id, age) or (None, None) from the server."""
    # crops have no block entity: execute tests, one per candidate state
    for crop in CROPS:
        if "passed" in r(f"execute if block {p[0]} {p[1]} {p[2]} {crop}").lower():
            for age in range(8):
                if "passed" in r(f"execute if block {p[0]} {p[1]} {p[2]} {crop}[age={age}]").lower():
                    return crop, age
    return None, None


def farmland_ok():
    return all("passed" in r(f"execute if block {x} {FY} {z} minecraft:farmland").lower() for (x, _, z) in plots())


def farm_case(k, replant=True, mature_share=0.66):
    lay = layout(k, mature_share)
    build_farm(lay)
    give([(BREAD, 16)])
    mature = {p: c for p, (c, a) in lay.items() if a == 7}

    def judge(res, i0, i1, b0, b1):
        after = {p: block_at(p) for p in plots()}
        ev = res.get("evidence") or {}
        bad_mature = [p for p in mature if (after[p][0] != mature[p] or after[p][1] == 7) if replant]
        gone_mature = [p for p in mature if after[p][0] is not None] if not replant else []
        bad_young = [p for p, (c, a) in lay.items() if a < 7 and (after[p][0] != c or after[p][1] < a)]
        harvested = sum((ev.get("harvested") or {}).values()) if isinstance(ev.get("harvested"), dict) else None
        # crops grow during a 20-40 s run: an immature plot that ripened and was then harvested (replanted / left empty)
        # is correct, provided the skill's own harvest count covers it (a9: two such plots were judged "destroyed")
        ripened = [p for p in bad_young if (after[p][0] == lay[p][0] if replant else after[p][0] is None)]
        if harvested is not None and len(ripened) <= harvested - len(mature):
            bad_young = [p for p in bad_young if p not in ripened]
        else:
            ripened = []
        # a mature wheat plot can yield no seed (vanilla's bonus roll): then the only correct outcome is that plot reported as
        # not replanted for want of seed, her inventory truly holding none, and the whole result `partial` (CP-38: missing
        # inputs give a clear partial report). Criterion fixed after a4/a5 judged such a run as a failure.
        SEEDS = {"minecraft:wheat": "minecraft:wheat_seeds", "minecraft:carrots": "minecraft:carrot", "minecraft:potatoes": "minecraft:potato"}
        nr = {(o["pos"]["x"], o["pos"]["y"], o["pos"]["z"]): o.get("reason", "") for o in (ev.get("not_replanted") or [])}
        no_seed_ok = [p for p in bad_mature if p in nr and nr[p].startswith("no ") and i1.get(SEEDS[mature[p]], 0) == 0
                      and after[p][0] is None]
        bad_mature = [p for p in bad_mature if p not in no_seed_ok]
        want_status = expect(res, "failed", "partial") if no_seed_ok else expect(res, "ok")
        checks = [want_status if not no_seed_ok else (want_status[0], want_status[1] + f"; {len(no_seed_ok)} plot(s) honestly reported without seed"),
                  (harvested == len(mature) + len(ripened), f"evidence harvested {harvested} == mature {len(mature)}"
                   + (f" + {len(ripened)} ripened during the run" if ripened else "")),
                  (not bad_young, f"immature untouched ({len(lay) - len(mature)}; bad {bad_young[:3]})"),
                  (farmland_ok(), "all 18 farmland blocks intact (no trampling)"),
                  (ev.get("trampled") == 0, f"evidence trampled {ev.get('trampled')}")]
        if replant:
            checks.append((not bad_mature, f"{len(mature) - len(bad_mature)}/{len(mature)} mature plots replanted with the same crop (bad {bad_mature[:3]})"))
        else:
            checks.append((not gone_mature, f"replant=false: {len(mature)} plots left empty (bad {gone_mature[:3]})"))
        return judged(checks)
    # the box grows by a k-dependent margin of path/air (harmless; ≤ 32 wide) so no two trials share params: the mind refuses
    # params that failed 3 times in 10 min (retry_limit), which turned CP-38 a2 into 6 refusals
    lo = {**FARM_MIN, "x": FARM_MIN["x"] - k % 5}
    hi = {**FARM_MAX, "x": FARM_MAX["x"] + (k // 5) % 4}
    return "harvest_replant", {"min": lo, "max": hi, "replant": replant}, 240, judge


def h_mixed(k):
    return farm_case(k)


def h_mostly_young(k):
    return farm_case(k, mature_share=0.2)


def h_no_replant(k):
    return farm_case(k, replant=False)


def c_chain(k):
    """Recipe chain through real menus: 3 logs -> 12 planks (2x2) -> 4 sticks (2x2) -> crafting table (2x2) -> iron pickaxe
    at the table. Hand count: planks 12 - 2 (sticks) - 4 (table) = 6; sticks 4 - 2 = 2; ingots 3 + k - 3 = k."""
    give([(LOG, 3), (I, 3 + k % 4), (BREAD, 16)]); stand(T)

    def judge(res, i0, i1, b0, b1):
        steps = [("sticks", do("craft", {"item": STICK, "count": 4}, 30)),
                 ("table", do("craft", {"item": "minecraft:crafting_table", "count": 1}, 30)),
                 ("pickaxe", do("craft", {"item": PICK, "count": 1, "pos": pos(T)}, 40))]
        i2 = inv()
        want = {PL: 6, STICK: 2, "minecraft:crafting_table": 1, PICK: 1, BREAD: 16, **({I: k % 4} if k % 4 else {})}
        return judged([expect(res, "ok")] + [(s.get("status") == "ok", f"{n} {s.get('status')}/{s.get('code')}") for n, s in steps]
                      + [(i2 == want, f"final inventory {Plans_diff(want, i2) or 'exact'}")])
    return "craft", {"item": PL, "count": 12}, 30, judge


# ---- CP-39 gather: the glass mine room as an animal pen (free zone); animals summoned by RCON, never killed by Eve ----
PEN_TAG = "evepen"


def pen(animals):
    """animals: [(type, nbt)] summoned on SPOTS around her; every one tagged so the judge can count them."""
    reset_room({})
    r(f"kill @e[tag={PEN_TAG}]")
    r(f"kill @e[type=item,x={MX0},y={MY},z={MZ0},dx={MX1 - MX0},dy=6,dz={MZ1 - MZ0}]")
    for i, (typ, nbt) in enumerate(animals):
        x, y, z = SPOTS[i % len(SPOTS)]
        extra = ("," + nbt) if nbt else ""
        r(f"summon minecraft:{typ} {x + 0.5} {y} {z + 0.5} {{Tags:[\"{PEN_TAG}\"],PersistenceRequired:1b{extra}}}")
    time.sleep(1.0)


def n_ent(sel):
    m = re.search(r"Count: (\d+)", r(f"execute if entity @e[{sel}]") or "")
    return int(m.group(1)) if m else 0


def wool(i):
    return sum(c for it, c in i.items() if it.endswith("_wool"))


BYSTANDERS = [("villager", ""), ("cat", f'Owner:[I;1,2,3,4]'), ("sheep", 'CustomName:"Dolly"'), ("sheep", "Age:-24000")]


def g_wool(k):
    want = 2 + k % 2
    pen([("sheep", ""), ("sheep", ""), ("sheep", "")] + BYSTANDERS)
    give([("minecraft:shears", 1), (BREAD, 16)])

    def judge(res, i0, i1, b0, b1):
        ev = res.get("evidence") or {}
        return judged([expect(res, "ok"), (wool(i1) - wool(i0) >= want, f"wool +{wool(i1) - wool(i0)} (want >= {want})"),
                       (n_ent(f"tag={PEN_TAG}") == 7, f"all 7 pen animals alive ({n_ent(f'tag={PEN_TAG}')})"),
                       (n_ent(f'tag={PEN_TAG},type=sheep,name=Dolly,nbt={{Sheared:0b}}') == 1, "named sheep Dolly not sheared"),
                       (n_ent(f"tag={PEN_TAG},type=sheep,nbt={{Age:-24000,Sheared:0b}}") + n_ent(f"tag={PEN_TAG},type=sheep,nbt={{Sheared:0b}}") >= 2,
                        "the lamb is not sheared"),
                       ((ev.get("protected_skipped") or 0) >= 1, f"evidence protected_skipped {ev.get('protected_skipped')}")])
    return "gather", {"product": "wool", "count": want}, 90, judge


def g_milk(k):
    want = 2
    pen([("cow", ""), ("cow", ""), ("cow", 'CustomName:"Bessie"'), ("cow", "Age:-24000"), ("villager", ""), ("cat", f'Owner:[I;1,2,3,4]')])
    give([("minecraft:bucket", 2 + k % 3), (BREAD, 16)])

    def judge(res, i0, i1, b0, b1):
        return judged([expect(res, "ok"), (moved(i0, i1, "minecraft:milk_bucket") == want, f"milk +{moved(i0, i1, 'minecraft:milk_bucket')} (want {want})"),
                       (n_ent(f"tag={PEN_TAG}") == 6, f"all 6 pen animals alive ({n_ent(f'tag={PEN_TAG}')})")])
    return "gather", {"product": "milk", "count": want}, 90, judge


def g_only_protected(k):
    """Only a named sheep, a lamb, a villager and a pet: nothing eligible -> not_found, nobody sheared or hurt."""
    pen(BYSTANDERS)
    give([("minecraft:shears", 1), (BREAD, 16)])
    want = 10 + k % 5

    def judge(res, i0, i1, b0, b1):
        return judged([expect(res, "failed", "not_found"), (wool(i1) == wool(i0), "no wool"),
                       (n_ent(f"tag={PEN_TAG},type=sheep,nbt={{Sheared:1b}}") == 0, "no sheep sheared"),
                       (n_ent(f"tag={PEN_TAG}") == 4, f"all 4 alive ({n_ent(f'tag={PEN_TAG}')})")])
    return "gather", {"product": "wool", "count": want}, 60, judge


def g_no_tool(k):
    pen([("sheep", ""), ("sheep", "")])
    give([(BREAD, 16)])
    want = 20 + k % 5
    return "gather", {"product": "wool", "count": want}, 60, lambda res, i0, i1, b0, b1: judged([
        expect(res, "failed", "no_tool"), (n_ent(f"tag={PEN_TAG},type=sheep,nbt={{Sheared:1b}}") == 0, "no sheep sheared")])


def g_wood(k):
    n = 2 + k % 2
    reset_room({s: "minecraft:birch_log" for s in SPOTS[:3]})
    r(f"kill @e[tag={PEN_TAG}]")
    give([(BREAD, 16)])
    return "gather", {"product": "wood", "count": n}, 90, lambda res, i0, i1, b0, b1: judged([
        expect(res, "ok"), (moved(i0, i1, "minecraft:birch_log") == n, f"birch logs +{moved(i0, i1, 'minecraft:birch_log')} (want {n})")])


# ---- CP-40 building block: prospect in natural stone inside the lanes' `mine` zone (break free below y 61) ----
# A 3x3x3 air chamber is carved by RCON at PX/PY/PZ (fixture setup); she stands in it facing east; the tunnel runs +x.
# Planted blocks sit one block north of the tunnel line at head height, so the tunnel exposes them. Each trial uses its
# own z so tunnels never overlap (k varies the z by 4).
PX, PY = 110 + MDX, 30 + MDY


CORRIDOR = [0]  # the corridor slot of the case being set up (p_mob/p_animal summon in it)


def chamber(k, y=None, low=None):
    # every case its own corridor: consecutive cases sharing one saw the previous tunnel for a moment after the refill
    # (cp40p a26: p_gap and p_stepdown walked a stale open tunnel). 20 slots, 5 apart: 2-thick shells never touch.
    CORRIDOR[0] = (CORRIDOR[0] + 1) % 20
    z = -100 + MDZ + 5 * CORRIDOR[0]
    y = PY if y is None else y
    lo = min(y, low if low is not None else y)
    r(f"forceload add {PX - 2} {z - 2} {PX + 70} {z + 2}")
    # a clean stone corridor (no caves, fluids or natural ores), down to the descend target, inside a 2-thick shell so
    # aquifer water next to it cannot flow in (cp40p a3: every hazard trip was natural water at 0 blocks)
    r(f"fill {PX - 3} {lo - 2} {z - 2} {PX + 68} {y + 4} {z + 2} minecraft:stone")
    r(f"fill {PX - 1} {y} {z - 1} {PX + 1} {y + 2} {z + 1} minecraft:air")
    r(f"setblock {PX} {y + 2} {z - 1} minecraft:torch")
    return z, y


def p_case(k, plant, params, judge_extra, y=None, descend=None, post=(), back=True, low=None):
    z, y0 = chamber(k, y, descend if descend is not None else low)  # low: rock this deep without a staircase (pits)
    for dx, dy, dz, block in plant:
        r(f"setblock {PX + dx} {y0 + dy} {z + dz} {block}")
    give([(STONE_PICK, 1), ("minecraft:torch", 16), (BREAD, 16)])
    tp(PX + 0.5, y0, z + 0.5, -90, 0)  # facing east (+x)
    for dx, dy, dz, block in post:  # in her own cell: safe_tp wants air at the feet, so it goes in after she lands
        r(f"setblock {PX + dx} {y0 + dy} {z + dz} {block}")
    prm = {"tag": "minecraft:iron_ores", "direction": "east", **params}
    if descend is not None:
        prm["descend_to"] = descend

    def judge(res, i0, i1, b0, b1):
        ev = res.get("evidence") or {}
        pos = re.findall(r"(-?[\d.]+)d", r(f"data get entity {P} Pos"))
        back_at = len(pos) >= 3 and abs(float(pos[0]) - (PX + 0.5)) <= 2.5 and abs(float(pos[2]) - (z + 0.5)) <= 2.5
        home = ev.get("returned") is True and back_at
        checks = [(home, f"returned to the start (evidence {ev.get('returned')}, at {pos[:3]})")] if back else []  # a threat stop stays put
        checks += [((ev.get("blocks_broken") or 0) <= prm.get("max_blocks", 64) + 3, f"blocks_broken {ev.get('blocks_broken')} within budget")]
        return judged(checks + judge_extra(res, i0, i1, ev, z, y0))
    return "prospect", prm, 240, judge


def p_ore(k):
    """Two iron ores beside the tunnel at 5 and 9 blocks: count 2 -> ok, +2 raw iron, back at the start."""
    return p_case(k, [(5, 1, -1, "minecraft:iron_ore"), (9, 1, -1, "minecraft:iron_ore")], {"count": 2, "max_blocks": 40},
                  lambda res, i0, i1, ev, z, y: [expect(res, "ok"), (moved(i0, i1, RAW) >= 2, f"raw iron +{moved(i0, i1, RAW)} (want >= 2)"),
                                                 ((ev.get("torches_placed") or 0) >= 1, f"torches placed {ev.get('torches_placed')}")])


def p_budget(k):
    """No ore in reach of a 16-block budget: an honest not_found / partial, budget kept, back home."""
    return p_case(k, [], {"count": 3, "max_blocks": 16 + k % 4},
                  lambda res, i0, i1, ev, z, y: [(res.get("status") == "failed" and res.get("code") in ("not_found", "partial"),
                                                  f"result {res.get('status')}/{res.get('code')} (want not_found)"),
                                                 (str(ev.get("stop_reason", "")).startswith("max_blocks"), f"stop_reason {ev.get('stop_reason')}")])


def p_lava(k):
    """Lava 6 blocks ahead in the tunnel line: she stops before it (hazard), the lava is untouched, she comes back."""
    d = 6 + k % 3

    def extra(res, i0, i1, ev, z, y):
        lava = "passed" in r(f"execute if block {PX + d} {y} {z} minecraft:lava").lower()
        return [expect(res, "failed", "hazard"), (lava, "lava still in place"), (ev.get("tunnel_length", 99) < d, f"tunnel {ev.get('tunnel_length')} < {d}")]
    return p_case(k, [(d, 0, 0, "minecraft:lava")], {"count": 1 + k % 3, "max_blocks": 40}, extra)


def p_descend(k):
    """From y 40 a staircase down to y 32, then the tunnel; an iron ore beside the line at the bottom."""
    top, bottom = 40 + MDY, 32 + MDY
    steps = top - bottom

    def extra(res, i0, i1, ev, z, y):
        return [expect(res, "ok"), (moved(i0, i1, RAW) >= 1, f"raw iron +{moved(i0, i1, RAW)}")]
    return p_case(k, [(steps + 3, 1 - steps, -1, "minecraft:iron_ore")], {"count": 1, "max_blocks": 60 + k % 3}, extra, y=top, descend=bottom)


def p_grass(k):
    """A grassy surface entry (CP-40 a8): she stands in short grass on grass_block, short grass (or a poppy) in the cell ahead,
    staircase down. The first stair dig aims past that outline-only plant; it must be cleared, not `blocked:obstructed`."""
    top, bottom = 40 + MDY, 36 + MDY
    steps = top - bottom
    plant = "minecraft:short_grass" if k % 2 else "minecraft:poppy"

    def extra(res, i0, i1, ev, z, y):
        return [expect(res, "ok"), (moved(i0, i1, RAW) >= 1, f"raw iron +{moved(i0, i1, RAW)}"),
                (not str(ev.get("stop_reason", "")).startswith("blocked"), f"stop_reason {ev.get('stop_reason')}")]
    return p_case(k, [(0, -1, 0, "minecraft:grass_block"), (1, -1, 0, "minecraft:grass_block"), (1, 0, 0, plant),
                      (steps + 3, 1 - steps, -1, "minecraft:iron_ore")],
                  {"count": 1, "max_blocks": 40 + k % 3}, extra, y=top, descend=bottom, post=[(0, 0, 0, "minecraft:short_grass")])


def p_hit(k):
    """CP-40 a9 (death in the tunnel): a hit while she digs ends prospect at once with `hurt`, where she stands.
    Peaceful: the hit is a scripted /damage 3 four seconds in."""
    def extra(res, i0, i1, ev, z, y):
        hp = re.search(r"(-?[\d.]+)f", r(f"data get entity {P} Health"))
        t = ev.get("threat") or {}
        return [expect(res, "failed", "hurt"), (t.get("kind") == "hit", f"threat {t}"), (ev.get("stop_at") is not None, f"stop_at {ev.get('stop_at')}"),
                (hp is not None and float(hp.group(1)) > 0, f"alive (health {hp.group(1) if hp else None})")]
    return (*p_case(k, [], {"count": 3, "max_blocks": 40 + k % 3}, extra, back=False),
            lambda: (time.sleep(4), r(f"damage {P} 3 minecraft:generic")))


def p_mob(k):
    """A hostile in sight ends prospect before it lands a hit: a NoAI zombie in an air pocket 8 ahead, opened by the tunnel.
    Difficulty easy for this case only (peaceful removes hostiles); the zombie is killed and peaceful restored in the judge."""
    def extra(res, i0, i1, ev, z, y):
        hp = re.search(r"(-?[\d.]+)f", r(f"data get entity {P} Health"))
        r(f"kill @e[tag={PEN_TAG}_z]")
        r("difficulty peaceful")
        t = ev.get("threat") or {}
        return [expect(res, "failed", "hurt"), (t.get("kind") == "hostile" and t.get("type") == "zombie" and t.get("entity_id") is not None, f"threat {t}"),
                (ev.get("stop_at") is not None, f"stop_at {ev.get('stop_at')}"),
                (hp is not None and float(hp.group(1)) >= 20, f"never hit (health {hp.group(1) if hp else None})")]
    case = p_case(k, [(7, 0, 0, "minecraft:air"), (7, 1, 0, "minecraft:air"), (8, 0, 0, "minecraft:air"), (8, 1, 0, "minecraft:air")],
                  {"count": 3, "max_blocks": 40 + k % 3}, extra, back=False)
    z = -100 + MDZ + 5 * CORRIDOR[0]  # chamber()'s corridor for this case
    r(f"effect give {P} minecraft:instant_health 1 4 true")  # full health: "never hit" is judged at 20
    r("difficulty easy")
    r(f"summon minecraft:zombie {PX + 8.5} {PY} {z + 0.5} {{NoAI:1b,PersistenceRequired:1b,Silent:1b,Tags:[\"{PEN_TAG}_z\"]}}")
    return case


def p_animal(k):
    """CP-40 a10 (O41n): a living blocker on the first stair dig's line is transient. A NoAI cow stands in front of her, 3 s
    later it goes; prospect waits and digs on (old jars: blocked:obstructed at once)."""
    top, bottom = 40 + MDY, 36 + MDY
    steps = top - bottom

    def extra(res, i0, i1, ev, z, y):
        return [expect(res, "ok"), (moved(i0, i1, RAW) >= 1, f"raw iron +{moved(i0, i1, RAW)}")]
    case = p_case(k, [(steps + 3, 1 - steps, -1, "minecraft:iron_ore")], {"count": 1, "max_blocks": 40 + k % 3}, extra, y=top,
                  descend=bottom)
    z = -100 + MDZ + 5 * CORRIDOR[0]  # chamber()'s corridor for this case
    r(f"summon minecraft:cow {PX + 1.5} {top} {z + 0.5} {{NoAI:1b,Silent:1b,Tags:[\"{PEN_TAG}_c\"]}}")
    return (*case, lambda: (time.sleep(3), r(f"kill @e[tag={PEN_TAG}_c]")))


def p_litter(k):
    """CP-40 a10: leaf litter (a 1-px outline) in the first stair cell. body's mine_block aims at the cell centre, which passes
    over it, so the dig comes back obstructed (crosshair beyond). Passes once mine_block aims at the block's outline."""
    top, bottom = 40 + MDY, 36 + MDY
    steps = top - bottom

    def extra(res, i0, i1, ev, z, y):
        return [expect(res, "ok"), (moved(i0, i1, RAW) >= 1, f"raw iron +{moved(i0, i1, RAW)}"),
                (not str(ev.get("stop_reason", "")).startswith("blocked"), f"stop_reason {ev.get('stop_reason')}")]
    # a10's geometry: one stair step down she stands in (x+1, y-1); the litter lies in the next column at her old ground
    # level (x+2, y), so the ray to its cell centre runs nearly flat and passes over its 1-px outline
    return p_case(k, [(0, -1, 0, "minecraft:grass_block"), (1, -1, 0, "minecraft:grass_block"), (2, -1, 0, "minecraft:grass_block"),
                      (2, 1, 0, "minecraft:air"), (2, 0, 0, "minecraft:leaf_litter[segment_amount=4]"),
                      (steps + 3, 1 - steps, -1, "minecraft:iron_ore")],
                  {"count": 1, "max_blocks": 40 + k % 3}, extra, y=top, descend=bottom)


def _sealed(n_key, at_least):
    def extra(res, i0, i1, ev, z, y):
        m = re.search(n_key + r" (\d+)", str(ev.get("stop_reason", "")))
        return [expect(res, "ok"), (moved(i0, i1, RAW) >= 1, f"raw iron +{moved(i0, i1, RAW)}"),
                (m is not None and int(m.group(1)) >= at_least, f"stop_reason {ev.get('stop_reason')} ({n_key} >= {at_least})")]
    return extra


def p_water(k):
    """O71 (CP-40 a12): a water source in the tunnel line, hidden behind rock until the dig opens it. Sealed with a mined
    block, then the tunnel goes on to the ore (old jars: hazard:water before digging)."""
    # own budget per case: identical params across cases meet the mind's retry_limit (O71 lane run, p_gap #2 INVALID)
    return p_case(k, [(5 + k % 2, 0, 0, "minecraft:water"), (9, 1, -1, "minecraft:iron_ore")], {"count": 1, "max_blocks": 41 + k % 3},
                  _sealed("seals", 1))


def p_water2(k):
    """O71: two sources in line (and one above the second): each seal, once dug through, exposes more water behind it."""
    return p_case(k, [(5, 0, 0, "minecraft:water"), (6, 0, 0, "minecraft:water"), (6, 1, 0, "minecraft:water"),
                      (10, 1, -1, "minecraft:iron_ore")], {"count": 1, "max_blocks": 45 + k % 3}, _sealed("seals", 2))


def p_gap(k):
    """O71: a single missing floor cell under the tunnel line (rock below, ahead and beside it) is bridged with one block
    (old jars: hazard:no_floor)."""
    return p_case(k, [(5 + k % 2, -1, 0, "minecraft:air"), (9, 1, -1, "minecraft:iron_ore")], {"count": 1, "max_blocks": 49 + k % 3},
                  _sealed("bridged", 1))


def p_stepdown(k):
    """O74 (CP-40 a13): a 1-wide pit under the tunnel line, 1-3 deep (k). 1 is bridged (O71); 2-3 are filled up to a
    1-block step, she steps down and tunnels on at the lower level to the ore, and walks back up (old jars: no_floor)."""
    h = 1 + k % 3
    holes = [(5, -d, 0, "minecraft:air") for d in range(1, h + 1)]
    return p_case(k, holes + [(9, 1, -1, "minecraft:iron_ore"), (9, 0, -1, "minecraft:iron_ore")],
                  {"count": 1, "max_blocks": 53 + k % 3}, _sealed("bridged", 1), low=PY - 6)


def p_drop(k):
    """O74: a pit 4 deep under the line is too far to step down: hazard:no_floor, nothing placed, she comes back."""
    holes = [(5, -d, 0, "minecraft:air") for d in range(1, 5)]

    def extra(res, i0, i1, ev, z, y):
        return [expect(res, "failed", "hazard"), (str(ev.get("stop_reason", "")).startswith("hazard:no_floor"), f"stop_reason {ev.get('stop_reason')}")]
    return p_case(k, holes, {"count": 1, "max_blocks": 57 + k % 3}, extra, low=PY - 7)


def p_fillfail(k):
    """CP-40 a14: a fill that cannot be placed (an invisible armour stand in the pit's bottom cell: place_block says
    occupied) ends prospect with hazard:no_floor and the failure in stop_reason, at once (old jar: the same failing
    placement every ten ticks until cancelled)."""
    holes = [(5, -1, 0, "minecraft:air"), (5, -2, 0, "minecraft:air")]

    def extra(res, i0, i1, ev, z, y):
        sr = str(ev.get("stop_reason", ""))
        r(f"kill @e[tag={PEN_TAG}_s]")
        return [expect(res, "failed", "hazard"), (sr.startswith("hazard:no_floor") and "fill failed" in sr, f"stop_reason {sr}")]
    case = p_case(k, holes, {"count": 1, "max_blocks": 61 + k % 3}, extra, low=PY - 6)
    z = -100 + MDZ + 5 * CORRIDOR[0]
    # O86: the bridge goes into the step's own floor cell (5, -1), so that is where the stand blocks it
    r(f"summon minecraft:armor_stand {PX + 5.5} {PY - 1} {z + 0.5} {{NoGravity:1b,Invisible:1b,Tags:[\"{PEN_TAG}_s\"]}}")
    return case


def p_stairdrop(k):
    """CP-40 a15: on the staircase, the floor under the third step is missing 2 deep (3 below her feet). The step's cells are
    dug first, then the pit is filled through the opened step to a 1-block step, and the staircase goes on to the ore."""
    top, bottom = 40 + MDY, 32 + MDY
    steps = top - bottom
    holes = [(3, -4, 0, "minecraft:air"), (3, -5, 0, "minecraft:air")]
    return p_case(k, holes + [(steps + 3, 1 - steps, -1, "minecraft:iron_ore")], {"count": 1, "max_blocks": 65 + k % 3},
                  _sealed("bridged", 1), y=top, descend=bottom)


def p_edge(k):
    """O85 (CP-40 a16 rerun): heading north, the tunnel meets the lane mine zone's edge (z min -112; z -113 is outside).
    The refused dig turns her left (west) at that depth, and she tunnels on to an ore beside the turned line, then back.
    Old jars: zone_denied at the edge. Its own spot south of the corridor slots; x moves with k so trials never share it."""
    x0, y0, z0 = PX - 18 * (k % 3), PY, -106 + MDZ
    r(f"forceload add {x0 - 14} {z0 - 10} {x0 + 4} {z0 + 4}")
    r(f"fill {x0 - 14} {y0 - 2} {z0 - 10} {x0 + 4} {y0 + 4} {z0 + 4} minecraft:stone")
    r(f"fill {x0 - 1} {y0} {z0 - 1} {x0 + 1} {y0 + 2} {z0 + 1} minecraft:air")
    r(f"setblock {x0 - 5} {y0 + 1} {-111 + MDZ} minecraft:iron_ore")  # south side of the turned (west) line, head height
    give([(STONE_PICK, 1), ("minecraft:torch", 16), (BREAD, 16)])
    tp(x0 + 0.5, y0, z0 + 0.5, 180, 0)  # facing north (-z)
    prm = {"tag": "minecraft:iron_ores", "direction": "north", "count": 1, "max_blocks": 69 + k % 3}

    def judge(res, i0, i1, b0, b1):
        ev = res.get("evidence") or {}
        sr = str(ev.get("stop_reason", ""))
        pos = re.findall(r"(-?[\d.]+)d", r(f"data get entity {P} Pos"))
        back = len(pos) >= 3 and abs(float(pos[0]) - (x0 + 0.5)) <= 2.5 and abs(float(pos[2]) - (z0 + 0.5)) <= 2.5
        return judged([expect(res, "ok"), (moved(i0, i1, RAW) >= 1, f"raw iron +{moved(i0, i1, RAW)}"),
                       ("turns 1" in sr, f"stop_reason {sr} (turns 1)"),
                       (ev.get("returned") is True and back, f"returned to the start (evidence {ev.get('returned')}, at {pos[:3]})")])
    return "prospect", prm, 240, judge


def _inflow(k, length, over=False):
    """O86u/O93 (CP-40 a19-a21 at 63,60,-37): p_edge's spot. Heading north she meets the zone edge and turns west, and the
    turned line runs into a water body hidden behind rock: `length` blocks long from the second turned cell on, beside it
    (south side, feet and head height, 3 deep) or, with `over`, over its head (2 high). An ore lies past the water."""
    x0, y0, z0 = PX - 18 * (k % 3), PY, -106 + MDZ
    r(f"forceload add {x0 - 14} {z0 - 10} {x0 + 4} {z0 + 4}")
    r(f"fill {x0 - 14} {y0 - 2} {z0 - 10} {x0 + 4} {y0 + 4} {z0 + 4} minecraft:stone")
    r(f"fill {x0 - 1} {y0} {z0 - 1} {x0 + 1} {y0 + 2} {z0 + 1} minecraft:air")
    if over:
        r(f"fill {x0 - 1 - length} {y0 + 2} {-112 + MDZ} {x0 - 2} {y0 + 3} {-112 + MDZ} minecraft:water")
    else:
        r(f"fill {x0 - 1 - length} {y0} {-111 + MDZ} {x0 - 2} {y0 + 1} {-109 + MDZ} minecraft:water")
    r(f"setblock {x0 - 4 - length} {y0 + 1} {-111 + MDZ} minecraft:iron_ore")  # south side of the west line, past the water
    give([(STONE_PICK, 1), ("minecraft:torch", 16), (BREAD, 16)])
    tp(x0 + 0.5, y0, z0 + 0.5, 180, 0)  # facing north (-z)
    return x0, z0, y0


def _inflow_judge(x0, z0, y0):
    """O93: detect, stop and go home - hazard:water before the water is opened, no seal, the turn made, back at the start,
    and not one water cell in the tunnel."""
    def judge(res, i0, i1, b0, b1):
        ev = res.get("evidence") or {}
        sr = str(ev.get("stop_reason", ""))
        pos = re.findall(r"(-?[\d.]+)d", r(f"data get entity {P} Pos"))
        back = len(pos) >= 3 and abs(float(pos[0]) - (x0 + 0.5)) <= 2.5 and abs(float(pos[2]) - (z0 + 0.5)) <= 2.5
        # the tunnel cells (the north leg and the turned leg up to the water) hold no water: nothing was opened
        dry = all("passed" not in r(f"execute if block {x} {y} {z} minecraft:water").lower()
                  for x, z in [(x0, zz) for zz in range(-112 + MDZ, z0)] + [(xx, -112 + MDZ) for xx in (x0 - 1, x0 - 2)] for y in (y0, y0 + 1))
        return judged([expect(res, "failed", "hazard"), (sr.startswith("hazard:water"), f"stop_reason {sr}"),
                       ("turns 1" in sr, "turns 1"), ((ev.get("seals") or 0) == 0, f"seals {ev.get('seals')} == 0"),
                       (dry, "the tunnel is dry (no water opened)"),
                       (ev.get("returned") is True and back, f"returned to the start (evidence {ev.get('returned')}, at {pos[:3]})")])
    return judge


def p_inflow(k):
    """O93: hidden water beside the turned line: hazard:water before the cell beside it is opened, no seal, home."""
    x0, z0, y0 = _inflow(k, 3)
    return "prospect", {"tag": "minecraft:iron_ores", "direction": "north", "count": 1, "max_blocks": 77 + k % 3}, 300, _inflow_judge(x0, z0, y0)


def p_inflow_big(k):
    """O93: hidden water over the turned line's head (an 11-long body 2 high): the same stop, no seal, home."""
    x0, z0, y0 = _inflow(k, 11, over=True)
    return "prospect", {"tag": "minecraft:iron_ores", "direction": "north", "count": 1, "max_blocks": 81 + k % 3}, 300, _inflow_judge(x0, z0, y0)


def p_nofiller(k):
    """O86 (CP-40 a17 north): the floor under her first step is a 2-deep pit while she carries no filler (nothing dug
    yet, no stone or dirt given). She digs one side block of her own cell for one, bridges, and tunnels on to the ore."""
    holes = [(1, -1, 0, "minecraft:air"), (1, -2, 0, "minecraft:air")]
    return p_case(k, holes + [(9, 1, -1, "minecraft:iron_ore")], {"count": 1, "max_blocks": 73 + k % 3}, _sealed("bridged", 1), low=PY - 6)


# ---- O86 dig_up (registry 0.15.0): the way out when go_home is home_blocked underground ----
# Each case its own block of rock south of the corridor slots (z -112..-102 is p_edge's), x stepped by k.
def u_block(x0, y0, z0, x_span=(-3, 14), y_top=12, z_span=(-4, 4)):
    r(f"forceload add {x0 + x_span[0]} {z0 + z_span[0]} {x0 + x_span[1]} {z0 + z_span[1]}")
    r(f"fill {x0 + x_span[0]} {y0 - 3} {z0 + z_span[0]} {x0 + x_span[1]} {y0 + y_top} {z0 + z_span[1]} minecraft:stone")
    r(f"fill {x0 - 1} {y0} {z0 - 1} {x0 + 1} {y0 + 1} {z0 + 1} minecraft:air")  # a 3x3x2 pocket: she is shut in


def u_judge(x0, y0, z0, want_status, want_code, extra):
    def judge(res, i0, i1, b0, b1):
        ev = res.get("evidence") or {}
        pos = re.findall(r"(-?[\d.]+)d", r(f"data get entity {P} Pos"))
        return judged([expect(res, want_status, want_code)] + extra(res, ev, [float(v) for v in pos[:3]] if len(pos) >= 3 else None))
    return judge


def u_flood(k):
    """O86: she is shut in a flooded pocket (water sources behind her at her feet); dig_up seals what touches her and
    climbs a staircase 6 up toward the target: ok, reached, standing at its level."""
    x0, y0, z0 = PX - 40 + 6 * (k % 3), PY, -96 + MDZ
    u_block(x0, y0, z0)
    give([(STONE_PICK, 1), ("minecraft:torch", 16), (BREAD, 16), ("minecraft:cobblestone", 8)])
    tp(x0 + 0.5, y0, z0 + 0.5, -90, 0)
    for dz in (-1, 0, 1):
        r(f"setblock {x0 - 1} {y0} {z0 + dz} minecraft:water")
    tgt = {"x": x0 + 8, "y": y0 + 6, "z": z0}

    def extra(res, ev, pos):
        return [(ev.get("reached") is True, f"reached {ev.get('reached')}, climbed {ev.get('climbed')}"),
                (pos is not None and pos[1] >= tgt["y"], f"standing at {pos} (y >= {tgt['y']})")]
    return "dig_up", {"toward": tgt, "max_blocks": 60 + k % 3}, 240, u_judge(x0, y0, z0, "ok", None, extra)


def u_edge(k):
    """O86: climbing east toward a target past the lane mine zone's x edge (max 192): at the edge the refused dig turns
    her (O85 rules) and she climbs on to the target level inside the zone: ok, reached, 'turns 1'."""
    x0, y0, z0 = 186, PY, -90 + MDZ + 6 * (k % 3)
    u_block(x0, y0, z0, x_span=(-3, 8), z_span=(-8, 8))
    give([(STONE_PICK, 1), ("minecraft:torch", 16), (BREAD, 16), ("minecraft:cobblestone", 8)])
    tp(x0 + 0.5, y0, z0 + 0.5, -90, 0)
    tgt = {"x": 198, "y": y0 + 8, "z": z0}

    def extra(res, ev, pos):
        return [(ev.get("reached") is True, f"reached {ev.get('reached')}, climbed {ev.get('climbed')}"),
                ("turns 1" in str(ev.get("stop_reason", "")), f"stop_reason {ev.get('stop_reason')}"),
                (pos is not None and pos[0] <= 193, f"inside the zone at {pos}")]
    return "dig_up", {"toward": tgt, "max_blocks": 63 + k % 3}, 240, u_judge(x0, y0, z0, "ok", None, extra)


def u_lava(k):
    """O86: lava over her head room (two above her head): dig_up stops hazard at once, breaks nothing, the lava stays.
    She stands against the pocket's east wall, where the climb starts: from its middle she walks flat to that wall first
    and climbs beside lava placed over the middle (digup a55: ok, reached, 18 broken, the lava never exposed)."""
    x0, y0, z0 = PX - 40 + 6 * (k % 3), PY, -84 + MDZ
    u_block(x0, y0, z0)
    give([(STONE_PICK, 1), ("minecraft:torch", 16), (BREAD, 16), ("minecraft:cobblestone", 8)])
    tp(x0 + 1.5, y0, z0 + 0.5, -90, 0)
    r(f"setblock {x0 + 1} {y0 + 3} {z0} minecraft:lava")
    tgt = {"x": x0 + 8, "y": y0 + 6, "z": z0}

    def extra(res, ev, pos):
        lava = "passed" in r(f"execute if block {x0 + 1} {y0 + 3} {z0} minecraft:lava").lower()
        return [(lava, "lava still in place"), ((ev.get("blocks_broken") or 0) == 0, f"blocks_broken {ev.get('blocks_broken')}"),
                ("lava" in str(ev.get("stop_reason", "")), f"stop_reason {ev.get('stop_reason')}")]
    return "dig_up", {"toward": tgt, "max_blocks": 66 + k % 3}, 120, u_judge(x0, y0, z0, "failed", "hazard", extra)


def u_pillar(k):
    """O86a: no pickaxe, cobblestone on her, an open 1x1 shaft over her: dig_up pillars (jump, place) 6 up: ok, reached.
    The shaft is capped one above the target, so a pillar past it would end no_tool."""
    x0, y0, z0 = PX - 40 + 6 * (k % 3), PY, -78 + MDZ
    u_block(x0, y0, z0)
    r(f"fill {x0} {y0 + 2} {z0} {x0} {y0 + 8} {z0} minecraft:air")  # the shaft over her (her head room is the pocket's)
    give([("minecraft:cobblestone", 16), ("minecraft:torch", 16), (BREAD, 16)])
    tp(x0 + 0.5, y0, z0 + 0.5, -90, 0)
    tgt = {"x": x0, "y": y0 + 6, "z": z0}

    def extra(res, ev, pos):
        return [(ev.get("reached") is True, f"reached {ev.get('reached')}, stop_reason {ev.get('stop_reason')}"),
                (pos is not None and pos[1] >= tgt["y"], f"standing at {pos} (y >= {tgt['y']})")]
    return "dig_up", {"toward": tgt, "max_blocks": 69 + k % 3}, 240, u_judge(x0, y0, z0, "ok", None, extra)


def Plans_diff(a, b):
    return {k: b.get(k, 0) - a.get(k, 0) for k in set(a) | set(b) if b.get(k, 0) != a.get(k, 0)}


SUITES = {
    "cp37": ("CP-37", [w_partial, w_multi, w_short, w_private, w_invfull, d_basic, d_topup, d_full, d_tool, d_food]),
    "cp36": ("CP-36", [c_planks, c_sticks, c_pickaxe, c_missing, c_needs_table, c_norecipe, s_iron, s_busy, s_nofuel]),
    "cp37adv": ("CP-37", [w_compete, w_private, d_full, w_invfull]),
    "cp38": ("CP-38", [h_mixed, h_mostly_young, h_no_replant]),
    "digup": ("CP-40", [u_flood, u_edge, u_lava, u_pillar]),
    "cp40p": ("CP-40", [p_ore, p_budget, p_lava, p_descend, p_grass, p_hit, p_mob, p_animal, p_litter, p_water, p_water2, p_gap, p_stepdown, p_drop, p_fillfail, p_stairdrop, p_edge, p_nofiller, p_inflow, p_inflow_big]),
    "cp39": ("CP-39", [g_wool, g_milk, g_only_protected, g_no_tool, g_wood]),
    "cp34": ("CP-34", [m_log, m_stone, m_coal, m_iron, m_iron_tag, m_no_tool, m_wrong_tool, m_not_found, m_inv_full, m_forbidden, m_worn]),
    "cp36chain": ("CP-36", [c_chain]),
    "cp36adv": ("CP-36", [s_out_full, s_fuel_short, s_taken, s_cancel, c_interrupt]),
}
if QUAL:  # reflex_arena containers are all shared by zone policy, so the private-chest refusal is a lane-only case
    SUITES = {k: (cp, [c for c in cs if c.__name__ != "w_private"]) for k, (cp, cs) in SUITES.items()}


LOCK = str(LANE / "ops" / "lane-lock.ps1")


def lane_lock(op, minutes=0):
    """eve-watch's atomic lane lock: acquire refuses (exit 1, naming the holder) while someone else holds it."""
    if QUAL:
        return  # the 45L: foundation schedules every run itself
    if __import__("os").environ.get("EVE_SERIES") == "1":
        return  # a window script holds the lock for its whole series and releases it once (builder's series.py way)
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", LOCK, op, "-By", "eve-skills"]
    if op == "acquire":
        args += ["-Minutes", str(minutes), "-What", "laneb_skills"]
    elif "--next" in sys.argv:  # I13 hand-over: reserve the lane for the named chat on release
        args += ["-Next", sys.argv[sys.argv.index("--next") + 1]]
    p = subprocess.run(args, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"lane lock {op}: {p.stdout.strip()} {p.stderr.strip()}")


MODS = next((m for m in (LANE / "src" / "parts" / "body" / "run" / "mods", LANE / "client" / "mods") if m.is_dir()),
            LANE / "src" / "parts" / "body" / "run" / "mods")  # lanes: the dev client's run/mods; the 45L: C:\eve\client\mods
BUILT = (Path(sys.argv[sys.argv.index("--jar") + 1]) if "--jar" in sys.argv  # --jar: deploy another build (replays of an old jar)
         else REPO / "parts" / "skills" / "build" / "libs" / "eve-skills-0.1.0.jar")


def jar_sha():
    import hashlib
    js = sorted(MODS.glob("eve-skills-*.jar")) if MODS.is_dir() else []
    return ", ".join(f"{j.name} {hashlib.sha256(j.read_bytes()).hexdigest()}" for j in js) or "none"


def planner_calls(since_ms):
    """O86g: the mind's model decisions (planner and objective goals; `rules` rows excluded) since since_ms, read-only from
    the lane's mind DB; None when it cannot be read (the 45L's DB is on its own host). Our runs dispatch directly (cli `do`,
    iron_kit), so 0 is expected; anything else means a planner call ran on the 0.15.0 prompt that qwen3:8b truncates."""
    import sqlite3
    try:
        c = sqlite3.connect((LANE / "mind" / "eve.db").as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            return c.execute("SELECT COUNT(*) FROM decisions WHERE model != 'rules' AND ts >= ?", (since_ms,)).fetchone()[0]
        finally:
            c.close()
    except sqlite3.Error:
        return None


def state_gap(since_ms, until_ms=None):
    """I29e/I29f: the longest gap (s) between the body's state messages in the lane mind's DB over a window (a client
    stall), as combat's harness reads it. Read at row time: the mind keeps only ~10 min of observations. None when fewer
    than 2 states or the DB cannot be read."""
    import sqlite3
    try:
        c = sqlite3.connect((LANE / "mind" / "eve.db").as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            ts = [t for (t,) in c.execute("SELECT ts FROM observations WHERE kind='state' AND ts BETWEEN ? AND ? ORDER BY ts",
                                          (since_ms, until_ms or int(time.time() * 1000)))]
        finally:
            c.close()
    except sqlite3.Error:
        return None
    return round(max(b - a for a, b in zip(ts, ts[1:])) / 1000, 2) if len(ts) > 1 else None


def export_candidates(run_dir, since, objective_id=None):
    """O94f: eve-watch's candidates_export.py copies the lane mind's eve-candidates/1 rows for this run (by objective id, else
    the run window) into <run_dir>/candidates.jsonl. It always exits 0; its last line is the report. Lanes B..E only."""
    if QUAL:
        return "candidates export: not on the 45L"
    args = [sys.executable, str(REPO / "parts" / "watch" / "laneb" / "candidates_export.py"), "--lane", LANE_ID, "--run", str(run_dir)]
    args += ["--objective-id", str(objective_id)] if objective_id else ["--since", since.isoformat(timespec="seconds"),
                                                                       "--until", datetime.now().isoformat(timespec="seconds")]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=120)
        return (p.stdout.strip().splitlines() or ["candidates export: " + (p.stderr.strip() or f"exit {p.returncode}")])[-1]
    except (OSError, subprocess.TimeoutExpired) as e:  # a learning export never fails a run
        return f"candidates export: failed ({e})"


def planner_note(since_ms):
    n = planner_calls(since_ms)
    return f"Planner calls (O86g): {n}" + ("" if n == 0 else " - NOT 0: check before counting this run")


def deploy():
    if QUAL:
        sys.exit("--deploy is for the Shark lanes; foundation deploys on the 45L (O19)")
    """eve-watch's standing permission (laneb/README "Standing permissions"): inside our own lock, swap our jar (exactly one
    eve-skills jar in mods) and restart the client with -By eve-skills; it waits for hello.ok and re-parks EveDev."""
    import shutil
    # the running client holds its jar open: stop it the way restart-client.ps1 does, swap, then restart-client starts it
    pat = "*" + str(LANE / "src" / "parts" / "body") + "*"
    stop = (f"Stop-ScheduledTask '{CLIENT_TASK}'; Get-CimInstance Win32_Process | Where-Object {{ $_.Name -in 'java.exe','javaw.exe'"
            f" -and $_.CommandLine -like '{pat}' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}")
    subprocess.run(["powershell", "-NoProfile", "-Command", stop], capture_output=True, timeout=60)
    time.sleep(3)
    for j in MODS.glob("eve-skills-*.jar"):
        j.unlink()
    shutil.copy2(BUILT, MODS / BUILT.name)
    p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LANE / "ops" / "restart-client.ps1"),
                        "-By", "eve-skills"], capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        lane_lock("release")
        sys.exit(f"restart-client failed: {p.stdout[-800:]} {p.stderr[-800:]}")


def her_pos_hp():
    pos = re.findall(r"(-?[\d.]+)d", r(f"data get entity {P} Pos"))
    hp = re.search(r"(-?[\d.]+)f", r(f"data get entity {P} Health"))
    return ([float(v) for v in pos[:3]] if len(pos) >= 3 else None), (float(hp.group(1)) if hp else None)


def over_fixture(xyz):
    """Inside or above either fixture's footprint (O35: nothing is removed while she could stand on or fall from it)."""
    x, y, z = xyz
    return any(x0 - 1 <= x <= x1 + 2 and z0 - 1 <= z <= z1 + 2 and y >= y0 - 1
               for x0, y0, z0, x1, z1 in ((PAD[0], PAD[1], PAD[2], PAD[3], PAD[5]), (MX0, MY, MZ0, MX1, MZ1),
                                          (STASH[0][0], STASH[0][1] - 1, STASH[0][2], STASH[1][0], STASH[1][2])))


def teardown_45l():
    """O35: park first (done by the caller), then confirm she is in the shelter, not over any fixture, with her health read;
    only then remove the fixture blocks. Otherwise leave them and say so (foundation removes them after parking her)."""
    for _ in range(10):
        xyz, hp = her_pos_hp()
        if xyz and not over_fixture(xyz) and hp:
            break
        time.sleep(1)
    if not xyz or over_fixture(xyz) or not hp:
        return f"TEARDOWN SKIPPED (O35): Eve at {xyz}, health {hp}; fixture blocks left in place"
    r(f"fill {PAD[0]} {PAD[1]} {PAD[2]} {PAD[3]} {PAD[4]} {PAD[5]} minecraft:air")
    r(f"fill {MX0} {MY} {MZ0} {MX1} {MY + 6} {MZ1} minecraft:air")
    return f"teardown after parking (O35): Eve at {[round(v, 1) for v in xyz]}, health {hp}; pad and mine room removed"


def fixture_check():
    """I26d: build every 45L fixture at its own coordinates (run with --geom 45L) on this lane's server by RCON, read each
    block back, prove no command wrote at or below y 210 or outside reflex_arena's x/z, then remove it all. Eve is never
    moved or used (tp is stubbed). Refuses to build unless every box is air now (compared with the same box 90 higher)."""
    global r, tp
    real_r, real_tp, sent = r, tp, []
    farm = (FX0 - 1, FY - 1, -65 + DZ, FX1 + 1, FY + 3, -61 + DZ)
    room = (MX0, MY, MZ0, MX1, MY + 6, MZ1)
    stash = (STASH[0][0], STASH[0][1] - 1, STASH[0][2], STASH[1][0], STASH[1][1], STASH[1][2])
    boxes = {"pad": PAD, "farm": farm, "room": room, "stash": stash}
    for name, (x0, y0, z0, x1, y1, z1) in boxes.items():
        real_r(f"forceload add {x0} {z0} {x1} {z1}")
        same = real_r(f"execute if blocks {x0} {y0} {z0} {x1} {y1} {z1} {x0} {y0 + 90} {z0} all")
        if "passed" not in same.lower():
            return [(False, f"{name} box {x0},{y0},{z0}..{x1},{y1},{z1} is not empty on this lane ({same.strip()[:80]}): nothing built")]
    r = lambda cmd: (sent.append(cmd), real_r(cmd))[1]  # noqa: E731
    tp = lambda *a, **k: None  # noqa: E731
    checks = []
    try:
        build_pad()
        build_farm(layout(0))
        build_room()
        for x, y, z in STASH:
            r(f"setblock {x} {y - 1} {z} minecraft:smooth_stone")
            r(f"setblock {x} {y} {z} minecraft:barrel")
        ys, xz_out = [], []
        for c in sent:
            parts = c.split()
            if parts[0] in ("fill", "setblock"):
                nums = [int(v) for v in parts[1:7 if parts[0] == "fill" else 4]]
                pts = [nums[i:i + 3] for i in range(0, len(nums), 3)]
                ys += [pt[1] for pt in pts]
                xz_out += [pt for pt in pts if not (-26 <= pt[0] <= 26 and -26 <= pt[2] <= 26)]
        checks.append((min(ys) >= 211, f"lowest y written {min(ys)} (>= 211: nothing at or below 210), {len(sent)} commands"))
        checks.append((not xz_out, f"all writes inside reflex_arena x/z -26..26 (outside: {xz_out[:3]})"))
        checks.append((PAD[2] > room[5], f"pad z {PAD[2]}..{PAD[5]} clear of the mine room z {room[2]}..{room[5]}"))
        want = [((PAD[0], PAD[1], PAD[2]), "smooth_stone"), ((PAD[3], PAD[1], PAD[5]), "smooth_stone"),
                ((PAD[0], PAD[1] + 1, PAD[2]), "glass"), (A, "chest"), (PRIV, "chest"), (F, "furnace"), (T, "crafting_table"),
                ((FX0, FY, FZ[0]), "farmland"), ((FX1, FY, FZ[1]), "farmland"), ((FX0, FY, -64 + DZ), "water"),
                ((FX0, FY - 1, -64 + DZ), "smooth_stone"), ((FX0 - 1, FY, -65 + DZ), "smooth_stone"),
                (STASH[0], "barrel"), (STASH[1], "barrel"), ((STASH[0][0], STASH[0][1] - 1, STASH[0][2]), "smooth_stone"),
                ((MX0, MY, MZ0), "smooth_stone"), ((MX1, MY + 6, MZ1), "glass"), ((MX0 + 1, MY + 1, MZ0 + 1), "glass"), ((MX0 + 1, MY + 2, MZ0 + 1), "air")]
        for pos_, b in want:
            checks.append((is_block(pos_, f"minecraft:{b}"), f"{b} at {pos_}"))
    finally:
        r, tp = real_r, real_tp
        for name, (x0, y0, z0, x1, y1, z1) in boxes.items():
            r(f"fill {x0} {y0} {z0} {x1} {y1} {z1} minecraft:air")
            r(f"forceload remove {x0} {z0} {x1} {z1}")
    return checks


def main():
    suite = sys.argv[1]
    if suite == "--fixture-check":
        lane_lock("acquire", 10)
        try:
            checks = fixture_check()
        finally:
            lane_lock("release")
        root = EVIDENCE / "fixtures"
        root.mkdir(parents=True, exist_ok=True)
        out = root / f"{datetime.now().strftime('%Y-%m-%d-%H%M')}-skills-45L-geometry-on-{LANE_ID}.md"
        ok = all(c for c, _ in checks)
        nl = chr(10)
        out.write_text(f"# skills 45L fixture geometry (I26d) read back on lane {LANE_ID}: {'PASS' if ok else 'FAIL'}{nl}{nl}"
                       f"Offsets pad {(DX, DY, DZ)}, mine room {(MDX, MDY, MDZ)}; boxes pad {PAD}, stash {STASH}, mine room "
                       f"{(MX0, MY, MZ0, MX1, MY + 6, MZ1)}. Built by RCON, read back, removed; Eve not moved.{nl}{nl}"
                       + nl.join(f"- {'PASS' if c else 'FAIL'}: {d}" for c, d in checks) + nl, encoding="utf-8")
        print(("PASS" if ok else "FAIL") + f" -> {out}")
        for c, d in checks:
            if not c:
                print("  FAIL:", d)
        return
    if suite == "--restore":  # after a crashed run: give her the stashed items back
        stash_restore()
        print("restored from the stash")
        return
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 2
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    cp, cases = SUITES[suite]
    cases = [c for c in cases if not only or c.__name__ in only]
    st = eve("status")
    if not st.get("body"):
        sys.exit(f"lane {LANE_ID} body is not up: {st}")
    lane_lock("acquire", 10 + len(cases) * n)
    root = EVIDENCE / cp
    root.mkdir(parents=True, exist_ok=True)
    num = 1 + sum(1 for d in root.iterdir() if d.is_dir() and f"-{LABEL}-attempt-" in d.name)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    out = root / f"{stamp}-{LABEL}-attempt-{num}"
    out.mkdir()
    if "--deploy" in sys.argv:
        deploy()
        st = eve("status")
    (out / "inventory-before.snbt").write_text(r(f"data get entity {P} Inventory"), encoding="utf-8")
    stash_save()
    rows, t0 = [], time.time()
    # --k0 replays another run's fixtures: case k is (trial + k0) % 40, and that run's k0 was 3 * its attempt number
    # (CP-38 45L attempt 1 = --k0 3: its round 2 was layout 4 for every case)
    k0 = int(sys.argv[sys.argv.index("--k0") + 1]) if "--k0" in sys.argv else 3 * num
    try:
        r("difficulty peaceful")
        (build_room if suite in ("cp34", "cp39") else build_pad)()
        for k in range(n):
            for case in cases:
                # the mind refuses params that failed 3 times in 10 min (retry_limit): every run uses its own sizes
                skill, params, wait, judge, *during = case((k + k0) % 40)  # sizes stay inside every case's limits
                time.sleep(0.5)
                i0, b0 = inv(), {p: box(p) for p in (A, B, C, PRIV, F)}
                t_trial = int(time.time() * 1000)
                th = None
                if during:  # interference while the action runs: another player at the container, an owner cancel
                    th = threading.Thread(target=during[0], daemon=True)
                    th.start()
                res = do(skill, params, wait)
                if th:  # never into the next case (cp40p a12: p_hit ended early, its /damage then landed in p_mob)
                    th.join(30)
                time.sleep(0.5)
                i1, b1 = inv(), {p: box(p) for p in (A, B, C, PRIV, F)}
                ok, detail = judge(res, i0, i1, b0, b1)
                verdict = "PASS" if ok else ("INVALID" if invalid(res) else "FAIL")
                if verdict == "INVALID":
                    detail = f"INVALID (O46: {invalid(res)}) - " + detail
                row = {"case": case.__name__, "trial": k + 1, "t_end": round(time.time(), 1), "max_state_gap_s": state_gap(t_trial),
                       "ok": ok, "verdict": verdict, "detail": detail, "skill": skill,
                       "params": params, "action_id": res.get("action_id"), "result": res, "inv_before": i0, "inv_after": i1,
                       "boxes_after": {str(p): b1[p] for p in b1}}
                rows.append(row)
                print(f"{verdict} {case.__name__} #{k + 1}: {detail} ({res.get('_elapsed_s')} s, state gap {row['max_state_gap_s']} s)", flush=True)
    finally:
        stash_restore()
        r(f"kill @e[tag={PEN_TAG}]")  # CP-39 pen animals (summoned by the fixture) never outlive the run
        r(f"kill @e[tag={PEN_TAG}_z]")  # p_mob's zombie
        r(f"kill @e[tag={PEN_TAG}_c]")  # p_animal's cow
        r(f"kill @e[tag={PEN_TAG}_s]")  # p_fillfail's armour stand
        r("difficulty peaceful")  # p_mob raises it for one case
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LANE / "ops" / "park-eve.ps1"),
                        "-Root", str(SERVER), "-Player", P], capture_output=True)
        teardown_note = None
        if QUAL:  # the 45L: nothing of ours stays in reflex_arena (body's and combat's fixtures share it)
            teardown_note = teardown_45l()
        r(f"forceload remove {PAD[0]} {PAD[2]} {PAD[3]} {PAD[5]}")
        r(f"forceload remove {MX0} {MZ0} {MX1} {MZ1}")
        r(f"forceload remove {STASH[0][0]} {STASH[0][2]} {STASH[1][0]} {STASH[1][2]}")
        lane_lock("release")
    (out / "rows.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
    passed = sum(x["ok"] for x in rows)
    n_invalid = sum(x["verdict"] == "INVALID" for x in rows)
    reg = (st.get("registry") or {})
    lines = [f"# {cp} — eve-skills lane {LANE_ID} (`{'45L' if QUAL else 'shark'}`) {stamp} attempt {num} (suite `{suite}`): {passed}/{len(rows) - n_invalid} valid trials"
             + (f" ({n_invalid} INVALID, O46)" if n_invalid else ""), "",
             f"Hypothesis: the skills move/craft/smelt exactly the requested counts through real menus, and every refusal or "
             f"shortage leaves the world as the server shows it, with the matching failure code.",
             f"Fixture: lane {LANE_ID} (eve-watch's Shark stack {LANE}), real mind {CLI_PORT} via /cli, real eve-sense, {P}; floating pad y=150 in "
             f"the home zone; stations A{A} B{B} C{C} (shared) P{PRIV} (private) F{F} T{T}. Judge: RCON `data get` before/after.",
             f"Versions: repo {git_head()} (eve-skills jar in the client: {jar_sha()}), registry {reg.get('version')} {str(reg.get('hash'))[:12]}. Elapsed {time.time() - t0:.0f} s. {planner_note(int(t0 * 1000))}. {export_candidates(out, datetime.fromtimestamp(t0))}. Fixture seeds k = trial + {k0} (mod 40). "
             f"Envelope caps (O45): {break_cap()}. Deaths 0 (peaceful), interventions 0 unless noted. "
             + ("Qualification lane (45L). " + (teardown_note or "") if QUAL else "Development evidence, not qualification."), "",
             "| case | trial | ok | max state gap s (I29e) | detail |", "|---|---|---|---|---|"]
    lines += [f"| {x['case']} | {x['trial']} | {x['verdict']} | {x['max_state_gap_s']} | {x['detail']} |" for x in rows]
    text = "\n".join(lines) + "\n"
    (out / "RESULT.md").write_text(text, encoding="utf-8")
    summ = REPO / "parts" / "skills" / "evidence-summaries" / f"{cp}-{stamp}-{LABEL}-attempt-{num}.md"
    summ.parent.mkdir(parents=True, exist_ok=True)  # absent in a `git archive parts/skills/tools` extract (the 45L)
    summ.write_text(text, encoding="utf-8")
    print(f"{passed}/{len(rows) - n_invalid} ({n_invalid} invalid) -> {out}")


def break_cap():
    """O45: the run's break/place caps as the lane's envelope (mind copy) states them; every RESULT names them."""
    try:
        env = json.loads((LANE / "mind" / "config" / "envelope.json").read_text(encoding="utf-8"))
        lim = env.get("limits", {})
        return f"blocks_broken_per_hour {lim.get('blocks_broken_per_hour')}, blocks_placed_per_hour {lim.get('blocks_placed_per_hour')}"
    except Exception as e:  # never let bookkeeping crash a finished run
        return f"unknown ({type(e).__name__})"


def git_head():
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()


if __name__ == "__main__":
    main()
