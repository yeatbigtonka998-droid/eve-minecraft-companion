"""CP-40 iron kit end to end (O41), one counted run per invocation, on a Shark lane.

    py -3.12 parts/skills/tools/ironkit.py --lane B --seed-index I [--tick 2] [--deploy] [--expect-mind C]   (counted: I = 0..19)
    py -3.12 parts/skills/tools/ironkit.py --lane B --seed N ...      (a replay of a known seed: recorded, outside the set)
    py -3.12 parts/skills/tools/ironkit.py --selftest                 (offline: seeds, world check, judge, O86d rows)

S30 (bars stated before the run, deterministic setup, server reads for every asserted state):
  - Seeds: the counted set is SEEDS (20 seeds derived from sha256("eve-cp40-seed-<i>"), committed below); a counted run
    names its index. A random seed is never a counted run.
  - Bars: BARS below, written to bars.json before anything is dispatched and quoted in RESULT.md. CP-40 = at least 19 PASS
    of 20 valid runs, one per seed index (O41).
  - World check (I37), after the fresh world and the kit and before the objective: every condition the bars rely on is read
    back from the server (her inventory exactly the kit, no armour, health 20, deaths 0, difficulty normal, keep_inventory
    false, advance_time true, the tick rate, the world binding on this fresh world, her position at its spawn). Any
    mismatch makes the run INVALID (world-check.json), never FAIL.
  - Evidence: evidence/CP-40/<stamp>-shark-ironkit-attempt-N/ = bars.json (before) + fresh.txt, fresh-world.json (eve-watch's
    tool) + world-check.json (I37) + record.json (the mind's run record) + server-truth.json (the end reads) + chat.txt,
    memory.txt, candidates.jsonl, restore.txt + RESULT.md; summary in parts/skills/evidence-summaries/CP-40-<folder>.md.

Under our lane lock: eve-watch's fresh-world.ps1 makes a fresh world (new seed unless --seed; normal difficulty, nights on,
keep-inventory off, tick rate x --tick, fresh_mine break cap 1200/h) and parks EveDev in the surface shelter. The fixture
gives the agreed starting kit (O41a) by RCON and starts a deathCount scoreboard; nothing else is touched. The mind's
deterministic runner does everything (`cli objective {name: iron_kit, entry: mine_entry}`); the harness only polls.
Judge (server truth, RCON): full iron armour worn, an iron pickaxe held or carried, no death, <= 72 000 game ticks from the
start, and Eve back at the shelter. Every attempt counts; a death is a FAIL; a run the mind refused or never started is
INVALID (O46). Evidence: evidence/CP-40/<stamp>-shark-ironkit-attempt-N/ (record.json = the mind's run record, RESULT.md),
summary in parts/skills/evidence-summaries/. The lane is restored to its dev world afterwards (fresh-world.ps1 -Restore).
O86d: silence underground is a FAIL - ending under the mine entry away from the shelter needs stop_reason `stranded` and
her owner line ("stuck underground") in the server log; a go_home home_blocked needs dig_up ok or that same stranded call.
Memory (I29) is sampled at the start, every 5 min and at the end (memory.txt); her chat lines go to chat.txt.
`--selftest` checks the O86d rows offline.
"""
import hashlib, json, re, subprocess, sys, time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "combat" / "tools"))
import laneb_skills as L  # noqa: E402  (reads --lane)
import mem_gate  # noqa: E402  (combat's I29 Shark memory sample)

