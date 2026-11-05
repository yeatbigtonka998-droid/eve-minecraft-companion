r"""CP-52 lane fixture (sense side, for eve-builder): a snapshotted house + farm in the home zone, a creeper-style crater
made without any player, an owner edit to preserve, and eve-sense's own `zone.repair` answer for it (link v1.3), checked
here case by case. Builder then repairs the site and re-asks zone.repair until no steps remain.
Needs eve-sense >= 0.1.11 and eve-sense-devtools >= 0.1.11 on the SERVER (scripted owner save, `/evetest query repair`);
no client devtools. Lanes: Shark B / C (`--lane shark`); never the 45L.
    <lane>\src\parts\mind\.venv\Scripts\python.exe tools\cp52_fixture.py --root C:\eve-shark\server-dev --ops C:\eve-shark\ops
    ... --reset           start over from the SAME snapshot: rebuild the site exactly as snapshotted (checked: zone.repair
                          reports nothing inside it), then the owner edit and the crater again, then the R1-R8 checks;
                          for repeated builder attempts (needs a previous run's state file on that server)
    ... --clean           remove the fixture and the snapshot this run created (after builder's run)
45L (foundation, not run yet): copy this file next to rcon.py in C:\eve\ops and, with devtools >= 0.1.11 on the 45L
SERVER and the lane free, run `python C:\eve\ops\cp52_fixture.py --lane 45L --root C:\eve\server-dev --ops C:\eve\ops
--out C:\eve\logs`; the owner edit is simulated unless Hunter is asked in game (same as Shark); `--clean` afterwards.

FROZEN FIXTURE (origin O = (-10, 160, -76), inside `home` (snapshot:true, task_only), clear sky; forceloaded while it exists)
  floor   stone x O..O+8, y O, z O..O+8
  house   cobblestone walls x -9..-5, y 161..163, z -75..-71 (hollow ring), oak door (west wall) at (-9, 161..162, -73),
          glass at (-7, 162, -71), oak-plank roof y 164 over the house
  chests  A (intact: 5 bread, 3 iron_ingot) at (-6, 161, -74) inside the house; B (destroyed: 4 coal) at (-5, 161, -72)
  farm    farmland x -4..-2, y 160, z -74..-70 with wheat age 7 at y 161
  owner   AFTER the snapshot: Hunter replaces the glass at (-7, 162, -71) with a lantern... (a SIMULATED owner edit: the
          block change plus the same edits.log line sense writes for a trusted player's place, since Hunter is not on Shark)
  crater  every position within 2.5 of C = (-4.5, 161.5, -72.5) becomes air except chest A (kept) and the owner's lantern;
          remaining farmland in the farm (not inside the crater) is trampled to dirt (its wheat drops: crop_cycle)
EVE SAFETY (rule O35, every mode): before the site is built, reset or cratered, Eve (envelope eve_account), if online,
must be alive and is parked (--park, the O15 shelter); the run REFUSES (changes nothing more) while she is inside or
above the site footprint (+2 blocks margin, any height). The check is repeated right before the crater. On --lane 45L
the world must also be peaceful (`difficulty` reads Peaceful), else the run refuses.
EXPECTED zone.repair (checked): every crater position is a step (remove top-down, then place bottom-up); chest B is in
`manual` (kind minecraft:chest, never a step); chest A appears nowhere; the lantern is in `skipped` as owner_edit; dropped
wheat is skipped crop_cycle; trampled farmland is remove dirt + place farmland. PASS = all of that.
"""
import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=r"C:\eve-shark\server-dev")
ap.add_argument("--ops", default=r"C:\eve-shark\ops")
ap.add_argument("--out", default=r"C:\eve-shark\logs")
ap.add_argument("--lane", default="shark", choices=["shark", "45L"], help="labels the evidence (O18)")
ap.add_argument("--reset", action="store_true", help="re-crater from the existing snapshot (same baseline)")
ap.add_argument("--park", default="-15.5 99 -47.5", help="where Eve is parked first (the O15 shelter)")
ap.add_argument("--clean", action="store_true")
a, _ = ap.parse_known_args()   # runners may pass harness-wide options (e.g. --mind-src)
sys.path.insert(0, a.ops)
import rcon  # noqa: E402
from lane_tp import lane_tp, parse  # noqa: E402  (O39: safe_tp on the Shark lanes)

