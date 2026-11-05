"""eve-combat fixtures on lane B (eve-watch's Shark stack: C:\\eve-shark, real mind on 8955, real eve-sense, dev account EveDev).
Every fight goes through the mind's /cli (`eve do fight_melee`, the owner-command path); the judge is the server (RCON), never
the skill's own evidence: the zombies are gone (killed, not despawned: PersistenceRequired), EveDev's death counter is unchanged,
her server health is sampled every 0.25 s for the minimum, and every protected bystander is alive at full health.

    py -3.12 parts/combat/tools/laneb_combat.py cp26|cp26v|cp28 [--lane c|b|45L] [--n 20] [--only case,case] [--deploy] [--floor 10]
    ... --lane-config parts/body/tools/lanes/F.json [--dry-run] [--expect-jar <sha>]   (O104j: lane F on the 45L, EveDevF, cli 8962)

45L (foundation runs it ON the 45L under O19, like body's tools/laneb.py --lane 45L): `git archive <commit> parts/combat/tools`
extracted to e.g. C:\\eve\\combat-tools, then with C:\\eve\\mind\\parts\\mind\\.venv\\Scripts\\python.exe run
C:\\eve\\combat-tools\\parts\\combat\\tools\\laneb_combat.py cp26 --lane 45L. Account FED_Chairman, mind 8951, token
C:\\eve\\mind\\config\\eve-link-token.txt, RCON via C:\\eve\\ops\\rcon.py (C:\\eve\\server-dev\\server.properties). Arena inside envelope
zone reflex_arena (x -22..24 y 204..210 z -14..16: clear of home's overlap z<-16, above body's sky arena at y 199..202), removed again
at the end. No lane lock and no jar swap there (foundation schedules and deploys). Health floor 10 by default (--floor): below
it every mob in the arena is killed and the trial FAILS, so a lost fight never becomes a death on her real account. Evidence:
evidence/CP-NN/<stamp>-45L-attempt-N (rows.jsonl written trial by trial, RESULT.md); nothing outside the extracted folder is read.

Lanes (eve-watch's table in parts/watch/STATUS.md): c = C:\\eve-shark-c (combat's default lane, zone combat_arena x 40..100
y 140..175 z 20..80, mind 8956); b = C:\\eve-shark (mind 8955, sky over the home zone, 44 blocks above eve-skills' pad).
Arena: a roofed, lantern-lit 17x17 box (no sun burn, no spawns), built by RCON. Normal difficulty only for the run (O15), then peaceful and re-parked.
Kit K1 (fixed): iron sword in hotbar 0, full iron armour, 16 bread in hotbar 8, nothing in the off hand (see STATUS: the body's
melee shield reflex would pause every swing). Every trial is counted; a death is a failure, never "recovered".
Results: evidence/CP-NN/<stamp>-shark-combat-attempt-N/ (gitignored) and parts/combat/evidence-summaries/ (committed).
"""
import asyncio, hashlib, json, math, re, shutil, socket, sqlite3, subprocess, sys, threading, time
from datetime import datetime
from pathlib import Path

LANES = {
    "c": (Path(r"C:\eve-shark-c"), "secrets/eve-link-token.txt", 8956, "EveDev2", (55, 85, 35, 65, 150)),  # combat_arena, 31x31 (CP-28 a1: 15x15 left no room to back off); roof 156
    "b": (Path(r"C:\eve-shark"), "secrets/eve-link-token.txt", 8955, "EveDev", (-44, -28, -76, -60, 200)),
    "d": (Path(r"C:\eve-shark-d"), "secrets/eve-link-token.txt", 8958, "EveDev3", (55, 85, 35, 65, 150)),  # lane D, same arena as C
    "e": (Path(r"C:\eve-shark-e"), "secrets/eve-link-token.txt", 8960, "EveDev4", (55, 85, 35, 65, 150)),  # lane E, same arena as C
    "45L": (Path(r"C:\eve"), "mind/config/eve-link-token.txt", 8951, "FED_Chairman", (-22, 24, -14, 16, 204)),  # reflex_arena, 47x31 (bow cases to 25, CP-28's 31-wide group)
}
# O104j: a lane from a config file (body's tools/lanes/F.json: label, root, token_file, cli_port, player, evidence_root, host,
# body_jar, pins_file), as body's laneb. Lane F on the 45L has the same combat_arena as the Shark lanes, so the C/D/E arena.
LC = json.loads(Path(sys.argv[sys.argv.index("--lane-config") + 1]).read_text(encoding="utf-8")) if "--lane-config" in sys.argv else {}
if LC:
    LANES[LC["label"]] = (Path(LC["root"]), LC["token_file"], int(LC["cli_port"]), LC["player"], (55, 85, 35, 65, 150))
LANE_ID = LC.get("label") or (sys.argv[sys.argv.index("--lane") + 1] if "--lane" in sys.argv else "c")
LANE, TOKEN_FILE, PORT, P, (X0, X1, Z0, Z1, Y) = LANES[LANE_ID]
LIVE_ROOT = Path(r"C:\eve")
if LC and (LIVE_ROOT in (LANE, *LANE.parents) or P == "FED_Chairman"):
    sys.exit(f"O104: a configured lane must never be live Eve's tree or account (root {LANE}, player {P}); nothing done")
QUAL = LANE_ID == "45L"
# O72a: --wide (or a k_follow_shallow run) stretches a lane arena east to the 45L's 47x31 shape; the 45L is already that shape
if not QUAL and ("--wide" in sys.argv or "k_follow_shallow" in " ".join(sys.argv)):
    X1 = X0 + 46
LABEL = "45L" if QUAL else f"45L-{LANE_ID}" if LC.get("host") == "45l" else "shark-combat"  # evidence dir: <stamp>-<LABEL>-attempt-N
EVIDENCE = Path(LC["evidence_root"]) if LC.get("evidence_root") else Path(__file__).resolve().parents[3] / "evidence"
FLOOR_HP = float(sys.argv[sys.argv.index("--floor") + 1]) if "--floor" in sys.argv else (10.0 if QUAL else 0.0)
SERVER = LANE / "server-dev"


def dry_run():
    """O104j: every path, bind and pin a run on this lane would use, checked read-only (no token read, no RCON, no hub, nothing
    launched), before `import rcon`. One line per check; exit 0 only when all hold, else 6 (body's laneb convention)."""
    rows = []

    def check(name, ok, detail=""):
        rows.append((bool(ok), name, str(detail)))

    check("lane root", LANE.is_dir(), LANE)
    check("not live Eve's tree", LIVE_ROOT not in (LANE, *LANE.parents), LANE)
    check("not live Eve's account", P != "FED_Chairman", P)
    sp = SERVER / "server.properties"
    props = dict(l.split("=", 1) for l in sp.read_text().splitlines() if "=" in l and not l.startswith("#")) if sp.is_file() else {}
    check("server.properties", sp.is_file(), sp)
    check("rcon enabled", props.get("enable-rcon") == "true", f"rcon.port {props.get('rcon.port')}")
    if LC:
        check("server bound to 127.0.0.1", props.get("server-ip") == "127.0.0.1", props.get("server-ip"))
        check("lane-local cli port", PORT != 8951, PORT)
    for rel in (TOKEN_FILE, "mind/eve.db", "ops/rcon.py", "ops/safe_tp.py", "ops/lane-lock.ps1", "ops/park-eve.ps1"):
        check(rel, (LANE / rel).exists(), LANE / rel)
    env = LANE / "mind" / "config" / "envelope.json"
    zone = next((z for z in json.loads(env.read_text(encoding="utf-8")).get("zones", []) if z.get("id") == "combat_arena"), None) if env.is_file() else None
    check("envelope combat_arena", zone, env)
    if zone:  # the arena with its roof (Y..Y+8) and the stash column at X1+1
        lo, hi = zone["min"], zone["max"]
        check("arena inside combat_arena", lo["x"] <= X0 and X1 + 1 <= hi["x"] and lo["z"] <= Z0 and Z1 <= hi["z"] and lo["y"] <= Y and Y + 8 <= hi["y"],
              f"x {X0}..{X1 + 1} y {Y}..{Y + 8} z {Z0}..{Z1} in {lo} {hi}")
    mods = [m for m in (LANE / "src" / "parts" / "body" / "run" / "mods", LANE / "client" / "mods", LANE / "client" / ".minecraft" / "mods") if m.is_dir()]
    jars = sorted(mods[0].glob("eve-combat-*.jar")) if mods else []
    shas = [hashlib.sha256(j.read_bytes()).hexdigest() for j in jars]
    check("combat jar in the client's mods", jars, f"{mods[0] if mods else 'no mods dir'}: " + (", ".join(f"{j.name} {h[:12]}" for j, h in zip(jars, shas)) or "none"))
    if "--expect-jar" in sys.argv:
        want = sys.argv[sys.argv.index("--expect-jar") + 1].lower()
        check("--expect-jar", any(h.startswith(want) for h in shas), f"want {want[:12]}")
    check("evidence root", EVIDENCE.is_dir() or EVIDENCE.parent.is_dir(), EVIDENCE)
    pins = {}
    if LC.get("pins_file"):  # lane F: eve-watch's roll writes C:\eve-lanes\pins\laneF.json (body, mind, registry)
        pf = Path(LC["pins_file"])
        check("pins file (written by the roll)", pf.is_file(), pf)
        if pf.is_file():
            pj = json.loads(pf.read_text(encoding="utf-8"))
            reg = str(pj.get("registry", "")).split()
            pins = {"body": pj.get("body"), "mind": pj.get("mind"), "registry": reg[-1] if reg else None}
            check("pins name a mind and a body", pins["mind"] and pins["body"], f"mind {pins['mind']} body {str(pins['body'])[:12]}")
    if LC.get("body_jar"):
        jar = Path(LC["body_jar"])
        sha = hashlib.sha256(jar.read_bytes()).hexdigest() if jar.is_file() else None
        check("body jar pin", sha and pins.get("body") and sha.startswith(str(pins["body"]).lower()),f"{jar} {sha and sha[:12]} want {str(pins.get('body'))[:12]}")
    if pins.get("mind"):
        g = subprocess.run(["git", "-C", str(LANE / "src"), "diff", "--ignore-cr-at-eol", "--quiet", pins["mind"], "--", "parts/mind/src"], capture_output=True)
        check("mind src pin", g.returncode == 0, f"{pins['mind']} (git exit {g.returncode})")
    if pins.get("registry"):
        reg = LANE / "src" / "contracts" / "skills" / "registry.json"
        h = hashlib.sha256(json.dumps(json.loads(reg.read_text(encoding="utf-8")), sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False).encode("utf-8")).hexdigest() if reg.is_file() else None
        check("registry pin", h and h.startswith(pins["registry"]), f"{h and h[:12]} want {pins['registry'][:12]}")
    lh = Path(__file__).resolve().parents[3] / "parts" / "watch" / "laneb" / "lane_health.py"
    check("lane_health (I29f)", lh.is_file(), f"{lh} --headroom --host {LC.get('host', 'shark')} --by combat")
    if LC and "--deploy" in sys.argv:
        check("no --deploy on a configured lane", False, "the roll places the combat jar")
    for ok, name, detail in rows:
        print(("OK   " if ok else "FAIL ") + f"{name}: {detail}")
    bad = [n for ok, n, _ in rows if not ok]
    print(f"dry-run {LANE_ID} ({P}, cli {PORT}): {len(rows) - len(bad)}/{len(rows)} checks hold" + (f"; FAIL: {', '.join(bad)}" if bad else ""))
    sys.exit(0 if not bad else 6)