TICK = int(sys.argv[sys.argv.index("--tick") + 1]) if "--tick" in sys.argv else 2
# S30: the counted set of 20 fresh-world seeds, deterministic and committed (a replay names --seed instead)
SEEDS = [int.from_bytes(hashlib.sha256(f"eve-cp40-seed-{i}".encode()).digest()[:8], "big", signed=True) for i in range(20)]
SEED_INDEX = int(sys.argv[sys.argv.index("--seed-index") + 1]) if "--seed-index" in sys.argv else None
SEED = str(SEEDS[SEED_INDEX]) if SEED_INDEX is not None else (sys.argv[sys.argv.index("--seed") + 1] if "--seed" in sys.argv else None)
BARS = {  # stated before the run (bars.json), quoted in RESULT.md
    "per_run_pass": [
        "armour: the server's equipment head/chest/legs/feet = iron_helmet / iron_chestplate / iron_leggings / iron_boots",
        "pickaxe: an iron_pickaxe in her main hand or inventory (server)",
        "deaths: 0 (server deathCount scoreboard evedeaths, zeroed at the start)",
        "time: game ticks used <= 72000 (server gametime from the objective's start to its end)",
        "home: within 4 blocks (x and z) of the fresh world's spawn shelter (server Pos)",
        "O86d: never silent underground; a go_home home_blocked ends in dig_up ok or a `stranded` stop plus the owner line",
        "report: the mind's record agrees with the server (status done exactly when the bars above hold)"],
    "invalid_not_fail": [
        "the fresh world was not made, or the I37 world check found a mismatch (nothing dispatched)",
        "the mind refused or never started the objective (O46)",
        "zero hostiles at midnight (I20: no natural spawns)",
        "max_state_gap_s > 1.0 over the run (I29e), or eve-watch's I29f memory verdict INVALID"],
    "checkpoint": ">= 19 PASS of 20 valid runs, one per seed index 0..19 (O41)",
    "reported_not_a_bar": ["planner calls (O86g)", "eve-candidates rows (O94f)", "memory samples (I29)", "her chat lines"],
}
KIT = [("minecraft:stone_pickaxe", 1), ("minecraft:stone_axe", 1), ("minecraft:stone_sword", 1), ("minecraft:bread", 16),
       ("minecraft:torch", 16), ("minecraft:crafting_table", 1), ("minecraft:oak_planks", 8)]  # O41a
DAY_TICKS = 72000
FRESH_S = 3600
ARMOUR = {"head": "minecraft:iron_helmet", "chest": "minecraft:iron_chestplate", "legs": "minecraft:iron_leggings",
          "feet": "minecraft:iron_boots"}


def fresh(run_dir, restore=False):
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(L.LANE / "ops" / "fresh-world.ps1"),
            "-Lane", L.LANE_ID, "-By", "eve-skills", "-RunDir", str(run_dir)]
    args += ["-Restore"] if restore else ["-BreakCap", "1200", "-TickRate", str(TICK)] + ([f"-Seed:{SEED}"] if SEED else [])  # -Seed:<n>: a bare negative seed parses as a parameter name
    try:  # up to 5 re-rolled worlds x 8 entry candidates (O41m walk check): typical minutes, worst case > 1 h
        p = subprocess.run(args, capture_output=True, text=True, timeout=FRESH_S)
    except subprocess.TimeoutExpired:  # no world to judge: INVALID (O46), never FAIL
        return False, f"fresh-world.ps1 timed out after {FRESH_S} s"
    (run_dir / ("restore.txt" if restore else "fresh.txt")).write_text(p.stdout + "\n" + p.stderr, encoding="utf-8")
    return p.returncode == 0, (p.stdout[-600:] + p.stderr[-600:])


def gametime():
    m = re.search(r"(\d+) tick", L.r("time query gametime"))
    return int(m.group(1)) if m else None


def daytime():
    m = re.search(r"(\d+)", L.r("time query daytime"))
    return int(m.group(1)) % 24000 if m else None


HOSTILE = ("zombie", "skeleton", "creeper", "spider", "enderman", "witch", "drowned", "husk", "stray", "slime")


def hostiles():
    """Raw count per type across the loaded world (I20: a zero at midnight means spawning is off)."""
    out = {}
    for t in HOSTILE:
        m = re.search(r"Count: (\d+)", L.r(f"execute if entity @e[type=minecraft:{t}]"))
        out[t] = int(m.group(1)) if m else 0
    return out


def equipment():
    out = L.r(f"data get entity {L.P} equipment")
    got = {}
    for slot in ("head", "chest", "legs", "feet", "mainhand", "offhand"):
        m = re.search(slot + r': \{[^{}]*?id: "([^"]+)"', out)
        if m:
            got[slot] = m.group(1)
    return got, out


