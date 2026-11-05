r"""CP-14 on the 45L: query.blocks / query.resources on the real world, with the deployed eve-sense jar's own query code
(through the console-only `/evetest query` of eve-sense-devtools >= 0.1.4), every reply validated with the mind's own
hub validators (eve_mind.hub). Expected answers are written here by hand for a fixture this script builds.
    C:\eve\mind\parts\mind\.venv\Scripts\python.exe C:\eve\ops\cp14_45l.py
Writes <out>\cp14-45l-<stamp>\{cases.jsonl,RESULT.txt}. Needs eve-sense >= 0.1.4 and devtools >= 0.1.4 on the server
(Eve's client is not involved). The link transport of these frames is covered by the local live-link run
(evidence/CP-14/...-attempt-3-live-link: the same jar over the real WebSocket to a hub, replies accepted by the same
validators).

FROZEN CRITERIA
Fixture in the HOME zone sky (envelope-checked): stone cube x -10..-6, y 220..224, z -70..-66 with coal_ore (-9,224,-67)
on top, iron_ore (-10,222,-68) on the west face, diamond_ore (-7,223,-67) enclosed; wheat age 3 on farmland at
(-2,221,-70) (home zone) and wheat age 5 at (20,221,-70) (gap: no zone). Random ticks are paused during the run.
12 normal cases (states exact, hidden, ore/logs/crops policy, refresh after a change, order) and 12 boundary/malformed
cases (volume 32768 ok / 32769 refused, swapped corners, y outside the world, missing/unknown dim, missing/non-numeric
corner, radius 0/33 refused and 32 ok, unknown kind, never-loaded area labelled and still not loaded). PASS = 24/24 and
every reply accepted by the mind's validators.
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
ap.add_argument("--ops", default=r"C:\eve\ops", help="folder holding foundation's rcon.py")
ap.add_argument("--mind-src", default=r"C:\eve\mind\parts\mind\src")
ap.add_argument("--out", default=r"C:\eve\logs")
a = ap.parse_args()
sys.path.insert(0, a.ops)
sys.path.insert(0, a.mind_src)
import rcon  # noqa: E402
from eve_mind import hub  # noqa: E402  the mind's own validators

ROOT = Path(a.root)
ENV = json.loads((ROOT / "config" / "envelope.json").read_text(encoding="utf-8"))
OUT = Path(a.out) / time.strftime(f"cp14-{a.lane.lower()}-%Y%m%d-%H%M")
OUT.mkdir(parents=True, exist_ok=True)
LOG = (OUT / "cases.jsonl").open("a", encoding="utf-8")
D = "minecraft:overworld"
cases = []


def R(cmd):
    return rcon.command(ROOT, cmd)


def zone_of(p):
    hits = [z for z in ENV["zones"] if z["dim"] == D and all(z["min"][k] <= v <= z["max"][k] for k, v in zip("xyz", p))]
    return ",".join(sorted(z["id"] for z in hits)) or "none"


def xyz(x, y, z):
    return {"x": x, "y": y, "z": z}


LATEST = ROOT / "logs" / "latest.log"


def query(kind, payload):
    """The full reply comes from the server log: RCON (foundation's rcon.py) returns only one 4 KB packet."""
    start = LATEST.stat().st_size
    R(f"evetest query {kind} " + json.dumps(payload, separators=(",", ":")))
    for _ in range(50):
        with LATEST.open("rb") as f:
            f.seek(start)
            text = f.read().decode("utf-8", "replace")
        m = re.search(r"\[evetest\] query -> (\{.*\})\s*$", text, re.M)
        if m:
            return json.loads(m.group(1))
        time.sleep(0.1)
    return {"code": "no_reply", "message": "no [evetest] query line in latest.log"}


def valid(kind, reply):
    if "code" in reply:  # an error frame payload
        return [e.message for e in hub._PAYLOAD_V["sense"]["error"].iter_errors(reply)]
    return [e.message for e in hub._PAYLOAD_V["sense"][f"query.{kind}.result"].iter_errors(reply)]


def case(name, expected, observed, schema=None):
    ok = expected == observed and not schema
    row = {"name": name, "ok": ok, "expected": expected, "observed": observed, "schema_errors": schema or []}
    cases.append(row)
    LOG.write(json.dumps(row) + "\n")
    LOG.flush()
    print(f"{'OK ' if ok else 'BAD'} {name}" + ("" if ok else f"\n    expected {expected}\n    observed {observed} {schema or ''}"), flush=True)


def blocks(mn, mx):
    r = query("blocks", {"dim": D, "min": xyz(*mn), "max": xyz(*mx)})
    return r, valid("blocks", r)


def at(r, x, y, z):
    mn, mx = r["min"], r["max"]
    dx, dz = mx["x"] - mn["x"] + 1, mx["z"] - mn["z"] + 1
    return r["palette"][r["data"][((y - mn["y"]) * dz + (z - mn["z"])) * dx + (x - mn["x"])]]


def resources(kind, center, radius):
    r = query("resources", {"kind": kind, "dim": D, "center": xyz(*center), "radius": radius})
    return r, valid("resources", r)


def items(r, age=False):
    return sorted(f"{i['pos']['x']},{i['pos']['y']},{i['pos']['z']} {i['block']}" + (f" age={i['state']['age']}" if age else "")
                  for i in r.get("items", []))


def code(r):
    return r.get("code", "ok")


want = {(-8, 222, -68): "home", (-2, 221, -70): "home", (20, 221, -70): "none"}
bad = [(p, w, zone_of(p)) for p, w in want.items() if zone_of(p) != w]
if bad:
    sys.exit(f"envelope does not match the frozen fixture: {bad}")
if "query" not in R("evetest query blocks {}") and "code" not in R("evetest query blocks {}"):
    sys.exit("evetest query not available: deploy eve-sense-devtools >= 0.1.4")

t0 = time.time()
tick_rule = None
try:
    R("forceload add -12 -72 22 -64")
    for name in ("random_tick_speed", "randomTickSpeed"):  # whichever this version uses
        m = re.search(r"(\d+)", R(f"gamerule {name}"))
        if m and "Unknown" not in R(f"gamerule {name}"):
            tick_rule = (name, m.group(1))
            R(f"gamerule {name} 0")
            break
    for c in ["fill -12 219 -72 22 226 -64 minecraft:air", "fill -10 220 -70 -6 224 -66 minecraft:stone",
              "setblock -9 224 -67 minecraft:coal_ore", "setblock -10 222 -68 minecraft:iron_ore", "setblock -7 223 -67 minecraft:diamond_ore",
              "setblock -2 220 -70 minecraft:farmland", "setblock -2 221 -70 minecraft:wheat[age=3]",
              "setblock 20 220 -70 minecraft:farmland", "setblock 20 221 -70 minecraft:wheat[age=5]"]:
        R(c)
    time.sleep(1)

    r, s = blocks((-10, 224, -70), (-6, 225, -66))
    wrong = [f"{x},{y},{z}" for x in range(-10, -5) for z in range(-70, -65) for y in (224, 225)
             if at(r, x, y, z) != ("minecraft:air" if y == 225 else "minecraft:coal_ore" if (x, z) == (-9, -67) else "minecraft:stone")]
    case("N01 top layer + air exact (50 blocks)", [], wrong, s)
    r, s = blocks((-7, 223, -67), (-7, 223, -67))
    case("N02 enclosed diamond ore is hidden", "hidden", at(r, -7, 223, -67), s)
    r, s = blocks((-8, 222, -68), (-8, 222, -68))
    case("N03 enclosed stone is hidden", "hidden", at(r, -8, 222, -68), s)
    r, s = blocks((-10, 222, -68), (-10, 222, -68))
    case("N04 west-face ore is visible", "minecraft:iron_ore", at(r, -10, 222, -68), s)
    r, s = resources("ore", (-8, 222, -68), 6)
    case("N05 ore: exposed only", ["-10,222,-68 minecraft:iron_ore", "-9,224,-67 minecraft:coal_ore"], items(r), s)
    case("N06 ore policy reported from the envelope", ENV["sensors"]["ore"], r.get("policy"))
    r, s = resources("logs", (-8, 222, -68), 6)
    case("N07 logs: none in the fixture", [], items(r), s)
    r, s = resources("crops", (9, 221, -70), 12)
    case("N08 crops: zones only, with age", ["-2,221,-70 minecraft:wheat age=3"], items(r, True), s)
    case("N09 crops policy reported from the envelope", ENV["sensors"]["crops"], r.get("policy"))
    R("setblock -9 224 -67 minecraft:stone")
    R("setblock -2 221 -70 minecraft:wheat[age=7]")
    time.sleep(0.5)
    r, s = resources("ore", (-8, 222, -68), 6)
    case("N10 after mining the coal: refreshed", ["-10,222,-68 minecraft:iron_ore"], items(r), s)
    r, s = resources("crops", (9, 221, -70), 12)
    case("N11 after the wheat ripens: refreshed", ["-2,221,-70 minecraft:wheat age=7"], items(r, True), s)
    r, s = blocks((-10, 220, -70), (-9, 221, -69))
    case("N12 order x,z,y over a 2x2x2 corner (stone; only the enclosed -9,221,-69 hidden)",
         ["minecraft:stone"] * 7 + ["hidden"],   # the bottom layer touches the air below it; only (-9,221,-69) is enclosed
         [at(r, -10 + (i & 1), 220 + (i >> 2), -70 + ((i >> 1) & 1)) for i in range(8)], s)

    r, s = blocks((-16, 200, -80), (15, 231, -49))
    case("B01 volume exactly 32768 answered", 32768, len(r.get("data", [])), s)
    r, s = blocks((0, 200, 0), (330, 208, 10))
    case("B02 volume 32769 refused", "bad_payload", code(r), s)
    r1, s1 = blocks((-10, 224, -70), (-6, 225, -66))
    r2, s2 = blocks((-6, 225, -66), (-10, 224, -70))
    case("B03 swapped corners give the same answer", r1, r2, s1 + s2)
    r, s = blocks((0, -70, 0), (0, -64, 0))
    case("B04 y below the world refused", "bad_payload", code(r), s)
    r, s = blocks((0, 319, 0), (0, 321, 0))
    case("B05 y above the world refused", "bad_payload", code(r), s)
    r = query("blocks", {"min": xyz(0, 64, 0), "max": xyz(0, 64, 0)})
    case("B06 missing dim refused", "bad_payload", code(r), valid("blocks", r))
    r = query("blocks", {"dim": "minecraft:nowhere", "min": xyz(0, 64, 0), "max": xyz(0, 64, 0)})
    case("B07 unknown dim refused", "bad_payload", code(r), valid("blocks", r))
    r = query("blocks", {"dim": D, "min": {"x": "a", "y": 64, "z": 0}, "max": xyz(0, 64, 0)})
    case("B08 non-numeric corner refused", "bad_payload", code(r), valid("blocks", r))
    r, s = resources("ore", (-8, 222, -68), 0)
    case("B09 radius 0 refused", "bad_payload", code(r), s)
    r, s = resources("ore", (-8, 222, -68), 33)
    case("B10 radius 33 refused; 32 answered", ["bad_payload", "ok"], [code(r), code(resources("ore", (-8, 222, -68), 32)[0])], s)
    r = query("resources", {"kind": "diamonds", "dim": D, "center": xyz(-8, 222, -68), "radius": 4})
    case("B11 unknown kind refused", "bad_payload", code(r), valid("resources", r))
    r, s = blocks((100000, 64, 100000), (100003, 64, 100003))
    still = "not loaded" in R("execute if block 100000 64 100000 minecraft:air")
    case("B12 never-loaded area labelled, and still not loaded afterwards", [["unloaded"], 1, True],
         [r.get("palette"), len(r.get("unloaded", [])), still], s)
finally:
    for c in ["fill -12 219 -72 22 226 -64 minecraft:air", "forceload remove -12 -72 22 -64"]:
        R(c)
    if tick_rule:
        R(f"gamerule {tick_rule[0]} {tick_rule[1]}")

ok = sum(c["ok"] for c in cases)
verdict = "PASS" if ok == len(cases) == 24 else "FAIL"
lines = [f"CP-14 on the {a.lane} (eve-sense query code via /evetest query) {verdict}  {time.strftime('%Y-%m-%d %H:%M')}  world={ENV['world_id']}",
         f"{ok}/{len(cases)} cases as expected; replies validated with the mind's hub validators", *(f"  {'OK ' if c['ok'] else 'BAD'} {c['name']}" for c in cases),
         f"elapsed {time.time() - t0:.0f}s"]
(OUT / "RESULT.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines), flush=True)
sys.exit(0 if verdict == "PASS" else 1)