if "--dry-run" in sys.argv:
    dry_run()
sys.path.insert(0, str(LANE / "ops"))
import rcon  # noqa: E402  foundation's RCON client, lane-B copy
try:  # I28a (eve-watch): world-changing commands need the lane's lock; reads pass. The 45L has no lane_guard.
    from lane_guard import check as _lane_guard  # noqa: E402
except ImportError:
    _lane_guard = None
from websockets.asyncio.client import connect  # noqa: E402
try:  # O39: every player teleport through the lane's shared helper (eve-watch); the 45L has none yet -> same checks below
    from safe_tp import safe_tp as _safe_tp  # noqa: E402
except ImportError:
    _safe_tp = None

REPO = Path(__file__).resolve().parents[3]
TOKEN = (LANE / TOKEN_FILE).read_text().strip()
CLI = f"ws://127.0.0.1:{PORT}/cli"
BY = "eve-combat"  # P (Eve's dev player) is per lane: EveDev2 on lane C since 16:25 (eve-watch), EveDev on lane B
CX, CZ = (X0 + X1) // 2, (Z0 + Z1) // 2  # floor at Y, she stands at Y+1
SEL = f"x={X0},y={Y},z={Z0},dx={X1 - X0},dy=8,dz={Z1 - Z0}"
MOBTAG, PROTTAG = "evec", "evep"


_RC, _RL = {"s": None}, threading.Lock()


def r(cmd):
    """One RCON connection for the run (O72f: a32 died on WinError 10048 after 121 s; rcon.command opens a socket per call and the
    0.1 s Watch left ~5000 in TIME_WAIT across the lanes). Player teleports keep rcon.command so the lane's O39 guard sees them;
    the 45L's rcon has no _player_tp, so there every call stays on rcon.command as before."""
    if _lane_guard:
        _lane_guard(LANE, cmd)  # raises PermissionError when refused (our own socket bypasses rcon.py's guards)
    if getattr(rcon, "_player_tp", lambda c: True)(cmd):
        return rcon.command(SERVER, cmd)
    with _RL:
        for fresh in (False, True):
            if _RC["s"] is None:
                p = rcon.props(SERVER)
                s = socket.create_connection(("127.0.0.1", int(p["rcon.port"])), timeout=10)
                s.sendall(rcon.packet(1, 3, p["rcon.password"]))
                if rcon.recv(s)[0] == -1:
                    s.close()
                    raise PermissionError("rcon login refused")
                _RC["s"], fresh = s, True
            try:
                _RC["s"].sendall(rcon.packet(2, 2, cmd))
            except OSError:  # a stale socket: reconnect once and resend (nothing reached the server)
                _RC["s"].close()
                _RC["s"] = None
                if fresh:
                    raise
                continue
            try:
                return rcon.recv(_RC["s"])[2]
            except (OSError, ConnectionError):  # sent but no reply: never resend (a summon would run twice)
                _RC["s"].close()
                _RC["s"] = None
                raise


async def _cli(cmd, args, timeout):
    async with connect(CLI, open_timeout=3, max_size=4 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"token": TOKEN, "cmd": cmd, "args": args}))
        return json.loads(await asyncio.wait_for(ws.recv(), timeout))


def eve(cmd, args=None, timeout=60):
    try:
        return asyncio.run(_cli(cmd, args or {}, timeout))
    except Exception as e:  # mind restarting: recorded as the trial's result, never a crash
        return {"ok": False, "error": "cli_exception", "detail": f"{type(e).__name__}: {e}"}


def num(text, default=None):
    m = re.search(r"(-?\d+(?:\.\d+)?)[fdbsL]?\s*$", text.strip())
    return float(m.group(1)) if m else default


def health():
    return num(r(f"data get entity {P} Health"))


def deaths():
    """Server deathCount ("EveDev2 has 1 [evedeaths]"; no score yet = 0). CP-28 attempt 1: an end-anchored parse read 0 after a death."""
    m = re.search(r"has (\d+) \[", r(f"scoreboard players get {P} evedeaths"))
    return int(m.group(1)) if m else 0


def score(objective):
    m = re.search(r"has (\d+) \[", r(f"scoreboard players get {P} {objective}"))
    return int(m.group(1)) if m else 0


def creeper_kills():
    return score("evekc")


def count(tag):
    m = re.search(r"passed\. Count: (\d+)", r(f"execute if entity @e[tag={tag}]") or "")  # "Test failed" = none
    return int(m.group(1)) if m else 0


# Full inventory stash: every slot copied exactly (components: enchantments, names, damage) into two barrels outside the arena
# with `item replace ... from entity`, and copied back afterwards. A crashed run leaves her items in the barrels, and the next run
# refuses to start until they are restored (`--restore`). On the 45L the barrels stand at (26, 205, -13/-12): INSIDE body's clear box
# (it reaches x 26, y 210; I26a). Body now excludes these two cells, and foundation's 45L pre-check guards a body run after a crash of ours.
SLOTS = [f"hotbar.{i}" for i in range(9)] + [f"inventory.{i}" for i in range(27)] +         ["armor.head", "armor.chest", "armor.legs", "armor.feet", "weapon.offhand"]
STASH_X = 26 if QUAL else X1 + 1
STASH = [(STASH_X, Y + 1, Z0 + 1), (STASH_X, Y + 1, Z0 + 2)]  # 2 x 27 slots >= 41


def _stash_slot(i):
    (x, y, z) = STASH[i // 27]
    return f"{x} {y} {z} container.{i % 27}"


def stash_items():
    """Number of non-empty stash slots (0 if the barrels are absent)."""
    n = 0
    for (x, y, z) in STASH:
        out = r(f"data get block {x} {y} {z} Items")
        n += len(re.findall(r'id: "', out)) if "Items" in out or "id:" in out else 0
    return n


def stash_save():
    if stash_items():
        sys.exit("the inventory stash still holds a previous run's items: run with --restore first (nothing was changed)")
    r(f"forceload add {STASH_X} {Z0} {STASH_X} {Z0 + 2}")
    for (x, y, z) in STASH:
        r(f"setblock {x} {y} {z} minecraft:barrel")
    for i, s in enumerate(SLOTS):
        r(f"item replace block {_stash_slot(i)} from entity {P} {s}")


def stash_restore():
    """Copy every slot back exactly (an empty stash slot empties hers), then remove the barrels."""
    r(f"forceload add {STASH_X} {Z0} {STASH_X} {Z0 + 2}")
    r(f"clear {P}")  # the kit goes first, so a slot that was empty before the run ends empty even if copying air is refused
    for i, s in enumerate(SLOTS):
        r(f"item replace entity {P} {s} from block {_stash_slot(i)}")
    for (x, y, z) in STASH:
        r(f"data merge block {x} {y} {z} {{Items:[]}}")
        r(f"setblock {x} {y} {z} minecraft:air")
    r(f"forceload remove {STASH_X} {Z0} {STASH_X} {Z0 + 2}")


def players_in_fixture():
    """Players inside the arena volume or anywhere above it (to the build limit): no block of it may be removed then."""
    out = r(f"execute if entity @a[x={X0 - 1},y={Y - 2},z={Z0 - 1},dx={X1 - X0 + 2 + (0 if QUAL else 10)},dy={320 - Y},dz={Z1 - Z0 + 2}]")  # +10: cp29's open-end platform
    m = re.search(r"passed\. Count: (\d+)", out or "")
    return int(m.group(1)) if m else 0


def park():
    """Eve out of the fixture: mobs gone, healed, parked in the O15 shelter (park-eve.ps1: teleport + peaceful)."""
    r(f"kill @e[tag={MOBTAG}]"); r("kill @e[tag=evecr]")
    r(f"effect give {P} minecraft:instant_health 1 4 true")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LANE / "ops" / "park-eve.ps1"),
                    "-Root", str(SERVER), "-Player", P], capture_output=True, timeout=120)
    time.sleep(1.0)


def safe_teardown(remove):
    """45L CP-28 attempt 1: the arena floor was removed with Eve on it at 9.99 HP; she fell 17 blocks to 0.15 HP. Now she is
    parked first, her health checked, and `remove` (the block teardown) runs only when no player is inside or above the
    fixture volume. Returns what happened (recorded in RESULT.md)."""
    park()
    hp = health()
    if hp is not None and hp < 10:
        r(f"effect give {P} minecraft:instant_health 1 4 true")
    inside = players_in_fixture()
    if inside:
        return f"teardown SKIPPED: {inside} player(s) inside/above the fixture after parking (blocks left in place)"
    remove()
    return f"parked (hp {hp}), fixture empty of players, blocks removed"


def build_arena():
    r(f"forceload add {X0} {Z0} {X1} {Z1}")
    r(f"fill {X0} {Y} {Z0} {X1} {Y + 6} {Z1} minecraft:smooth_stone")
    r(f"fill {X0 + 1} {Y + 1} {Z0 + 1} {X1 - 1} {Y + 5} {Z1 - 1} minecraft:air")
    for x in range(X0 + 2, X1 - 1, 4):
        for z in range(Z0 + 2, Z1 - 1, 4):
            r(f"setblock {x} {Y} {z} minecraft:sea_lantern")
    r("scoreboard objectives add evedeaths deathCount")
    r("scoreboard objectives add evekc minecraft.killed:minecraft.creeper")  # creepers killed BY Eve (an explosion is not a kill)
    r("scoreboard objectives add evekz minecraft.killed:minecraft.zombie")  # O280: zombies credited to Eve (the game credits her if she hit one in the last 5 s)
    r("scoreboard objectives add evedd minecraft.custom:minecraft.damage_dealt")  # O280: all her damage, auto_fight included (the stat is x 10)


def clear_arena():
    r(f"kill @e[type=!player,{SEL}]")
    r(f"fill {X0 + 1} {Y + 1} {Z0 + 1} {X1 - 1} {Y + 5} {Z1 - 1} minecraft:air")


PARITY = sys.argv[sys.argv.index("--kit-parity") + 1] if "--kit-parity" in sys.argv else None  # O326: full | no-bow | no-shield
PARITY_KIT = {}


NON_BLOCKS = ("sword", "pickaxe", "_axe", "shovel", "hoe", "bow", "arrow", "shield", "bread", "helmet", "chestplate", "leggings", "boots",
              "raw_", "stick", "ingot", "coal", "apple", "beef", "pork", "chicken", "mutton", "potato", "carrot", "torch", "bucket")


