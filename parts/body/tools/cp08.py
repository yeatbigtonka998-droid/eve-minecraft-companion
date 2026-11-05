"""CP-08 trustworthy player state on the Shark local fixture: change the world by RCON, read server truth by RCON, and compare
the body's `state` frames with it. Every case is counted into evidence/CP-08/<stamp>-attempt-N/.

    py -3.12 tools/cp08.py [--player EveDev]   (dev server + dev client up, EVE_LINK_TOKEN set, stub not running)
"""
import ctypes
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from arena import ARENA_KILL, FLOOR, Run, nums  # noqa: E402

BOUND_MS = 600  # declared update bound: state at 4 Hz plus immediately on health/dimension/inventory change, plus RCON latency
CRITERIA = f"""Each case changes the fixture by RCON, reads the server's own value by RCON (`data get entity`), then waits for the first
body `state` frame that matches it. Pass = it matches within {BOUND_MS} ms of the server read (latency recorded per case).
Positions within 0.01, entity positions within 0.15, health/food exact, saturation exact when it changes with health/food and always in being zero or not (vanilla does not re-send it otherwise), inventory slot/item/count/damage exact.
- Normal (K): health after /damage (two values incl. a half heart), hunger and saturation after a hunger effect, equipment in all
  six slots, inventory add/remove/damaged tool/full stack, position after tp (3 places), the Nether and back (dimension), xp
  level, gamemode, nearby entities (type, position, hostile, protected, uuid = server UUID), entity removal.
- Boundary / identity / staleness: empty inventory (= [] with fresh age, not missing), a full 36-slot inventory, health 1.0, an
  entity at 31 blocks (listed) and at 34 (not), 70 entities (the 64 nearest, nearest first), an entity behind a wall
  (line_of_sight false), named and villager entities protected, world identity on every frame after sense's world op, the session
  on every frame, a suspended server (freshness.age_ms climbs past 2000 = stale while the last values stay; recovers on resume),
  and a kick (no state at all afterwards = missing, plus event disconnect)."""


def uuid_from_ints(ints):
    if len(ints) != 4:
        return None  # no such entity
    b = b"".join(int(i).to_bytes(4, "big", signed=True) for i in ints)
    return str(uuid.UUID(bytes=b))


class T:
    def __init__(self, r: Run):
        self.r, self.p = r, r.player

    def rc(self, *cmds):
        for c in cmds:
            out = self.r.rcon(c)
            if re.search(r"Unknown|Incorrect|Expected|Invalid|Failed to parse|Could not", out):
                raise RuntimeError(f"fixture command failed: {c!r} -> {out!r}")

    def get(self, path):
        return self.r.rcon(f"data get entity {self.p} {path}")

    def wait_state(self, pred, since, timeout=BOUND_MS / 1000 + 2):
        f = self.r.hub.wait(lambda m: m["type"] == "state" and pred(m["payload"]), timeout, since)
        return f

    def case(self, variant, change_cmds, truth_fn, match_fn, detail_fn=lambda st, truth: ""):
        """Apply the change, read the server's value once it took effect, then find the body's first state that matches it.
        Latency is measured from the end of the change command (from the read when there is no change)."""
        since = len(self.r.hub.frames)
        if change_cmds:
            self.rc(*change_cmds)
        t0 = time.time()
        time.sleep(0.3)  # let the server tick the change before reading its value
        truth = truth_fn()
        if not change_cmds:
            since, t0 = len(self.r.hub.frames), time.time()
        f = self.wait_state(lambda st: match_fn(st, truth), since)
        lat = (f["t"] - t0) * 1000 if f else None
        ok = f is not None and lat <= BOUND_MS
        last = self.r.state()
        self.r.record(variant, "state", {}, ok, f"latency_ms={lat and round(lat)} truth={str(truth)[:160]} "
                      + (detail_fn(f["msg"]["payload"], truth) if f else f"last_state={str(last and {k: last.get(k) for k in ('health', 'food', 'pos', 'dim', 'equipment')})[:200]}"),
                      latency_ms=lat, truth=str(truth)[:2000])
        return ok