ROOT = Path(a.root)
ENV = json.loads((ROOT / "config" / "envelope.json").read_text(encoding="utf-8"))
OWNER = ENV["owner"]["trusted_players"][0]
LATEST = ROOT / "logs" / "latest.log"
SNAPDIR = ROOT / "eve-sense" / "snapshots"
STATE = ROOT / "eve-sense" / "cp52-fixture.json"
O = (-10, 160, -76)
BOX = (O, (O[0] + 8, O[1] + 6, O[2] + 8))
CHEST_A, CHEST_B, LANTERN = (-6, 161, -74), (-5, 161, -72), (-7, 162, -71)
FARM = [(x, 160, z) for x in range(-4, -1) for z in range(-74, -69)]
CENTER = (-4.5, 161.5, -72.5)


def R(c):
    return rcon.command(ROOT, c)


def xyz(p):
    return " ".join(str(v) for v in p)


def logged(cmd, pattern, tries=80):
    start = LATEST.stat().st_size
    R(cmd)
    for _ in range(tries):
        with LATEST.open("rb") as f:
            f.seek(start)
            m = re.search(pattern, f.read().decode("utf-8", "replace"), re.M)
        if m:
            return m
        time.sleep(0.1)
    return None


def fill(p0, p1, block):
    R(f"fill {xyz(p0)} {xyz(p1)} {block}")


if a.clean:
    st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    fill(BOX[0], BOX[1], "minecraft:air")
    R(f"forceload remove {O[0]} {O[2]} {O[0] + 8} {O[2] + 8}")
    for f in st.get("created", []):
        Path(f).unlink(missing_ok=True)
    STATE.unlink(missing_ok=True)
    print("cleaned", st.get("snapshot_id"))
    sys.exit(0)

EVE = ENV["owner"]["eve_account"]


def eve_clear(stage, park=True):
    """O35: Eve parked, alive, and not inside or above the site; on the 45L the world is peaceful. Exits (refuses) if not."""
    if a.lane == "45L" and "Peaceful" not in R("difficulty"):
        sys.exit(f"O35 refused at {stage}: the 45L is not peaceful ({R('difficulty')!r})")
    if EVE not in R("list"):
        print(f"O35 {stage}: {EVE} offline, cannot be in the site", flush=True)
        return
    hp = re.search(r"([\d.]+)f", R(f"data get entity {EVE} Health"))
    if not hp or float(hp.group(1)) <= 0:
        sys.exit(f"O35 refused at {stage}: {EVE} is not alive")
    if park:
        lane_tp(ROOT, a.ops, EVE, *parse(a.park))           # O39: refuses (SystemExit) rather than an unsafe tp
        time.sleep(1.5)
    m = re.search(r"\[([-\d.]+)d, ([-\d.]+)d, ([-\d.]+)d\]", R(f"data get entity {EVE} Pos"))
    if not m:
        sys.exit(f"O35 refused at {stage}: cannot read {EVE}'s position")
    x, y, z = (float(v) for v in m.groups())
    if BOX[0][0] - 2 <= x <= BOX[1][0] + 3 and BOX[0][2] - 2 <= z <= BOX[1][2] + 3 and y >= BOX[0][1] - 2:
        sys.exit(f"O35 refused at {stage}: {EVE} is inside or above the site at {x:.1f} {y:.1f} {z:.1f}")
    print(f"O35 {stage}: {EVE} clear of the site at {x:.1f} {y:.1f} {z:.1f}", flush=True)


if not a.clean:
    eve_clear("before the build")
if "home" not in [z["id"] for z in ENV["zones"] if z.get("snapshot")]:
    sys.exit("the envelope's home zone has no snapshot:true")
OUT = Path(a.out) / time.strftime(f"cp52-fixture-{a.lane.lower()}-%Y%m%d-%H%M" + ("-reset" if a.reset else ""))
OUT.mkdir(parents=True, exist_ok=True)
cases = []


def case(name, ok, detail):
    cases.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"{'OK ' if ok else 'BAD'} {name}" + ("" if ok else f"  {detail}"), flush=True)