def parity_load():
    """O326: Eve's live 45L kit, slot for slot, read once per run over ssh (read-only, watch's kit_parity.py); kit() then lays it each trial."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "watch" / "laneb"))
    import kit_parity, host45l
    live = host45l.last_json(host45l.run45(kit_parity.SRC, timeout=40))
    PARITY_KIT.update({"from_45l_ts": live["ts"], "variant": PARITY, "inventory": live["inventory"],
                       "commands": kit_parity.commands(P, [i for i in live["inventory"] if PARITY != "no-blocks" or any(w in i["item"] for w in NON_BLOCKS)],
                                                       no_bow=PARITY == "no-bow", no_shield=PARITY == "no-shield")})


def kit():
    if PARITY:
        for c in PARITY_KIT["commands"]:
            r(c)
        r(f"effect clear {P}")
        r(f"effect give {P} minecraft:instant_health 1 10 true")
        r(f"effect give {P} minecraft:saturation 1 20 true")
        return
    r(f"clear {P}")
    for slot, item in (("hotbar.0", "iron_sword"), ("armor.head", "iron_helmet"), ("armor.chest", "iron_chestplate"),
                       ("armor.legs", "iron_leggings"), ("armor.feet", "iron_boots"), ("hotbar.8", "bread 16")):
        r(f"item replace entity {P} {slot} with minecraft:{item}")
    r(f"effect clear {P}")
    r(f"effect give {P} minecraft:instant_health 1 10 true")
    r(f"effect give {P} minecraft:saturation 1 20 true")


def tp(x, y, z, yaw=0, pitch=0):
    """Teleport Eve's feet to (x, y, z) only onto a solid floor with air at feet and head (O39; refuses = SystemExit, she is
    not moved). Lane ops' safe_tp also checks the envelope zone and logs; without it (the 45L today) the floor/air check runs here."""
    if _safe_tp:
        _safe_tp(SERVER, P, x, y, z, yaw, pitch)
        return
    if QUAL:  # the 45L has C:\eve\ops\safe_tp.py (foundation); never fall back to a raw tp on her real account
        raise SystemExit(r"safe_tp not importable from C:\eve\ops: refusing to teleport on the 45L")
    bx, by, bz = math.floor(x), math.floor(y), math.floor(z)
    if "passed" in r(f"execute if block {bx} {by - 1} {bz} #minecraft:replaceable") or             "passed" not in r(f"execute if block {bx} {by} {bz} #minecraft:air") or "passed" not in r(f"execute if block {bx} {by + 1} {bz} #minecraft:air"):
        raise SystemExit(f"unsafe teleport refused: {P} -> {x} {y} {z} (needs a solid floor and air at feet and head)")
    r(f"tp {P} {x} {y} {z} {yaw} {pitch}")


def stand(yaw):
    tp(CX + 0.5, Y + 1, CZ + 0.5, yaw, 0)
    time.sleep(1.0)


ARCHERS = ("skeleton", "stray", "bogged")
SPAWNS = []  # I37: what every threat spawned this trial held in its main hand, read right after the spawn (into the row)


def armed(kind, tag):
    """summon with NBT skips finalizeSpawn, so no default equipment: a summoned skeleton has no bow and fights melee (body a95:
    0 arrows in 35 trials). Give archers their bow (that also makes them reassess to their bow goal), then I37: read the main
    hand back and record it; an archer without its bow raises, which ends the run ABORTED (= INVALID), never a silent trial."""
    sel = f"@e[tag={tag},type=minecraft:{kind}]"
    if kind in ARCHERS:
        r(f"item replace entity {sel} weapon.mainhand with minecraft:bow")  # all of them: re-arming is harmless
    one = f"@e[tag={tag},type=minecraft:{kind},limit=1]"
    raw = r(f"data get entity {one} equipment.mainhand.id")  # 26.3's mob equipment
    if "found no elements" in raw.lower():
        raw = r(f"data get entity {one} HandItems[0].id")  # the pre-1.21.5 name, in case
    held = raw.split("entity data: ", 1)[-1].strip().strip('"') if "entity data: " in raw else f"(none: {raw[:80]})"
    SPAWNS.append({"kind": kind, "HandItems": held})  # key named as eve-watch's I37 verdict looks for it
    if kind in ARCHERS and held != "minecraft:bow":
        raise RuntimeError(f"I37: a summoned {kind} reads HandItems {held}, not minecraft:bow")
    if kind in ARCHERS:  # every one of them (c_volley spawns two): any archer without the bow
        unarmed = r(f'execute if entity @e[tag={tag},type=minecraft:{kind},nbt=!{{equipment:{{mainhand:{{id:"minecraft:bow"}}}}}}]')
        if unarmed.startswith("Test passed"):  # 26.3 equipment.mainhand; a missing path also lands here: loud, never silent
            raise RuntimeError(f"I37: a summoned {kind} is not holding its bow ({unarmed}; HandItems read {SPAWNS[-1]['HandItems']})")


DMG = (("evedmg", "minecraft.custom:minecraft.damage_taken"), ("eveblk", "minecraft.custom:minecraft.damage_blocked_by_shield"))


def damage_reset():
    """O102: incoming damage per trial from the server's own stats (tenths of a point), zeroed before the fight."""
    for name, crit in DMG:
        r(f"scoreboard objectives add {name} {crit}")  # 'already exists' after the first: harmless
        r(f"scoreboard players set {P} {name} 0")


def damage_read():
    def score(name):
        try:
            return int(r(f"scoreboard players get {P} {name}").split(" has ", 1)[1].split(" ", 1)[0]) / 10
        except (IndexError, ValueError):
            return None
    me = r(f"data get entity {P} UUID")
    ints = me.split("[I;", 1)[-1].rstrip("]").replace(" ", "") if "[I;" in me else None
    left = r(f"execute if entity @e[type=minecraft:arrow,{SEL}" + (f",nbt=!{{Owner:[I;{ints}]}}" if ints else "") + "]")
    return {"damage_taken": score("evedmg"), "damage_blocked": score("eveblk"),
            "foreign_arrows_left": int(left.rsplit(" ", 1)[-1]) if left.startswith("Test passed") else 0}


def mob(kind, dx, dz, tag=MOBTAG):
    r(f'summon minecraft:{kind} {CX + 0.5 + dx} {Y + 1} {CZ + 0.5 + dz} {{Tags:["{tag}"],PersistenceRequired:1b,CanPickUpLoot:0b}}')
    armed(kind, tag)


def zombie(dx, dz):
    r(f'summon minecraft:zombie {CX + 0.5 + dx} {Y + 1} {CZ + 0.5 + dz} {{Tags:["{MOBTAG}"],PersistenceRequired:1b,CanPickUpLoot:0b}}')


def at(dist, deg):
    """Offset at dist blocks, deg clockwise from south (+z, where yaw 0 faces)."""
    a = math.radians(deg)
    return -math.sin(a) * dist, math.cos(a) * dist


# ---- cases: setup(k) -> (params, wait, n_mobs, protected [(tag, max_health)]) ----
def m_flat(k):
    stand(0); zombie(*at(5, 0))
    return {"max_targets": 1, "radius": 16}, 90, 1, []


def m_behind(k):
    stand(0); zombie(*at(4, 150 + 20 * (k % 4)))
    return {"max_targets": 1, "radius": 16}, 90, 1, []


def m_side(k):
    stand(0); zombie(*at(3, 90 if k % 2 else -90))
    return {"max_targets": 1, "radius": 16}, 90, 1, []


def m_far(k):
    stand(0); zombie(*at(7, (k * 37) % 360))  # the arena's corners are ~11 away; 7 is the far end on every heading
    return {"max_targets": 1, "radius": 16}, 90, 1, []


def m_wall(k):
    """A 3-wide, 3-high wall between them: she must walk round it (walk_to) before the fight."""
    stand(0)
    r(f"fill {CX - 1} {Y + 1} {CZ + 3} {CX + 1} {Y + 3} {CZ + 3} minecraft:stone")
    time.sleep(0.5)  # the client must have the wall before it sees the zombie (a13: auto_fight started at 6.0 m in 3/5)
    zombie(*at(6, 0))
    return {"max_targets": 1, "radius": 16}, 90, 1, []


def g_five(k):
    """CP-28 original arena: five zombies in an arc 6-7 blocks out, a named pig and a villager (zombies hunt villagers, so it
    stands in a 1-block glass cell beside the arc: inside the sweep box of a zombie next to it, unreachable for them)."""
    stand(0)
    r(f"item replace entity {P} weapon.offhand with minecraft:shield")  # kit K2 = K1 + a fresh shield every trial
    for i, deg in enumerate((-60, -30, 0, 30, 60)):
        zombie(*at(6 + i % 2, deg))
    px, pz = at(5, 45)
    r(f'summon minecraft:pig {CX + 0.5 + px} {Y + 1} {CZ + 0.5 + pz} {{Tags:["{PROTTAG}"],CustomName:"Bacon",PersistenceRequired:1b}}')
    vx, vz = (round(v) for v in at(6, -45))
    r(f"fill {CX + vx - 1} {Y + 1} {CZ + vz - 1} {CX + vx + 1} {Y + 3} {CZ + vz + 1} minecraft:glass")
    r(f"fill {CX + vx} {Y + 1} {CZ + vz} {CX + vx} {Y + 2} {CZ + vz} minecraft:air")
    r(f'summon minecraft:villager {CX + vx + 0.5} {Y + 1} {CZ + vz + 0.5} {{Tags:["{PROTTAG}"],PersistenceRequired:1b,NoAI:1b}}')
    # floor 12 = the pass line: a losing fight disengages as a counted FAIL instead of a death (attempts 1 and 3 died)
    return {"radius": 16, "max_targets": 5, "health_floor": 12}, 110, 5, [("pig", 10), ("villager", 20)]


def n_enderman(k):
    """O83d (CP-31 a9 attempt 11 death): a zombie 5 ahead and a calm enderman 3 to her side (nearer than the zombie, so the old
    `instanceof Enemy` nearest-first pick fought it). Unnamed, so body does not protect it; NoAI, so her crosshair cannot anger it
    and the case tests only the target rule. Pass: zombie dead, enderman at full health (never hit), no death."""
    stand(0)
    zombie(*at(5, 0))
    ex, ez = at(3, 90 if k % 2 else -90)
    r(f'summon minecraft:enderman {CX + 0.5 + ex} {Y + 1} {CZ + 0.5 + ez} {{Tags:["{PROTTAG}"],PersistenceRequired:1b,NoAI:1b}}')
    return {"max_targets": 2, "radius": 16}, 90, 1, [("enderman", 40)]


def _mixed(extra, tag=MOBTAG):
    stand(0)
    r(f"item replace entity {P} weapon.offhand with minecraft:shield")
    for deg in (-40, 0, 40):
        zombie(*at(6, deg))
    mob(extra, *at(9, 180 if extra == "creeper" else 20), tag=tag)
    return {"radius": 16, "max_targets": 5, "health_floor": 12}, 110, 4 if tag == MOBTAG else 3, []


def x_skeleton(k):
    """CP-28 mixed threats: 3 zombies and a skeleton shooting from 9 (ranged; she must close on it between the zombies)."""
    return _mixed("skeleton")


def x_archer(k):
    """O102v (option A's bar): one armed skeleton at 12 in the open arena, K2 (sword, shield, iron armour), floor 12. Pass = killed
    within 30 s, no death, min HP >= 12 (no floor retreat). Isolates closing on a kiting archer from x_skeleton's zombie group."""
    stand(0)
    r(f"item replace entity {P} weapon.offhand with minecraft:shield")
    mob("skeleton", *at(12, 0))
    return {"radius": 16, "max_targets": 1, "health_floor": 12}, 30, 1, []