def inv_server(t: T):
    out = t.get("Inventory")
    return sorted((int(s), i, int(c)) for c, s, i in re.findall(r'count: (\d+), Slot: (\d+)b, id: "([^"]+)"', out)) if "[" in out else []


def inv_body(st):
    return sorted((o["slot"], o["item"], o["count"]) for o in st["inventory"] if o["slot"] < 36)


def equip_server(t: T):
    out = t.get("equipment")
    m = dict(re.findall(r'(head|chest|legs|feet|mainhand|offhand): \{count: \d+, id: "([^"]+)"', out))
    return m


def suspend(pid, on):
    k = ctypes.WinDLL("kernel32")
    nt = ctypes.WinDLL("ntdll")
    h = k.OpenProcess(0x0800, False, pid)  # PROCESS_SUSPEND_RESUME
    (nt.NtSuspendProcess if on else nt.NtResumeProcess)(h)
    k.CloseHandle(h)


def server_pid():
    """The one server JVM to suspend. Default: body's dev server (its loom config path, then KnotServer); on the 45L set
    EVE_SERVER_MATCH to a regex unique to that server's command line. Gradle wrappers and clients never match."""
    pat = os.environ.get("EVE_SERVER_MATCH", r"parts[\\/]body[\\/]\.gradle.*KnotServer")
    ps = ("Get-CimInstance Win32_Process -Filter \"name='java.exe'\" | Where-Object { $_.CommandLine -match $env:EVE_SERVER_MATCH "
          "-and $_.CommandLine -notmatch 'KnotClient' -and $_.CommandLine -notmatch 'gradlew' } | ForEach-Object { $_.ProcessId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         env={**os.environ, "EVE_SERVER_MATCH": pat}).stdout.split()
    if len(out) != 1:
        raise RuntimeError(f"EVE_SERVER_MATCH {pat!r} matched {len(out)} processes; refusing to suspend")
    return int(out[0])


def run(r: Run):
    r.fixture()
    t = T(r)
    p = r.player
    r.rcon("difficulty easy")
    # Natural regeneration would move health between the server read and the body's report (attempt 2): off for this run.
    t.rc("gamerule natural_health_regeneration false", f"tp {p} 0.5 {FLOOR:.0f} 0.5 0 0", f"effect clear {p}", ARENA_KILL)
    if nums(t.get("Health"))[0] <= 0:
        raise RuntimeError("Eve is dead on the fixture (death screen); reset her player data and restart the client first")
    time.sleep(1.2)
    r.clear_entities()
    # --- health
    for amount in (5, 2.5):
        t.case(f"health_damage_{amount}", [f"damage {p} {amount}"], lambda: nums(t.get("Health"))[0],
               lambda st, v: abs(st["health"] - v) < 1e-3, lambda st, v: f"body={st['health']}")
    t.rc(f"effect give {p} minecraft:instant_health 1 4")
    time.sleep(0.6)  # the heal lands a tick later; attempt 2 damaged first and killed her
    t.case("health_low_1.0", [f"damage {p} {nums(t.get('Health'))[0] - 1.0}"], lambda: nums(t.get("Health"))[0],
           lambda st, v: abs(st["health"] - v) < 1e-3, lambda st, v: f"body={st['health']}")
    t.rc(f"effect give {p} minecraft:instant_health 1 4")
    # --- hunger / saturation (compared at rest after a short hunger effect)
    t.rc(f"effect give {p} minecraft:hunger 3 120")
    time.sleep(3.5)
    t.case("food_after_hunger", [], lambda: (nums(t.get("foodLevel"))[0], nums(t.get("foodSaturationLevel"))[0]),
           # vanilla re-sends saturation only when health, food or saturation==0 changes (ServerPlayer's set-health packet), so a
           # client cannot know a drained-but-nonzero saturation exactly: food exact, saturation exact only in being zero or not
           lambda st, v: st["food"] == v[0] and (st["saturation"] == 0) == (v[1] == 0), lambda st, v: f"body=({st['food']},{st['saturation']})")
    # --- equipment, all six slots
    t.rc(f"clear {p}")
    slots = {"armor.head": "iron_helmet", "armor.chest": "iron_chestplate", "armor.legs": "iron_leggings", "armor.feet": "iron_boots",
             "weapon.mainhand": "stone_sword", "weapon.offhand": "shield"}
    t.case("equipment_six_slots", [f"item replace entity {p} {s} with minecraft:{i}" for s, i in slots.items()], lambda: equip_server(t),
           lambda st, v: all(st["equipment"].get(k) == v.get(k) for k in ("head", "chest", "legs", "feet", "offhand")) and len(v) == 5
           and st["equipment"]["mainhand"] == "minecraft:stone_sword",
           lambda st, v: f"body={st['equipment']}")
    # --- inventory
    t.rc(f"clear {p}")
    t.case("inventory_empty", [], lambda: inv_server(t), lambda st, v: inv_body(st) == v == [] and st["freshness"]["age_ms"] < 2000,
           lambda st, v: f"body={inv_body(st)} age={st['freshness']['age_ms']}")
    t.case("inventory_add", [f"give {p} minecraft:oak_log 12", f"give {p} minecraft:bread 5", f"give {p} minecraft:wooden_pickaxe[damage=40]"],
           lambda: inv_server(t), lambda st, v: inv_body(st) == v and any(o["item"] == "minecraft:wooden_pickaxe" and o["damage"] == 40 and o["max_damage"] == 59 for o in st["inventory"]),
           lambda st, v: f"body={inv_body(st)}")
    t.case("inventory_remove", [f"clear {p} minecraft:bread"], lambda: inv_server(t), lambda st, v: inv_body(st) == v, lambda st, v: f"body={inv_body(st)}")
    t.case("inventory_full_stack", [f"clear {p}", f"give {p} minecraft:cobblestone 64"], lambda: inv_server(t),
           lambda st, v: inv_body(st) == v and v == [(0, "minecraft:cobblestone", 64)], lambda st, v: f"body={inv_body(st)}")
    t.case("inventory_full_36", [f"clear {p}", f"give {p} minecraft:dirt {36 * 64}"], lambda: inv_server(t),
           lambda st, v: inv_body(st) == v and len(v) == 36, lambda st, v: f"body_slots={len(inv_body(st))}")
    t.rc(f"clear {p}")
    # --- position and dimension
    for i, (x, z) in enumerate([(10.5, -7.5), (-12.25, 3.75), (0.5, 0.5)]):
        t.case(f"position_{i}", [f"tp {p} {x} {FLOOR:.0f} {z}"], lambda: nums(t.get("Pos")),
               lambda st, v: abs(st["pos"]["x"] - v[0]) < 0.01 and abs(st["pos"]["y"] - v[1]) < 0.01 and abs(st["pos"]["z"] - v[2]) < 0.01,
               lambda st, v: f"body={st['pos']}")
    t.rc("execute in minecraft:the_nether run fill -3 100 -3 3 104 3 minecraft:air", "execute in minecraft:the_nether run fill -3 99 -3 3 99 3 minecraft:obsidian")
    t.case("dimension_nether", [f"execute in minecraft:the_nether run tp {p} 0.5 100 0.5"], lambda: t.get("Dimension").split(": ")[-1].strip('"'),
           lambda st, v: st["dim"] == v == "minecraft:the_nether", lambda st, v: f"body={st['dim']}")
    t.case("dimension_back", [f"execute in minecraft:overworld run tp {p} 0.5 {FLOOR:.0f} 0.5"], lambda: t.get("Dimension").split(": ")[-1].strip('"'),
           lambda st, v: st["dim"] == v == "minecraft:overworld", lambda st, v: f"body={st['dim']}")
    # --- xp, gamemode
    t.case("xp_level", [f"xp set {p} 7 levels"], lambda: nums(t.get("XpLevel"))[0], lambda st, v: st["xp_level"] == v == 7, lambda st, v: f"body={st['xp_level']}")
    t.case("gamemode_creative", [f"gamemode creative {p}"], lambda: "creative", lambda st, v: st["gamemode"] == "creative", lambda st, v: f"body={st['gamemode']}")
    t.case("gamemode_survival", [f"gamemode survival {p}"], lambda: "survival", lambda st, v: st["gamemode"] == "survival", lambda st, v: f"body={st['gamemode']}")
    # --- nearby entities: identity, type, position, flags
    r.clear_entities()
    mobs = [("pig", 3.5, 0.5, "", False, False), ("zombie", 0.5, 6.5, "", True, False), ("villager", -4.5, 2.5, "", False, True), ("pig", 2.5, -3.5, ",CustomName:\"Bob\"", False, True)]
    for i, (m, x, z, extra, hostile, prot) in enumerate(mobs):
        t.rc(f"summon minecraft:{m} {x} {FLOOR:.0f} {z} {{NoAI:1b,PersistenceRequired:1b,Tags:[\"e{i}\"]{extra}}}")
    for i, (m, x, z, extra, hostile, prot) in enumerate(mobs):
        def truth(i=i):
            return uuid_from_ints(nums(t.r.rcon(f"data get entity @e[tag=e{i},limit=1] UUID"))), nums(t.r.rcon(f"data get entity @e[tag=e{i},limit=1] Pos"))
        def match(st, v, m=m, hostile=hostile, prot=prot):
            e = next((e for e in st["nearby_entities"] if e.get("uuid") == v[0]), None)
            return e is not None and e["type"] == f"minecraft:{m}" and e["hostile"] == hostile and e["protected"] == prot and all(abs(e["pos"][k] - v[1][j]) < 0.15 for j, k in enumerate("xyz"))
        t.case(f"entity_{i}_{m}{'_named' if extra else ''}", [], truth, match,
               lambda st, v: str(next((e for e in st["nearby_entities"] if e.get("uuid") == v[0]), "missing"))[:200])
    t.case("entity_removed", ["kill @e[tag=e0]"], lambda: "gone", lambda st, v: not any(e["type"] == "minecraft:pig" and abs(e["pos"]["x"] - 3.5) < 0.2 and abs(e["pos"]["z"] - 0.5) < 0.2 for e in st["nearby_entities"]),
           lambda st, v: "pig at (3.5,0.5) no longer listed")
    r.clear_entities()
    # radius boundary and cap
    t.rc(f"summon minecraft:pig 31.5 {FLOOR:.0f} 0.5 {{NoAI:1b,Tags:[\"near\"]}}", f"summon minecraft:pig 0.5 {FLOOR:.0f} 34.5 {{NoAI:1b,Tags:[\"far\"]}}")
    t.case("entity_radius_31_listed_34_not", [], lambda: (uuid_from_ints(nums(r.rcon("data get entity @e[tag=near,limit=1] UUID"))), uuid_from_ints(nums(r.rcon("data get entity @e[tag=far,limit=1] UUID")))),
           lambda st, v: any(e.get("uuid") == v[0] for e in st["nearby_entities"]) and not any(e.get("uuid") == v[1] for e in st["nearby_entities"]),
           lambda st, v: f"listed={[e['distance'] for e in st['nearby_entities']]}")
    r.clear_entities()
    t.rc(*[f"summon minecraft:chicken {2.5 + (i % 10)} {FLOOR:.0f} {2.5 + (i // 10)} {{NoAI:1b}}" for i in range(70)])
    t.case("entity_cap_64_nearest", [], lambda: 70,
           lambda st, v: len(st["nearby_entities"]) == 64 and all(a["distance"] <= b["distance"] for a, b in zip(st["nearby_entities"], st["nearby_entities"][1:])),
           lambda st, v: f"listed={len(st['nearby_entities'])} sorted")
    r.clear_entities()
    t.rc(f"summon minecraft:pig 0.5 {FLOOR:.0f} 5.5 {{NoAI:1b,Tags:[\"hid\"]}}", f"fill -2 {FLOOR:.0f} 3 2 {FLOOR + 2:.0f} 3 minecraft:stone")
    t.case("entity_behind_wall_no_los", [f"tp {p} 0.5 {FLOOR:.0f} 0.5 0 0"], lambda: uuid_from_ints(nums(r.rcon("data get entity @e[tag=hid,limit=1] UUID"))),
           lambda st, v: any(e.get("uuid") == v and e["line_of_sight"] is False for e in st["nearby_entities"]), lambda st, v: "pig behind stone wall")
    t.rc(f"fill -2 {FLOOR:.0f} 3 2 {FLOOR + 2:.0f} 3 minecraft:air")
    r.clear_entities()
    # --- identity: world after sense-style world op, session on every frame
    want_world = os.environ.get("EVE_EXPECT_WORLD")  # 45L: sense announces the real world on join; Shark: the dev sender does
    since = len(r.hub.frames)
    if not want_world:
        t.rc(f"evetest control world {p} world")
    f = r.hub.wait(lambda m: m["type"] == "state" and m["payload"].get("world_id") == (want_world or "world:evetest") and m["payload"].get("level_name"), 8,
                   0 if want_world else since)
    r.record("world_identity_after_world_op", "state", {}, bool(f), f"state.world_id={f and f['msg']['payload']['world_id']}")
    time.sleep(3)
    sess = r.hub.session
    last_hello = max(i for i, x in enumerate(r.hub.frames) if x["dir"] == "in" and x["msg"]["type"] == "hello")  # noqa: E501
    frames = [x["msg"] for x in r.hub.frames[last_hello + 1:] if x["dir"] == "in"]
    r.record("session_on_every_frame", "state", {}, len(frames) >= 8 and all(m.get("session") == sess for m in frames),
             f"all {len(frames)} frames since the re-hello carry session {sess[:8]}")
    # --- staleness: suspend the server process; age climbs, values kept; resume recovers
    pid = server_pid()
    since = len(r.hub.frames)
    suspend(pid, True)
    try:
        f = r.hub.wait(lambda m: m["type"] == "state" and m["payload"]["freshness"]["age_ms"] > 2000, 6, since)
    finally:
        suspend(pid, False)
    st = f and f["msg"]["payload"]
    r.record("stale_when_server_silent", "state", {}, bool(f) and st["pos"] is not None and isinstance(st["inventory"], list),
             f"age_ms={st and st['freshness']['age_ms']} (stale > 2000; values kept, so stale != empty)")
    since = len(r.hub.frames)
    f = r.hub.wait(lambda m: m["type"] == "state" and m["payload"]["freshness"]["age_ms"] < 1000, 8, since)
    r.record("fresh_after_resume", "state", {}, bool(f), f"age_ms={f and f['msg']['payload']['freshness']['age_ms']}")
    # --- missing: kicked = no state at all + event disconnect (last case: the dev client stays on the disconnect screen)
    since = len(r.hub.frames)
    r.rcon(f"kick {p} cp08 missing-data case")
    ev = r.hub.wait(lambda m: m["type"] == "event" and m["payload"]["kind"] == "disconnect", 5, since)
    time.sleep(2.0)
    after = [x for x in r.hub.frames[since:] if x["dir"] == "in" and x["msg"]["type"] == "state" and ev and x["t"] > ev["t"]]
    r.record("missing_after_disconnect", "state", {}, bool(ev) and not after, f"event disconnect={bool(ev)} states after it={len(after)}")
    r.rcon("gamerule natural_health_regeneration true")
    r.finish(CRITERIA, "The dev client was kicked by the last case; restart it before the next run.")


if __name__ == "__main__":
    player = sys.argv[sys.argv.index("--player") + 1] if "--player" in sys.argv else "EveDev"
    run_ = Run("CP-08", player)
    try:
        run(run_)
    except BaseException as e:
        run_.finish(CRITERIA, f"**ABORTED** after {len(run_.rows)} attempts: {e!r}")
        raise