def world_issues(reads, fw, tick):
    """I37 (pure): what the world check found unlike the intended start; [] = as intended. `reads` holds the parsed server
    replies taken right after the fresh world and the kit (see world_reads)."""
    bad = []
    kit = dict(KIT)
    if reads.get("inventory") != kit:
        bad.append(f"inventory {reads.get('inventory')} is not exactly the kit")
    worn = {s: v for s, v in (reads.get("equipment") or {}).items() if s in ARMOUR}
    if worn:
        bad.append(f"armour already worn {worn}")
    if reads.get("health") != 20.0:
        bad.append(f"health {reads.get('health')} (want 20)")
    if reads.get("deaths") != 0:
        bad.append(f"deaths {reads.get('deaths')} (want 0)")
    if "normal" not in str(reads.get("difficulty", "")).lower():
        bad.append(f"difficulty: {reads.get('difficulty')}")
    if not str(reads.get("keep_inventory", "")).strip().lower().endswith("false"):
        bad.append(f"keep_inventory: {reads.get('keep_inventory')}")
    if not str(reads.get("advance_time", "")).strip().lower().endswith("true"):
        bad.append(f"advance_time: {reads.get('advance_time')}")
    if not re.search(rf"\b{20 * tick:g}(\.0+)?\b", str(reads.get("tick", ""))):
        bad.append(f"tick rate not {20 * tick:g}: {reads.get('tick')}")
    b = reads.get("binding") or {}
    if not b.get("ok") or str(fw.get("world_id") or "?") not in str(b.get("detail")):
        bad.append(f"binding {b} (want ok on {fw.get('world_id')})")
    here, spawn = reads.get("pos") or [], fw.get("spawn") or [None, None, None]
    if len(here) < 3 or spawn[0] is None or any(abs(here[i] - spawn[i]) > 3 for i in range(3)):
        bad.append(f"position {here} is not the fresh spawn {spawn}")
    return bad


def world_reads():
    """The server replies the world check judges, taken under the lane lock right after the fresh world and the kit."""
    eq, _ = equipment()
    hp = re.search(r"(-?[\d.]+)f?\s*$", L.r(f"data get entity {L.P} Health") or "")
    d = re.search(r"has (\d+) \[evedeaths\]", L.r(f"scoreboard players get {L.P} evedeaths") or "")
    return {"inventory": L.inv(), "equipment": eq, "health": float(hp.group(1)) if hp else None, "deaths": int(d.group(1)) if d else None,
            "difficulty": L.r("difficulty"), "keep_inventory": L.r("gamerule keep_inventory"), "advance_time": L.r("gamerule advance_time"),
            "tick": L.r("tick query"), "binding": L.eve("status").get("binding"),
            "pos": [float(v) for v in re.findall(r"(-?[\d.]+)d", L.r(f"data get entity {L.P} Pos"))[:3]]}


def judge_run(truth, record, fw, entry, lines):
    """The per-run bars against the end reads (pure). truth = server-truth.json's fields."""
    eq, inv, pos = truth.get("equipment") or {}, truth.get("inventory") or {}, truth.get("pos") or []
    spawn = fw.get("spawn") or fw.get("shelter_floor") or [None, None, None]
    home = bool(pos) and spawn[0] is not None and abs(pos[0] - spawn[0]) <= 4 and abs(pos[2] - spawn[2]) <= 4
    used = truth.get("ticks_used")
    rows = [(all(eq.get(s) == item for s, item in ARMOUR.items()), f"armour worn: {[eq.get(s) for s in ARMOUR]}"),
            (eq.get("mainhand") == "minecraft:iron_pickaxe" or inv.get("minecraft:iron_pickaxe", 0) >= 1,
             f"iron pickaxe held or carried (mainhand {eq.get('mainhand')}, carried {inv.get('minecraft:iron_pickaxe', 0)})"),
            (truth.get("deaths") == 0, f"deaths {truth.get('deaths')}"),
            (used is not None and used <= DAY_TICKS, f"game ticks used {used} (<= {DAY_TICKS})"),
            (home, f"back at the shelter: {pos} vs ({spawn[0]}, {spawn[2]})")]
    srows, _ = strand(record, pos, entry, home, lines)
    physical = all(ok for ok, _ in rows + srows)
    claims = record.get("status") == "done"  # the mind finishes a full kit "done", anything short "partial"
    rows += srows + [(claims == physical, f"report agrees: the mind says {record.get('status')}, the server bars {'hold' if physical else 'fail'}")]
    return rows


def mem(label):
    """(line, free GB) - a failed sample never stops a run."""
    try:
        free, com, lim, pages = mem_gate.sample()
        return f"{time.strftime('%H:%M:%S')} {label}: free {free:.1f} GB, commit {com:.1f}/{lim:.1f} GB, {pages:.0f} pages/s", free
    except Exception as e:
        return f"{time.strftime('%H:%M:%S')} {label}: sample failed ({e})", None