def x_wall(k):
    """O126 (Hunter live: fight_melee froze on a mob behind a wall): a zombie sealed in a stone cell 6 ahead (no way in: body's
    walk_to never breaks blocks), K1 kit. fight_melee in group mode. Pass = ends failed `unreachable` within 10 s with the owner
    line, no freeze, no death. The zombie is left alive (the next trial's clear_arena removes it and the cell)."""
    stand(0)
    zx, zz = at(6, 0)
    x, z = CX + int(zx), CZ + int(zz)
    r(f"fill {x - 1} {Y + 1} {z - 1} {x + 1} {Y + 4} {z + 1} minecraft:stone hollow")  # 1x1x2 air inside, floor at Y + 1
    r(f'summon minecraft:zombie {x + 0.5} {Y + 2} {z + 0.5} {{Tags:["{MOBTAG}"],PersistenceRequired:1b,CanPickUpLoot:0b}}')
    return {"radius": 16, "max_targets": 1}, 20, 1, []


def x_spider(k):
    """3 zombies and a spider (fast, jumps)."""
    return _mixed("spider")


def x_creeper(k):
    """3 zombies and a creeper behind her: fight_melee never targets creepers; body's creeper_flee reflex (priority 4) takes
    over, and the fight resumes after. Only the zombies are counted; the creeper (tag evecr) is removed after the trial."""
    return _mixed("creeper", tag="evecr")


# ---- CP-27 bow half: Eve near the west wall facing east (+x), the target out along +x ----
def _bow_setup():
    r(f"item replace entity {P} hotbar.1 with minecraft:bow")
    r(f"item replace entity {P} inventory.0 with minecraft:arrow 32")
    tp(X0 + 3.5, Y + 1, CZ + 0.5, -90, 0)
    time.sleep(1.0)


def _bow_target(kind, dist, dz=0):
    r(f'summon minecraft:{kind} {X0 + 3.5 + dist} {Y + 1} {CZ + 0.5 + dz} {{Tags:["{MOBTAG}"],PersistenceRequired:1b,CanPickUpLoot:0b}}')
    armed(kind, MOBTAG)


def b_8(k):
    _bow_setup(); _bow_target("skeleton", 8, (k % 3) - 1)
    return {"radius": 30, "max_arrows": 16}, 100, 1, []


def b_14(k):
    _bow_setup(); _bow_target("skeleton", 14, (k % 5) - 2)
    return {"radius": 30, "max_arrows": 16}, 100, 1, []


def b_22(k):
    _bow_setup(); _bow_target("skeleton", 22, (k % 5) - 2)
    return {"radius": 30, "max_arrows": 16}, 100, 1, []


def b_wall(k):
    """A 3x3 wall halfway: no line of sight from the start; she walks round it (walk_to) and shoots."""
    _bow_setup()
    r(f"fill {X0 + 10} {Y + 1} {CZ - 1} {X0 + 10} {Y + 3} {CZ + 1} minecraft:stone")
    _bow_target("skeleton", 14)
    return {"radius": 30, "max_arrows": 16}, 100, 1, []


def x_fence(k):
    """O116 (body's floor_pen, reflex.py c7ce9feb, CP-20 a105: 14 arrows into a fence): an oak-fence ring (collider 1.5 high) with
    its +x face 2.5 from her centre as in floor_pen, an armed skeleton 9 out, K3 bow kit. Eye-to-eye sight passes over the fence;
    the centre shot's falling path does not. Pass = killed by her, no death (block_holds recorded)."""
    _bow_setup()  # she stands at X0 + 3.5, facing +x
    r(f"fill {X0 + 1} {Y + 1} {CZ - 3} {X0 + 6} {Y + 1} {CZ + 3} minecraft:oak_fence hollow")  # +x face at X0 + 6: 2.5 out
    _bow_target("skeleton", 9)  # armed + I37 recorded there
    return {"radius": 30, "max_arrows": 16}, 100, 1, []


def b_pig(k):
    """A protected named pig (NoAI, so it stays put) 0.6 off the line of fire, halfway: she must hold and sidestep, never hit it."""
    _bow_setup()
    side = 0.6 if k % 2 else -0.6
    r(f'summon minecraft:pig {X0 + 3.5 + 7} {Y + 1} {CZ + 0.5 + side} {{Tags:["{PROTTAG}"],CustomName:"Bacon",PersistenceRequired:1b,NoAI:1b}}')
    _bow_target("skeleton", 14)
    return {"radius": 30, "max_arrows": 16}, 100, 1, [("pig", 10)]


def b_moving(k):
    """A zombie walking at her from 20: a moving target (lead). She must kill it before it is within 3 (too_close)."""
    _bow_setup(); _bow_target("zombie", 20, (k % 3) - 1)
    return {"radius": 30, "max_arrows": 16}, 100, 1, []


def b_creeper(k):
    """A creeper walking at her from 16: shoot it from beyond 7 (inside 7, in sight, body's creeper_flee pre-empts everything).
    Wins only if Eve killed it (server minecraft.killed:creeper score): a creeper that explodes is never a win."""
    _bow_setup(); _bow_target("creeper", 16, (k % 3) - 1)
    return {"radius": 30, "max_arrows": 16}, 100, 1, []


# ---- CP-27 combined fixture (ruling O32): no shield while the bow is drawn. Defences while shooting = dodge, armour, range;
# the off-hand shield works once she switches to the sword inside 3. One fight_ranged call per target, repeated by the harness
# (as the mind would) until the arena is clear.
def c_volley(k):
    """Two skeletons shooting at her from 14 and 18 while she shoots back."""
    _bow_setup(); r(f"item replace entity {P} weapon.offhand with minecraft:shield")
    _bow_target("skeleton", 14, -4); _bow_target("skeleton", 18, 5)
    return {"radius": 30, "max_arrows": 16}, 110, 2, []


def c_rush(k):
    """A skeleton at 16 and two zombies rushing from 10: she shoots, then the sword (with the shield) when they arrive."""
    _bow_setup(); r(f"item replace entity {P} weapon.offhand with minecraft:shield")
    _bow_target("skeleton", 16); _bow_target("zombie", 10, -3); _bow_target("zombie", 10, 3)
    return {"radius": 30, "max_arrows": 16}, 110, 3, []


def hut_wool(place=False):
    """O284: the "build": a 3x3x3 wool hut near her; its wool count (fill counts changed blocks, so swap and swap back)."""
    x, z = CX - 9, CZ + 6
    if place:
        r(f"fill {x} {Y + 1} {z} {x + 2} {Y + 3} {z + 2} minecraft:white_wool")
    m = re.search(r"(\d+) block", r(f"fill {x} {Y + 1} {z} {x + 2} {Y + 3} {z + 2} minecraft:light_gray_wool replace minecraft:white_wool"))
    r(f"fill {x} {Y + 1} {z} {x + 2} {Y + 3} {z + 2} minecraft:white_wool replace minecraft:light_gray_wool")
    return int(m.group(1)) if m else 0


def _creeper_case(arrows, creepers=((9, 180),), wall=False, wait=110):
    """O284/O301: 3 zombies ahead of her, creeper(s) at (distance, deg), a bow and arrows, a hut, griefing on."""
    r("gamerule mob_griefing true")
    hut_wool(place=True)
    stand(0)
    if not PARITY:
        r(f"item replace entity {P} weapon.offhand with minecraft:shield")
        r(f"item replace entity {P} hotbar.1 with minecraft:bow")
        r(f"item replace entity {P} inventory.0 with minecraft:arrow {arrows}")
    elif arrows < 32:  # creeper_low on the parity kit: her own bow, only `arrows` arrows
        r(f"clear {P} minecraft:arrow")
        r(f"give {P} minecraft:arrow {arrows}")
    if wall:  # opaque, 7 wide, between her and the creeper: it has to walk round
        r(f"fill {CX - 3} {Y + 1} {CZ - 4} {CX + 3} {Y + 3} {CZ - 4} minecraft:smooth_stone")
    for deg in (-40, 0, 40):
        zombie(*at(6, deg))
    for dist, deg in creepers:
        mob("creeper", *at(dist, deg), tag="evecr")
    return {"radius": 16, "max_targets": 5, "health_floor": 12}, wait, 3, []


def creeper_first(k):
    """O284's bow case. Bar n=10: no creeper exploded, hut undamaged, no death, every zombie gone."""
    return _creeper_case(32)


def creeper_two(k):
    """O301: two creepers in bow range. Same bar as creeper_first. Body's reflex only looks at the nearest one."""
    return _creeper_case(32, creepers=((9, 180), (9, 110)))


def creeper_close(k):
    """O301: first seen inside bow range, at 4 m: flee first, then the bow. Bar: no death, hut undamaged, every zombie gone; a blast is recorded, not failed."""
    return _creeper_case(32, creepers=((4, 180),))


def creeper_low(k):
    """O301: 3 arrows, under the minimum: she must flee, not stand and click. Bar: no death, not one arrow shot, a flee happened, hut undamaged."""
    return _creeper_case(3, wait=30)


def creeper_cover(k):
    """O301: a wall between them, so it is unseen until it walks round. Same bar as creeper_first."""
    return _creeper_case(32, wall=True)


def sealed_skeleton(k):
    """Live 13:15 on the 45L: sealed in a stone box with a skeleton 7 m off outside it. No fight dispatched: bar = auto_fight never starts."""
    stand(0)
    r(f"fill {CX - 2} {Y + 1} {CZ - 2} {CX + 2} {Y + 4} {CZ + 2} minecraft:stone hollow")
    time.sleep(0.5)  # the client must have the box before it sees the skeleton (m_wall a13)
    mob("skeleton", *at(7, 0))
    return {}, 60, 1, []  # O313: zero engagements in 60 s


def sealed_gap(k):
    """O318: the same box with a one-block gap at eye height facing the skeleton, so she can see it and cannot reach it. Sword and shield, no bow.
    The skeleton has no AI: a32's walked up to the gap and she killed it through it, so the fight ended ok and body's backstop never ran; this one cannot come or shoot.
    No fight dispatched: bar = at most 2 auto_fight starts in 60 s, and at least one (a fight that never starts tests nothing)."""
    stand(0)
    r(f"item replace entity {P} weapon.offhand with minecraft:shield")
    r(f"fill {CX - 2} {Y + 1} {CZ - 2} {CX + 2} {Y + 4} {CZ + 2} minecraft:stone hollow")
    r(f"setblock {CX} {Y + 2} {CZ + 2} minecraft:air")
    time.sleep(0.5)
    dx, dz = at(7, 0)
    r(f'summon minecraft:skeleton {CX + 0.5 + dx} {Y + 1} {CZ + 0.5 + dz} {{Tags:["{MOBTAG}"],PersistenceRequired:1b,NoAI:1b}}')
    return {}, 60, 1, []


def auto_fight_starts(offset):
    """The reason of every auto_fight start in the lane client's log since `offset` (read before the spawn; the DB count missed a32's)."""
    try:
        with open(LANE / "logs" / "client.log", "rb") as fh:
            fh.seek(offset)
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    return re.findall(r'auto_fight start \{.*?"reason":"(\w+)"', text)


def _tunnel():
    """O326: an L of 1x2 tunnel cut in stone: leg A along +x (CZ, x CX..CX+9), then leg B along +z (x CX, z CZ..CZ+10). From B at CZ+2
    there are only 2 m straight behind her before the corner, so no 7 m run; the creeper comes down B."""
    r(f"fill {CX - 1} {Y + 1} {CZ - 1} {CX + 10} {Y + 3} {CZ + 11} minecraft:stone")
    r(f"fill {CX} {Y + 1} {CZ} {CX + 9} {Y + 2} {CZ} minecraft:air")
    r(f"fill {CX} {Y + 1} {CZ} {CX} {Y + 2} {CZ + 10} minecraft:air")
    r(f"fill {CX} {Y} {CZ - 1} {CX + 9} {Y} {CZ + 10} minecraft:smooth_stone")
    time.sleep(0.5)


