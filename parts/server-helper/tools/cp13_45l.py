r"""CP-13 live: the storage index the MIND stores (its DB, read-only) agrees with server truth (RCON `data get block`) at
the stated freshness, through Eve as scan centre, content changes, kinds, break/move, another player's take, chunk
unload/reload, the scan-radius bound and a helper restart. Needs eve-sense >= 0.1.5 on the server; Eve online for phase A.
    phase A:  C:\eve\mind\parts\mind\.venv\Scripts\python.exe C:\eve\ops\cp13_45l.py
    restart the server (your normal restart), then
    phase B:  C:\eve\mind\parts\mind\.venv\Scripts\python.exe C:\eve\ops\cp13_45l.py --phase b
"Another player": --other <name> --other-mode driver (that player's client has eve-sense-devtools; local runs) or
--other-mode manual (a real player, e.g. Hunter, takes from a marked, glowing chest; default on the 45L; anyone still near the far
site when it must unload is moved home with a chat notice) or
--other-mode skip (the case is recorded as not run).

FROZEN CRITERIA
Far site F (Eve as scan centre, > chunk_scan_radius from home): stone platform under Eve at home centre + (0, y 230, +448).
Containers: chest C1 (diamond 5, iron_ingot 20), barrel C2 (empty), hopper C3 (coal 3), double chest C4 (both halves;
emerald 1 left), ender chest C5 (must never be indexed), chest E1 in Eve's last scanned chunk (indexed), chest E2 one
chunk past it (never indexed). Restart chest R (redstone 9) in the sky inside home's scan radius.
Pass rule per case: the mind's row and items equal server truth within 5 s (unload/reload within 120 s); loaded=0 rows
carry no items. Phase B: R re-indexed after the restart with an observed_ts later than the restart and exact counts.
PASS = every case as expected (a skipped other-player case keeps CP-13 open, and RESULT says so).
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=r"C:\eve\server-dev")
ap.add_argument("--lane", default="45L", choices=["45L", "shark"], help="which test lane this server is (O18): labels the evidence")
ap.add_argument("--ops", default=r"C:\eve\ops")
ap.add_argument("--db", default=r"C:\eve\mind\eve.db")
ap.add_argument("--out", default=r"C:\eve\logs")
ap.add_argument("--park", default="-15.5 99 -47.5")
ap.add_argument("--phase", default="a", choices=["a", "b"])
ap.add_argument("--other", default=None, help="the other player (default: the first trusted player)")
ap.add_argument("--other-mode", default="manual", choices=["driver", "manual", "skip"])
a = ap.parse_args()
sys.path.insert(0, a.ops)
import rcon  # noqa: E402
from ask_player import ask  # noqa: E402

ROOT = Path(a.root)
ENV = json.loads((ROOT / "config" / "envelope.json").read_text(encoding="utf-8"))
P = ENV["owner"]["eve_account"]
OTHER = a.other or ENV["owner"]["trusted_players"][0]
D = "minecraft:overworld"
HX, HZ = ENV["home"]["center"]["x"], ENV["home"]["center"]["z"]
RAD = ENV["limits"]["chunk_scan_radius"]
OUTROOT = Path(a.out)
STATE = OUTROOT / f"cp13-{a.lane.lower()}-state.json"
OUT = OUTROOT / time.strftime(f"cp13-{a.lane.lower()}-%Y%m%d-%H%M-{a.phase}")
OUT.mkdir(parents=True, exist_ok=True)
LOG = (OUT / "cases.jsonl").open("a", encoding="utf-8")
cases = []

Y = 230
FX, FZ = HX, HZ + 448                                  # far site: Eve's own scan centre, far outside home's
C1, C2, C3, C4L, C4R, C5 = (FX - 2, Y, FZ - 2), (FX + 2, Y, FZ - 2), (FX - 2, Y, FZ + 2), (FX + 2, Y, FZ + 2), (FX + 3, Y, FZ + 2), (FX, Y, FZ - 3)
EVE_F = (FX + 0.5, Y, FZ + 0.5)
ecz = (FZ >> 4) + RAD                                  # Eve's last scanned chunk row
E1, E2 = (FX, Y, ecz * 16 + 8), (FX, Y, (ecz + 1) * 16 + 8)
RCH = (HX + 24, Y + 5, HZ + 8)                         # restart chest, in home's scan radius


def eve_alive():
    """A dead Eve sits on the respawn screen: her position and chunks freeze and every case would fail for that reason."""
    m = re.search(r"([\d.]+)f", rcon.command(ROOT, f"data get entity {P} Health"))
    return m is not None and float(m.group(1)) > 0


if a.phase == "a" and not eve_alive():   # phase B runs after a restart and does not need her
    sys.exit(f"{P} is not online and alive (dead players wait on the respawn screen): fix that first")




def unload_far(timeout=120):
    """Eve has left and the forceload is off; any other player near the far site still keeps its chunks loaded (Hunter
    did in the 45L slot). Move them home with a chat notice, then wait until the chunk really unloads."""
    near = f"@a[name=!{P},x={FX - 256},dx=512,y=-64,dy=384,z={FZ - 256},dz=512]"
    if R(f"execute if entity {near}").startswith("Test passed"):
        R(f"tellraw {near} " + json.dumps({"text": "[CP-13] Moving you home: the test site must unload now. Thanks!", "color": "yellow"}))
        R(f"tp {near} {a.park}")
    end = time.time() + timeout
    while R(f"execute if loaded {xyz(C3)}").startswith("Test passed"):
        if time.time() > end:
            return False
        time.sleep(1)
    return True


def R(cmd):
    out = rcon.command(ROOT, cmd)
    if re.search(r"Unknown|Incorrect|Expected|Invalid|Failed to parse|not loaded|No player was found", out):
        raise RuntimeError(f"command failed: {cmd!r} -> {out!r}")
    return out


def cid(p):
    return f"{D}:{p[0]},{p[1]},{p[2]}"


def truth(p):
    """Server truth: item -> total count in the container at p (None = no container block entity there)."""
    out = rcon.command(ROOT, f"data get block {p[0]} {p[1]} {p[2]} Items")
    if "not a block entity" in out or "Found no elements" in out and "Items" not in out:
        return None
    counts = {}
    for m in re.finditer(r'count:\s*(\d+)[^}]*?id:\s*"([^"]+)"|id:\s*"([^"]+)"[^}]*?count:\s*(\d+)', out):
        item, n = (m.group(2), m.group(1)) if m.group(2) else (m.group(3), m.group(4))
        counts[item] = counts.get(item, 0) + int(n)
    return counts


def mind_row(p):
    db = sqlite3.connect(f"file:{Path(a.db).as_posix()}?mode=ro", uri=True)
    try:
        row = db.execute("SELECT kind, loaded, gone, observed_ts FROM containers WHERE container_id=?", (cid(p),)).fetchone()
        items = dict(db.execute("SELECT item, count FROM container_items WHERE container_id=?", (cid(p),)).fetchall())
    finally:
        db.close()
    return (None if row is None else {"kind": row[0], "loaded": row[1], "gone": row[2], "observed_ts": row[3]}), items


def case(name, ok, detail, **extra):
    row = {"ts": time.strftime("%H:%M:%S"), "name": name, "ok": bool(ok), "detail": detail, **extra}
    cases.append(row)
    LOG.write(json.dumps(row, default=str) + "\n")
    LOG.flush()
    print(f"{'OK ' if ok else 'BAD'} {name}  {detail}", flush=True)


def agree(name, p, kind=None, timeout=5.0, since=0):
    """Poll the mind until its row for p equals server truth (loaded, items, kind) and is newer than `since`."""
    t, end = time.time(), time.time() + timeout
    while True:
        tr = truth(p)
        row, items = mind_row(p)
        ok = (row is not None and row["gone"] == 0 and row["loaded"] == 1 and tr is not None and items == tr
              and (kind is None or row["kind"] == kind) and (row["observed_ts"] or 0) >= since)
        if ok or time.time() > end:
            case(name, ok, f"truth={tr} mind={row} items={items}", latency_s=round(time.time() - t, 2))
            return ok
        time.sleep(0.25)


def poll(name, p, want, timeout):
    """want(row, items) -> bool, polled up to timeout."""
    t, end = time.time(), time.time() + timeout
    while True:
        row, items = mind_row(p)
        if want(row, items) or time.time() > end:
            ok = want(row, items)
            case(name, ok, f"mind={row} items={items}", latency_s=round(time.time() - t, 2))
            return ok
        time.sleep(0.5)


def wait_loaded(p, timeout=90):
    """Fresh terrain can take a while to generate after a teleport; never build into a chunk that is not loaded yet."""
    end = time.time() + timeout
    while not rcon.command(ROOT, f"execute if loaded {p[0]} {p[1]} {p[2]}").startswith("Test passed"):
        if time.time() > end:
            raise RuntimeError(f"chunk at {p} still not loaded after {timeout}s")
        time.sleep(1)


def put(p, block, *items):
    R(f"setblock {p[0]} {p[1]} {p[2]} {block}")
    for slot, (item, n) in enumerate(items):
        R(f"item replace block {p[0]} {p[1]} {p[2]} container.{slot} with {item} {n}")


def xyz(p):
    return " ".join(str(v) for v in p)


t0 = time.time()
print(f"CP-13 live phase {a.phase.upper()}: Eve={P} other={OTHER} ({a.other_mode}) home=({HX},{HZ}) radius={RAD} far=({FX},{FZ})", flush=True)
other_ran = None
if a.phase == "a":
    if FZ - HZ <= 16 * (2 * RAD + 1):
        sys.exit("far site is not outside home's scan radius")
    try:
        # load (and if needed generate) the far site BEFORE Eve goes there: she must land on a platform, not fall
        R(f"forceload add {FX - 4} {FZ - 4} {FX + 4} {FZ + 4}")
        wait_loaded((FX - 4, Y, FZ - 4))
        wait_loaded((FX + 4, Y, FZ + 4))
        R(f"fill {FX - 4} {Y - 1} {FZ - 4} {FX + 4} {Y - 1} {FZ + 4} minecraft:stone")
        R(f"fill {FX - 4} {Y} {FZ - 4} {FX + 4} {Y + 3} {FZ + 4} minecraft:air")
        R(f"tp {P} {xyz(EVE_F)}")
        # The far site stays forceloaded while Eve is there, so its chunks do not depend on her client's render distance;
        # sense's scan centre is her server-side position. The unload cases remove the forceload as she leaves, so only
        # her absence decides.
        time.sleep(2)
        for q in [E1, E2]:   # the radius-edge chunks may lie beyond her client's view: load them for the bound cases
            R(f"forceload add {q[0]} {q[2]}")
            wait_loaded(q)
        R(f"gamemode survival {P}")
        time.sleep(1)
        put(C1, "minecraft:chest", ("minecraft:diamond", 5), ("minecraft:iron_ingot", 20))
        put(C2, "minecraft:barrel")
        put(C3, "minecraft:hopper", ("minecraft:coal", 3))
        R(f"setblock {xyz(C4L)} minecraft:chest[type=left,facing=north]")
        R(f"setblock {xyz(C4R)} minecraft:chest[type=right,facing=north]")
        R(f"item replace block {xyz(C4L)} container.0 with minecraft:emerald 1")
        R(f"setblock {xyz(C5)} minecraft:ender_chest")
        put(E1, "minecraft:chest", ("minecraft:stick", 2))
        put(E2, "minecraft:chest", ("minecraft:stick", 3))

        agree("N01 Eve as scan centre: chest indexed, exact counts", C1, "minecraft:chest")
        R(f"item replace block {xyz(C1)} container.2 with minecraft:gold_ingot 7")
        agree("N02 item added", C1)
        R(f"item replace block {xyz(C1)} container.0 with minecraft:air")
        agree("N03 item removed", C1)
        R(f"item replace block {xyz(C1)} container.3 with minecraft:iron_ingot 10")
        agree("N04 one item over two slots is summed", C1)
        agree("N05 empty barrel indexed with no items", C2, "minecraft:barrel")
        agree("N06 hopper indexed with its kind", C3, "minecraft:hopper")
        agree("N07a double chest: left half", C4L, "minecraft:chest")
        agree("N07b double chest: right half", C4R, "minecraft:chest")
        for i in range(30):
            R(f"item replace block {xyz(C1)} container.4 with minecraft:redstone {i + 1}")
        agree("B01 30 changes in a burst: the last one wins", C1)
        for s in range(27):
            R(f"item replace block {xyz(C2)} container.{s} with {['minecraft:oak_log', 'minecraft:dirt', 'minecraft:sand'][s % 3]} {s + 1}")
        agree("B02 a full barrel (27 slots, 3 items) summed exactly", C2)
        agree("B03 chest in Eve's last scanned chunk is indexed", E1)
        time.sleep(3)
        r2, _ = mind_row(E2)
        case("B04 chest one chunk past the scan radius is never indexed", r2 is None, f"mind={r2}")
        r5, _ = mind_row(C5)
        case("B05 ender chest (per-player, not a container) never indexed", r5 is None, f"mind={r5}")
        for q in [E1, E2]:
            R(f"forceload remove {q[0]} {q[2]}")

        # another player takes items (CP-13: "have another player take reserved supplies")
        before = truth(C1)
        if a.other_mode == "driver":
            m = re.search(r"([\d.]+)f", rcon.command(ROOT, f"data get entity {OTHER} Health"))
            if not m or float(m.group(1)) <= 0:
                raise RuntimeError(f"{OTHER} is not online and alive")
            R(f"tp {OTHER} {FX - 0.5} {Y} {FZ - 0.5}")
            time.sleep(1.5)
            R(f"evetest drive {OTHER} " + json.dumps({"do": "open", "pos": list(C1), "face": "up"}))
            time.sleep(1.0)
            R(f"evetest drive {OTHER} " + json.dumps({"do": "click", "slot": 2, "input": "QUICK_MOVE"}))
            time.sleep(0.6)
            R(f"evetest drive {OTHER} " + json.dumps({"do": "close"}))
            time.sleep(0.5)
        elif a.other_mode == "manual":
            online = OTHER in R("list")
            if online:
                ask(rcon, ROOT, OTHER, "[CP-13] Open the marked chest and take the gold ingots out of it.",
                    [(C1, "TAKE THE GOLD", "minecraft:chest")],
                    lambda: (truth(C1) or {}).get("minecraft:gold_ingot", 0) != before.get("minecraft:gold_ingot", 0),
                    lambda: "the gold ingots are still in the chest", stand=(C1[0] + 0.5, Y, C1[2] + 3.5))
            else:
                print(f"NOTE {OTHER} is not online: the other-player case is not run", flush=True)
        after = truth(C1)
        other_ran = after is not None and after.get("minecraft:gold_ingot", 0) < before.get("minecraft:gold_ingot", 0)
        if other_ran:
            agree(f"N08 another player ({OTHER}) took items: the index follows", C1)
        else:
            case(f"N08 another player ({OTHER}) took items", False, f"not run ({a.other_mode}); truth unchanged {after}", skipped=True)
        if a.other_mode == "driver":
            R(f"tp {OTHER} {a.park}")

        R(f"setblock {xyz(C1)} minecraft:air")
        poll("N09 chest broken: marked gone, no items", C1, lambda r, it: r is not None and r["gone"] == 1 and not it, 5)
        NEW = (C1[0], C1[1], C1[2] + 1)
        put(NEW, "minecraft:chest", ("minecraft:diamond", 2))
        agree("N10 chest moved: the new position is indexed", NEW)
        r1, _ = mind_row(C1)
        case("N10b ... and the old position stays gone", r1 is not None and r1["gone"] == 1, f"mind={r1}")
        R(f"setblock {FX} {Y} {FZ + 3} minecraft:chest")
        R(f"setblock {FX} {Y} {FZ + 3} minecraft:air")
        time.sleep(3)
        rb, ib = mind_row((FX, Y, FZ + 3))
        case("B06 placed and broken within a second: never a live entry", rb is None or (rb["gone"] == 1 and not ib), f"mind={rb} items={ib}")

        R(f"tp {P} {a.park}")                          # Eve leaves: the far chunks unload
        R(f"forceload remove {FX - 4} {FZ - 4} {FX + 4} {FZ + 4}")
        if not unload_far():
            for n in ("N11 chunk unloaded: labelled loaded=0, no items promised", "B07 every unloaded entry carries no items"):
                case(n, False, "not run: the far chunk stayed loaded 120 s after everyone left (not a sense fault)", skipped=True)
        else:
            poll("N11 chunk unloaded: labelled loaded=0, no items promised", C3,
                 lambda r, it: r is not None and r["loaded"] == 0 and r["gone"] == 0 and not it, 120)
            r2u, i2u = mind_row(C2)
            case("B07 every unloaded entry carries no items", r2u is not None and r2u["loaded"] == 0 and not i2u, f"mind={r2u} items={i2u}")
        R(f"forceload add {FX - 4} {FZ - 4} {FX + 4} {FZ + 4}")
        R(f"tp {P} {xyz(EVE_F)}")
        agree("N12 chunk reloaded: loaded=1 and exact counts again", C3, timeout=60)
        agree("N12b ... the barrel too", C2, timeout=10)

        # restart fixture in home's radius; its chunk stays forceloaded (saved with the world) so it is loaded right after
        # the restart whether or not Eve rejoins first; phase B removes the forceload
        R(f"forceload add {RCH[0]} {RCH[2]}")
        put(RCH, "minecraft:chest", ("minecraft:redstone", 9))
        agree("B08 chest in home's radius indexed while Eve is away from it", RCH)
        R(f"tp {P} {a.park}")
        R(f"forceload remove {FX - 4} {FZ - 4} {FX + 4} {FZ + 4}")
        unload_far()
        far = [C2, C3, C4L, NEW]
        poll("N14 Eve leaves again: every far entry labelled unloaded, no items", C3,
             lambda r, it: all((mind_row(q)[0] or {}).get("loaded") == 0 and not mind_row(q)[1] for q in far), 120)
        STATE.write_text(json.dumps({"phase_a_end_ms": int(time.time() * 1000), "restart_chest": RCH,
                                     "far": [C2, C3, C4L, NEW]}), encoding="utf-8")
    finally:
        rcon.command(ROOT, f"tp {P} {a.park}")
        rcon.command(ROOT, f"forceload remove {FX - 4} {FZ - 4} {FX + 4} {FZ + 4}")
        for q in [E1, E2]:
            rcon.command(ROOT, f"forceload remove {q[0]} {q[2]}")
else:
    st = json.loads(STATE.read_text(encoding="utf-8"))
    rch = tuple(st["restart_chest"])
    agree("N13 after the helper restart: re-indexed with exact counts, observed after the restart", rch, timeout=60,
          since=st["phase_a_end_ms"])
    obs = {}
    for p in st["far"]:
        r, it = mind_row(tuple(p))
        obs[cid(p)] = r
    case("N13b far containers after the restart (unloaded, not in the new full index): no items promised",
         all(not mind_row(tuple(p))[1] for p in st["far"]), f"mind rows={obs}")
    for c in [f"setblock {xyz(rch)} minecraft:air", f"forceload remove {rch[0]} {rch[2]}"]:
        rcon.command(ROOT, c)

ok = sum(c["ok"] for c in cases)
skipped = [c["name"] for c in cases if c.get("skipped")]
verdict = "PASS" if ok == len(cases) else "FAIL"
lines = [f"CP-13 live [{a.lane}] phase {a.phase.upper()} {verdict}  {time.strftime('%Y-%m-%d %H:%M')}  world={ENV['world_id']}  eve={P}",
         f"{ok}/{len(cases)} cases as expected" + (f"; NOT RUN: {skipped}" if skipped else ""),
         *(f"  {'OK ' if c['ok'] else 'BAD'} {c['name']}" + (f"  ({c['latency_s']} s)" if "latency_s" in c else "") for c in cases),
         f"elapsed {time.time() - t0:.0f}s"]
(OUT / "RESULT.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines), flush=True)
sys.exit(0 if verdict == "PASS" else 1)
