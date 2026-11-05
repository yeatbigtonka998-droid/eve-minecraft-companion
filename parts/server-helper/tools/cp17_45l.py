r"""CP-17 on the 45L: zone snapshots (repair baselines) and diffs on the real world with the deployed eve-sense jar.
The snapshot is taken through the owner's command path (/evetest as <owner> eve save_zone home), the diff through the
console-only `/evetest query diff` (eve-sense-devtools >= 0.1.5), replies read in full from latest.log and validated with
the mind's hub validators. Expected answers are written by hand for a fixture this script builds.
    C:\eve\mind\parts\mind\.venv\Scripts\python.exe C:\eve\ops\cp17_45l.py [--owner-mode manual|driver|skip]
"Hunter deliberately renovates a wall" (CP-17): --owner-mode manual (default) asks the first trusted player in game to
break one marked, glowing wall block and place a block on a marked floor spot (the step stays open until sense's
edits.log shows both, up to 30 min; "done" in chat re-checks); driver = that player's client has the dev
driver (local runs); skip = the case is recorded as not run (CP-17 then stays open).
The snapshot files this run creates are deleted at the end, so it leaves no test baseline behind. The fixture stays
standing for the slot (rebuilt at the start of every run); --clean removes it.

FROZEN CRITERIA
Fixture at ground level in the HOME zone, just west of the home pad (envelope-checked; the sky fixture at y 240 was never
sent to Hunter's client): stone floor x -44..-36, y 98, z -44..-36; stone-brick wall x -44..-40, y 99..101, z -44; closed
oak door at (-38, 99..100, -42); floor lever (-36, 99, -40); farmland with wheat
age 2 (-42, 99, -38) and carrots age 4 (-41, 99, -38). Random ticks paused during the run.
Changes after snapshot S1: wall (-43,100,-44) -> air and (-44,99,-44) -> cobblestone with no player (damage); wheat
-> age 7, carrots -> air (crop_cycle); door opened, lever pulled (operational_state); the owner breaks (-42,101,-44) and
places a block on (-37, 98, -37) (owner_edit, owner mode only).
Pass, within the fixture box: missing == {(-43,100,-44) stone_bricks}; changed == {(-44,99,-44) stone_bricks ->
cobblestone}; ignored == crop_cycle x2 + operational_state x3 (+ owner_edit x2 in owner mode); a new baseline S2 diffs
empty; S1 still shows the damage; diff refusals (unknown snapshot, other zone, malformed id) answer error{bad_payload};
an untrusted /eve save_zone from Eve's own client writes no snapshot. Every reply valid under the mind's validators.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=r"C:\eve\server-dev")
ap.add_argument("--lane", default="45L", choices=["45L", "shark"], help="which test lane this server is (O18): labels the evidence")
ap.add_argument("--ops", default=r"C:\eve\ops")
ap.add_argument("--mind-src", default=r"C:\eve\mind\parts\mind\src")
ap.add_argument("--out", default=r"C:\eve\logs")
ap.add_argument("--clean", action="store_true", help="remove the fixture at the end")
ap.add_argument("--owner-mode", default="manual", choices=["manual", "driver", "skip"])
a = ap.parse_args()
sys.path.insert(0, a.ops)
sys.path.insert(0, a.mind_src)
import rcon  # noqa: E402
from ask_player import ask  # noqa: E402
from eve_mind import hub  # noqa: E402

ROOT = Path(a.root)
ENV = json.loads((ROOT / "config" / "envelope.json").read_text(encoding="utf-8"))
P, OWNER = ENV["owner"]["eve_account"], ENV["owner"]["trusted_players"][0]
LATEST = ROOT / "logs" / "latest.log"
SNAPDIR = ROOT / "eve-sense" / "snapshots"
OUT = Path(a.out) / time.strftime(f"cp17-{a.lane.lower()}-%Y%m%d-%H%M")
OUT.mkdir(parents=True, exist_ok=True)
LOG = (OUT / "cases.jsonl").open("a", encoding="utf-8")
BOX = ((-44, 98, -44), (-36, 104, -36))
cases, created = [], []


def eve_alive():
    """A dead Eve sits on the respawn screen: her position and chunks freeze and every case would fail for that reason."""
    m = re.search(r"([\d.]+)f", rcon.command(ROOT, f"data get entity {P} Health"))
    return m is not None and float(m.group(1)) > 0


if not eve_alive():
    sys.exit(f"{P} is not online and alive (dead players wait on the respawn screen): fix that first")




def R(cmd):
    return rcon.command(ROOT, cmd)


def zone_of(p):
    hits = [z for z in ENV["zones"] if z["dim"] == "minecraft:overworld" and all(z["min"][k] <= v <= z["max"][k] for k, v in zip("xyz", p))]
    return ",".join(sorted(z["id"] + (":snapshot" if z.get("snapshot") else "") for z in hits)) or "none"


def logged(cmd, pattern):
    """Run an RCON command and return the first new latest.log line matching pattern (RCON replies are 4 KB at most)."""
    start = LATEST.stat().st_size
    R(cmd)
    for _ in range(80):
        with LATEST.open("rb") as f:
            f.seek(start)
            text = f.read().decode("utf-8", "replace")
        m = re.search(pattern, text, re.M)
        if m:
            return m
        time.sleep(0.1)
    return None


def diff(sid, zone="home"):
    m = logged("evetest query diff " + json.dumps({"zone_id": zone, "snapshot_id": sid}, separators=(",", ":")),
               r"\[evetest\] query -> (\{.*\})\s*$")
    r = json.loads(m.group(1)) if m else {"code": "no_reply"}
    v = hub._PAYLOAD_V["sense"]["error" if "code" in r else "zone.diff.result"]
    return r, [e.message for e in v.iter_errors(r)]


def save():
    m = logged(f"evetest as {OWNER} eve save_zone home", r"\[eve-audit\] zone\.snapshot\.saved (\{.*\})\s*$")
    if not m:
        raise RuntimeError("no zone.snapshot.saved audit after the owner's /eve save_zone home")
    h = json.loads(m.group(1))
    created.append(SNAPDIR / f"home-v{h['version']}.snap")
    return h


def owner_edits(since_ms):
    """Positions in the fixture box the owner broke or placed after since_ms, from sense's edits.log (what owner_edit uses)."""
    f = SNAPDIR / "edits.log"
    out = set()
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            t = line.split()
            if len(t) == 6 and int(t[0]) >= since_ms and t[5] == f"player:{OWNER}":
                p = (int(t[2]), int(t[3]), int(t[4]))
                if all(BOX[0][i] <= p[i] <= BOX[1][i] for i in range(3)):
                    out.add(p)
    return out