def chat():
    """Her chat lines in the lane server's log (the fresh world's server started it, so it holds this run only)."""
    try:
        text = (L.LANE / "server-dev" / "logs" / "latest.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [ln for ln in text.splitlines() if f"<{L.P}>" in ln]


def strand(record, pos, entry, home, lines):
    """O86d rows: no silence underground; a home_blocked ends in dig_up ok or a stranded stop with her owner line."""
    acts = [a for ph in record.get("phases") or [] for a in ph.get("actions") or []]
    blocked = sum(1 for a in acts if a.get("skill") == "go_home" and a.get("code") == "home_blocked")
    digs = [f"{a.get('status')}/{a.get('code')}" for a in acts if a.get("skill") == "dig_up"]
    called = record.get("stop_reason") == "stranded" and any("stuck underground" in ln for ln in lines)
    under = not home and bool(pos) and bool(entry) and pos[1] < float(entry["y"]) - 1  # the mind's below(): > 1 under the entry
    said = f"stop_reason {record.get('stop_reason')}, owner line {'in' if called else 'NOT in'} the server log"
    rows = [(not under or called, f"O86d no silence underground: feet y {pos[1] if pos else '?'} vs entry y "
                                  f"{entry.get('y') if entry else '?'}, {'underground' if under else 'not underground'}; {said}")]
    if blocked:
        rows.append((any(d.startswith("ok/") for d in digs) or called, f"O86d go_home home_blocked x{blocked}: dig_up "
                                                                     f"{digs or 'never dispatched'}; {said}"))
    return rows, f"O86d stranded path {'exercised' if blocked else 'NOT exercised'}: home_blocked x{blocked}, dig_up {digs}"


def selftest():
    e, line = {"x": 0, "y": 70, "z": 0}, ["[Server thread/INFO]: <EveDev> I'm stuck underground at 0 60 0 (x). Please come"]
    ok = lambda rows: all(o for o, _ in rows)  # noqa: E731
    hb = {"phases": [{"actions": [{"skill": "go_home", "code": "home_blocked"}, {"skill": "dig_up", "status": "failed", "code": "hazard"}]}],
          "stop_reason": "stranded"}
    assert ok(strand(hb, [0, 60, 0], e, False, line)[0])                   # stranded and the owner is called
    assert not ok(strand(hb, [0, 60, 0], e, False, [])[0])                 # stranded, no owner line
    assert not ok(strand({"phases": [], "stop_reason": None}, [0, 60, 0], e, False, [])[0])  # silence underground
    assert ok(strand({"phases": [], "stop_reason": None}, [0, 63, 0], e, True, [])[0])       # home (a18's shelter is under the entry)
    dug = {"phases": [{"actions": [{"skill": "go_home", "code": "home_blocked"}, {"skill": "dig_up", "status": "ok", "code": None},
                                   {"skill": "go_home", "status": "ok", "code": None}]}]}
    rows, cover = strand(dug, [0, 63, 0], e, True, [])
    assert ok(rows) and len(rows) == 2 and "exercised" in cover and "NOT" not in cover
    assert not ok(strand({**hb, "stop_reason": "partial"}, [0, 70, 0], e, False, [])[0])     # blocked, dig failed, no call
    # S30 seeds: 20 distinct, deterministic (recomputed the same way), the counted set by index
    assert len(set(SEEDS)) == 20 and SEEDS == [int.from_bytes(hashlib.sha256(f"eve-cp40-seed-{i}".encode()).digest()[:8], "big", signed=True)
                                              for i in range(20)]
    # I37 world check: the intended start, then one mismatch at a time
    fw = {"world_id": "fresh-b-x:24fc4e5355a5", "spawn": [-16, 63, -48]}
    good = {"inventory": dict(KIT), "equipment": {"mainhand": "minecraft:stone_pickaxe"}, "health": 20.0, "deaths": 0,
            "difficulty": "The difficulty is Normal", "keep_inventory": "Gamerule keep_inventory is currently set to: false",
            "advance_time": "Gamerule advance_time is currently set to: true", "tick": "Target tick rate: 40.0 per second",
            "binding": {"ok": True, "detail": "fresh-b-x:24fc4e5355a5"}, "pos": [-15.5, 63.0, -47.5]}
    assert world_issues(good, fw, 2) == [], world_issues(good, fw, 2)
    for change, want in [({"inventory": {**dict(KIT), "minecraft:iron_ingot": 1}}, "inventory"), ({"equipment": {"head": "minecraft:iron_helmet"}}, "armour"),
                         ({"health": 18.0}, "health"), ({"deaths": 1}, "deaths"), ({"difficulty": "The difficulty is Peaceful"}, "difficulty"),
                         ({"keep_inventory": "Gamerule keep_inventory is currently set to: true"}, "keep_inventory"),
                         ({"advance_time": "Gamerule advance_time is currently set to: false"}, "advance_time"),
                         ({"tick": "Target tick rate: 20.0 per second"}, "tick rate"), ({"binding": {"ok": True, "detail": "dev-shark:a1"}}, "binding"),
                         ({"pos": [-15.5, 99.0, -47.5]}, "position")]:
        got = world_issues({**good, **change}, fw, 2)
        assert len(got) == 1 and got[0].startswith(want), (change, got)
    # the per-run bars on the end reads
    done = {"status": "done", "phases": [], "stop_reason": None}
    truth = {"equipment": dict(ARMOUR), "inventory": {"minecraft:iron_pickaxe": 1}, "deaths": 0, "pos": [-15.0, 63.0, -47.0], "ticks_used": 60000}
    assert ok(judge_run(truth, done, fw, e, [])), judge_run(truth, done, fw, e, [])
    for change, rec in [({"equipment": {**ARMOUR, "feet": None}}, done), ({"inventory": {}}, done), ({"deaths": 1}, done),
                        ({"ticks_used": 72001}, done), ({"pos": [40.0, 63.0, -20.0]}, done), ({}, {**done, "status": "partial"})]:
        rows = judge_run({**truth, **change}, rec, fw, e, [])
        assert not ok(rows), (change, rec)
    short = judge_run({**truth, "deaths": 1}, {**done, "status": "partial"}, fw, e, [])  # honest partial: only the bar it misses fails
    assert [d.split(":")[0] for o, d in short if not o] == ["deaths 1"], short
    print("selftest ok")


def main():
    if "--selftest" in sys.argv:
        return selftest()
    hold = Path(__file__).with_name("HOLD")
    if hold.exists() and "--dry-run" not in sys.argv:  # a counted run is held until a root cause is in (local file, never committed)
        sys.exit(f"held: {hold.read_text(encoding='utf-8').strip()}")
    if "--expect-mind" in sys.argv:  # the lane's mind checkout must contain this commit (O41i: re-tooling at 52ce200)
        want = sys.argv[sys.argv.index("--expect-mind") + 1]
        src = L.LANE / "src"
        ok = subprocess.run(["git", "-C", str(src), "merge-base", "--is-ancestor", want, "HEAD"], capture_output=True).returncode == 0
        head = subprocess.run(["git", "-C", str(src), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        running = L.eve("status").get("mind_src")
        if running:  # O83h: the commit the running mind's src came from, not the checkout's HEAD (eve-watch, O86j)
            ok = subprocess.run(["git", "-C", str(L.REPO), "merge-base", "--is-ancestor", want, running], capture_output=True).returncode == 0
            head = f"{head} (running mind_src {running})"
        elif not ok and "--mind-tree" in sys.argv:
            # eve-watch can install a mind freeze into a lane whose HEAD is older (a16: HEAD be90deb, mind tree 67a01fb):
            # accept when the working tree's parts/mind/src equals that commit and the commit contains `want`
            tree = sys.argv[sys.argv.index("--mind-tree") + 1]
            same = subprocess.run(["git", "-C", str(src), "diff", "--quiet", tree, "--", "parts/mind/src"]).returncode == 0
            has = subprocess.run(["git", "-C", str(src), "merge-base", "--is-ancestor", want, tree], capture_output=True).returncode == 0
            ok = same and has
            head = f"{head} (mind tree = {tree}: {'equal' if same else 'DIFFERENT'}, {'contains' if has else 'lacks'} {want})"
        if not ok:
            sys.exit(f"lane {L.LANE_ID} src is at {head}, which does not contain mind {want}: nothing run")
        print(f"lane src {head} contains {want}")
    dry = "--dry-run" in sys.argv
    if SEED is None and not dry:  # S30: a counted run names its seed (the set's index) - never a random world
        sys.exit("S30: deterministic seeds only: --seed-index 0..19 (the counted set) or --seed N (a replay); nothing run")
    root = L.EVIDENCE / "CP-40"
    root.mkdir(parents=True, exist_ok=True)
    num = 1 + sum(1 for d in root.iterdir() if d.is_dir() and f"-{L.TAG}-ironkit-attempt-" in d.name)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    out = root / (f"{stamp}-{L.TAG}-ironkit-dryrun" if dry else f"{stamp}-{L.TAG}-ironkit-attempt-{num}")
    budget_s = DAY_TICKS / (20 * TICK) + 900
    L.lane_lock("acquire", int((budget_s + FRESH_S) / 60) + 20)
    out.mkdir()
    (out / "bars.json").write_text(json.dumps({"bars": BARS, "seed": SEED, "seed_index": SEED_INDEX, "set": "counted" if SEED_INDEX is not None
                                                else "replay", "kit": dict(KIT), "tick": TICK, "day_ticks": DAY_TICKS,
                                                "skills_jar": L.jar_sha(), "stated_at": datetime.now().isoformat(timespec="seconds")}, indent=2),
                                    encoding="utf-8")  # S30: the bars exist before anything is dispatched
    verdict, notes, record, judge = "INVALID", [], {}, []
    t_start = time.time()
    mems = [mem("start")]
    notes.append(f"memory at start (I29): {mems[0][0]}")
    try:
        # deploy BEFORE the fresh world: restart-client.ps1 re-parks her with the dev world's park-eve.ps1, which in a fresh
        # world builds a sealed box at y 98..103 around her (attempt 2: walk to the mine entry `unreachable`). fresh-world.ps1
        # restarts the client itself, with whatever jar is in mods, and parks her in the fresh world's own shelter.
        if "--deploy" in sys.argv:
            L.deploy()
        ok, msg = fresh(out)
        if not ok:
            notes.append(f"fresh-world failed: {msg}")
            return
        fw = json.loads((out / "fresh-world.json").read_text(encoding="utf-8-sig"))
        # never continue unless she stands at the fresh world's own spawn (O41h): the dev-world park (-15.5, 99, -47.5)
        # inside a fresh world is a sealed box (attempt 2)
        spawn = fw.get("spawn") or [None, None, None]
        here = [float(v) for v in re.findall(r"(-?[\d.]+)d", L.r(f"data get entity {L.P} Pos"))[:3]]
        notes.append(f"start position {here}, fresh spawn {spawn}")
        if len(here) < 3 or spawn[0] is None or abs(here[0] - spawn[0]) > 3 or abs(here[2] - spawn[2]) > 3 or abs(here[1] - spawn[1]) > 3:
            notes.append("REFUSED: she is not at the fresh world's spawn (dev-world park?) - nothing dispatched")
            return
        entry = fw.get("mine_entry")
        if isinstance(entry, list):
            entry = {"x": entry[0], "y": entry[1], "z": entry[2]}
        L.give(KIT)
        L.r("scoreboard objectives add evedeaths deathCount")
        L.r(f"scoreboard players set {L.P} evedeaths 0")
        time.sleep(1.0)  # the kit and the score land before they are read back
        reads = world_reads()
        issues = world_issues(reads, fw, TICK)
        (out / "world-check.json").write_text(json.dumps({"reads": reads, "issues": issues}, indent=2), encoding="utf-8")
        notes.append(f"world check (I37): {'as intended' if not issues else 'MISMATCH: ' + '; '.join(issues)}")
        if issues and not dry:  # a world not as the bars assume: INVALID, nothing dispatched
            return
        if "--dry-run" in sys.argv:  # O41h: create, kit, park check, restore; no objective, not a counted attempt
            notes.append(f"DRY RUN: kit {L.inv()} - no objective dispatched")
            if "--inspect" in sys.argv and entry:  # the cells around the mine entry, for a root cause (a3: blocked:walk)
                cands = ["air", "short_grass", "tall_grass", "fern", "grass_block", "dirt", "coarse_dirt", "podzol", "stone",
                         "granite", "diorite", "andesite", "deepslate", "sand", "red_sand", "sandstone", "terracotta", "gravel",
                         "water", "snow", "snow_block", "oak_leaves", "birch_leaves", "spruce_leaves", "oak_log", "birch_log",
                         "spruce_log", "dead_bush", "clay", "moss_block", "mud", "orange_terracotta", "red_terracotta",
                         "yellow_terracotta", "white_terracotta", "brown_terracotta", "light_gray_terracotta",
                         # outline-only ground cover (a8: `?` all over the entry's y 64 layer)
                         "leaf_litter", "bush", "firefly_bush", "wildflowers", "pink_petals", "short_dry_grass", "tall_dry_grass",
                         "large_fern", "dandelion", "poppy", "cornflower", "oxeye_daisy", "azure_bluet", "lily_of_the_valley",
                         "sweet_berry_bush", "red_mushroom", "brown_mushroom", "moss_carpet", "pale_moss_carpet", "snow",
                         "dead_bush", "allium", "blue_orchid", "orange_tulip", "red_tulip", "white_tulip", "pink_tulip",
                         "lilac", "rose_bush", "peony", "sunflower", "oak_sapling", "birch_sapling", "vine", "glow_lichen"]
                lines = []
                ex, ey, ez = entry["x"], entry["y"], entry["z"]
                # --inspect-dir: the grid runs along that heading (a13 failed north and west; the default looks east)
                hd = sys.argv[sys.argv.index("--inspect-dir") + 1] if "--inspect-dir" in sys.argv else "east"
                fx, fz = {"east": (1, 0), "west": (-1, 0), "south": (0, 1), "north": (0, -1)}[hd]
                for y in range(ey + 3, ey - 6, -1):
                    for side in (-1, 0, 1):
                        row = []
                        for ahead in range(-1, 6):
                            x, z = ex + fx * ahead + fz * side, ez + fz * ahead + fx * side
                            hit = next((c for c in cands if "passed" in L.r(f"execute if block {x} {y} {z} minecraft:{c}").lower()), "?")
                            row.append(f"{x},{y},{z}={hit}")
                        lines.append(f"{hd} side {side:+d}: " + "  ".join(row))
                for t in ("cow", "sheep", "pig", "chicken", "horse", "donkey", "rabbit", "goat", "wolf", "fox", "llama", "zombie",
                          "skeleton", "creeper", "spider", "item"):  # a10: something took the crosshair one block into the dig
                    c = L.r(f"execute if entity @e[x={ex},y={ey},z={ez},distance=..10,type=minecraft:{t}]")
                    if "passed" in c.lower():
                        lines.append(f"entities within 10 of the entry: {t}: {c.strip()}")
                (out / "inspect.txt").write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
                notes.append(f"inspect: {len(lines)} rows around the entry in inspect.txt")
            verdict = "DRY-RUN"
            return
        g0 = gametime()
        st = L.eve("objective", {"name": "iron_kit", "entry": entry}, 30)
        oid = st.get("objective_id")
        if not oid:
            notes.append(f"the mind did not start the run: {st}")
            return
        last = st
        midnight = None
        polls, gaps = 0, []
        while time.time() - t_start < budget_s:
            time.sleep(10)
            polls += 1
            g = L.state_gap(int((time.time() - 15) * 1000))  # I29f: read as it happens; the mind keeps ~10 min of states
            if g is not None:
                gaps.append(g)
            if polls % 30 == 0:
                mems.append(mem("run"))
            last = L.eve("objective", {"status": oid}, 30)
            if midnight is None and (daytime() or 0) >= 18000:  # I20: natural spawns are claimed, so count them at midnight
                midnight = hostiles()
                notes.append(f"midnight hostile count {midnight}")
            if last.get("status") not in (None, "running"):
                break
        g1 = gametime()
        notes.append(f"max_state_gap_s (I29e) {max(gaps) if gaps else None} over {len(gaps)} of {polls} polls (15 s windows)")
        record = last
        (out / "record.json").write_text(json.dumps(last, indent=2), encoding="utf-8")
        eq, raw_eq = equipment()
        deaths = re.search(r"has (\d+) \[evedeaths\]", L.r(f"scoreboard players get {L.P} evedeaths"))
        truth = {"equipment": eq, "equipment_raw": raw_eq, "inventory": L.inv(), "deaths": int(deaths.group(1)) if deaths else None,
                 "pos": [float(v) for v in re.findall(r"(-?[\d.]+)d", L.r(f"data get entity {L.P} Pos"))[:3]],
                 "gametime": [g0, g1], "ticks_used": (g1 - g0) if (g0 is not None and g1 is not None) else None}
        lines = chat()
        (out / "chat.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (out / "server-truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")  # every asserted end state, read back
        judge = judge_run(truth, last, fw, entry, lines)
        spawn = fw.get("spawn") or [None, None, None]
        home = bool(truth["pos"]) and spawn[0] is not None and abs(truth["pos"][0] - spawn[0]) <= 4 and abs(truth["pos"][2] - spawn[2]) <= 4
        notes.append(strand(last, truth["pos"], entry, home, lines)[1])
        verdict = "PASS" if all(ok for ok, _ in judge) else "FAIL"
        if gaps and max(gaps) > 1.0:  # I29e: a client stall makes the run's timing untrustworthy - INVALID, never FAIL
            verdict = "INVALID"
            notes.append(f"INVALID (I29e): max_state_gap_s {max(gaps)} > 1.0")
        if midnight is not None and sum(midnight.values()) == 0:  # I20: no natural spawns at midnight = conditions not met
            verdict = "INVALID"
            notes.append("INVALID (I20): zero hostiles at midnight, so the world had no natural spawns")
        if midnight is None:
            notes.append("midnight not reached in this run (no hostile count)")
        if last.get("status") == "refused":  # O46: the runner refused before any action (a11: no heading with room)
            verdict = "INVALID"
            notes.append(f"INVALID (O46): the mind's runner refused, nothing started: {last.get('report')}")
    finally:
        if "--keep" in sys.argv and "--dry-run" in sys.argv:  # diagnosis: leave the fresh world and our lock; restore by hand
            notes.append("KEPT the fresh world (--keep): run fresh-world.ps1 -Restore and release the lock by hand")
        else:
            ok, msg = fresh(out, restore=True)
            if not ok:
                notes.append(f"RESTORE FAILED: {msg}")
            L.lane_lock("release")
        mems.append(mem("end"))
        (out / "memory.txt").write_text("\n".join(m for m, _ in mems) + "\n", encoding="utf-8")
        frees = [f for _, f in mems if f is not None]
        notes.append(f"memory (I29): {len(mems)} samples, min free {min(frees):.1f} GB" if frees else "memory (I29): no samples")
        notes.append(L.planner_note(int(t_start * 1000)))
        notes.append(L.export_candidates(out, datetime.fromtimestamp(t_start), (record or {}).get("id")))  # O94f: the iron_kit objective's rows
        write(out, num, stamp, verdict, notes, record, judge, time.time() - t_start)


def write(out, num, stamp, verdict, notes, record, judge, elapsed):
    fw = {}
    try:
        fw = json.loads((out / "fresh-world.json").read_text(encoding="utf-8-sig"))
    except Exception:
        pass
    phases = record.get("phases") or []
    lines = [f"# CP-40 — iron kit end to end, lane {L.LANE_ID} (`{L.TAG}`) {stamp} attempt {num}: {verdict}", "",
             f"Fresh world (eve-watch's fresh-world.ps1): seed {fw.get('seed')}, world_id {fw.get('world_id')}, tick rate x{TICK} "
             f"(recorded x{fw.get('tick_multiplier')}, {fw.get('tick_rate')} tps), mine_entry {fw.get('mine_entry')}, fresh_mine cap "
             f"{fw.get('blocks_broken_per_hour_in_fresh_mine')}/h; "
             f"normal difficulty, nights on, keep-inventory off. Starting kit (O41a): {', '.join(f'{n} {i}' for i, n in KIT)}.",
             f"Mind runner `iron_kit` (deterministic, no model): status {record.get('status')}, report: {record.get('report')}",
             f"Real time {elapsed:.0f} s; day ticks used {record.get('day_ticks_used')}; raw iron {record.get('raw_iron')}, ingots "
             f"{record.get('ingots')}, crafted {record.get('crafted')}, equipped {record.get('equipped')}; injuries "
             f"{len(record.get('injuries') or [])}; refusals {record.get('refusals')}; shortfalls {record.get('shortfalls')}.",
             f"Client jar: {L.jar_sha()}; envelope caps (O45): {L.break_cap()}; repo {L.git_head()}.", ""]
    if notes:
        lines += ["Notes: " + " | ".join(notes), ""]
    lines += ["## Bars (stated before the run: bars.json)", ""] + [f"- {b}" for b in BARS["per_run_pass"]]
    lines += [f"- INVALID, not FAIL: {'; '.join(BARS['invalid_not_fail'])}", f"- Checkpoint: {BARS['checkpoint']}", ""]
    lines += [f"Seed {SEED} ({'counted set index ' + str(SEED_INDEX) if SEED_INDEX is not None else 'replay, outside the counted set'}).", ""]
    lines += ["## Judge (server truth)", ""] + [f"- {'PASS' if ok else 'FAIL'}: {d}" for ok, d in judge]
    lines += ["", "## Phases (mind record)", "", "| phase | result | ticks | breaks | actions | reason |", "|---|---|---|---|---|---|"]
    for ph in phases:
        acts = ph.get("actions") or []
        lines.append(f"| {ph.get('name')} | {ph.get('result')} | {ph.get('start_tick')}..{ph.get('end_tick')} | {ph.get('breaks')} | "
                     f"{len(acts)} ({', '.join(str(a.get('skill')) + ':' + str(a.get('status')) for a in acts[:6])}) | {ph.get('reason')} |")
    text = "\n".join(lines) + "\n"
    (out / "RESULT.md").write_text(text, encoding="utf-8")
    summ = L.REPO / "parts" / "skills" / "evidence-summaries" / (f"CP-40-{out.name}.md")
    summ.parent.mkdir(parents=True, exist_ok=True)
    summ.write_text(text, encoding="utf-8")
    print(f"{verdict} -> {out}")


if __name__ == "__main__":
    main()