def _tunnel_creeper(dist, lit):
    """O332: calm at 4 m, or primed (Fuse 30, ignited) at 3 m, straight down leg B from her at CZ+2.5."""
    extra = ",ignited:1b" if lit else ""
    r(f'summon minecraft:creeper {CX + 0.5} {Y + 1} {CZ + 2.5 + dist} {{Tags:["evecr"],PersistenceRequired:1b{extra}}}')


def _t_walk_in():
    _tunnel()
    tp(CX + 9.5, Y + 1, CZ + 0.5, 90, 0)
    time.sleep(0.5)
    eve("do", {"skill": "walk_to", "params": {"pos": {"x": CX + 0.5, "y": Y + 1, "z": CZ + 2.5}, "tolerance": 0.6}, "wait": 25}, 40)


def _t_put_in():
    _tunnel()
    tp(CX + 0.5, Y + 1, CZ + 2.5, 0, 0)
    time.sleep(1.0)


def t_trail(k):
    """O326/O332 (a): she walks in along A and round the corner to B (a 10 m trail, only 2 m straight behind her), then a calm creeper 4 m
    down B. Expect creeper_flee's trail branch (trail_retreat / trail_shielded). No fight dispatched; 30 s watch."""
    _t_walk_in(); _tunnel_creeper(4, False)
    return {}, 30, 0, []


def t_trail_lit(k):
    """O332: as t_trail, the creeper primed at 3 m."""
    _t_walk_in(); _tunnel_creeper(3, True)
    return {}, 15, 0, []


def t_block(k):
    """O326/O332 (b): put straight into B (no trail), a calm creeper 4 m down B. Expect a block between them (wall_1 / wall_2) when a hotbar
    block exists, else the shield. No fight dispatched; 30 s watch."""
    _t_put_in(); _tunnel_creeper(4, False)
    return {}, 30, 0, []


def t_block_lit(k):
    """O332: as t_block, the creeper primed at 3 m."""
    _t_put_in(); _tunnel_creeper(3, True)
    return {}, 15, 0, []


def blasts_since(since_ms):
    """Explosion damage to her since since_ms (lane mind DB): (amount, source angle deg, blocking). Body a148: front and 90 deg take 0, behind 10.3."""
    try:
        con = sqlite3.connect(f"file:{(LANE / 'mind' / 'eve.db').as_posix()}?mode=ro", uri=True, timeout=5)
        rows = [json.loads(p).get("detail") or {} for (p,) in con.execute("SELECT payload FROM events WHERE kind='event' AND ts >= ?", (since_ms,))]
        con.close()
    except sqlite3.Error:
        return None
    return [(round(d.get("amount") or 0, 2), d.get("source_angle_deg"), d.get("blocking")) for d in rows if "explosion" in str(d.get("cause"))]


def flee_ends(offset):
    """creeper_flee end lines in the lane client's log since `offset`: (result, detail) each."""
    try:
        with open(LANE / "logs" / "client.log", "rb") as fh:
            fh.seek(offset)
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    for m in re.finditer(r"creeper_flee end (\w+)[^{\n]*(\{.*)", text):
        try:
            out.append((m.group(1), json.loads(m.group(2))))
        except ValueError:
            out.append((m.group(1), {}))
    return out


def arrows_held():
    m = re.search(r"Found (\d+)", r(f"clear {P} minecraft:arrow 0") or "")
    return int(m.group(1)) if m else 0


def flee_counts(since_ms):
    """O301: this trial's creeper_flee starts from the lane mind's DB: how many, and how many carried body's claimed: true (a claim overridden by a primed creeper)."""
    try:
        con = sqlite3.connect(f"file:{(LANE / 'mind' / 'eve.db').as_posix()}?mode=ro", uri=True, timeout=5)
        rows = [json.loads(p).get("detail") or {} for (p,) in con.execute("SELECT payload FROM events WHERE kind='event' AND ts >= ?", (since_ms,))]
        con.close()
    except sqlite3.Error:
        return None, None
    starts = [d for d in rows if d.get("reflex") == "creeper_flee" and d.get("phase") == "start"]
    return len(starts), sum(bool((d.get("trigger") or {}).get("claimed")) for d in starts)


def combat_log_since(offset):
    """O301: the combat jar's shot and claim lines since `offset`, from the lane client's log: shot distances, claimed creeper ids. Empty on a jar without them."""
    try:
        with open(LANE / "logs" / "client.log", "rb") as fh:
            fh.seek(offset)
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return [], []
    return [float(x) for x in re.findall(r"eve-combat shot dist=([0-9.]+)", text)], sorted(set(re.findall(r"eve-combat claim creeper=(\d+)", text)))


def client_log_size():
    try:
        return (LANE / "logs" / "client.log").stat().st_size
    except OSError:
        return 0


def x_creeper_r(k):
    """Mixed threat with the bow (orchestrator, after a11): 3 zombies + a creeper. fight_ranged first (a creeper in sight is its
    first target), then fight_melee for the zombies. Win = Eve killed the creeper (server score) and every zombie is gone."""
    stand(0)
    r(f"item replace entity {P} weapon.offhand with minecraft:shield")
    r(f"item replace entity {P} hotbar.1 with minecraft:bow")
    r(f"item replace entity {P} inventory.0 with minecraft:arrow 32")
    for deg in (-40, 0, 40):
        zombie(*at(7, deg))
    mob("creeper", *at(12, 20), tag="evecr")
    return {"radius": 16, "max_targets": 5, "health_floor": 12}, 110, 3, []


O41A = [("stone_pickaxe", 1), ("stone_axe", 1), ("stone_sword", 1), ("bread", 16), ("torch", 16), ("crafting_table", 1),
        ("oak_planks", 8)]  # CP-40's starting kit (parts/skills/tools/ironkit.py), no armour, no shield


def k_tunnel(k):
    """O58 (CP-40 a9 death): Eve at the closed end of a 1-wide, 2-high tunnel with the O41a kit; a zombie comes in from the
    open end. No room to circle or back off, no shield: stand and swing."""
    r(f"clear {P}")
    for it, n in O41A:
        r(f"give {P} minecraft:{it} {n}")
    r(f"fill {CX - 1} {Y + 1} {CZ - 1} {CX + 1} {Y + 3} {CZ + 10} minecraft:stone")
    r(f"fill {CX} {Y + 1} {CZ} {CX} {Y + 2} {CZ + 10} minecraft:air")  # open at z CZ+10
    tp(CX + 0.5, Y + 1, CZ + 0.5, 0, 0)
    time.sleep(1.0)
    r(f'summon minecraft:zombie {CX + 0.5} {Y + 1} {CZ + 8.5} {{Tags:["{MOBTAG}"],PersistenceRequired:1b,CanPickUpLoot:0b}}')
    return {"max_targets": 1, "radius": 16}, 90, 1, []


SUITES = {
    "cp26": ("CP-26", [m_flat]),
    "cp26v": ("CP-26", [m_behind, m_side, m_far, m_wall]),
    "cp28": ("CP-28", [g_five]),
    # mixed threats: separate win/retreat limits (CP-28), not the arena pass; every trial records outcome win/retreat/death
    "cp28m": ("CP-28", [x_skeleton, x_spider, x_creeper, x_archer, x_wall]),
    "sealed": ("CP-28", [sealed_skeleton, sealed_gap]),
    "cp29t": ("CP-29", [t_trail, t_trail_lit, t_block, t_block_lit]),  # O326/O332 cave rule (body 73b0251e+)
    "cp29b": ("CP-29", [creeper_first, creeper_two, creeper_close, creeper_low, creeper_cover]),  # O284/O301: the creeper goes first, with the bow
    "cp28mr": ("CP-28", [x_creeper_r]),  # the creeper case with the bow (fight_ranged, then fight_melee)
    "cp27": ("CP-27", [b_8, b_14, b_22, b_wall, b_pig, b_moving, b_creeper, x_fence]),
    "cp27c": ("CP-27", [c_volley, c_rush]),
    "cp40t": ("CP-40", [k_tunnel]),
    "cp83": ("CP-31", [n_enderman]),  # O83d: a calm neutral mob is never a target  # O58: fight_melee in a 1-wide tunnel, CP-40 kit
}


TICK_RATE = int(sys.argv[sys.argv.index("--tick-rate") + 1]) if "--tick-rate" in sys.argv else None


def x_creeper_decided(rows):
    """O293: the x_creeper gate stops once it has failed: any trial off the O259 bar, a death, the 3rd explosion, or the 3rd trial she did not fight. None = still open."""
    xs = [x for x in rows if x["case"] == "x_creeper"]
    blasts = sum(x.get("creeper") == "exploded" for x in xs)
    idle = sum(not (((x.get("result") or {}).get("evidence") or {}).get("swings") or x.get("eve_damage")) for x in xs)
    off = sum(not x["ok"] for x in xs)
    return f"O293 gate decided FAIL after {len(xs)}: off the bar {off}, explosions {blasts}, did not fight {idle}" if off or blasts >= 3 or idle >= 3 else None


def x_creeper_gate(rows):
    """O280's series bar for x_creeper: the O259 bar every trial, n=10, creeper explosions <= 2, she fought in >= 8 (dispatched swings, or server damage for auto_fight)."""
    xs = [x for x in rows if x["case"] == "x_creeper"]
    if not xs:
        return []
    blasts = sum(x.get("creeper") == "exploded" for x in xs)
    fought = sum(bool(((x.get("result") or {}).get("evidence") or {}).get("swings") or x.get("eve_damage")) for x in xs)
    good = len(xs) >= 10 and all(x["ok"] for x in xs) and blasts <= 2 and fought >= 8
    return [f"O280 x_creeper gate: {'PASS' if good else 'FAIL'}: O259 bar {sum(x['ok'] for x in xs)}/{len(xs)}, creeper exploded {blasts} (<= 2), fought in {fought} (>= 8), "
            f"zombies credited to Eve {sum(x.get('zombies_by_eve') or 0 for x in xs)} of {3 * len(xs)}, her damage {sum(x.get('eve_damage') or 0 for x in xs):.1f}.", ""]


def protected_ok(expect):
    """Every protected bystander alive at full health (server read)."""
    out = []
    for kind, full in expect:
        hp = num(r(f"data get entity @e[tag={PROTTAG},type=minecraft:{kind},limit=1] Health"))
        out.append((hp is not None and hp >= full, f"{kind} health {hp} (want {full})"))
    return out


class Sampler(threading.Thread):
    """Server health every 0.25 s while the fight runs: the minimum is the server's, not the skill's."""

    def __init__(self):
        super().__init__(daemon=True)
        self.min, self.stop, self.floor_hit = None, threading.Event(), None

    def run(self):
        while not self.stop.is_set():
            try:
                h = health()
                if h is not None:
                    self.min = h if self.min is None else min(self.min, h)
                    if FLOOR_HP and h < FLOOR_HP and self.floor_hit is None:  # end the trial, never a death (true-survival account)
                        self.floor_hit = h
                        r(f"kill @e[tag={MOBTAG}]"); r("kill @e[tag=evecr]")  # never the protected bystanders
            except Exception:
                pass
            time.sleep(0.25)