def inbox(e):
    p = e["pos"]
    return all(BOX[0][i] <= p[k] <= BOX[1][i] for i, k in enumerate("xyz"))


def entries(r, key, reason=None):
    out = []
    for e in r.get(key, []):
        if inbox(e) and (reason is None or e.get("reason") == reason):
            out.append(f"{e['pos']['x']},{e['pos']['y']},{e['pos']['z']} {e['expected'].split('[')[0]}" + (f" -> {e['actual'].split('[')[0]}" if key == "changed" else ""))
    return sorted(out)


def case(name, expected, observed, schema=None):
    ok = expected == observed and not schema
    row = {"name": name, "ok": ok, "expected": expected, "observed": observed, "schema_errors": schema or []}
    cases.append(row)
    LOG.write(json.dumps(row) + "\n")
    LOG.flush()
    print(f"{'OK ' if ok else 'BAD'} {name}" + ("" if ok else f"\n    expected {expected}\n    observed {observed} {schema or ''}"), flush=True)


for p in [BOX[0], BOX[1], (-40, 99, -42)]:
    if zone_of(p) != "home:snapshot":
        sys.exit(f"envelope does not match the frozen fixture: {p} is in {zone_of(p)}, wanted the home zone with snapshot:true")
t0 = time.time()
tick_rule, owner_ran = None, False
try:
    R("forceload add -44 -44 -36 -36")
    for name in ("random_tick_speed", "randomTickSpeed"):
        out = R(f"gamerule {name}")
        m = re.search(r"(\d+)", out)
        if m and "Unknown" not in out and "Incorrect" not in out:
            tick_rule = (name, m.group(1))
            R(f"gamerule {name} 0")
            break
    for c in ["fill -44 98 -44 -36 104 -36 minecraft:air", "fill -44 98 -44 -36 98 -36 minecraft:stone",
              "fill -44 99 -44 -40 101 -44 minecraft:stone_bricks",
              "setblock -38 99 -42 minecraft:oak_door[facing=east,half=lower,open=false]",
              "setblock -38 100 -42 minecraft:oak_door[facing=east,half=upper,open=false]",
              "setblock -36 99 -40 minecraft:lever[face=floor,powered=false]",
              "setblock -42 98 -38 minecraft:farmland", "setblock -41 98 -38 minecraft:farmland",
              "setblock -42 99 -38 minecraft:wheat[age=2]", "setblock -41 99 -38 minecraft:carrots[age=4]"]:
        R(c)
    time.sleep(1)
    s1 = save()
    S1 = s1["snapshot_id"]
    case("N01 the owner's /eve save_zone writes a versioned baseline with owner and policy",
         [OWNER, True, True], [s1.get("requested_by"), "policy" in s1 and "update" in s1["policy"], s1.get("version", 0) >= 1])
    r, s = diff(S1)
    case("N02 right after the snapshot: nothing in the fixture differs", [[], [], []],
         [entries(r, "missing"), entries(r, "changed"), entries(r, "ignored")], s)

    R("setblock -43 100 -44 minecraft:air")
    R("setblock -44 99 -44 minecraft:cobblestone")
    R("setblock -42 99 -38 minecraft:wheat[age=7]")
    R("setblock -41 99 -38 minecraft:air")
    R("setblock -38 99 -42 minecraft:oak_door[facing=east,half=lower,open=true]")
    R("setblock -38 100 -42 minecraft:oak_door[facing=east,half=upper,open=true]")
    R("setblock -36 99 -40 minecraft:lever[face=floor,powered=true]")
    if a.owner_mode == "driver":
        R(f"gamemode creative {OWNER}")                # breaks stone bricks in one hit, like a builder
        R(f"tp {OWNER} -39.5 99 -40.5")
        time.sleep(1.5)
        R(f"give {OWNER} minecraft:oak_planks 4")
        R(f"evetest drive {OWNER} " + json.dumps({"do": "break", "pos": [-42, 101, -44]}))
        time.sleep(0.8)
        R(f"evetest drive {OWNER} " + json.dumps({"do": "place", "pos": [-37, 98, -37], "face": "up", "item": "minecraft:oak_planks"}))
        time.sleep(0.8)
    elif a.owner_mode == "manual" and OWNER in R("list"):
        R("setblock -37 99 -37 minecraft:air")
        since = s1["created_ms"]
        ask(rcon, ROOT, OWNER, "[CP-17] Renovate Eve's test house: BREAK the marked wall block and PLACE any block on the marked floor block.",
            [((-42, 101, -44), "BREAK THIS", "minecraft:stone_bricks"), ((-37, 98, -37), "PLACE A BLOCK ON THIS", "minecraft:stone")],
            lambda: len(owner_edits(since)) >= 2,
            lambda: f"sense saw {len(owner_edits(since))} of your 2 edits (a break and a place) inside the house",
            stand=(-41.5, 99, -39.5))
    edited = owner_edits(s1["created_ms"])
    owner_ran = len(edited) >= 2
    time.sleep(1)
    r, s = diff(S1)
    def not_edited(lst):   # the owner may have chosen other blocks than suggested: his positions are judged in N07
        return [x for x in lst if tuple(int(v) for v in x.split()[0].split(",")) not in edited] if owner_ran else lst
    case("N03 damage without a player: the broken wall block is missing", ["-43,100,-44 minecraft:stone_bricks"], not_edited(entries(r, "missing")), s)
    case("N04 ... and the replaced one is changed", ["-44,99,-44 minecraft:stone_bricks -> minecraft:cobblestone"], not_edited(entries(r, "changed")), s)
    case("N05 crop growth and harvest are ignored (crop_cycle)", ["-41,99,-38 minecraft:carrots", "-42,99,-38 minecraft:wheat"],
         entries(r, "ignored", "crop_cycle"), s)
    case("N06 door and lever are ignored (operational_state)", sorted(["-36,99,-40 minecraft:lever", "-38,99,-42 minecraft:oak_door", "-38,100,-42 minecraft:oak_door"]),
         entries(r, "ignored", "operational_state"), s)
    if owner_ran:
        reported = {tuple(int(v) for v in x.split()[0].split(",")): "owner_edit" for x in entries(r, "ignored", "owner_edit")}
        reported.update({tuple(int(v) for v in x.split()[0].split(",")): "DAMAGE" for x in entries(r, "missing") + entries(r, "changed")})
        case(f"N07 {OWNER}'s renovation (every position he changed) is ignored as owner_edit, never reported as damage",
             {str(p): "owner_edit" for p in sorted(edited) if reported.get(p) is not None},
             {str(p): reported.get(p) for p in sorted(edited) if reported.get(p) is not None}, s)
        print(f"N07 owner positions {sorted(edited)} -> {[reported.get(p) for p in sorted(edited)]}", flush=True)
    else:
        print(f"NOTE N07 {OWNER}'s renovation not run ({a.owner_mode}; online: {OWNER in R('list')}): CP-17 stays open", flush=True)
    damage = (entries(r, "missing"), entries(r, "changed"))

    s2 = save()
    S2 = s2["snapshot_id"]
    r2, s = diff(S2)
    case("N08 a new baseline accepts the current state: empty diff", [[], [], []], [entries(r2, "missing"), entries(r2, "changed"), entries(r2, "ignored")], s)
    r1, s = diff(S1)
    case("N09 the old baseline still shows the same damage", list(damage), [entries(r1, "missing"), entries(r1, "changed")], s)
    case("N10 versions increase", True, s2["version"] == s1["version"] + 1)

    r, s = diff("home@v999999")
    case("B01 unknown snapshot refused", "bad_payload", r.get("code"), s)
    r, s = diff(S1, zone="mine")
    case("B02 another zone's snapshot refused", "bad_payload", r.get("code"), s)
    r, s = diff("home-v1")
    case("B03 malformed snapshot id refused", "bad_payload", r.get("code"), s)
    before = sorted(p.name for p in SNAPDIR.glob("home-v*.snap"))
    R(f"evetest drive {P} " + json.dumps({"do": "command", "text": "eve save_zone home"}))
    time.sleep(2)
    case("B04 an untrusted /eve save_zone from Eve's own client writes no snapshot", before, sorted(p.name for p in SNAPDIR.glob("home-v*.snap")))
finally:
    if a.clean:
        R("fill -44 98 -44 -36 104 -36 minecraft:air")
    R("forceload remove -44 -44 -36 -36")
    if tick_rule:
        R(f"gamerule {tick_rule[0]} {tick_rule[1]}")
    for f in created:                                   # leave no test baseline behind
        try:
            f.unlink()
        except OSError as e:
            print(f"could not delete {f}: {e}", flush=True)

ok = sum(c["ok"] for c in cases)
verdict = "FAIL" if ok < len(cases) else "PASS" if owner_ran else "INCOMPLETE (owner renovation not run)"
lines = [f"CP-17 on the {a.lane} {verdict}  {time.strftime('%Y-%m-%d %H:%M')}  world={ENV['world_id']}  snapshots {[str(f.name) for f in created]} (deleted)",
         f"{ok}/{len(cases)} cases as expected", *(f"  {'OK ' if c['ok'] else 'BAD'} {c['name']}" for c in cases), f"elapsed {time.time() - t0:.0f}s"]
(OUT / "RESULT.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines), flush=True)
sys.exit(0 if verdict == "PASS" else 1)