R(f"forceload add {O[0]} {O[2]} {O[0] + 8} {O[2] + 8}")
time.sleep(1)
fill(BOX[0], BOX[1], "minecraft:air")
fill(O, (O[0] + 8, O[1], O[2] + 8), "minecraft:stone")
fill((-9, 161, -75), (-5, 163, -71), "minecraft:cobblestone")
fill((-8, 161, -74), (-6, 163, -72), "minecraft:air")                     # hollow ring
R("setblock -9 161 -73 minecraft:oak_door[facing=west,half=lower]")
R("setblock -9 162 -73 minecraft:oak_door[facing=west,half=upper]")
R(f"setblock {xyz(LANTERN)} minecraft:glass")
fill((-9, 164, -75), (-5, 164, -71), "minecraft:oak_planks")
for p, items in [(CHEST_A, [("minecraft:bread", 5), ("minecraft:iron_ingot", 3)]), (CHEST_B, [("minecraft:coal", 4)])]:
    R(f"setblock {xyz(p)} minecraft:chest")
    for i, (it, n) in enumerate(items):
        R(f"item replace block {xyz(p)} container.{i} with {it} {n}")
fill((-4, 160, -74), (-2, 160, -70), "minecraft:farmland")
fill((-4, 161, -74), (-2, 161, -70), "minecraft:wheat[age=7]")
time.sleep(1)


def repair(sid):
    box = {"min": dict(zip("xyz", BOX[0])), "max": dict(zip("xyz", BOX[1]))}   # only the site: other work in home stays
    m = logged("evetest query repair " + json.dumps({"zone_id": "home", "snapshot_id": sid, "box": box}, separators=(",", ":")),
               r"\[evetest\] query -> (\{.*\})\s*$")
    return json.loads(m.group(1)) if m else {"code": "no_reply"}


inbox = lambda p: all(BOX[0][i] <= p[i] <= BOX[1][i] for i in range(3))  # noqa: E731
P = lambda q: (q["x"], q["y"], q["z"])  # noqa: E731
if a.reset:                                             # the same baseline as the first run
    if not STATE.exists():
        sys.exit(f"--reset needs a previous run's {STATE}")
    st = json.loads(STATE.read_text(encoding="utf-8"))
    SID, created = st["snapshot_id"], st["created"]
    r0 = repair(SID)
    left = [s for s in r0.get("steps", []) + r0.get("manual", []) if inbox(P(s["pos"]))]
    case("R0 the rebuilt site equals the snapshot (zone.repair: nothing inside it)", "steps" in r0 and not left,
         {"snapshot": SID, "left": left[:5], "code": r0.get("code")})
else:
    m = logged(f"evetest as {OWNER} eve save_zone home", r"\[eve-audit\] zone\.snapshot\.saved (\{.*\})\s*$")
    if not m:
        sys.exit("no zone.snapshot.saved after the scripted owner save (devtools on the server?)")
    saved = json.loads(m.group(1))
    SID = saved["snapshot_id"]
    created = [str(SNAPDIR / f"home-v{saved['version']}.snap")]

# the owner's edit after the snapshot (simulated: Hunter is not on Shark; same edits.log line sense writes for his place)
R(f"setblock {xyz(LANTERN)} minecraft:lantern")
with (SNAPDIR / "edits.log").open("a", encoding="utf-8") as f:
    f.write(f"{int(time.time() * 1000)} minecraft:overworld {xyz(LANTERN)} player:{OWNER}\n")

eve_clear("before the crater", park=False)              # O35: she must not have come back meanwhile
# the crater: no player, like a creeper
crater = []
for x in range(-8, 0):
    for y in range(159, 165):
        for z in range(-76, -68):
            p = (x, y, z)
            if p in (CHEST_A, LANTERN) or y < 160:
                continue
            if math.dist((x + 0.5, y + 0.5, z + 0.5), CENTER) <= 2.5:
                crater.append(p)
solid = [p for p in crater if not R(f"execute if block {xyz(p)} minecraft:air").startswith("Test passed")]  # what it destroys
for p in crater:
    R(f"setblock {xyz(p)} minecraft:air")