LOCK = str(LANE / "ops" / "lane-lock.ps1")


def lane_lock(op, minutes=0):
    if QUAL:
        return  # the 45L: foundation schedules every run itself
    if LC:  # lane F: lanef-fire.ps1 up holds the lock for the whole series (acquire would be refused); only check it is ours
        held = (LANE / "LANE-IN-USE.txt").read_text(encoding="utf-8", errors="replace") if (LANE / "LANE-IN-USE.txt").is_file() else ""
        if op == "acquire" and f"by={BY}" not in held:
            sys.exit(f"lane {LANE_ID}: not up for {BY} (lanef-fire.ps1 up -By {BY} first); lock: {held.strip() or 'free'}")
        return
    args =["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", LOCK, op, "-By", BY]
    if op == "acquire":
        args += ["-Minutes", str(minutes), "-What", "laneb_combat"]
    if op == "release" and "--next" in sys.argv:  # I13 hand-over: the named chat gets the lane next (reserved a few minutes)
        args += ["-Next", sys.argv[sys.argv.index("--next") + 1]]
    p = subprocess.run(args, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"lane lock {op}: {p.stdout.strip()} {p.stderr.strip()}")


MODS = next((m for m in (LANE / "src" / "parts" / "body" / "run" / "mods", LANE / "client" / "mods", LANE / "client" / ".minecraft" / "mods")
             if m.is_dir()), LANE / "src" / "parts" / "body" / "run" / "mods")
BUILT = Path(sys.argv[sys.argv.index("--jar") + 1]) if "--jar" in sys.argv else REPO / "parts" / "combat" / "build" / "libs" / "eve-combat-0.1.0.jar"  # --jar: a baseline build (O72d)


def jar_sha():
    js = sorted(MODS.glob("eve-combat-*.jar")) if MODS.is_dir() else []
    return ", ".join(f"{j.name} {hashlib.sha256(j.read_bytes()).hexdigest()}" for j in js) or "none"


def state_gap(since_ms, until_ms=None):
    """I29e: the longest gap (s) between the body's state messages in the lane mind's DB over a trial window: a client
    stall. Read at row time, because the mind keeps only ~10 min of observations. None when fewer than 2 states."""
    try:
        con = sqlite3.connect(f"file:{(LANE / 'mind' / 'eve.db').as_posix()}?mode=ro", uri=True, timeout=5)
        ts = [t for (t,) in con.execute("SELECT ts FROM observations WHERE kind='state' AND ts BETWEEN ? AND ? ORDER BY ts",
                                        (since_ms, until_ms or int(time.time() * 1000)))]
        con.close()
    except sqlite3.Error:
        return None
    return round(max(b - a for a, b in zip(ts, ts[1:])) / 1000, 2) if len(ts) > 1 else None


def export_candidates(run, t0, t1=None):
    """O94f (eve-watch): append the lane mind's eve-candidates/1 rows for this run's window to <run>/candidates.jsonl. Lanes
    only (the 45L's mind journal is foundation's); the exporter always exits 0, and a failure here never fails a run."""
    if LANE_ID.upper() not in ("B", "C", "D", "E"):  # candidates_export.py knows the Shark lanes only
        return f"candidates: skipped on lane {LANE_ID}"
    iso = lambda t: datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")
    cmd = [sys.executable, str(REPO / "parts" / "watch" / "laneb" / "candidates_export.py"), "--lane", LANE_ID.upper(),
           "--run", str(run), "--since", iso(t0), "--until", iso(t1 or time.time())]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return "candidates: " + ((r.stdout or r.stderr).strip().splitlines() or ["no output"])[-1][:300]
    except Exception as e:  # noqa: BLE001
        return f"candidates: export failed: {type(e).__name__}: {e}"


class StubLink:
    """O102 (b): skills only the 0.16.0 draft declares (fight_melee.doorway, harvest_animal) run on body's dev client (built with
    -Peve.registry=<draft>) connected to body's stub_mind Hub (EVE_REGISTRY = the same draft, EVE_HUB_PORT, EVE_LINK_TOKEN) instead
    of the lane mind. This drives the Hub in-process: dispatch, entity ids and state gaps come from its frames. At start it
    refuses unless the client's registry hash matches the stub's and the draft declares `skill` (and `param`)."""

    def __init__(self, skill, param=None):
        sys.path.insert(0, str(REPO / "parts" / "body" / "tools"))
        from stub_mind import Hub  # body's tool, read-only use
        self.hub = Hub(verbose=False).start()
        end = time.time() + 90
        while self.hub.conn is None and time.time() < end:
            time.sleep(0.5)
        if self.hub.conn is None:
            sys.exit("--stub: no client connected to the stub hub in 90 s (is the dev client pointed at EVE_HUB_PORT?)")
        hello = next((f["msg"] for f in self.hub.frames if f["msg"].get("type") == "hello"), {})
        body_hash = hello.get("payload", {}).get("registry_hash")
        sk = next((s for s in self.hub.reg["skills"] if s["name"] == skill), None)
        if body_hash != self.hub.hash or sk is None or (param and param not in sk["params"]["properties"]):
            sys.exit(f"--stub: registry mismatch or no {skill}{'.' + param if param else ''} (body {body_hash}, stub {self.hub.hash[:12]})")
        self.registry = self.hub.hash

    def states(self, since_t=0):
        return [f for f in self.hub.frames if f["dir"] == "in" and f["msg"].get("type") == "state" and f["t"] >= since_t]

    def net_id(self, u, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            for f in reversed(self.states(time.time() - 2)):
                for e in f["msg"]["payload"].get("nearby_entities") or []:
                    if e.get("uuid") == u:
                        return e["id"]
            time.sleep(0.3)
        return None

    def do(self, skill, params, timeout):
        since = len(self.hub.frames)
        aid = self.hub.request(skill, params)
        res = self.hub.wait_result(aid, timeout, since) or {"status": "no_result"}
        return {**res, "status": res.get("status"), "code": res.get("code"), "evidence": res.get("evidence") or {}}

    def gap(self, t0):
        ts = [f["t"] for f in self.states(t0)]
        return round(max(b - a for a, b in zip(ts, ts[1:])), 2) if len(ts) > 1 else None


def body_sha():
    """O83d: the body jar the client runs (mods on the 45L; the lane's runClient loads its own build/libs), for the evidence header."""
    if LC.get("body_jar"):  # O104j: a configured lane names its body jar
        j = Path(LC["body_jar"])
        return f"{j.name} {hashlib.sha256(j.read_bytes()).hexdigest()}" if j.is_file() else "none"
    js = sorted(MODS.glob("eve-body-*.jar")) if MODS.is_dir() else []
    js = js or sorted((LANE / "src" / "parts" / "body" / "build" / "libs").glob("eve-body-*.jar"))
    return ", ".join(f"{j.name} {hashlib.sha256(j.read_bytes()).hexdigest()}" for j in js) or "none"


def check_jar():
    """--expect-jar <sha256> (O35): refuse to start unless the client's mods hold exactly that eve-combat jar."""
    if "--expect-jar" not in sys.argv:
        return
    want = sys.argv[sys.argv.index("--expect-jar") + 1].lower()
    have = jar_sha()
    if want not in have:
        sys.exit(f"--expect-jar {want[:12]}: the client has {have} (mods {MODS}); not running")
    if "--expect-body" in sys.argv:  # O86b: the body bytes are part of what a series proves
        want = sys.argv[sys.argv.index("--expect-body") + 1].lower()
        have = body_sha()
        if want not in have:
            sys.exit(f"--expect-body {want[:12]}: the lane has {have}; not running")


def restart_client(respawn=False):
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LANE / "ops" / "restart-client.ps1"), "-By", BY]
    p = subprocess.run(args + (["-Respawn"] if respawn else []), capture_output=True, text=True, timeout=300)
    return p.returncode == 0, (p.stdout[-600:] + p.stderr[-600:])


def deploy():
    if QUAL or LC:
        sys.exit("--deploy is for the Shark lanes; on the 45L (live or lane F) the roll places the combat jar")
    """Same standing-permission shape as eve-skills (asked of eve-watch for eve-combat): inside our own lock, swap exactly one
    eve-combat jar into the lane-B client's mods and restart it."""
    task = "Eve Shark Client" + ("" if LANE_ID == "b" else " " + LANE_ID.upper())
    stop = (f"Stop-ScheduledTask '{task}'; Get-CimInstance Win32_Process | Where-Object {{ $_.Name -in 'java.exe','javaw.exe'"
            f" -and $_.CommandLine -like '*{LANE}\\src\\parts\\body*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}")
    subprocess.run(["powershell", "-NoProfile", "-Command", stop], capture_output=True, timeout=60)
    time.sleep(3)
    for j in MODS.glob("eve-combat-*.jar"):
        j.unlink()
    shutil.copy2(BUILT, MODS / BUILT.name)
    ok, out = restart_client()
    if not ok:
        lane_lock("release")
        sys.exit(f"restart-client failed: {out}")


def expect_ok(res):
    st = res.get("status") if res.get("ok") is not False else "refused"
    return st == "ok", f"result {st}/{res.get('code') or res.get('error')}"


def reset():
    """Undo what a run changes, for a run that was killed before its `finally`. The lane's baseline is --baseline
    difficulty=..,spawn_mobs=..,mob_griefing=.. (default peaceful/true/true: what F's first runs read at their start). What it
    finds first is printed, so the record shows the state the cut run left."""
    base = {"difficulty": "peaceful", "spawn_mobs": "true", "mob_griefing": "true"}
    if "--baseline" in sys.argv:
        base.update(kv.split("=", 1) for kv in sys.argv[sys.argv.index("--baseline") + 1].split(","))
    tags = (MOBTAG, PROTTAG, "evecr", "evedr")
    lane_lock("acquire", 10)
    try:  # O43: every lane RCON, reads too, under the lane's lock
        r("tick rate 20")  # O293: a cut tick trial must not leave the lane fast
        found = {"difficulty": r("difficulty"), "spawn_mobs": r("gamerule spawn_mobs"), "mob_griefing": r("gamerule mob_griefing"),
                 "stash": bool(stash_items()), **{t: r(f"execute if entity @e[tag={t}]") for t in tags}}
        print("found:", json.dumps(found))
        for t in tags:  # anywhere in the world, not only the arena box: the cut trial's mobs may have walked out
            r(f"kill @e[tag={t}]")
        teardown = safe_teardown(clear_arena)  # parks her first (park-eve.ps1, peaceful), removes blocks only with nobody inside
        if found["stash"]:
            stash_restore()  # her pre-run inventory, stashed by the cut run
        r(f"gamerule spawn_mobs {base['spawn_mobs']}")
        r(f"gamerule mob_griefing {base['mob_griefing']}")
        r(f"difficulty {base['difficulty']}")
        r(f"forceload remove {X0} {Z0} {X1} {Z1}")
        print(f"reset lane {LANE_ID}: tagged mobs killed, {teardown}, stash {'restored' if found['stash'] else 'empty'}, "
              f"difficulty {base['difficulty']}, spawn_mobs {base['spawn_mobs']}, mob_griefing {base['mob_griefing']}")
    finally:
        lane_lock("release")


def main():
    suite = sys.argv[1]
    if suite == "--restore" or "--restore" in sys.argv:  # after a crashed run: give her the stashed items back
        stash_restore()
        print("inventory restored from the stash")
        return
    if suite == "reset":  # O102ah: the `finally` a cut run never reached (killed mid-trial on an order), under our lock
        reset()
        return
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 20
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    cp, cases = SUITES[suite]
    cases = [c for c in cases if not only or c.__name__ in only]
    st = eve("status")
    if not st.get("body"):
        sys.exit(f"lane {LANE_ID} body is not up: {st}")
    check_jar()
    lane_lock("acquire", 15 + len(cases) * n * 2)
    root = EVIDENCE / cp
    root.mkdir(parents=True, exist_ok=True)
    num_ = 1 + sum(1 for d in root.iterdir() if d.is_dir() and f"-{LABEL}-attempt-" in d.name)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    out = root / f"{stamp}-{LABEL}-attempt-{num_}"
    out.mkdir()
    rows_file = out / "rows.jsonl"
    if PARITY:
        parity_load()
        (out / "kit.json").write_text(json.dumps(PARITY_KIT, indent=1), encoding="utf-8")
    if "--deploy" in sys.argv:
        deploy()
        st = eve("status")
    rows, t0, aborted = [], time.time(), None
    stash_save()
    (out / "inventory-before.snbt").write_text(r(f"data get entity {P} Inventory") + "\n" + r(f"data get entity {P} equipment"), encoding="utf-8")
    was = {"difficulty": r("difficulty").rsplit(" ", 1)[-1].strip().lower(), "spawn_mobs": r("gamerule spawn_mobs").rsplit(" ", 1)[-1].strip(),
           "mob_griefing": r("gamerule mob_griefing").rsplit(" ", 1)[-1].strip()}
    try:
        build_arena()
        r("gamerule spawn_mobs false")  # no natural spawns anywhere while the world is on normal; /summon still works
        r("difficulty peaceful")  # 26.3 recomputes spawn flags only on a real difficulty change (I20): the next "normal" is one
        r("gamerule mob_griefing false")  # a creeper blast breaks no blocks (mixed-threat suite)
        r("difficulty normal")  # O15: only for the run; restored and re-parked (peaceful) in finally
        if TICK_RATE:  # O293 tick trial: development evidence only, a gate's final run is at 20 tps
            r(f"tick rate {TICK_RATE}")
        for k in range(n):
            for case in cases:
                if "--max-minutes" in sys.argv and time.time() - t0 > 60 * float(sys.argv[sys.argv.index("--max-minutes") + 1]):
                    raise RuntimeError(f"time cap (S96: a case <= 10 min): stopped cleanly before {case.__name__} #{k + 1}")
                if not QUAL:  # S96: noon every trial on a lane; the mind's dusk routine ("going to the shelter") must not cut a fight (the arena is roofed)
                    r("time set 6000")
                clear_arena(); kit()
                r("difficulty normal")  # re-asserted every trial: attempt 4 lost 10 trials to a mid-run peaceful (source unknown)
                diff = r("difficulty")
                d0 = deaths()
                kc0, kz0, dd0 = creeper_kills(), score("evekz"), score("evedd")
                SPAWNS.clear()
                t_ms, log0 = int(time.time() * 1000), client_log_size()  # before the spawn: auto_fight starts on the spawn tick (a31, a32 missed them with these after it)
                params, wait, mobs, prot = case(k)
                # the mind refuses params that failed 3 times in 10 min (retry_limit): every trial gets its own radius
                params = {**params, "radius": params.get("radius", 16) + ((k + 20 * num_) % 100) / 100}
                time.sleep(0.5)
                spawned = count(MOBTAG)
                creepers0, arrows0 = count("evecr"), arrows_held()
                damage_reset()
                s = Sampler(); s.start()
                t = time.time()
                skill = "fight_ranged" if suite.startswith("cp27") else "fight_melee"
                if suite == "cp28mr":  # bow first (the creeper), then the sword for the zombies
                    first = eve("do", {"skill": "fight_ranged", "params": {"radius": 24 + k / 100, "max_arrows": 16}, "wait": 40}, 70)
                    res = eve("do", {"skill": "fight_melee", "params": params, "wait": wait}, wait + 30) if count(MOBTAG) else dict(first)
                    res = {**res, "calls": [{"skill": "fight_ranged", "status": first.get("status"), "code": first.get("code") or first.get("error"),
                                             "evidence": first.get("evidence")}]}
                elif suite == "cp27c":  # one call per target until the arena is clear (as the mind would loop)
                    calls, end = [], time.time() + wait
                    # always one call, then stop only on two zero counts in a row (attempt 5 c_volley #9: a single RCON read of 0
                    # with both skeletons alive made no call at all; counted, harness fault)
                    while time.time() < end and len(calls) < 8 and deaths() == d0 and (not calls or count(MOBTAG) > 0 or count(MOBTAG) > 0):
                        p_ = {**params, "radius": params["radius"] + len(calls) / 1000}
                        calls.append(eve("do", {"skill": skill, "params": p_, "wait": max(10, int(end - time.time()))}, wait + 30))
                        if calls[-1].get("status") != "ok":
                            break
                    res = dict(calls[-1]) if calls else {"ok": False, "error": "no_call"}
                    evs = [c.get("evidence") or {} for c in calls]
                    res["evidence"] = {key: sum((e.get(key) or 0) for e in evs) for key in ("shots", "hits", "kills", "melee_switches", "protected_holds")}
                    res["calls"] = [{"status": c.get("status"), "code": c.get("code") or c.get("error"), "evidence": c.get("evidence")} for c in calls]
                elif case.__name__.startswith("t_"):  # no dispatch: body's creeper_flee is under test
                    time.sleep(wait)
                    res = {"status": "ok", "flee_ends": flee_ends(log0), "auto_fight_starts": auto_fight_starts(log0), "blasts": blasts_since(t_ms)}
                elif case.__name__ in ("sealed_skeleton", "sealed_gap"):  # no dispatch: only the reflex is under test
                    time.sleep(wait)
                    res = {"status": "ok", "auto_fight_starts": auto_fight_starts(log0)}
                else:
                    res = eve("do", {"skill": skill, "params": params, "wait": wait}, wait + 30)
                    if case.__name__ == "creeper_low":  # her fight is not the bar here: end it for the next trial
                        eve("cancel", {"reason": "creeper_low: trial over"})
                    if case.__name__ == "x_creeper" and PARITY == "no-bow":  # O325: after the break-off, watch 20 s for a re-entry (a35 ended the trial at once)
                        time.sleep(20)
                        res = {**res, "auto_fight_starts": auto_fight_starts(log0)}
                el = round(time.time() - t, 1)
                time.sleep(1.5)  # death animation, last hit sync
                s.stop.set(); s.join()
                left, died, hp = count(MOBTAG), deaths() - d0, health()
                eve_killed_creeper = creeper_kills() > kc0
                # O259: what became of the creeper, recorded per trial (x_creeper's bar does not require it)
                creeper = ("killed" if eve_killed_creeper else "alive" if count("evecr") else "exploded") if "creeper" in case.__name__ else None
                creepers_blown = max(0, creepers0 - (creeper_kills() - kc0) - count("evecr"))
                shots, flees_claimed = arrows0 - arrows_held(), flee_counts(t_ms)
                shot_dists, claims = combat_log_since(log0)
                r("kill @e[tag=evecr]")
                code = res.get("code") or res.get("error")
                # H-autofight: with the hook live the auto_fight reflex engages as soon as the mobs spawn, the dispatched fight waits
                # paused under it and then finds nothing: every mob dead, no death, no_target is a win by her reflex, flagged in the row
                auto_first = code == "no_target" and left == 0 and spawned == mobs and not died
                if auto_first:
                    res = {**res, "status": "ok", "auto_fight_first": True}
                outcome = "death" if died else "win" if left == 0 and res.get("status") == "ok" else                     "retreat" if code in ("health_floor", "interrupted") else                     "evade_loop" if res.get("status") == "running" else f"other:{code}"
                # CP-28 attempt 11: the creeper's blast killed the zombies and the harness called it a win. Orchestrator's rule:
                # a creeper trial wins only with the creeper killed by Eve (so it never exploded on her) and every zombie gone.
                # O259: x_creeper (sword only, fight_melee never targets creepers) is exempt: all 3 zombies dead, no death, no unreachable.
                if case.__name__ in ("x_creeper_r", "b_creeper") and outcome == "win" and not eve_killed_creeper:
                    outcome = "creeper_not_killed"
                checks = [expect_ok(res), (spawned == mobs, f"spawned {spawned}/{mobs}"), (left == 0, f"zombies left {left}"),
                          (died == 0, f"deaths {died}"), (s.min is not None and s.min > 0, f"server min health {s.min}")]
                if s.floor_hit is not None:
                    checks.append((False, f"health floor {FLOOR_HP}: hp {s.floor_hit}, arena cleared to end the trial"))
                if case.__name__.startswith("creeper_"):
                    wool = hut_wool()
                    checks.append((wool == 27, f"hut wool {wool}/27"))
                    if case.__name__ in ("creeper_first", "creeper_two", "creeper_cover"):
                        checks.append((creepers_blown == 0, f"creepers exploded {creepers_blown}/{creepers0}"))
                    if case.__name__ == "creeper_low":  # O301: flee, not stand and click; the fight itself is not the bar
                        checks = [(spawned == mobs, f"spawned {spawned}/{mobs}"), (died == 0, f"deaths {died}"), (wool == 27, f"hut wool {wool}/27"),
                                  (shots == 0, f"arrows shot {shots}"), ((flees_claimed[0] or 0) >= 1, f"flees {flees_claimed[0]}")]
                if suite in ("cp28m", "cp28mr", "cp27", "cp27c", "cp29b") and case.__name__ not in ("x_wall", "creeper_low"):
                    checks.append((outcome == "win", f"outcome {outcome}"))  # pass = a win without a death; retreats are recorded apart
                if suite == "cp28":
                    checks.append((s.min is not None and s.min >= 12, f"never below six hearts (min {s.min})"))
                if case.__name__ == "x_wall":  # O126 bar: an honest unreachable, not a kill (a live zombie is the point)
                    checks = [(res.get("status") == "failed" and res.get("code") in ("unreachable", "unlisted:unreachable"),  # O206: the mind prefixes a code the registry does not declare
                               f"result {res.get('status')}/{res.get('code') or res.get('error')}"),
                              ("behind a wall" in json.dumps(res), "owner line in the result"), (el <= 10, f"ended within 10 s ({el} s)"),
                              (died == 0, f"deaths {died}"), (s.min is not None and s.min > 0, f"server min health {s.min}")]
                if case.__name__ == "x_archer":  # O102v bar: killed within 30 s with no floor retreat
                    checks += [(s.min is not None and s.min >= 12, f"min HP {s.min} (>= 12)"), (el <= 30, f"killed within 30 s ({el} s)")]
                if case.__name__.startswith("t_"):  # O332 bar: no death; 0 blast damage unless the only cover was a shield facing away (>= 135 deg); branch recorded
                    keys = {kk for _, d in res["flee_ends"] for kk, v in d.items() if v}
                    fired = sorted(k for k in keys if k.startswith(("trail_", "wall_", "abandoned", "confined_shield", "cornered", "fled_shielded")))  # body c27b2d79's fields
                    hurt = [b for b in (res["blasts"] or []) if b[0] > 0 and not (PARITY == "no-blocks" and (b[1] or 0) >= 135)]
                    checks = [(died == 0, f"deaths {died}"), (s.min is not None and s.min > 0, f"server min health {s.min}"),
                              (not hurt, f"blasts {res['blasts']}"),
                              (True, f"branch {fired or 'none'}; flee ends {[x for x, _ in res['flee_ends']]}; auto_fight {res['auto_fight_starts']}")]
                if case.__name__ == "x_creeper" and PARITY == "no-bow":  # O325 bar: she breaks off, never re-enters, does not die
                    starts = res.get("auto_fight_starts")
                    checks = [(spawned == mobs, f"spawned {spawned}/{mobs}"), (res.get("code") == "interrupted" and "no bow" in json.dumps(res), f"result {res.get('status')}/{res.get('code')}"),
                              (starts == [], f"auto_fight starts in 20 s {starts}"), (died == 0, f"deaths {died}"), (s.min is not None and s.min > 0, f"server min health {s.min}")]
                if case.__name__ in ("sealed_skeleton", "sealed_gap"):
                    limit = 0 if case.__name__ == "sealed_skeleton" else 2
                    checks = [(spawned == mobs, f"spawned {spawned}/{mobs}"), (died == 0, f"deaths {died}"), (res["auto_fight_starts"] is not None and (case.__name__ == "sealed_skeleton" or len(res["auto_fight_starts"]) >= 1) and len(res["auto_fight_starts"]) <= limit, f"auto_fight starts {res['auto_fight_starts']} (<= {limit})"), (s.min is not None and s.min > 0, f"server min health {s.min}")]
                checks += protected_ok(prot)
                ok = all(c for c, _ in checks)
                detail = "; ".join(d for _, d in checks) if ok else "FAIL: " + "; ".join(d for c, d in checks if not c)
                ev = res.get("evidence") or {}
                incoming = damage_read()
                # O174: from/to on this host's clock (the 45L), to match sense's [eve-audit] eve.hit lines to trials
                t_to = time.time()
                rows.append({"case": case.__name__, "trial": k + 1, "from": time.strftime("%H:%M:%S", time.localtime(t_to - (el or 0))), "to": time.strftime("%H:%M:%S", time.localtime(t_to)), "t_end": round(t_to, 1), "spawned": list(SPAWNS), "incoming": incoming, "max_state_gap_s": state_gap(int((time.time() - (el or 0) - 2) * 1000)), "auto_fight_first": auto_first, "creeper": creeper, "zombies_by_eve": score("evekz") - kz0, "eve_damage": (score("evedd") - dd0) / 10, "creepers": creepers0, "creepers_blown": creepers_blown, "shots": shots, "flees": flees_claimed[0], "claimed": flees_claimed[1], "shot_dists": shot_dists, "claims": claims, "ok": ok, "detail": detail, "params": params, "elapsed_s": el,
                             "server": {"min_health": s.min, "end_health": hp, "deaths": died, "mobs_left": left, "difficulty": diff,
                                        "floor_hit": s.floor_hit}, "outcome": outcome, "result": res})
                with open(rows_file, "a", encoding="utf-8") as fh:  # on disk trial by trial: a killed run keeps its counted rows
                    fh.write(json.dumps(rows[-1]) + "\n")
                print(f"{'PASS' if ok else 'FAIL'} {case.__name__} #{k + 1}: from={rows[-1]['from']} to={rows[-1]['to']} {detail} | swings {ev.get('swings')} shots {ev.get('shots')} hits "
                      f"{ev.get('hits')} reach<= {ev.get('max_swing_reach')} ({el} s) creeper {creeper} by_eve {rows[-1]['zombies_by_eve']} dmg {rows[-1]['eve_damage']} blown {creepers_blown}/{creepers0} arrows {shots} flees {flees_claimed[0]} claimed {flees_claimed[1]} shot_at {shot_dists} claims {len(claims)}", flush=True)
                if not PARITY and x_creeper_decided(rows):
                    raise RuntimeError(x_creeper_decided(rows))
                # O293: the bar is every trial, so one miss decides it; --allow-miss N for a bar that spares N (O313 creeper_first: >= 4/5)
                allowed = int(sys.argv[sys.argv.index("--allow-miss") + 1]) if "--allow-miss" in sys.argv else 0
                if (case.__name__.startswith("creeper_") or case.__name__.startswith("t_")) and sum(not x["ok"] for x in rows if x["case"] == case.__name__) > allowed:
                    raise RuntimeError(f"O293 gate decided FAIL at {case.__name__} #{k + 1}")
                if spawned != mobs:  # the fixture is broken: stop rather than burn the remaining trials (this one stays counted)
                    aborted = f"fixture fault in {case.__name__} #{k + 1}: spawned {spawned}/{mobs}, {diff.strip()}"
                    raise RuntimeError(aborted)
                if died or (s.min is not None and s.min <= 0):  # a death ends the attempt: respawn, then stop counting more fights
                    if QUAL:
                        raise RuntimeError(f"DEATH in {case.__name__} #{k + 1} on the 45L: run stopped, foundation respawns")
                    good, msg = restart_client(respawn=True)
                    aborted = f"death in {case.__name__} #{k + 1}" + ("" if good else f"; respawn failed: {msg}")
                    raise RuntimeError(aborted)
    except Exception as e:  # any error: teardown in finally, then the summary is still written
        aborted = aborted or f"{type(e).__name__}: {e}"
    finally:
        teardown = safe_teardown(lambda: (clear_arena(), r(f"fill {X0} {Y} {Z0} {X1} {Y + 6} {Z1} minecraft:air") if QUAL else None))
        stash_restore()
        if TICK_RATE:
            r("tick rate 20")
        r(f"gamerule spawn_mobs {was['spawn_mobs']}")
        r(f"gamerule mob_griefing {was['mob_griefing']}")
        r(f"difficulty {was['difficulty']}")
        r(f"forceload remove {X0} {Z0} {X1} {Z1}")
        lane_lock("release")
    passed = sum(x["ok"] for x in rows)
    reg = st.get("registry") or {}
    where = "the 45L (`45L`, foundation's qualification lane)" if QUAL else f"lane {LANE_ID.upper()} (`{'45L' if LC.get('host') == '45l' else 'shark'}`)"
    lines = [f"# {cp} — eve-combat {where} {stamp} attempt {num_}: {passed}/{len(rows)}", "",
             "Hypothesis: fight_melee kills the fixture's zombies with legal swings, without a death, and never hurts a protected bystander.",
             f"Fixture: {where}, real mind {PORT} via /cli, real eve-sense, {P}; roofed lit arena x {X0}..{X1} y {Y} z {Z0}..{Z1}; normal "
             f"difficulty for the run; kit {'K2 (K1 + a fresh off-hand shield every trial)' if suite.startswith('cp28') else 'K3 (K1 + bow in hotbar 1 + 32 arrows)' if suite == 'cp27' else 'K3 + off-hand shield (for the sword phase)' if suite == 'cp27c' else 'O41a (stone tools, no armour, no shield)' if suite == 'cp40t' else 'K1'} (iron sword, iron armour, 16 bread). Judge: RCON (zombies tagged {MOBTAG} gone, "
             f"deathCount unchanged, health sampled every 0.25 s, protected bystanders at full health"
             + (f", health floor {FLOOR_HP} ends a trial as a FAIL" if FLOOR_HP else "") + "). Natural spawning off during the run; "
             f"difficulty/spawn_mobs read first ({was['difficulty']}/{was['spawn_mobs']}) and restored; Eve re-parked (O15).",
             f"Versions: repo {git_head()}, lane src (mind/body) {lane_src()} (eve-combat jar in the client: {jar_sha()}; eve-body jar: {body_sha()}), registry {reg.get('version')} "
             f"{str(reg.get('hash'))[:12]}. Elapsed {time.time() - t0:.0f} s. Interventions 0 unless noted. Teardown: {teardown}."
             + (f" ABORTED: {aborted}" if aborted else "") + (f" Server /tick rate {TICK_RATE}, not 20: development evidence only (O293)." if TICK_RATE else "") + (f" Kit: Eve's live 45L kit, variant {PARITY} (O326, kit.json; 45L state ts {PARITY_KIT.get('from_45l_ts')})." if PARITY else "") + (" Qualification lane." if QUAL else " Development evidence, not qualification."), "",
             "Outcomes (win / retreat / death / other, counted apart; a retreat is never a win): "
             + ", ".join(f"{o} {sum(1 for x in rows if x.get('outcome') == o)}" for o in ("win", "retreat", "death"))
             + f", other {sum(1 for x in rows if str(x.get('outcome', '')).startswith('other'))}.", "",
             # O102e: the amended b_wall bar counts fight_ranged's under-3-block sword switches per trial; reported for every case
             f"Trials won by the auto_fight reflex before the dispatched fight ran (auto_fight_first): {sum(1 for x in rows if x.get('auto_fight_first'))}/{len(rows)}.",
             "Creeper per trial (O259; recorded, not required for x_creeper): " + ", ".join(f"{o} {sum(1 for x in rows if x.get('creeper') == o)}" for o in ("killed", "alive", "exploded")) + ".",
             *x_creeper_gate(rows),
             *[f"{c}: creepers exploded {sum(x.get('creepers_blown') or 0 for x in rows if x['case'] == c)}, arrows used {sum(x.get('shots') or 0 for x in rows if x['case'] == c)}, flees {sum(x.get('flees') or 0 for x in rows if x['case'] == c)}, claimed: true {sum(x.get('claimed') or 0 for x in rows if x['case'] == c)}, claims logged {sum(len(x.get('claims') or []) for x in rows if x['case'] == c)}, shot distances {[d for x in rows if x['case'] == c for d in x.get('shot_dists') or []]}." for c in dict.fromkeys(x["case"] for x in rows) if c.startswith("creeper_")],
             "Sword switches (fight_ranged under 3 blocks), trials with one / trials: "
             + ", ".join(f"{c} {sum(1 for x in rows if x['case'] == c and ((x.get('result') or {}).get('evidence') or {}).get('melee_switches'))}"
                         f"/{sum(1 for x in rows if x['case'] == c)}" for c in dict.fromkeys(x["case"] for x in rows)) + ".", "",
             "| case | trial | ok | s | server min hp | detail |", "|---|---|---|---|---|---|"]
    lines += [f"| {x['case']} | {x['trial']} | {'PASS' if x['ok'] else 'FAIL'} | {x['elapsed_s']} | {x['server']['min_health']} | {x['detail']} |"
              for x in rows]
    text = "\n".join(lines) + "\n"
    (out / "RESULT.md").write_text(text, encoding="utf-8")
    print(export_candidates(out, t0), flush=True)
    summ = REPO / "parts" / "combat" / "evidence-summaries"
    summ.mkdir(parents=True, exist_ok=True)
    (summ / f"{cp}-{stamp}-{LABEL}-attempt-{num_}.md").write_text(text, encoding="utf-8")
    print(f"{passed}/{len(rows)} -> {out}")


def lane_src():
    r"""The commit the lane's mind/body/jar were built from (<lane>\src); 'n/a' where the lane has no git checkout (the 45L)."""
    try:
        return subprocess.run(["git", "-C", str(LANE / "src"), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip() or "n/a"
    except OSError:
        return "n/a"


def git_head():
    try:  # a git-archive extract (the 45L) has no .git and maybe no git: say so instead of crashing the summary
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip() or "archive"
    except OSError:
        return "archive"


if __name__ == "__main__":
    main()