trampled = [p for p in FARM if p not in crater]
for p in trampled:
    R(f"setblock {xyz(p)} minecraft:dirt")
    R(f"setblock {p[0]} 161 {p[2]} minecraft:air")
time.sleep(1)
STATE.write_text(json.dumps({"snapshot_id": SID, "created": created, "crater": crater, "destroyed": solid, "trampled": trampled}), encoding="utf-8")

r = repair(SID)
(OUT / "zone.repair.result.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
steps = [s for s in r.get("steps", []) if inbox(P(s["pos"]))]
manual = [s for s in r.get("manual", []) if inbox(P(s["pos"]))]
skipped = {P(s["pos"]): s["reason"] for s in r.get("skipped", []) if inbox(P(s["pos"]))}
step_pos = {P(s["pos"]) for s in steps}
case("R1 the reply is a v1.3 zone.repair.result", all(k in r for k in ("steps", "manual", "skipped")) and not r.get("truncated"),
     {k: r.get(k) for k in ("code", "message", "truncated", "total")})
lost = [p for p in solid if p != CHEST_B and not (p[1] == 161 and (p[0], 160, p[2]) in FARM)]    # wheat is crop_cycle
missing = [p for p in lost if p not in step_pos]
extra = [p for p in step_pos if p not in solid and p not in trampled]
case("R2 every block the crater destroyed (but chest B and wheat) is a repair step, and nothing else is",
     not missing and not extra, {"missing": missing[:10], "extra": sorted(extra)[:10], "destroyed": len(solid)})
ops = [s["op"] for s in steps]
first_place = ops.index("place") if "place" in ops else len(ops)
order_ok = all(o == "remove" for o in ops[:first_place]) and all(o == "place" for o in ops[first_place:])
ys = [s["pos"]["y"] for s in steps if s["op"] == "place"]
case("R3 removals first, then placements bottom up", order_ok and ys == sorted(ys), {"ops": "".join(o[0] for o in ops)[:60]})
case("R4 the destroyed chest B is manual (container, never a step)", [P(s["pos"]) for s in manual] == [CHEST_B] and CHEST_B not in step_pos
     and manual[0].get("kind") == "minecraft:chest" and bool(manual[0].get("note")), {"manual": manual})
case("R5 the intact chest A is untouched by the repair", CHEST_A not in step_pos and CHEST_A not in skipped
     and CHEST_A not in [P(s["pos"]) for s in manual], {})
case("R6 the owner's lantern is preserved: skipped as owner_edit", skipped.get(LANTERN) == "owner_edit" and LANTERN not in step_pos,
     {"lantern": skipped.get(LANTERN)})
tr_ok = all(any(P(s["pos"]) == p and s["op"] == "remove" and s["block"].startswith("minecraft:dirt") for s in steps) and
            any(P(s["pos"]) == p and s["op"] == "place" and s["block"].startswith("minecraft:farmland") for s in steps) for p in trampled)
case("R7 trampled farmland is remove dirt + place farmland", tr_ok, {"trampled": len(trampled)})
wheat = [(p[0], 161, p[2]) for p in trampled]
case("R8 dropped wheat is skipped as crop_cycle, never a step", all(skipped.get(w) == "crop_cycle" and w not in step_pos for w in wheat),
     {"wheat": {str(w): skipped.get(w) for w in wheat}})
ok = sum(c["ok"] for c in cases)
lines = [f"CP-52 fixture [{a.lane}]{' reset' if a.reset else ''} {'PASS' if ok == len(cases) else 'FAIL'}  {time.strftime('%Y-%m-%d %H:%M')}  world={ENV['world_id']}  snapshot={SID}",
         f"{ok}/{len(cases)} cases; crater destroyed {len(solid)} blocks, {len(trampled)} trampled; steps {len(steps)}, manual {len(manual)}",
         *(f"  {'OK ' if c['ok'] else 'BAD'} {c['name']}" for c in cases),
         "fixture left standing for builder; remove it with --clean afterwards"]
(OUT / "RESULT.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
(OUT / "cases.json").write_text(json.dumps(cases, indent=1, default=str), encoding="utf-8")
print("\n".join(lines), flush=True)
sys.exit(0 if ok == len(cases) else 1)
