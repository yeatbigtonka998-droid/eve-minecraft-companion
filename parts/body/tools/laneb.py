"""Body's Phase 3 reflex fixtures through a REAL Eve stack: lane B (eve-watch's Shark stack, C:\\eve-shark, mind 8955, dev account
EveDev, label `shark`) or the 45L (foundation runs it ON the 45L: C:\\eve, mind 8951, Eve's account FED_Chairman, label `45L`).
Same fixtures and criteria as tools/reflex.py; every action goes through the mind's /cli (`eve do`), body events are read from
that mind's DB (read-only), and server truth is RCON. Evidence: evidence/<CP>/<stamp>-<label>-attempt-N.

    py -3.12 tools/laneb.py [eat,retreat,...] [--n 20] [--lane shark|shark-c|45L] [--cp CP-20] [--difficulty easy|normal]

The arena floats at EVE_ARENA_Y (default 200) around x/z 0, with a glass rim and a catch floor 8 below, force-loaded for the run.
Gamerules and difficulty it changes are read first and put back; every kill is limited to the arena. Ends by re-parking Eve
(park-eve.ps1, O15). Lane B results are development evidence (`shark`), not qualification.
"""
import asyncio, json, math, os, re, sqlite3, subprocess, sys, threading, time
from datetime import datetime
from pathlib import Path

def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


LANES = {  # label: (root, token file under root, mind /cli port, player)
    "shark": (Path(r"C:\eve-shark"), "secrets/eve-link-token.txt", 8955, "EveDev"),
    "shark-c": (Path(r"C:\eve-shark-c"), "secrets/eve-link-token.txt", 8956, "EveDev2"),  # eve-watch's lane C (S21)
    "shark-d": (Path(r"C:\eve-shark-d"), "secrets/eve-link-token.txt", 8958, "EveDev3"),  # lanes D/E (S27b)
    "shark-e": (Path(r"C:\eve-shark-e"), "secrets/eve-link-token.txt", 8960, "EveDev4"),
    "45L": (Path(r"C:\eve"), "mind/config/eve-link-token.txt", 8951, "FED_Chairman"),
}
# O104: a lane can come from a config file instead of the table: lane F on the 45L (C:\eve-lanes\F, eve-watch's 45L-LANE-F-PLAN),
# run there as eveuser from F's own src. Keys: label, root, token_file (under root), cli_port, player, evidence_root, host,
# body_jar, pins {body_jar_sha256, mind_src, registry_hash}. Never live Eve's tree (C:\eve) or her account (FED_Chairman).
LC = json.loads(Path(arg("--lane-config", "")).read_text(encoding="utf-8")) if "--lane-config" in sys.argv else {}
if LC:
    LANES[LC["label"]] = (Path(LC["root"]), LC["token_file"], int(LC["cli_port"]), LC["player"])
    if "--lane" not in sys.argv:
        sys.argv += ["--lane", LC["label"]]
LABEL = arg("--lane", "shark")
LANE, TOKEN_FILE, CLI_PORT, PLAYER = LANES[LABEL]
LIVE_ROOT = Path(r"C:\eve")
if LC and (LIVE_ROOT in (LANE, *LANE.parents) or PLAYER == "FED_Chairman"):
    sys.exit(f"O104: a configured lane must never be live Eve's tree or account (root {LANE}, player {PLAYER}); nothing done")
EVIDENCE = Path(LC["evidence_root"]) if LC.get("evidence_root") else None  # None: the repo's evidence/ (the Shark lanes)
os.environ.setdefault("EVE_SERVER_PROPS", str(LANE / "server-dev" / "server.properties"))
os.environ.setdefault("EVE_DIFFICULTY", arg("--difficulty", "easy"))  # O15: the 45L is peaceful between runs; survival runs say `normal
os.environ.setdefault("EVE_ARENA_Y", "200")
if LABEL != "45L":  # I28a: on a Shark lane the guard passes this harness's writes only under eve-body's own lock (the 45L: foundation's runs)
    os.environ.setdefault("EVE_LOCK_BY", "eve-body")
sys.path.insert(0, str(Path(__file__).parent))
import arena  # noqa: E402
import reflex  # noqa: E402
from websockets.asyncio.client import connect  # noqa: E402

def dry_run():
    """O104: every path, bind and pin a run on this lane would use, checked read-only: no token read, no RCON, no hub, nothing
    launched. Prints one line per check and exits 0 only when all hold."""
    rows = []

    def check(name, ok, detail=""):
        rows.append((bool(ok), name, str(detail)))

    check("lane root", LANE.is_dir(), LANE)
    check("not live Eve's tree", LIVE_ROOT not in (LANE, *LANE.parents), LANE)
    check("not live Eve's account", PLAYER != "FED_Chairman", PLAYER)
    sp = LANE / "server-dev" / "server.properties"
    props = dict(l.split("=", 1) for l in sp.read_text().splitlines() if "=" in l and not l.startswith("#")) if sp.is_file() else {}
    check("server.properties", sp.is_file(), sp)
    check("rcon enabled", props.get("enable-rcon") == "true", props.get("rcon.port"))
    if LC:  # lane F: loopback only (plan section 1); the Shark lanes keep their own binds
        check("server bound to 127.0.0.1", props.get("server-ip") == "127.0.0.1", props.get("server-ip"))
        check("lane-local cli port", CLI_PORT not in (8951,), CLI_PORT)
    for rel in (TOKEN_FILE, "mind/eve.db", "mind/config/envelope.json", "ops/safe_tp.py", "ops/lane-lock.ps1", "ops/park-eve.ps1"):
        check(rel, (LANE / rel).exists(), LANE / rel)
    fm = [p for p in (LANE / "ops" / "fixture-map-45l.json", arena.REPO / "parts" / "foundation" / "ops" / "fixture-map-45l.json") if p.exists()]
    check("fixture map (I26a)", fm, fm[0] if fm else "none")
    ev = EVIDENCE or arena.REPO / "evidence"
    check("evidence root", ev.is_dir() or ev.parent.is_dir(), ev)
    pins = LC.get("pins", {})
    if LC.get("pins_file"):  # lane F: eve-watch's roll writes C:\eve-lanes\pins\laneF.json (body, body_src, mind, src_min, registry)
        pf = Path(LC["pins_file"])
        check("pins file (written by the roll)", pf.is_file(), pf)
        if pf.is_file():
            pj = json.loads(pf.read_text(encoding="utf-8"))
            reg = str(pj.get("registry", "")).split()
            pins = {"body_jar_sha256": pj.get("body"), "mind_src": pj.get("mind"), "registry_hash": reg[-1] if reg else None,
                    "body_src": pj.get("body_src"), "src_min": pj.get("src_min")}
            check("pins name a mind and a body", pins["mind_src"] and pins["body_jar_sha256"], f"mind {pins['mind_src']} body {str(pins['body_jar_sha256'])[:12]}")
    if LC.get("body_jar"):
        jar = Path(LC["body_jar"])
        sha = hashlib.sha256(jar.read_bytes()).hexdigest() if jar.is_file() else None
        check("body jar pin", sha and sha == pins.get("body_jar_sha256"), f"{jar} {sha and sha[:12]} want {str(pins.get('body_jar_sha256'))[:12]}")
    if pins.get("mind_src"):
        src = LANE / "src"
        r = subprocess.run(["git", "-C", str(src), "diff", "--ignore-cr-at-eol", "--quiet", pins["mind_src"], "--", "parts/mind/src"], capture_output=True)
        check("mind src pin", r.returncode == 0, f"{pins['mind_src']} (git exit {r.returncode})")
    if pins.get("registry_hash"):
        reg = LANE / "src" / "contracts" / "skills" / "registry.json"
        h = hashlib.sha256(json.dumps(json.loads(reg.read_text(encoding="utf-8")), sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False).encode("utf-8")).hexdigest() if reg.is_file() else None
        check("registry pin", h and h.startswith(pins["registry_hash"]), f"{h and h[:12]} want {pins['registry_hash'][:12]}")
    lh = arena.REPO / "parts" / "watch" / "laneb" / "lane_health.py"
    check("lane_health (I29f)", lh.is_file(), f"{lh} --host {LC.get('host', 'shark')}")
    for ok, name, detail in rows:
        print(("OK   " if ok else "FAIL ") + f"{name}: {detail}")
    bad = [n for ok, n, _ in rows if not ok]
    print(f"dry-run {LABEL}: {len(rows) - len(bad)}/{len(rows)} checks hold" + (f"; FAIL: {', '.join(bad)}" if bad else ""))
    sys.exit(0 if not bad else 6)


if "--dry-run" in sys.argv:
    import hashlib
    dry_run()

DB = f"file:{(LANE / 'mind' / 'eve.db').as_posix()}?mode=ro"
TOKEN = (LANE / TOKEN_FILE).read_text().strip()


async def _call(cmd, args, timeout):
    async with connect(f"ws://127.0.0.1:{CLI_PORT}/cli", open_timeout=3, max_size=4 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"token": TOKEN, "cmd": cmd, "args": args}))
        return json.loads(await asyncio.wait_for(ws.recv(), timeout))


def eve(cmd, args=None, timeout=60):
    try:
        return asyncio.run(_call(cmd, args or {}, timeout))
    except Exception as e:  # mind restarting: record, do not crash the suite
        return {"ok": False, "error": "cli_exception", "detail": f"{type(e).__name__}: {e}"}


def db_rows(sql, args=()):
    c = sqlite3.connect(DB, uri=True)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


class Handle:
    """An `eve do` running in a thread (the mind's reply comes when the action ends)."""

    def __init__(self, skill, params, wait):
        self.reply, self.action_id = None, None
        self.t = threading.Thread(target=self._run, args=(skill, params, wait), daemon=True)
        self.t.start()

    def _run(self, skill, params, wait):
        self.reply = eve("do", {"skill": skill, "params": params, "wait": wait}, wait + 30)
        self.action_id = self.reply.get("action_id")


def as_result(reply):
    """Map an `eve do` reply onto the stub's action.result / action.rejected shapes used by reflex.py."""
    if reply is None:
        return None
    if "status" in reply:
        return reply
    return {"code": reply.get("code") or reply.get("error"), "reason": reply.get("detail")}


def fixture_map():
    """I26a: foundation's fixture map (stashes, every part's run boxes): the lane's ops copy, else the repo's."""
    for f in (LANE / "ops" / "fixture-map-45l.json", arena.REPO / "parts" / "foundation" / "ops" / "fixture-map-45l.json"):
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    sys.exit("I26a: no fixture-map-45l.json (lane ops or repo): cannot tell which cells belong to other parts; nothing changed")


def foreign_boxes(m, rcon=None):
    """Cells of other parts this harness must never write (overworld): their O59 stash barrels always, and their declared stash
    and chest boxes whenever anything is in them (45L 2026-10-21: combat's barrels (26, 205, -13/-12) and builder's CP-51 chest
    row sat inside the arena clear). An all-air declared box (the usual case, e.g. the chest row between builder runs) is not
    excluded, so the catch floor stays whole; rcon=None excludes every declared box."""
    out = [tuple(c) * 2 for s in m["stashes"] if s["part"] != "body" for c in s.get("barrels", [])]
    for part, rs in m["runs"].items():
        for x in rs if part != "body" else []:
            if x.get("dim") != "overworld" or not re.search(r"stash|chest", x.get("what", ""), re.I):
                continue
            b, h = x["box"], x["box"][4] - x["box"][1]
            empty = rcon is not None and _t(rcon, f"execute if blocks {b[0]} {b[1]} {b[2]} {b[3]} {b[4]} {b[5]} {b[0]} {300} {b[2]} all")                 and _t(rcon, f"execute if block {b[0]} {300 + h} {b[2]} minecraft:air")  # compared against open sky at y 300+
            if not empty:
                out.append(tuple(b))
    return out


def foreign_stash(rcon, m):
    """Read-only: every other part's stash that is out now (a record file, or its barrels holding items). [] = none."""
    out = []
    for s in m["stashes"]:
        if s["part"] == "body":
            continue
        if "file" in s:  # the 45L path as mapped, and the same record name for this lane under the repo's evidence
            name = Path(s["file"].replace("\\\\", "\\")).name
            for f in (Path(s["file"]), (EVIDENCE or arena.REPO / "evidence") / name.replace("45L", LABEL)):
                if f.exists():
                    out.append(f"{s['part']} stash record {f}")
        for x, y, z in s.get("barrels", []):
            for _ in range(20):  # a chunk force-loaded a moment ago may not be readable yet
                got = rcon(f"data get block {x} {y} {z} Items")
                if "not loaded" not in got:
                    break
                time.sleep(0.5)
            if "not loaded" in got:
                out.append(f"{s['part']} stash barrel ({x}, {y}, {z}) unreadable (chunk not loaded)")
            elif re.findall(r'id: "', got):
                out.append(f"{s['part']} stash barrel ({x}, {y}, {z}) holds items")
    return out


def _tiles(b, excl):
    """Box b minus every excluded box, as boxes of at most 32768 blocks (vanilla's fill limit)."""
    parts = [b]
    for e in excl:
        nxt = []
        for p in parts:
            if not all(p[i] <= e[i + 3] and e[i] <= p[i + 3] for i in range(3)):
                nxt.append(p)
                continue
            c = [max(p[i], e[i]) for i in range(3)] + [min(p[i + 3], e[i + 3]) for i in range(3)]
            if p[0] < c[0]: nxt.append((p[0], p[1], p[2], c[0] - 1, p[4], p[5]))  # noqa: E701
            if c[3] < p[3]: nxt.append((c[3] + 1, p[1], p[2], p[3], p[4], p[5]))  # noqa: E701
            if p[1] < c[1]: nxt.append((c[0], p[1], p[2], c[3], c[1] - 1, p[5]))  # noqa: E701
            if c[4] < p[4]: nxt.append((c[0], c[4] + 1, p[2], c[3], p[4], p[5]))  # noqa: E701
            if p[2] < c[2]: nxt.append((c[0], c[1], p[2], c[3], c[4], c[2] - 1))  # noqa: E701
            if c[5] < p[5]: nxt.append((c[0], c[1], c[5] + 1, c[3], c[4], p[5]))  # noqa: E701
        parts = nxt
    out = []
    while parts:
        p = parts.pop()
        dims = [p[i + 3] - p[i] + 1 for i in range(3)]
        if dims[0] * dims[1] * dims[2] <= 32768:
            out.append(p)
            continue
        i = dims.index(max(dims))
        mid = p[i] + dims[i] // 2 - 1
        lo, hi = list(p), list(p)
        lo[i + 3], hi[i] = mid, mid + 1
        parts += [tuple(lo), tuple(hi)]
    return out


EXCL = []  # set by LaneB.fixture from the fixture map (I26a)


def fills(x1, y1, z1, x2, y2, z2, block):
    """fill commands for the box, around every other part's stash and chest cells (EXCL)."""
    return [f"fill {t[0]} {t[1]} {t[2]} {t[3]} {t[4]} {t[5]} {block}" for t in _tiles((x1, y1, z1, x2, y2, z2), EXCL)]


def clear_cmds(F):
    """The arena volume (53 x 19 x 53) cleared to air, as fills under vanilla's 32768-block limit (one fill silently refused it
    until 2026-10-20), around other parts' stash and chest cells (I26a)."""
    return fills(-26, F - 8, -26, 26, F + 10, 26, "minecraft:air")


# O59/O73: her whole inventory goes back exactly at the end of every laneb run (45L CP-33 a1 kept 11 cobblestone from the
# break-budget trials, and the fixture's own `clear` + kit had been replacing whatever she carried). Before the fixture touches
# it, every slot (components and all: damage, enchantments, names) is copied with `item replace ... from entity` into two
# barrels in the arena's corner above every fill (x -24/-23, y F+12, z 24: inside reflex_arena x/z -26..26 y 190..215; the
# suites fill up to F+10 and drop her at x 0..13). At the end, once she is parked, `clear` + every slot copied back. Pass =
# start == end on the body's own inventory_hash (the one foundation's guard records) and on the server's text. The record
# file lives while her items are out: a crashed run leaves it with the barrels, and the next run refuses to start until
# `laneb.py restore_inventory --lane X` has put them back.
SLOTS = [f"hotbar.{i}" for i in range(9)] + [f"inventory.{i}" for i in range(27)] + \
    ["armor.head", "armor.chest", "armor.legs", "armor.feet", "weapon.offhand"]
STASH_FILE = (EVIDENCE or arena.REPO / "evidence") / f"body-stash-{LABEL}.json"


def stash_cells():
    F = int(arena.FLOOR)
    return [(-24, F + 12, 24), (-23, F + 12, 24)]  # 2 x 27 slots >= 41


def inv_text(rcon, p):
    """Inventory + equipment as the server prints them; an absent field reads as empty (after a dimension change the server
    omits an empty `equipment`: builder O60b)."""
    out = []
    for k, empty in (("Inventory", "[]"), ("equipment", "{}")):
        t = rcon(f"data get entity {p} {k}")
        out.append(t.split("entity data:", 1)[1].strip() if "entity data:" in t else empty)
    return "".join(out)


def body_inv_hash(after_ms=0, wait=10.0):
    """The body's inventory_hash from the newest state row at or after after_ms (the body sends one on every change)."""
    end = time.time() + wait
    while True:
        rows = db_rows("SELECT payload FROM observations WHERE kind='state' AND ts >= ? ORDER BY id DESC LIMIT 1", (after_ms,))
        if rows or time.time() > end:
            return json.loads(rows[0][0]).get("inventory_hash") if rows else None
        time.sleep(0.5)


def _stacks(rcon, what):
    return len(re.findall(r'id: "', rcon(f"data get {what}")))


def _drop_barrels(rcon, cells):
    for x, y, z in cells:
        rcon(f"data merge block {x} {y} {z} {{Items:[]}}")
        rcon(f"setblock {x} {y} {z} minecraft:air")


def park_boxes():
    """The fill boxes of the lane's park-eve.ps1 (I19: a hollow fill wipes any container inside it)."""
    pk = LANE / "ops" / "park-eve.ps1"
    txt = pk.read_text(encoding="utf-8", errors="replace") if pk.exists() else ""
    return [tuple(map(int, m)) for m in re.findall(r"fill (-?\d+) (-?\d+) (-?\d+) (-?\d+) (-?\d+) (-?\d+)", txt)]


def stash_save(rcon, p):
    """Copies her inventory into the barrels and writes the record. Exits with her inventory untouched when it cannot prove
    the copy (barrel not placed, a short copy, the cells inside a park fill box or not air)."""
    cells, boxes = stash_cells(), park_boxes()
    if not boxes:
        sys.exit(f"I19: no fill box readable in {LANE / 'ops' / 'park-eve.ps1'}; cannot prove the stash is outside it; nothing changed")
    if any(all(min(b[i], b[i + 3]) - 1 <= c[i] <= max(b[i], b[i + 3]) + 1 for i in range(3)) for c in cells for b in boxes):
        sys.exit(f"I19: stash cells {cells} inside a park fill box {boxes}; nothing changed")
    for _ in range(20):  # the arena chunks were force-loaded just before: wait until the cells can be read
        if all(rcon(f"execute if block {x} {y} {z} minecraft:air").startswith("Test passed") for x, y, z in cells):
            break
        time.sleep(0.5)
    else:
        sys.exit(f"O59: stash cells {cells} not air or not loaded; nothing changed")
    body0, text0 = body_inv_hash(), inv_text(rcon, p)
    for x, y, z in cells:
        rcon(f"setblock {x} {y} {z} minecraft:barrel")
    STASH_FILE.parent.mkdir(parents=True, exist_ok=True)
    STASH_FILE.write_text(json.dumps({"player": p, "cells": cells, "body_hash": body0, "text": text0}), encoding="utf-8")
    for i, s in enumerate(SLOTS):
        x, y, z = cells[i // 27]
        rcon(f"item replace block {x} {y} {z} container.{i % 27} from entity {p} {s}")
    held = sum(_stacks(rcon, f"block {x} {y} {z} Items") for x, y, z in cells)
    mine = _stacks(rcon, f"entity {p} Inventory") + _stacks(rcon, f"entity {p} equipment")
    if held < mine:  # never clear her on a short copy: barrels and record go, she keeps everything
        _drop_barrels(rcon, cells)
        STASH_FILE.unlink()
        sys.exit(f"O59: the stash copied {held} stacks of her {mine}; her inventory untouched, nothing to restore")
    print(f"O59 stash: {mine} stacks in {cells}, body inventory_hash {body0}, server text sha {_sha(text0)}")


def _sha(t):
    import hashlib
    return hashlib.sha256(t.encode()).hexdigest()[:16]


def stash_restore(rcon, p, out_dir=None):
    """`clear` + every slot back from the barrels, then start == end. Returns (ok, note); ok None = no stash out. Barrels and
    record go only on a match; on a mismatch both raw texts are saved and the barrels stay for a hand restore."""
    if not STASH_FILE.exists():
        return None, "O59: no stash out"
    st = json.loads(STASH_FILE.read_text(encoding="utf-8"))
    cells = [tuple(c) for c in st["cells"]]
    if not all(rcon(f"execute if block {x} {y} {z} minecraft:barrel").startswith("Test passed") for x, y, z in cells):
        return False, f"O59: stash barrels {cells} not readable (unloaded?); her inventory left as is: `laneb.py restore_inventory`"
    held, want = sum(_stacks(rcon, f"block {x} {y} {z} Items") for x, y, z in cells), len(re.findall(r'id: "', st["text"]))
    if held < want:  # a copy cut short (crash mid-stash): clearing her would lose what never reached the barrels
        return False, f"O59: barrels hold {held} stacks, the record {want}; her inventory left as is (raw text in {STASH_FILE})"
    t = int(time.time() * 1000)
    rcon(f"clear {p}")  # first, so a slot that was empty before ends empty
    for i, s in enumerate(SLOTS):
        x, y, z = cells[i // 27]
        rcon(f"item replace entity {p} {s} from block {x} {y} {z} container.{i % 27}")
    time.sleep(1.0)
    text1 = inv_text(rcon, p)
    body1 = body_inv_hash(t + 500) if st.get("body_hash") else None
    ok = text1 == st["text"] and body1 == st.get("body_hash")
    rec = {"ok": ok, "body_hash": [st.get("body_hash"), body1], "text_sha": [_sha(st["text"]), _sha(text1)],
           "start": st["text"], "end": text1}
    if out_dir:
        (Path(out_dir) / "inventory-o59.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    if ok:
        _drop_barrels(rcon, cells)
        STASH_FILE.unlink()
    else:
        (STASH_FILE.parent / f"body-stash-{LABEL}-mismatch-{datetime.now():%Y%m%d-%H%M%S}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return ok, (f"O59 inventory {'restored exactly' if ok else 'MISMATCH (barrels and record kept)'}: body inventory_hash "
                f"{st.get('body_hash')} -> {body1}, server text sha {_sha(st['text'])} -> {_sha(text1)}")


def park(p):
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LANE / "ops" / "park-eve.ps1"),
                           "-Root", str(LANE / "server-dev"), "-Player", p], capture_output=True, text=True, timeout=120)


# O39: every harness teleport goes through the lane's own safe_tp (<root>/ops/safe_tp.py; the 45L has C:\eve\ops too, with
# enforce on). No lane ops = no run: never a fallback to a plain tp (foundation 2026-10-20).
if not (LANE / "ops" / "safe_tp.py").exists():
    sys.exit(f"{LANE / 'ops' / 'safe_tp.py'} missing: refusing to run without the O39 safe teleport")
sys.path.insert(0, str(LANE / "ops"))
from safe_tp import safe_tp as SAFE_TP  # noqa: E402


class LaneB(arena.Run):
    def tp(self, x, y, z, yaw=0, pitch=0):
        """O39: through the lane's safe_tp (solid floor, air at feet and head, inside an envelope zone; refused = not moved and
        the run stops). Shark lanes and the 45L alike (C:/eve/ops/safe_tp.py, server root C:/eve/server-dev)."""
        self.clear_entities()
        SAFE_TP(LANE / "server-dev", self.player, x, y, z, yaw, pitch, cmd=self.rcon)
        time.sleep(1.0)

    lane = LABEL

    def __init__(self, cp, player):
        self.cp, self.player = cp, player
        root = (EVIDENCE or arena.REPO / "evidence") / cp  # O104: lane F's own evidence folder on the 45L
        root.mkdir(parents=True, exist_ok=True)
        n = 1 + sum(1 for d in root.iterdir() if d.is_dir() and re.fullmatch(rf"\d{{4}}-\d\d-\d\d-\d{{4}}-{LABEL}-attempt-\d+", d.name))
        self.stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
        self.dir = root / f"{self.stamp}-{LABEL}-attempt-{n}"
        self.dir.mkdir()
        self.rows, self.t0 = [], time.time()
        self.rcon = arena.Rcon(arena.SERVER_PROPS)
        st = eve("status")
        if not st.get("body"):
            sys.exit(f"lane B body is not up: {st}")
        self.hub = type("NoHub", (), {"hash": (st.get("registry") or {}).get("hash", "?"), "frames": []})()
        where = (f"Shark lane `{LABEL}`**: eve-watch's Shark stack ({LANE}, real eve-sense, real mind on {CLI_PORT}); development evidence"
                 if LABEL.startswith("shark") else "the 45L (`45L`)**: foundation's qualification lane (C:\\eve, real eve-sense, real mind on 8951)")
        self.fixture_desc = (f"Fixture: **{where}. Account `{player}`, arena floating at y={arena.FLOOR:.0f} around x/z 0, difficulty "
                             f"{os.environ['EVE_DIFFICULTY']} during the run. Actions via the mind's /cli; body events from that mind's DB; "
                             "server truth by RCON.")
        self.saved = {}
        self._state_ts, self._state_last, self._state_lock = [], int(time.time() * 1000) - 2000, threading.Lock()
        threading.Thread(target=self._state_reader, daemon=True).start()  # I29f: every state frame's time, for max_state_gap_s

    # ---- body view: the mind DB ----
    def mark(self):
        return int(time.time() * 1000)

    def events(self, since_ms, kind):
        out = []
        for ts, payload in db_rows("SELECT ts, payload FROM events WHERE source='body' AND kind='event' AND ts >= ? ORDER BY id", (since_ms,)):
            p = json.loads(payload)
            if p.get("kind") == kind:
                out.append({**(p.get("detail") or {}), "t": ts / 1000})
        return out

    def _copy_states(self):
        with self._state_lock:
            new = db_rows("SELECT ts FROM observations WHERE kind='state' AND ts > ? ORDER BY ts", (self._state_last,))
            if new:
                self._state_ts.extend(t for (t,) in new)
                self._state_last = new[-1][0]

    def _state_reader(self):
        while True:
            try:
                self._copy_states()
            except Exception:  # the mind DB busy or restarting: the next pass copies what is there
                pass
            time.sleep(20)

    def state_times(self, t0, t1):
        """I29f on a lane: arrival times (s) of the body's state frames between t0 and t1, from the mind's stored state rows
        (one per frame, ~300 ms apart). The mind keeps only the last ~160 s of them (lane E 14:33: 550 rows over 163 s), so a
        reader thread started with the run copies their times every 20 s (a DB read, not RCON: O43's no-poller rule is about
        RCON)."""
        self._copy_states()
        return [t / 1000 for t in self._state_ts if t0 * 1000 <= t <= t1 * 1000]

    def state(self):
        r = db_rows("SELECT payload FROM observations WHERE kind='state' ORDER BY id DESC LIMIT 1")
        return json.loads(r[0][0]) if r else None

    # ---- actions and owner control through the mind ----
    def request(self, skill, params, wait=60):
        return Handle(skill, params, wait)

    def result(self, handle, timeout):
        handle.t.join(timeout)
        return as_result(handle.reply)

    def act(self, skill, params, timeout=30):
        h = self.request(skill, params, timeout)
        return h.action_id, None, self.result(h, timeout + 30)

    def owner_stop(self):
        return eve("stop", {"reason": "reflex fixture baseline"})

    def owner_resume(self):
        return eve("resume")

    # ---- fixture ----
    def clear_entities(self):
        sel = f"type=!minecraft:player,x=0,y={arena.FLOOR:.0f},z=0,distance=..60"
        if "No entity" not in self.rcon(f"kill @e[{sel}]"):
            time.sleep(1.2)
            self.rcon(f"kill @e[{sel}]")

    def fixture(self):
        r, F, p = self.rcon, int(arena.FLOOR), self.player
        for g in ("spawn_wandering_traders", "spawn_mobs", "natural_health_regeneration", "keep_inventory"):
            self.saved[g] = r(f"gamerule {g}").rsplit(" ", 1)[-1].strip()
        self.saved["difficulty"] = r("difficulty").rsplit(" ", 1)[-1].strip().lower()
        self.owner_resume()
        r("forceload add -32 -32 32 32")
        m = fixture_map()  # I26a: never write another part's stash or chest cells; never run while its stash is out
        EXCL[:] = foreign_boxes(m, r)
        print(f"I26a: arena fills keep clear of {EXCL}")
        out = foreign_stash(r, m)
        if out:
            sys.exit("I26a: another part's inventory stash is out, so this run would endanger it: " + "; ".join(out) +
                     " (nothing changed; that part restores first)")
        stash_save(r, p)  # O59: before the fixture's `clear` and kit
        self.stashed = True
        self.built = True  # from here on the arena is ours to clear at the end
        for c in ["gamerule spawn_wandering_traders false", "gamerule spawn_mobs false", "difficulty peaceful", f"gamemode survival {p}",
                  *clear_cmds(F),
                  *fills(-24, F - 1, -24, 24, F - 1, 24, "minecraft:smooth_stone"), *fills(-26, F - 8, -26, 26, F - 8, 26, "minecraft:stone"),
                  # a 3-high glass rim (walls only): nothing she does in the arena can take her over the edge of a sky platform
                  *fills(-25, F - 1, -25, 25, F + 2, -25, "minecraft:glass"), *fills(-25, F - 1, 25, 25, F + 2, 25, "minecraft:glass"),
                  *fills(-25, F - 1, -25, -25, F + 2, 25, "minecraft:glass"), *fills(25, F - 1, -25, 25, F + 2, 25, "minecraft:glass"),
                  f"clear {p}", f"give {p} minecraft:stone_sword",
                  f"give {p} minecraft:bread 8", f"effect give {p} minecraft:saturation 1 20 true"]:
            r(c)
        self.clear_entities()
        self.tp(0.5, F, 0.5)  # onto the floor just built (O39: before 2026-10-20 this tp came first, into open sky)
        time.sleep(1.0)

    def restore(self):
        r, F = self.rcon, int(arena.FLOOR)
        for g in ("spawn_wandering_traders", "spawn_mobs", "natural_health_regeneration", "keep_inventory"):
            if g in self.saved:
                r(f"gamerule {g} {self.saved[g]}")
        if self.saved.get("difficulty"):
            r(f"difficulty {self.saved['difficulty']}")
        self.owner_resume()
        r(f"effect clear {self.player}")
        r(f"effect give {self.player} minecraft:instant_health 1 4 true")
        out = park(self.player)
        for c in clear_cmds(F) if getattr(self, "built", False) else []:  # a run refused before the fixture changes nothing
            r(c)
        o59 = ""
        if getattr(self, "stashed", False):  # parked first: nothing in the arena is picked up after the copy-back
            for _ in range(20):  # items copied onto a dead player are lost at respawn (keep_inventory off in the nights)
                try:
                    if reflex.health(self) > 0:
                        break
                except Exception:  # unreadable: the restore's own check will say MISMATCH and keep the barrels
                    break
                time.sleep(1.0)
            self.o59_ok, o59 = stash_restore(r, self.player, self.dir)
            print(o59)
            res = self.dir / "RESULT.md"
            if res.exists():
                res.write_text(res.read_text(encoding="utf-8") + f"\n\n## O59 inventory (start == end is a harness pass condition)\n\n{o59}\n",
                               encoding="utf-8")
        r("forceload remove -32 -32 32 32")
        return o59 + " | " + out.stdout[-400:] + out.stderr[-400:]


HOSTILE = ("zombie", "zombie_villager", "husk", "drowned", "skeleton", "stray", "bogged", "spider", "cave_spider", "creeper", "witch",
           "enderman", "slime", "phantom")
# --kit-live (Hunter, 2026-10-22: Eve always carries a shield and a pickaxe): stone sword, stone pickaxe, bread x16, a shield in
# the off hand - the default night kit, the joint O98 night's too - plus the live probe's seal item at dusk (f4de04b1: dirt)
NIGHT_KIT = [(0, "minecraft:stone_sword", 1), (1, "minecraft:stone_pickaxe", 1), (3, "minecraft:bread", 16), (2, "minecraft:dirt", 2)]
NIGHT_SHIELD = "minecraft:shield"  # O83: in the off hand, so shield_arrow answers skeletons (a9 a10: 4 arrow deaths without one)
DURABLE = ("minecraft:stone_sword", "minecraft:stone_pickaxe", NIGHT_SHIELD)


def _t(rcon, c):
    return rcon(c).startswith("Test passed")


# I27a (foundation's audit of the 45L restore mechanisms, 2da5790): the --door (+20), --stair (+30), --room (+40) and --pad
# snapshots are clones floating in open sky, and a clone there does not keep what decays, falls, flows, melts or grows (lane D
# a9 attempt 9: 399 leaf drops in 12 min). A box holding any of these refuses the run before its snapshot; an id the server does
# not know refuses too (fail closed: 26.3 has no #minecraft:concrete_powder tag). Selftest: `laneb.py snap_selftest` (dev).
# ponytail: a fixed list. Ceilings: grass spreading to dirt in more sky light, copper weathering, waterlogged blocks other than
# those listed, and blocks a later MC adds: an unlisted one is not caught (a compare of the copy after the clone is no check: on
# the dev server a cloned sand block over air was still in place 3 s later, it falls only once a neighbour update reaches it).
DYES = ("white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray", "light_gray", "cyan", "purple", "blue",
        "brown", "green", "red", "black")
UNSTABLE = ("#minecraft:leaves", "minecraft:sand", "minecraft:red_sand", "minecraft:suspicious_sand", "minecraft:gravel",
            "minecraft:suspicious_gravel", *(f"minecraft:{c}_concrete_powder" for c in DYES), "#minecraft:anvil",
            "minecraft:dragon_egg", "minecraft:pointed_dripstone", "minecraft:scaffolding", "minecraft:water", "minecraft:lava",
            "minecraft:bubble_column", "minecraft:snow", "minecraft:snow_block", "minecraft:powder_snow", "minecraft:ice",
            "minecraft:frosted_ice", "#minecraft:crops", "minecraft:farmland", "#minecraft:saplings", "minecraft:bamboo_sapling",
            "minecraft:bamboo", "minecraft:sugar_cane", "minecraft:cactus", "minecraft:sweet_berry_bush", "minecraft:cocoa",
            "minecraft:vine", "#minecraft:cave_vines", "minecraft:weeping_vines", "minecraft:weeping_vines_plant",
            "minecraft:twisting_vines", "minecraft:twisting_vines_plant", "minecraft:kelp", "minecraft:kelp_plant",
            "minecraft:seagrass", "minecraft:tall_seagrass", "minecraft:turtle_egg", "minecraft:sniffer_egg", "minecraft:fire",
            "minecraft:soul_fire")


def unstable_at(rcon, b):
    """I27a: the first cell of box b holding an UNSTABLE block, as (x, y, z, what), or None. Air cells are skipped; any reply
    other than a plain pass/fail counts as unstable (fail closed)."""
    for x in range(b[0], b[3] + 1):
        for z in range(b[2], b[5] + 1):
            for y in range(b[1], b[4] + 1):
                if _t(rcon, f"execute if block {x} {y} {z} minecraft:air"):
                    continue
                for k in UNSTABLE:
                    out = rcon(f"execute if block {x} {y} {z} {k}")
                    if out != "Test failed":
                        return x, y, z, (k if out.startswith("Test passed") else f"{k} (server: {out[:60]})")
    return None


def refuse_unstable(rcon, b, what):
    """I27a: SystemExit, before any snapshot, when box b holds a block a floating clone does not keep."""
    u = unstable_at(rcon, b)
    if u:
        sys.exit(f"I27a: the {what} snapshot box {b} holds {u[3]} at {u[:3]}, which a clone in open sky does not keep (decay, "
                 "gravity, flow, melt, growth); refused before its snapshot")


def snap_clone(rcon, b, dy, what):
    """I27a: refuse_unstable, then snapshot box b to dy above it (its destination already checked empty)."""
    refuse_unstable(rcon, b, what)
    rcon(f"clone {b[0]} {b[1]} {b[2]} {b[3]} {b[4]} {b[5]} {b[0]} {b[1] + dy} {b[2]}")


def room_repair(rcon, b, lantern, door):
    """a14: on late-seal night 14 a creeper got to the open doorway, and the next night's safe_tp found no floor under the stand
    (the set stopped at 15 of 20). Before every night the room's shell b goes back on its air cells only (fill ... keep, so a
    block she or the night put there stays), with the roof lantern. The doorway cells are filled too; the 6000 reset re-cuts
    them right after. Returns the shell cells re-laid, not counting doorway cells that were open."""
    x0, y0, z0, x1, y1, z1 = b
    door_air = sum(_t(rcon, f"execute if block {door[0]} {y} {door[1]} minecraft:air") for y in (y0 + 1, y0 + 2)) if door else 0
    n = rcon("setblock {} {} {} minecraft:sea_lantern keep".format(*lantern)).startswith("Changed")
    for f in ((x0, y0, z0, x1, y0, z1), (x0, y1, z0, x1, y1, z1), (x0, y0, z0, x0, y1, z1), (x1, y0, z0, x1, y1, z1),
              (x0, y0, z0, x1, y1, z0), (x0, y0, z1, x1, y1, z1)):  # floor, roof, 4 walls; edges overlap, keep counts a cell once
        m = re.search(r"filled (\d+)", rcon("fill {} {} {} {} {} {} minecraft:stone_bricks keep".format(*f)))
        n += int(m.group(1)) if m else 0
    return n - door_air


def room_cells(rcon, b, lantern, door):
    """O90i: the room as it stands, read before a repair or the teardown overwrites it, so damage is observed, not inferred:
    every cell of box b that is not what the built room has there (stone bricks on the shell, the roof lantern, air inside),
    as [x, y, z, "air" | "other"]. The doorway cells are left out (open or sealed: sealed_at_dawn has them)."""
    x0, y0, z0, x1, y1, z1 = b
    out = []
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            for z in range(z0, z1 + 1):
                if door and (x, z) == tuple(door) and y in (y0 + 1, y0 + 2):
                    continue
                want = "sea_lantern" if (x, y, z) == tuple(lantern) else "stone_bricks" if x in (x0, x1) or y in (y0, y1) or z in (z0, z1) else "air"
                if not _t(rcon, f"execute if block {x} {y} {z} minecraft:{want}"):
                    out.append([x, y, z, "air" if want != "air" and _t(rcon, f"execute if block {x} {y} {z} minecraft:air") else "other"])
    return out


def door_frame(door, stand):
    """O98: the doorway cell's centre (x, z) and its outward axis (away from the stand): (cx, cz, ox, oz)."""
    dx, dz = door[0] - stand[0], door[1] - stand[1]
    ox, oz = ((dx > 0) - (dx < 0), 0) if abs(dx) > abs(dz) else (0, (dz > 0) - (dz < 0))
    return door[0] + 0.5, door[1] + 0.5, ox, oz


def back_corner(door, hx, hz):
    """The room's back wall, its left corner cell (x, z): about 5 from the doorway cells, past mine_block's and place_block's reach."""
    ox, oz = ((door[0] > hx) - (door[0] < hx), 0) if abs(door[0] - hx) > abs(door[1] - hz) else (0, (door[1] > hz) - (door[1] < hz))
    return (hx - 2 * ox, hz - 2) if ox else (hx - 2, hz - 2 * oz)


def past_door(fr, x, z):
    """Signed distance past the door cell's centre toward the outside (combat cp98_doorway.signed, for any outward axis)."""
    return (x - fr[0]) * fr[2] + (z - fr[1]) * fr[3]


def in_line_of_fire(fr, t, e):
    """combat cp98_doorway: the line from the shooter t (x, z), outside, to Eve e (x, z), inside, crosses the door plane within
    0.8 of the opening's centre."""
    st, se = past_door(fr, *t), past_door(fr, *e)
    if st <= 0 or se >= 0:
        return False
    lat = lambda q: (q[0] - fr[0]) * fr[3] + (q[1] - fr[1]) * fr[2]
    return abs(lat(t) + (lat(e) - lat(t)) * st / (st - se)) <= 0.8


ARCHER_REACH = 15.0  # a skeleton's bow attack radius
DAWN_TOD = 23000  # sunrise: an opening at or after it, once the doorway was shut, is her dawn open


def fire_seconds(track, summon_t, reach=ARCHER_REACH):
    """O98o: seconds in the line of fire from 2 s after the summon, from the 0.5 s track rows [t, her past, in line, its past,
    shut, distance to it, time of day]. Raw counts every in-line row; judged (the bar from a23 on) only while the archer is
    within its bow reach and before her dawn open (the first open row at or after sunrise once the doorway had been shut;
    a22's 2.5 s at dawn had the skeleton 33.7 out). Returns (raw, judged, dawn_open_t)."""
    shut_seen, dawn_open = False, None
    for x in track:
        shut_seen = shut_seen or x[4]
        if shut_seen and not x[4] and len(x) > 6 and isinstance(x[6], int) and x[6] >= DAWN_TOD:
            dawn_open = x[0]
            break
    rows = [x for x in track if x[0] >= summon_t + 2.0 and x[2]]
    judged = [x for x in rows if (dawn_open is None or x[0] < dawn_open) and (len(x) < 6 or x[5] is None or x[5] <= reach)]
    return 0.5 * len(rows), 0.5 * len(judged), dawn_open


def ent_pos(r, sel):
    """Server position [x, y, z] of one entity (RCON data get), or None when there is none."""
    out = r.rcon(f"data get entity {sel} Pos")
    v = arena.nums(out) if "has the following entity data" in out else []
    return v[:3] if len(v) >= 3 else None


def raw_light(rcon, x, y, z):
    """Raw max brightness at a cell (block or sky light, what a mob's spawn check reads); a read-only predicate test."""
    for n in range(15, 0, -1):
        if _t(rcon, f'execute positioned {x} {y} {z} if predicate {{type:"minecraft:location_check",predicate:{{light:{{light:{{min:{n}}}}}}}}}'):
            return n
    return 0


def door_approach(rcon, door, sy, stand, n=5):
    """O75 pre-check, read-only (lane D a6: the doorway sat 4 blocks above the ground, 10 nights with no hostile inside).
    For the n cells straight out from the doorway: the walk level (the first non-replaceable block down from sy+6, plus 1)
    and the light there. Reachable = the cell just outside is at the doorway's walk level sy or one below it."""
    (dx, dz), (hx, hz) = door, stand
    ox, oz = ((dx > hx) - (dx < hx), 0) if abs(dx - hx) > abs(dz - hz) else (0, (dz > hz) - (dz < hz))
    cells = []
    for k in range(1, n + 1):
        x, z = dx + ox * k, dz + oz * k
        top = next((y for y in range(sy + 6, sy - 25, -1) if not _t(rcon, f"execute if block {x} {y} {z} #minecraft:replaceable")), None)
        walk = None if top is None else top + 1
        cells.append({"k": k, "x": x, "z": z, "walk_y": walk, "light": None if walk is None else raw_light(rcon, x, walk, z)})
    w0 = cells[0]["walk_y"]
    return {"door": [dx, sy, dz], "out": [ox, oz], "door_light": [raw_light(rcon, dx, sy, dz), raw_light(rcon, dx, sy + 1, dz)],
            "inside_light": raw_light(rcon, hx, sy, hz), "cells": cells, "reachable": w0 is not None and 0 <= sy - w0 <= 1}


MOB_EYE, EYE = 1.7, 1.62  # zombie/skeleton eyes 1.74, creeper 1.45; hers 1.62


def los_block(rcon, a, b, open_cells=()):
    """The first cell on the straight line a -> b (0.2-block steps) that is not replaceable (blocks sight), else None.
    open_cells are taken as open (the doorway before it is cut). Read-only."""
    import math
    n, seen = int(math.dist(a, b) / 0.2) + 1, set()
    for i in range(n + 1):
        c = tuple(math.floor(a[j] + (b[j] - a[j]) * i / n) for j in range(3))
        if c in seen or c in open_cells:
            continue
        seen.add(c)
        if not _t(rcon, f"execute if block {c[0]} {c[1]} {c[2]} #minecraft:replaceable"):
            return c
    return None


def site_check(rcon, door, sy, stand, n=10, room=False):
    """O75c pre-check, read-only (lane D a8: the stair made the doorway walkable, but from the valley its top step hid her and
    in 10 nights no hostile came in). Pass needs all of:
    - the ground straight out from the doorway at her floor's walk level sy for n blocks, with head room;
    - a straight ray from n out at mob eye height to her eye at the stand, clear through the doorway;
    - room=True (the room is not built yet): its 7x7 footprint at the same walk level.
    Light at the doorway, inside and outside is reported. Returns the facts, ok and every reason it fails."""
    (dx, dz), (hx, hz) = door, stand
    ox, oz = ((dx > hx) - (dx < hx), 0) if abs(dx - hx) > abs(dz - hz) else (0, (dz > hz) - (dz < hz))
    why, cells = [], []
    for k in range(1, n + 1):
        x, z = dx + ox * k, dz + oz * k
        w = walk_level(rcon, x, z, sy)
        head = w is not None and _t(rcon, f"execute if block {x} {w + 1} {z} #minecraft:replaceable")
        cells.append({"k": k, "x": x, "z": z, "walk_y": w, "head_clear": head, "light": None if w is None else raw_light(rcon, x, w, z)})
    bad = [c["k"] for c in cells if c["walk_y"] != sy or not c["head_clear"]]
    if bad:
        why.append(f"ground in front not level with her floor (walk {sy}) or no head room at k={bad} "
                   f"(walk levels {[c['walk_y'] for c in cells]})")
    if room:
        off = [(x, z, w) for x in range(hx - 3, hx + 4) for z in range(hz - 3, hz + 4)
               if (w := walk_level(rcon, x, z, sy)) != sy]
        if off:
            why.append(f"room footprint not level at {len(off)}/49 cells, e.g. {off[:4]}")
    a, b = (dx + ox * n + 0.5, sy + MOB_EYE, dz + oz * n + 0.5), (hx + 0.5, sy + EYE, hz + 0.5)
    blk = los_block(rcon, a, b, open_cells={(dx, sy, dz), (dx, sy + 1, dz)})
    if blk:
        why.append(f"line of sight from {n} out at mob eye height to her stand blocked at {blk}")
    return {"door": [dx, sy, dz], "out": [ox, oz], "cells": cells, "los_from": a, "los_to": b, "los_blocked_at": blk,
            "door_light": [raw_light(rcon, dx, sy, dz), raw_light(rcon, dx, sy + 1, dz)], "inside_light": raw_light(rcon, hx, sy, hz),
            "ok": not why, "why": why}


def walk_near(rcon, x, z, guess):
    """Walk level of column x,z searched from a guess (a neighbour's): up while the guess cell is solid, else down to ground."""
    rep = lambda y: _t(rcon, f"execute if block {x} {y} {z} #minecraft:replaceable")  # noqa: E731
    y = guess
    if not rep(y):
        while y < 318 and not rep(y + 1):
            y += 1
        return y + 1
    while y > -63 and rep(y - 1):
        y -= 1
    return y


def site_search(rcon, box, avoid, n=10):
    """O75c, read-only: candidate shelter sites in box (x1, z1, x2, z2): a level 7x7 footprint and a door whose ground runs
    level for n blocks straight out (scored by how much of the 3-wide band in front is level too), clear of every avoid box
    (x1, z1, x2, z2) by 5. Coarse first (a heightmap of the box), so every candidate still gets site_check before use."""
    x1, z1, x2, z2 = box
    h, guess = {}, 120
    for x in range(x1, x2 + 1):
        for z in range(z1, z2 + 1):
            guess = h[(x, z)] = walk_near(rcon, x, z, guess)
    near = lambda x, z: any(a[0] - 5 <= x <= a[2] + 5 and a[1] - 5 <= z <= a[3] + 5 for a in avoid)  # noqa: E731
    out = []
    for hx in range(x1 + 3, x2 - 2):
        for hz in range(z1 + 3, z2 - 2):
            w = h[(hx, hz)]
            if any(h.get((x, z)) != w for x in range(hx - 3, hx + 4) for z in range(hz - 3, hz + 4)):
                continue
            for ox, oz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                dx, dz = hx + 3 * ox, hz + 3 * oz
                line = [(dx + ox * k, dz + oz * k) for k in range(1, n + 1)]
                if any(h.get(c) != w for c in line) or any(near(*c) for c in line + [(hx, hz)]):
                    continue
                band = sum(h.get((x + oz * s, z + ox * s)) == w for x, z in line for s in (-1, 1))
                out.append({"stand": [hx, w, hz], "door": [dx, dz], "band_level": band, "of": 2 * n})
    out.sort(key=lambda c: -c["band_level"])
    return out, h


def walk_level(rcon, x, z, sy):
    """Where a mob stands in column x,z nearest her floor's walk level sy (None if nothing within reach): when the cell at sy is
    open, down to the first solid block below (+1); when it is solid, up to the first cell with solid below and two open cells.
    Leaves or canopy above head height are ignored (45L O75d: oak leaves at y 105-106 over the doorway cell, floor 98, read
    as walk 106 when the scan came down from sy+6)."""
    rep = lambda y: _t(rcon, f"execute if block {x} {y} {z} #minecraft:replaceable")  # noqa: E731
    if rep(sy):
        y = sy
        while y > sy - 25 and rep(y - 1):
            y -= 1
        return y if y > sy - 25 else None
    return next((y for y in range(sy + 1, sy + 7) if rep(y) and rep(y + 1)), None)


def stair_plan(rcon, door, sy, stand, n=8):
    """O75: the fewest blocks (1-wide, only added, 1-block rises) that let a mob walk into the doorway:
    - "out": straight out, step k topping out at walk level sy - (k - 1), until the ground is at or above the next step;
    - "along": from the cell outside the doorway, along the wall either way at the doorway's walk level, until natural ground
      within one block of it (lane D a7: the ground fell away as fast as a stair out descends; a ramp ran up the west side).
    Returns (name, [(x, z, y_from, y_to)], join cell (x, z)) or None."""
    (dx, dz), (hx, hz) = door, stand
    ox, oz = ((dx > hx) - (dx < hx), 0) if abs(dx - hx) > abs(dz - hz) else (0, (dz > hz) - (dz < hz))
    plans = []
    steps = []
    for k in range(1, n + 1):
        x, z = dx + ox * k, dz + oz * k
        w, want = walk_level(rcon, x, z, sy), sy - (k - 1)
        if w is None:
            break
        if w >= want:
            plans.append(("out", steps, (x, z)))
            break
        steps.append((x, z, w, want - 1))
        side = next(((x + sx, z + sz) for sx, sz in ((oz, ox), (-oz, -ox))  # lane D: joins sideways at the 4th step
                     if (ws := walk_level(rcon, x + sx, z + sz, sy)) is not None and want - 1 <= ws <= want + 1), None)
        if side:
            plans.append(("out", steps, side))
            break
    for px, pz in ((oz, ox), (-oz, -ox)):
        steps = []
        for j in range(n):
            x, z = dx + ox + px * j, dz + oz + pz * j
            w = walk_level(rcon, x, z, sy)
            if w is None or w > sy + 1:  # no ground, or a bank higher than one step
                break
            if j and w >= sy - 1:
                plans.append((f"along {px},{pz}", steps, (x, z)))
                break
            if w < sy:
                steps.append((x, z, w, sy - 1))
    plans = [p for p in plans if p[1]]
    # a stair out first: it faces the open ground, so a mob there sees her through the doorway (dev: a ledge across the
    # doorway hid her from the valley and no zombie came in, 0/3); else the ledge with the fewest blocks
    outs = [p for p in plans if p[0] == "out"]
    return outs[0] if outs else min(plans, key=lambda p: sum(c[3] - c[2] + 1 for c in p[1])) if plans else None


def pad_level(rcon, pad, sy):
    """O75e: level a ground pad (top block sy-1) at x1..x2, z1..z2 for the room in the forest. pad = (x1, z1, x2, z2, ybot, ytop):
    ybot = the lowest ground under the pad minus 1, ytop = the highest canopy within 7 of it. Snapshot box = the pad + 7 on every
    side (a leaf whose log the pad removes is within 6 of it, so any decay stays inside the box), cloned to just above itself.
    Exits with the reason and nothing changed for: water at the pad level in or next to the pad, a box over clone's 32768, or a
    snapshot destination that is not empty air. Returns (box, dy) once leveled."""
    px1, pz1, px2, pz2, ybot, ytop = pad
    b = (px1 - 7, ybot, pz1 - 7, px2 + 7, ytop, pz2 + 7)
    dy = ytop - ybot + 2
    vol = (b[3] - b[0] + 1) * (b[4] - b[1] + 1) * (b[5] - b[2] + 1)
    if vol > 32768:
        sys.exit(f"O75e: pad snapshot box {b} is {vol} blocks (> 32768); nothing changed")
    if b[4] + dy > 319 or not _t(rcon, f"execute if blocks {b[0]} {b[1] + dy} {b[2]} {b[3]} {b[4] + dy} {b[5]} {b[0]} {b[4] + dy + 1} {b[2]} all"):
        sys.exit(f"O75e: the pad snapshot destination ({dy} above {b}) is not empty air; nothing changed")
    wet = [(x, z) for x in range(px1 - 2, px2 + 3) for z in range(pz1 - 2, pz2 + 3)
           if _t(rcon, f"execute if block {x} {sy} {z} minecraft:water") or _t(rcon, f"execute if block {x} {sy - 1} {z} minecraft:water")]
    if wet:
        sys.exit(f"O75e: water at the pad level in or next to the pad ({len(wet)} cells, e.g. {wet[:3]}); nothing changed")
    snap_clone(rcon, b, dy, "pad")  # I27a (was O75e's own list after a9 attempt 9): refuses before any change to the site
    rcon(f"fill {px1} {sy} {pz1} {px2} {ytop} {pz2} minecraft:air")
    rcon(f"fill {px1} {ybot} {pz1} {px2} {sy - 2} {pz2} minecraft:dirt replace #minecraft:replaceable")
    rcon(f"fill {px1} {sy - 1} {pz1} {px2} {sy - 1} {pz2} minecraft:grass_block")
    return b, dy


def pad_restore(rcon, b, dy):
    """The pad box back from its snapshot, checked block-for-block; the snapshot is cleared only on a match."""
    rcon(f"clone {b[0]} {b[1] + dy} {b[2]} {b[3]} {b[4] + dy} {b[5]} {b[0]} {b[1]} {b[2]}")
    same = _t(rcon, f"execute if blocks {b[0]} {b[1]} {b[2]} {b[3]} {b[4]} {b[5]} {b[0]} {b[1] + dy} {b[2]} all")
    if same:
        rcon(f"fill {b[0]} {b[1] + dy} {b[2]} {b[3]} {b[4] + dy} {b[5]} minecraft:air")
    return same


def nights(r):
    """CP-31 (O54a): dusk-to-dawn nights driven by the MIND (`objective night_survival`, mind >= 09c09ab). The harness never
    plays: it lays the fixture, starts the objective each night, samples, and judges from the server at dawn.
    - Fixture, restored after: keep_inventory OFF (deaths on this dev account are by design), difficulty --difficulty (normal),
      mob spawning and natural regen on. The shelter is the envelope home: the stand is the first air cell (2 high, solid
      floor) at the home centre, searched down from it. A red bed goes beside the stand (foot 1 out, head 2 out), and her
      respawn point is set at the stand. `--door x,z` (O54b: lane D `-16,-45`) cuts a 1x2 doorway in that wall cell so
      hostiles can come in. Its two blocks are cloned 20 up first and cloned back at teardown, checked block-for-block.
    - O75 pre-check (read-only, before anything changes, and again once the doorway is open): walk level and light for the
      cells straight out from the doorway; a doorway no mob can walk into (the cell outside not at its walk level or one
      below) refuses the run. `--stair` instead builds the fewest cobblestone that make it walkable, 1-wide with 1-block
      rises: a stair straight out, or a ledge along the wall to ground within a step (stair_plan); snapshotted 30 up and
      restored block-for-block like the doorway, and the built path's walk levels are checked before any night.
    - Per night: at 6000, hostiles and loose items within 48 of the stand are removed, she is safe_tp'd to the stand, healed
      and kitted: --kit-live by default (stone sword, stone pickaxe, bread 16, a shield in the off hand; dirt 2 to seal the doorway,
      the live probe's seal item). The durables check is the sword, the pickaxe and the shield.
      With --door the objective gets the two cut cells as `doorway`: she seals them at dusk and opens them at dawn. A seal still
      there at dawn is recorded (sealed_at_dawn) and set back to air at the next 6000; at teardown the doorway's snapshot overwrites it.
      --late-seal i,j,...: those nights (0-based) start without `doorway`, so the door stays open from dusk (real fights inside or
      at the doorway), and the harness itself closes both cells with cobblestone at midnight, once she is inside and both cells are
      air (O90). Every row has door_min_dist, door_within3 (samples with a hostile within 3 of the doorway), fights_ok, late_seal.
      --far-at-dawn i,j,...: on those nights (0-based), just before dawn (time 22600-23000), with her doorway sealed, she is safe_tp'd to the back corner of the room
      (about 5 from the door cells, past reach), so the mind's dawn unseal must walk to the door first (mind O90 fix proof). Time goes to 13000 (dusk) and the objective starts.
      --far-at-dusk i,j,... (O101): those nights start (6000 reset) in the room's back corner instead of the stand, eyes ~5.4 from the
      doorway cells, so a sealing mind must walk within reach first (mind O90c). O101e kits, per night (bare flag: every night):
      --kit-no-seal-block [i,j]: the live 45L kit exactly (O101a: stone sword hotbar 0, bread x16 hotbar 3, no shield, nothing else)
      plus the probe's 2 cobblestone at dusk (a19's state: nothing she can open by hand); --kit-no-pickaxe [i,j]: the same plus dirt 8
      (f4de04b1, -SealItem dirt). The durables check is then the stone sword only.
      --threat-seal (O98, mind 1d083023 and later): late-seal nights start the objective with the doorway and late_seal: true,
      so she leaves the door open at dusk and seals it herself on a threat (a hostile within 4 of a cell, or a ranged one in sight);
      the harness's midnight seal then only fills cells she left open. An older mind would seal a doorway at dusk: opt-in.
      --doorway-skeleton i,j,... (O98, combat's cp98 placement): at time 15000, with both doorway cells still open, one AI skeleton
      (its own bow, tag evedr) is summoned 1.3 outside the door cell's centre on her floor. Every 0.5 s until it is gone, her and
      its server positions are read (in the night loop: O43, no poller thread). Then, on top of the usual pass: she is never more
      than 0.5 past the door cell's centre, and <= 1.0 s in its line of fire through the opening after its first 2 s; and every
      fight_melee accepted while it lived (mind DB actions) carried `doorway`, and held at it (evidence door_hold_ticks > 0) when it
      was on the skeleton (its network id from her state, or no id given) and the skeleton was still outside then (combat_ok:
      recorded always, in the pass only with --door-hold-bar; O98c: combat's hold needs a registry with fight_melee's doorway).
      With --threat-seal, also: the doorway shut while it lived (the 0.5 s track reads both cells; a shut doorway is never in the
      line of fire) and the mind's note says it sealed "on a threat". A night whose
      doorway was already sealed at 15000, or whose skeleton holds no bow after the summon (I37: the server's own read of its
      main hand), is INVALID (it tested nothing). The skeleton is killed by tag when the night ends.
      Every 5 s the mind's state (hp, food, pos, reflex, hostiles near) and the objective's status are sampled. Series,
      status and deaths are saved to night-NN.json.
    - Judge, server truth: pass = the objective ended `done`, she is alive (RCON health), and the three tools and the shield are back (RCON
      counts; bread may be eaten), so every death was recovered. A refused or never-started objective, or an owner stop,
      is INVALID (O46).
    """
    p, K = r.player, int(arg("--nights", "20"))
    late = {int(v) for v in arg("--late-seal", "").split(",") if v.strip()}  # O90: nights (0-based) whose seal waits for midnight
    far = {int(v) for v in arg("--far-at-dawn", "").split(",") if v.strip()}  # O90c: nights (0-based) she is parked far from the door at dawn
    dsk = {int(v) for v in arg("--doorway-skeleton", "").split(",") if v.strip()}  # O98: nights with combat's scripted doorway skeleton
    # when it is summoned (time of day): combat's 15000 by default; a21 (01:08): a natural zombie reached the doorway at 13396 and
    # the threat seal came before 15000, so the night tested nothing - --doorway-skeleton-tod 13100 summons it at dusk instead
    dsk_tod = int(arg("--doorway-skeleton-tod", "15000"))
    # eve-watch (a21): --doorway-skeleton-clear R kills natural hostiles within R of the door just before the summon (recorded),
    # so the skeleton is the night's doorway threat
    dsk_clear = float(arg("--doorway-skeleton-clear", "0"))
    fdusk = {int(v) for v in arg("--far-at-dusk", "").split(",") if v.strip()}  # O101: nights she starts in the back corner
    # O101e kits, per night (a bare flag: every night). --kit-no-pickaxe: no pickaxe, dirt 8 + cobblestone (the live probe kit
    # f4de04b1, -SealItem dirt). --kit-no-seal-block: no pickaxe, cobblestone only (a19: no hand-openable block, the door left open, 4 deaths)
    def nights_of(flag):
        v = (sys.argv + [""])[sys.argv.index(flag) + 1] if flag in sys.argv else None  # safe as the last argument
        return set(range(K)) if v is not None and not re.fullmatch(r"\d+(,\d+)*", v) else {int(x) for x in (v or "").split(",") if x.strip()}
    nopick, noseal = nights_of("--kit-no-pickaxe"), nights_of("--kit-no-seal-block")
    hc = json.loads((LANE / "mind" / "config" / "envelope.json").read_text(encoding="utf-8"))["home"]["center"]
    hx, hy, hz = int(hc["x"] // 1), int(hc["y"] // 1), int(hc["z"] // 1)

    def test(c):
        return r.rcon(c).startswith("Test passed")

    def air(x, y, z):
        return test(f"execute if block {x} {y} {z} minecraft:air")

    def solid(x, y, z):
        return test(f"execute unless block {x} {y} {z} minecraft:air unless block {x} {y} {z} minecraft:water unless block {x} {y} {z} minecraft:lava")

    room = tuple(int(v) for v in arg("--room", "").split(",")) if "--room" in sys.argv else None  # O75c: stand x,y,z of a new room
    if room:
        hx, sy, hz = room
        if abs(hc["x"] - (hx + 0.5)) > 1.5 or abs(hc["z"] - (hz + 0.5)) > 1.5 or abs(hc["y"] - sy) > 2:  # go_home targets it
            sys.exit(f"O75c: the lane's envelope home centre is {hc}, not the room stand {room}: go_home would take her out of the "
                     "room; nothing changed")
    else:
        if "--stand" in sys.argv:  # lane D: the room is under the home centre (floor 98, roof 103, centre y 109 above it)
            hx, hy, hz = (int(v) for v in arg("--stand", "").split(","))
        sy = next((y for y in range(hy + 2, hy - 24, -1) if air(hx, y, hz) and air(hx, y + 1, hz) and solid(hx, y - 1, hz)), None)
        if sy is None:
            sys.exit(f"no stand at the home centre ({hx}, {hy}, {hz}): nothing to judge against")
    stand = (hx, sy, hz)
    door = tuple(int(v) for v in arg("--door", "").split(",")) if "--door" in sys.argv else None  # x,z of a wall cell: 1x2 opening
    stair = box = room_box = None
    done, saved_spawn, bed = set(), None, None
    near = f"x={hx},y={sy},z={hz},distance=..48"
    def teardown():  # restores exactly what was changed (a failure half-way restores only the steps that ran)
        if "room_built" in done:  # O90i: the room's cells as they are, before the restore overwrites them
            try:
                cells = room_cells(r.rcon, room_box, (hx, sy + 4, hz), door)
                (r.dir / "room-before-teardown.json").write_text(json.dumps(cells), encoding="utf-8")
                print(f"room before teardown: {len(cells)} cell(s) not as built {cells[:12]}")
            except Exception as e:  # the restore below must still run
                print(f"room before teardown: not read ({e})")
        if bed:
            for c in bed:
                r.rcon(f"setblock {c[0]} {c[1]} {c[2]} minecraft:air")
        if "door" in done:
            dx_, dz_ = door
            r.rcon(f"clone {dx_} {sy + 20} {dz_} {dx_} {sy + 21} {dz_} {dx_} {sy} {dz_}")
            same = test(f"execute if blocks {dx_} {sy} {dz_} {dx_} {sy + 1} {dz_} {dx_} {sy + 20} {dz_} all")
            print(f"doorway restored block-for-block: {same}")
            if same:
                r.rcon(f"fill {dx_} {sy + 20} {dz_} {dx_} {sy + 21} {dz_} minecraft:air")
            else:
                print(f"KEPT the snapshot at ({dx_}, {sy + 20}, {dz_}) for a hand restore")
        if "stair" in done:
            b = box
            r.rcon(f"clone {b[0]} {b[1] + 30} {b[2]} {b[3]} {b[4] + 30} {b[5]} {b[0]} {b[1]} {b[2]}")
            same = test(f"execute if blocks {b[0]} {b[1]} {b[2]} {b[3]} {b[4]} {b[5]} {b[0]} {b[1] + 30} {b[2]} all")
            print(f"stair restored block-for-block: {same}")
            if same:
                r.rcon(f"fill {b[0]} {b[1] + 30} {b[2]} {b[3]} {b[4] + 30} {b[5]} minecraft:air")
            else:
                print(f"KEPT the stair snapshot at {b} +30 for a hand restore")
        if "room" in done and "pad" not in done:  # after the doorway (its snapshot holds the room's own wall blocks), the site's own blocks back
            b = room_box
            r.rcon(f"clone {b[0]} {b[1] + 40} {b[2]} {b[3]} {b[4] + 40} {b[5]} {b[0]} {b[1]} {b[2]}")
            same = test(f"execute if blocks {b[0]} {b[1]} {b[2]} {b[3]} {b[4]} {b[5]} {b[0]} {b[1] + 40} {b[2]} all")
            print(f"room site restored block-for-block: {same}")
            if same:
                r.rcon(f"fill {b[0]} {b[1] + 40} {b[2]} {b[3]} {b[4] + 40} {b[5]} minecraft:air")
            else:
                print(f"KEPT the room snapshot at {b} +40 for a hand restore")
        if "pad" in done:  # last: the whole pad box (room, doorway and leveled ground inside it) back as it was
            same = pad_restore(r.rcon, pad_box, pad_dy)
            print(f"pad site restored block-for-block: {same}" + ("" if same else f" - KEPT the snapshot at {pad_box} +{pad_dy} for a hand restore"))
        v = re.findall(r"-?\d+", saved_spawn.split("data:")[-1]) if saved_spawn and "has the following" in saved_spawn else None
        if v and len(v) >= 3:
            r.rcon(f"spawnpoint {p} {v[0]} {v[1]} {v[2]}")
        r.rcon("time set 1000")
        for t in HOSTILE:
            r.rcon(f"kill @e[type=minecraft:{t},{near}]")
        eve("retain", {"minutes": 10})
    try:  # setup: any failure from here restores what already changed
        # O75e: --pad x1,z1,x2,z2,ybot,ytop levels a ground pad (top block sy-1) around the room in the forest. Snapshot box = the
        # pad + 7 on every side (a leaf whose log the pad removes is within 6 of it, so any decay stays inside the box) from ybot
        # (lowest ground under the pad - 1) to ytop (highest canopy in the box), cloned to just above itself, restored and checked
        # block-for-block at teardown. Refused before any change: water in or next to the pad, a box over clone's 32768, or a
        # snapshot destination that is not empty air. Only the full site check after the build (doorway open) decides the site.
        pad = tuple(int(v) for v in arg("--pad", "").split(",")) if "--pad" in sys.argv else None
        pad_box = pad_dy = None
        if pad:
            pad_box, pad_dy = pad_level(r.rcon, pad, sy)  # exits with the reason, nothing changed, if it may not
            done.add("pad")
            print(f"pad leveled x {pad[0]}..{pad[2]} z {pad[1]}..{pad[3]} top block y {sy - 1}; snapshot of {pad_box} at +{pad_dy}")
        if door and "--stair" not in sys.argv and not pad:  # O75c: read-only, before anything changes; a site that fails is refused
            sc = site_check(r.rcon, door, sy, (hx, hz), room=bool(room))
            (r.dir / "doorway-site-before.json").write_text(json.dumps(sc, indent=1), encoding="utf-8")
            print(f"doorway site pre-check (read-only): ok={sc['ok']} walk_y out={[c['walk_y'] for c in sc['cells']]} vs {sy}, "
                  f"line of sight blocked at {sc['los_blocked_at']}, light out={[c['light'] for c in sc['cells']]}")
            if not sc["ok"]:
                sys.exit("O75c: site refused (nothing changed): " + "; ".join(sc["why"]))
        if room:  # O75c: the shelter room here (lane D's: stone bricks, 5x5x4 inside, a sea lantern in the roof); snapshot 40 up
            room_box = (hx - 3, sy - 1, hz - 3, hx + 3, sy + 4, hz + 3)
            b = room_box
            if not pad:  # on a pad the pad's own snapshot holds the room box
                if not all(air(x, y + 40, z) for x in range(b[0], b[3] + 1) for y in range(b[1], b[4] + 1) for z in range(b[2], b[5] + 1)):
                    sys.exit(f"no free snapshot cells 40 above the room box {room_box}")
                snap_clone(r.rcon, b, 40, "room")  # I27a
                done.add("room")
            r.rcon(f"fill {b[0]} {b[1]} {b[2]} {b[3]} {b[4]} {b[5]} minecraft:stone_bricks hollow")
            r.rcon(f"setblock {hx} {sy + 4} {hz} minecraft:sea_lantern")
            done.add("room_built")
            print(f"room built at {room_box}, stand {stand}, snapshot at +40")
        if door and "--stair" in sys.argv:  # O75 (a8): the lenient walk-in rule and the stair, kept as run
            ap = door_approach(r.rcon, door, sy, (hx, hz))
            (r.dir / "doorway-approach-before.json").write_text(json.dumps(ap, indent=1), encoding="utf-8")
            print(f"doorway approach (read-only): reachable={ap['reachable']} walk_y out={[c['walk_y'] for c in ap['cells']]} vs {sy}, "
                  f"light out={[c['light'] for c in ap['cells']]}")
            if not ap["reachable"]:
                if "--stair" not in sys.argv:
                    sys.exit(f"O75: no mob can walk into the doorway {ap['door']} (walk levels out {[c['walk_y'] for c in ap['cells']]} vs {sy}); "
                             "nothing changed (--stair builds a 1-wide stair up to it)")
                plan = stair_plan(r.rcon, door, sy, (hx, hz))
                if not plan:
                    sys.exit("O75: no 1-wide stair or ledge within 8 blocks joins the ground to the doorway; nothing changed")
                how, stair, join = plan
                xs, zs = [c[0] for c in stair], [c[1] for c in stair]
                box = (min(xs), min(c[2] for c in stair), min(zs), max(xs), max(c[3] for c in stair), max(zs))
                if not all(air(x, y + 30, z) for x in range(box[0], box[3] + 1) for y in range(box[1], box[4] + 1) for z in range(box[2], box[5] + 1)):
                    sys.exit(f"no free snapshot cells 30 above the stair box {box}; nothing changed")
        if door and not pad:  # I27a: the doorway and the stair box are checked before either is snapshotted or changed
            refuse_unstable(r.rcon, (door[0], sy, door[1], door[0], sy + 1, door[1]), "doorway")
        if stair:
            refuse_unstable(r.rcon, box, "stair")
        bed = None
        beds = () if "--no-bed" in sys.argv else ((1, 0, "east"), (-1, 0, "west"), (0, 1, "south"), (0, -1, "north"))
        for dx, dz, facing in beds:  # foot beside the stand, head 2 out; --no-bed: a night she cannot sleep through
            foot, head = (hx + dx, sy, hz + dz), (hx + 2 * dx, sy, hz + 2 * dz)
            if all(air(*c) and solid(c[0], c[1] - 1, c[2]) for c in (foot, head)):
                r.rcon(f"setblock {foot[0]} {sy} {foot[2]} minecraft:red_bed[facing={facing},part=foot]")
                r.rcon(f"setblock {head[0]} {sy} {head[2]} minecraft:red_bed[facing={facing},part=head]")
                bed = (foot, head)
                break
        if door:  # O54b: block-for-block back after - a clone 20 up (open air above the roof) is the snapshot, checked on restore
            dx_, dz_ = door
            if not pad:  # on a pad the pad's own snapshot holds the doorway (a9 attempt 9: its cell 20 up was inside that snapshot)
                if not (air(dx_, sy + 20, dz_) and air(dx_, sy + 21, dz_)):
                    sys.exit(f"no free snapshot cells above the doorway ({dx_}, {sy + 20}, {dz_})")
                snap_clone(r.rcon, (dx_, sy, dz_, dx_, sy + 1, dz_), 20, "doorway")  # I27a: any wall, not only stone bricks
                done.add("door")
            r.rcon(f"fill {dx_} {sy} {dz_} {dx_} {sy + 1} {dz_} minecraft:air")
        if stair:  # O75: the snapshot 30 up, then the steps; restored and checked block-for-block like the doorway
            snap_clone(r.rcon, box, 30, "stair")  # I27a
            done.add("stair")
            for x, z, y0, y1 in stair:
                r.rcon(f"fill {x} {y0} {z} {x} {y1} {z} minecraft:cobblestone")
            print(f"stair ({how}, joins the ground at {join}): {stair} (x, z, y from, y to), snapshot of {box} at +30")
    except BaseException:
        teardown()
        raise
    saved_spawn = r.rcon(f"data get entity {p} respawn.pos")
    near = f"x={hx},y={sy},z={hz},distance=..48"
    # O54d "adjust spawning, not the rule": --spawn-hostiles N keeps up to N scripted hostiles coming from the doorway side when
    # the lane spawns none (lane D 2026-10-21: no monster world-wide at night). No creepers: the shelter is the lane's fixture.
    cap = int(arg("--spawn-hostiles", "0"))
    ground = []
    if cap:
        side = (door[1] - hz) if door else 3
        for dx, dz in ((0, 12), (-5, 11), (5, 11), (-9, 9), (9, 9), (0, 14)):
            x, z = hx + dx, hz + (dz if side > 0 else -dz)
            y = next((y for y in range(sy + 10, sy - 10, -1) if air(x, y, z) and air(x, y + 1, z) and solid(x, y - 1, z)), None)
            if y is not None:
                ground.append((x, y, z))
        print(f"scripted hostiles: up to {cap}, from {ground}")
    # 26.3 recomputes the mob-spawning flags only when the difficulty CHANGES: the fixture's spawn_mobs false + peaceful, then
    # setup's normal, left them off, and our later "difficulty normal" was a no-op (a3/a5: 0 monsters world-wide at midnight;
    # a bare peaceful -> normal probe: 70 in 45 s). So spawn_mobs first, then peaceful -> the run's difficulty.
    for c in ("gamerule keep_inventory false", "gamerule spawn_mobs true", "gamerule natural_health_regeneration true",
              "difficulty peaceful", f"difficulty {os.environ['EVE_DIFFICULTY']}", f"spawnpoint {p} {hx} {sy} {hz}"):
        r.rcon(c)
    print(f"nights: stand {stand}, bed {bed}, {K} nights, difficulty {os.environ['EVE_DIFFICULTY']}")
    eve("retain", {"minutes": 300})  # I14d: keep the state rows for the whole run
    try:
        if door and "--stair" not in sys.argv:  # O75c: the full check again with the doorway open: it must pass now
            sc = site_check(r.rcon, door, sy, (hx, hz))
            (r.dir / "doorway-site.json").write_text(json.dumps(sc, indent=1), encoding="utf-8")
            print(f"doorway site after: ok={sc['ok']} light door={sc['door_light']} inside={sc['inside_light']} "
                  f"out={[c['light'] for c in sc['cells']]} why={sc['why']}")
            if not sc["ok"]:
                raise SystemExit("O75c: the site fails with the doorway open; restoring, no night run")
        elif door:  # the pre-check again with the doorway open (and the stair, if any): it must pass now; its light is the night's
            ap = door_approach(r.rcon, door, sy, (hx, hz))
            (r.dir / "doorway-approach.json").write_text(json.dumps(ap, indent=1), encoding="utf-8")
            print(f"doorway approach after: reachable={ap['reachable']} walk_y out={[c['walk_y'] for c in ap['cells']]} vs {sy}, "
                  f"light door={ap['door_light']} inside={ap['inside_light']} out={[c['light'] for c in ap['cells']]}")
            path = [walk_level(r.rcon, x, z, sy) for x, z, *_ in stair or []] + ([walk_level(r.rcon, *join, sy)] if stair else [])
            if stair:
                print(f"stair path walk levels (door side first): {path}")
            if not ap["reachable"] or (path and any(w is None or abs(w - v) > 1 for w, v in zip(path, path[1:]))):
                raise SystemExit("O75: the doorway is still not walkable after the fixture; restoring, no night run")
        for i in range(K):
            r.rcon("time set 6000")
            for t in HOSTILE:
                r.rcon(f"kill @e[type=minecraft:{t},{near}]")
            r.rcon(f"kill @e[type=minecraft:item,{near}]")
            for _ in range(20):  # a death at the end of the last night: the body respawns her itself (40 ticks)
                if reflex.health(r) > 0:
                    break
                time.sleep(1.0)
            repaired = room_repair(r.rcon, room_box, (hx, sy + 4, hz), door) if room else None
            if repaired:
                print(f"room repaired before night {i}: {repaired} shell cell(s) re-laid")
            reset = []
            for y in (sy, sy + 1) if door else ():  # O83e: a seal she did not open at dawn (in the last row) is set back to air
                if not air(door[0], y, door[1]):
                    r.rcon(f"setblock {door[0]} {y} {door[1]} minecraft:air")
                    reset.append(y)
            if reset:  # O101m: the reset is evidence (night 1's walled_in left both cells; mind's next note reads "walled in since the last dawn")
                print(f"doorway reset before night {i}: y {reset} set back to air (O83e)")
            far_at_dusk = None
            if door and i in fdusk:  # O101: the dusk seal must walk to the inside stand first (eyes > 4 from the cells)
                cx, cz = back_corner(door, hx, hz)
                r.tp(cx + 0.5, sy, cz + 0.5)
                far_at_dusk = {"pos": [cx + 0.5, sy, cz + 0.5],
                               "eye_to_door": round(math.dist((cx + 0.5, sy + 1.62, cz + 0.5), (door[0] + 0.5, sy + 0.5, door[1] + 0.5)), 2)}
            else:
                r.tp(hx + 0.5, sy, hz + 0.5)
            # O101a: on the mirror nights, the live 45L kit exactly (stone sword in hotbar 0, bread x16 in hotbar 3, nothing else, no
            # shield) plus the live probe's seal item, given here at dusk: --kit-no-seal-block 2 cobblestone (a19's state),
            # --kit-no-pickaxe dirt 8 + cobblestone 2 (f4de04b1, -SealItem dirt)
            mirror = i in nopick or i in noseal
            if mirror:
                slots = [(0, "minecraft:stone_sword", 1), (3, "minecraft:bread", 16), (1, "minecraft:cobblestone", 2)]
                if i in nopick and i not in noseal:
                    slots.append((2, "minecraft:dirt", 8))
            else:  # --kit-live, the default
                slots = list(NIGHT_KIT)
            kit = {item: n for _, item, n in slots}
            offhand = None if mirror else NIGHT_SHIELD
            durable = tuple(k for k in DURABLE if k in kit or k == offhand)  # the sword, the pickaxe and the shield she was given
            reflex.heal_full(r)
            r.rcon(f"clear {p}")
            for k, item, n in slots:
                r.rcon(f"item replace entity {p} hotbar.{k} with {item} {n}")
            if offhand:
                r.rcon(f"item replace entity {p} weapon.offhand with {offhand} 1")
            r.act("select_hotbar", {"slot": 0})
            counted = (*kit, *([offhand] if offhand else []))
            dusk = {k: reflex.count(r, k) for k in counted}
            r.rcon("time set 13000")  # dusk itself: with the bed she may sleep from 12542, before the objective sees dusk
            since, t0 = r.mark(), time.time()
            threat = "--threat-seal" in sys.argv and i in late  # O98 (mind 1d083023+): the doorway with late_seal, sealed on a threat
            doorway = [{"x": door[0], "y": y, "z": door[1]} for y in (sy, sy + 1)] if door and (i not in late or threat) else None  # O83e / O90
            start = eve("objective", {"name": "night_survival", **({"doorway": doorway} if doorway else {}), **({"late_seal": True} if threat else {})})
            if not start.get("ok"):
                r.record(f"night#{i}", "night_survival", {}, False, f"objective refused: {start}", invalid=True)
                continue
            oid, series, status, summoned, last_summon, probe = start["objective_id"], [], {}, 0, 0.0, None
            OBJECTIVE_IDS.append(oid)
            late_seal = far_at_dawn = skel = None
            track = []
            while time.time() < t0 + 1500:  # dusk wait ~50 s + a night of 500 s at 20 tps, with room for a slow server
                status = eve("objective", {"status": oid})
                s = r.state() or {}
                series.append({"t": round(time.time() - t0, 1), "tod": s.get("time_of_day"), "hp": s.get("health"), "food": s.get("food"),
                               "pos": s.get("pos"), "reflex": (s.get("control") or {}).get("reflex_active"),
                               "action": (s.get("control") or {}).get("active_action_id"),
                               "hostiles": [(e.get("type"), e.get("distance"), *[round((e.get("pos") or {}).get(a, 0), 1) for a in "xyz"])
                                            for e in s.get("nearby_entities", []) if e.get("hostile")][:8]})
                if status.get("status") not in ("running", None):
                    break
                if probe is None and isinstance(s.get("time_of_day"), int) and s["time_of_day"] >= 18000:  # O66: raw proof, once a night
                    probe = {t: r.rcon(f"execute if entity @e[type=minecraft:{t}]") for t in ("zombie", "skeleton", "creeper", "spider")}
                # O90 late-seal night: the door stays open from dusk (real fights inside or at the doorway), and at midnight the
                # harness itself closes both cells with cobblestone - only with her inside the room and both cells empty air, so she
                # is never locked out and no mob is set into a block. She does not know of this seal: the 6000 reset reopens it.
                # (O98: not while the doorway skeleton lives - the air test cannot see a mob standing in the door cell)
                if (door and i in late and late_seal is None and isinstance(s.get("time_of_day"), int) and s["time_of_day"] >= 18000
                        and not (skel and "t" in skel and "gone_t" not in skel)):
                    pp = s.get("pos") or {}
                    if (abs(pp.get("x", 99) - hx - 0.5) <= 2.5 and abs(pp.get("z", 99) - hz - 0.5) <= 2.5
                            and all(air(door[0], y, door[1]) for y in (sy, sy + 1))):
                        for y in (sy, sy + 1):
                            r.rcon(f"setblock {door[0]} {y} {door[1]} minecraft:cobblestone")
                        late_seal = {"t": series[-1]["t"], "tod": s["time_of_day"]}
                # mind's O90 unseal-walk proof (--far-at-dawn): just before dawn, with her doorway sealed, she is set down in the room's
                # back corner (about 5 from the door cells, past mine_block's reach), so the dawn unseal has to walk to the door first
                tod = s.get("time_of_day")
                if door and i in far and far_at_dawn is None and isinstance(tod, int) and 22600 <= tod < 23000:
                    if not any(air(door[0], y, door[1]) for y in (sy, sy + 1)):
                        cx, cz = back_corner(door, hx, hz)
                        r.tp(cx + 0.5, sy, cz + 0.5)
                        far_at_dawn = {"t": series[-1]["t"], "tod": tod, "pos": [cx + 0.5, sy, cz + 0.5],
                                       "eye_to_door": round(math.dist((cx + 0.5, sy + 1.62, cz + 0.5), (door[0] + 0.5, sy + 0.5, door[1] + 0.5)), 2)}
                if ground and time.time() - last_summon >= 40 and len(series[-1]["hostiles"]) < cap:
                    gx, gy, gz = ground[summoned % len(ground)]
                    kind = ("zombie", "skeleton", "spider", "husk")[summoned % 4]
                    r.rcon(f"summon minecraft:{kind} {gx + 0.5} {gy} {gz + 0.5} {{Tags:[\"nightmob\"]}}")
                    summoned, last_summon = summoned + 1, time.time()
                if door and i in dsk and skel is None and isinstance(tod, int) and tod >= dsk_tod:
                    fr = door_frame(door, (hx, hz))
                    if all(air(door[0], y, door[1]) for y in (sy, sy + 1)):
                        cleared = 0
                        if dsk_clear > 0:
                            for t in HOSTILE:
                                out = r.rcon(f"kill @e[type=minecraft:{t},x={fr[0]:.1f},y={sy},z={fr[1]:.1f},distance=..{dsk_clear},tag=!evedr]")
                                m = re.search(r"Killed (\d+)", out)
                                cleared += int(m.group(1)) if m else (1 if out.startswith("Killed") else 0)
                        sx, sz = fr[0] + fr[2] * 1.3, fr[1] + fr[3] * 1.3
                        r.rcon(f'summon minecraft:skeleton {sx:.2f} {sy} {sz:.2f} {{Tags:["evedr"],PersistenceRequired:1b}}')
                        # a summon with NBT skips finalizeSpawn: no default bow (dev a95, 0 arrows); the bow re-picks its ranged attack
                        r.rcon("item replace entity @e[tag=evedr,limit=1] weapon.mainhand with minecraft:bow")
                        skel = {"t": series[-1]["t"], "tod": tod, "pos": [round(sx, 2), sy, round(sz, 2)], "cleared_near_door": cleared,
                                "bow": test("execute if items entity @e[tag=evedr,limit=1] weapon.mainhand minecraft:bow"),  # I37 (O102)
                                "HandItems": r.rcon("data get entity @e[tag=evedr,limit=1] equipment.mainhand")[-160:]}  # 26.3: equipment.mainhand
                    else:
                        skel = {"skipped": f"doorway sealed at {dsk_tod}", "tod": tod}
                if skel and "t" in skel and "net_id" not in skel:  # its network id (fight_melee's entity_id), from her own view
                    near_sk = [(math.hypot((e.get("pos") or {}).get("x", 99) - skel["pos"][0], (e.get("pos") or {}).get("z", 99) - skel["pos"][2]), e.get("id"))
                               for e in s.get("nearby_entities", []) if str(e.get("type", "")).endswith("skeleton")]
                    if near_sk and min(near_sk)[0] <= 3.0:
                        skel["net_id"] = min(near_sk)[1]
                if skel and "t" in skel and "gone_t" not in skel:  # the 5 s wait, spent reading positions every 0.5 s
                    for _ in range(10):
                        time.sleep(0.5)
                        e, k, now = ent_pos(r, p), ent_pos(r, "@e[tag=evedr,limit=1]"), round(time.time() - t0, 1)
                        if k is None:
                            skel["gone_t"] = now
                            break
                        if e:  # [t, her past, in line, its past, doorway sealed, distance, tod]: a sealed doorway blocks the line
                            shut = not any(air(door[0], y, door[1]) for y in (sy, sy + 1))
                            track.append([now, round(past_door(fr, e[0], e[2]), 2), in_line_of_fire(fr, (k[0], k[2]), (e[0], e[2])) and not shut,
                                          round(past_door(fr, k[0], k[2]), 2), shut, round(math.dist(e, k), 2), tod])
                    continue
                time.sleep(5.0)
            time.sleep(3.0)  # a death right at dawn: let the respawn land before the judge
            dj = None
            if skel and "t" in skel:
                r.rcon("kill @e[tag=evedr]")
                fire_raw, fire_s, dawn_open = fire_seconds(track, skel["t"])  # O98o: judged on fire_s, raw kept
                dj = {**skel, "samples": len(track), "max_past": max((x[1] for x in track), default=None), "fire_s": fire_s,
                      "fire_s_raw": fire_raw, "fire_rule": f"in line from summon+2 s, archer within {ARCHER_REACH}, before dawn open",
                      "dawn_open_t": dawn_open, "entered": any(x[3] <= 0.5 for x in track)}
                # combat O98: a fight_melee accepted while the skeleton lived must carry the doorway and have held at it
                # (evidence door_hold_ticks > 0); the mind DB's actions table is not pruned like its observations
                lived = ((t0 + skel["t"]) * 1000, (t0 + (skel.get("gone_t") or time.time() - t0)) * 1000)
                dj["fights"] = []
                for ts, fst, code, pa, evd in db_rows("SELECT accepted_ts, status, code, params, evidence FROM actions WHERE skill='fight_melee' "
                                                      "AND accepted_ts BETWEEN ? AND ?", (int(t0 * 1000), int(time.time() * 1000))):
                    try:
                        pa, evd = json.loads(pa or "{}"), json.loads(evd or "{}") or {}
                    except ValueError:
                        pa, evd = {}, {}
                    # combat's rule: doorway always; a hold only when the fight was on the skeleton (its id, or none given) and it
                    # was still outside (past the door centre > 0.5) when the fight was accepted - a zombie inside, or a skeleton
                    # already in the door cell, is fought at once
                    at = min(track, key=lambda x: abs((t0 + x[0]) * 1000 - ts)) if track else None
                    on_skel = pa.get("entity_id") is None or skel.get("net_id") is None or pa.get("entity_id") == skel.get("net_id")
                    dj["fights"].append({"ts": ts, "status": fst, "code": code, "while_skeleton": lived[0] <= ts <= lived[1],
                                         "entity_id": pa.get("entity_id"), "on_skeleton": on_skel,
                                         "skeleton_outside": bool(at and at[3] > 0.5), "doorway": bool(pa.get("doorway")),
                                         "door_hold_ticks": evd.get("door_hold_ticks")})
                dj["combat_ok"] = all(x["doorway"] and (not (x["on_skeleton"] and x["skeleton_outside"]) or (x["door_hold_ticks"] or 0) > 0)
                                      for x in dj["fights"] if x["while_skeleton"])
                # O98c: with --threat-seal the mind seals on the skeleton: the doorway shut while it lived, and her note says so
                shut_t = next((x[0] for x in track if len(x) > 4 and x[4]), None)
                dj["threat_seal_t"] = shut_t
                dj["threat_seal_note"] = any("on a threat" in str(n) for n in status.get("notes", []))
                dj["ok"] = (bool(track) and dj["max_past"] <= 0.5 and fire_s <= 1.0
                            and ("--threat-seal" not in sys.argv or (shut_t is not None and dj["threat_seal_note"]))
                            and (dj["combat_ok"] or "--door-hold-bar" not in sys.argv))  # O98c: the hold is recorded, judged only on request
            alive, dawn = reflex.health(r) > 0, {k: reflex.count(r, k) for k in counted}
            sealed_at_dawn = [y for y in (sy, sy + 1) if door and not air(door[0], y, door[1])]  # O83e: she opens it at dawn
            room_after = room_cells(r.rcon, room_box, (hx, sy + 4, hz), door) if room else None  # O90i: before the next repair
            deaths = r.events(since, "death")
            slept = any(e.get("reflex") == "sleep" and e.get("phase") == "start" for e in r.events(since, "reflex"))
            lost = {k: dusk[k] - dawn[k] for k in durable if dawn[k] < dusk[k]}
            hps = [x["hp"] for x in series if isinstance(x.get("hp"), (int, float))]
            seen = max([len(x["hostiles"]) for x in series] or [0])
            # O54d: a hostile inside the shelter = within the 5x5 room around the stand, at its floor (lane D: x -18..-14, z -50..-46)
            inside = max([sum(1 for h in x["hostiles"] if len(h) == 5 and abs(h[2] - hx - 0.5) <= 2.5 and abs(h[4] - hz - 0.5) <= 2.5
                              and 0 <= h[3] - sy < 3) for x in series] or [0])
            kinds = sorted({h[0].split(":")[-1] for x in series for h in x["hostiles"] if h[0]})
            # O90: contact = a hostile at her floor level near the doorway cell (horizontal distance to its centre)
            dd = [[math.dist((h[2], h[4]), (door[0] + 0.5, door[1] + 0.5)) for h in x["hostiles"] if len(h) == 5 and abs(h[3] - sy) < 3]
                  for x in series] if door else []
            door_min = round(min((d for ds in dd for d in ds), default=99), 1)
            door3 = sum(1 for ds in dd if any(d <= 3 for d in ds))
            fights_ok = sum(1 for fi in status.get("fights", []) if fi.get("status") == "ok")
            world = sum(int(m.group(1)) for v in (probe or {}).values() for m in [re.search(r"count:\s*(\d+)", v, re.I)] if m)
            (r.dir / f"night-{i:02d}.json").write_text(json.dumps({"night": i, "midnight_probe_raw": probe, "objective": status, "deaths": deaths, "dusk": dusk, "dawn": dawn,
                                                                    "late_seal": late_seal if i in late else None, "far_at_dawn": far_at_dawn, "far_at_dusk": far_at_dusk,
                                                                    "kit": list(kit), "room_repaired": repaired, "room_after": room_after,
                                                                    "doorway_skeleton": dj or skel, "doorway_track": track, "door_min_dist": door_min,
                                                                    "door_within3": door3, "fights_ok": fights_ok, "series": series}, indent=1), encoding="utf-8")
            st = status.get("status")
            r.record(f"night#{i}", "night_survival", {}, st == "done" and alive and not lost and (i not in dsk or bool(dj and dj["ok"])),
                     f"status={st} report={status.get('report')!r} alive={alive} deaths={[(d.get('cause'), d.get('killer_type')) for d in deaths]} "
                     f"recovered={[d.get('recovered') for d in status.get('deaths', [])]} lost={lost} slept={slept} "
                     f"fights={[(f.get('type'), f.get('status'), f.get('code')) for f in status.get('fights', [])]} hostiles_max={seen} "
                     f"inside_max={inside} summoned={summoned} world_hostiles_midnight={world if probe else None} kinds={kinds} hp_min={min(hps) if hps else None} {time.time() - t0:.0f}s"
                     f" door_min_dist={door_min} door_within3={door3}/{len(series)} fights_ok={fights_ok}"
                     f" late_seal={late_seal if i in late else 'n/a'} far_at_dawn={far_at_dawn} far_at_dusk={far_at_dusk} doorway_skeleton={({k: v for k, v in dj.items()} if dj else skel) if i in dsk else 'n/a'} room_repaired={repaired} room_after={room_after if room_after is None or len(room_after) < 9 else f'{len(room_after)} cells'} sealed_at_dawn={sealed_at_dawn} doorway_notes={[n for n in status.get('notes', []) if str(n).startswith('doorway')]}",
                     invalid=st == "stopped" or st is None or (probe is not None and world == 0) or (i in dsk and not (skel and "t" in skel and skel.get("bow"))))
            if probe is not None and world == 0:  # O66: no monster in the world at midnight = the night tested nothing
                print(f"STOPPED: night {i} midnight probe found no monsters: {probe}")
                break
            if deaths and "--stop-on-death" in sys.argv:  # O54d: stop, root-cause it, report before going on
                print(f"STOPPED on a death in night {i}: {[(d.get('cause'), d.get('killer_type'), d.get('pos')) for d in deaths]}")
                break
    finally:
        teardown()


reflex.SUITES["nights"] = nights


OBJECTIVE_IDS = []  # O94f: the mind objectives this run started (nights), for candidates_export
LANE_LETTER = {"shark": "B", "shark-c": "C", "shark-d": "D", "shark-e": "E"}.get(LABEL)


def export_candidates(run_dir, since):
    """O94f: eve-watch's candidates_export.py copies the lane mind's eve-candidates/1 rows for this run (by its objective ids,
    else the run window) into <run_dir>/candidates.jsonl. It always exits 0; its last line is the report. Shark lanes only."""
    if not LANE_LETTER:
        return "candidates export: not on a Shark lane"
    args = [sys.executable, str(Path(__file__).resolve().parents[3] / "parts" / "watch" / "laneb" / "candidates_export.py"),
            "--lane", LANE_LETTER, "--run", str(run_dir)]
    for o in OBJECTIVE_IDS:
        args += ["--objective-id", str(o)]
    if not OBJECTIVE_IDS:
        args += ["--since", since.isoformat(timespec="seconds"), "--until", datetime.now().isoformat(timespec="seconds")]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=120)
        return (p.stdout.strip().splitlines() or ["candidates export: " + (p.stderr.strip() or f"exit {p.returncode}")])[-1]
    except (OSError, subprocess.TimeoutExpired) as e:  # a learning export never fails a run
        return f"candidates export: failed ({e})"


def lane_lock(op, what=""):
    """The Shark lanes' lock (lane-lock.ps1): exit 0 = done, 1 = refused. A held lane is never touched (2026-10-20 16:24: a run
    went ahead on lane B during eve-mind's lock because the refusal was not checked)."""
    script = LANE / "ops" / "lane-lock.ps1"
    if LABEL == "45L" or not script.exists():
        return True  # the 45L: foundation schedules every run itself
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), op, "-By", "eve-body"]
    if op == "acquire":
        args += ["-Minutes", arg("--lock-minutes", "30"), "-What", what]
    if op == "release" and "--next" in sys.argv:  # I13 hand-over: only the named chat's acquire succeeds for a few minutes
        args += ["-Next", arg("--next", ""), "-NextMinutes", arg("--next-minutes", "5")]
    out = subprocess.run(args, capture_output=True, text=True, timeout=60)
    print(f"lane lock {op}: {(out.stdout + out.stderr).strip()[:200]}")
    return out.returncode == 0


if __name__ == "__main__":
    which = sys.argv[1].split(",") if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else list(reflex.SUITES)
    if which == ["snap_selftest"]:  # I27a: the guard's whole list, on the BODY'S OWN dev server only (it writes blocks)
        # Never through a lane label: laneb's default `shark` is eve-watch's lane B. 2026-10-21 10:13: a draft of this test carved
        # 1260 air cells under lane B, because its "area not empty" refusal sat inside the cleanup's try/finally.
        dev = arena.BODY / "run-server" / "server.properties"
        if "level-type=minecraft\\:flat" not in dev.read_text(encoding="utf-8"):
            sys.exit(f"snap_selftest: {dev} is not the body's flat dev world; nothing sent")
        rc = arena.Rcon(dev)
        X, Y, Z = 80, -59, 80  # an empty corner of the flat dev world (grass at -61), cleared at the end
        member = {"#minecraft:leaves": "minecraft:oak_leaves[persistent=true]", "#minecraft:anvil": "minecraft:anvil",
                  "#minecraft:crops": "minecraft:wheat", "#minecraft:saplings": "minecraft:oak_sapling",
                  "#minecraft:cave_vines": "minecraft:cave_vines"}
        support = {"minecraft:soul_fire": (-1, "minecraft:soul_sand"), "minecraft:weeping_vines_plant": (-1, "minecraft:weeping_vines"),
                   "minecraft:twisting_vines_plant": (1, "minecraft:twisting_vines"), "minecraft:kelp_plant": (1, "minecraft:kelp")}
        area = f"{X - 2} {Y - 1} {Z - 2} {X + 6} {Y + 26} {Z + 2}"
        fails = []
        rc(f"forceload add {X - 2} {Z - 2} {X + 6} {Z + 2}")  # an unloaded chunk reads as "not loaded", never as air
        time.sleep(1.0)
        if not (_t(rc, f"execute if block {X} -61 {Z} minecraft:grass_block") and _t(rc, f"execute if blocks {area} {X - 2} 200 {Z - 2} all")):
            rc(f"forceload remove {X - 2} {Z - 2} {X + 6} {Z + 2}")
            sys.exit(f"snap_selftest: not the flat dev world, or the test area {area} is not empty air; nothing changed")
        try:  # from here on every block in the area is this test's own, so the cleanup may clear it
            rc(f"fill {X - 1} {Y - 1} {Z - 1} {X + 1} {Y + 1} {Z + 1} minecraft:glass hollow")  # nothing falls or flows out
            for k in UNSTABLE:  # every listed id/tag is known to the server, placed, matched by its own entry and refused
                rc(f"setblock {X} {Y} {Z} minecraft:air")  # a fresh cell: no leftover for the next block to merge with
                sdy, sblk = support.get(k, (0, None))  # what the block needs beside it to stay itself
                if sblk:
                    rc(f"setblock {X} {Y + sdy} {Z} {sblk}")
                put = rc(f"setblock {X} {Y} {Z} {member.get(k, k)}")
                u = unstable_at(rc, (X, Y, Z, X, Y, Z))
                if not put.startswith("Changed the block") or not _t(rc, f"execute if block {X} {Y} {Z} {k}") or not u or "server:" in u[3]:
                    fails.append(f"{k}: setblock {put[:50]!r}, guard {u}")
                if sblk:
                    rc(f"setblock {X} {Y + sdy} {Z} minecraft:glass")
            rc(f"fill {X - 1} {Y - 1} {Z - 1} {X + 1} {Y + 1} {Z + 1} minecraft:air")
            ok_box = (X + 3, Y, Z, X + 5, Y + 3, Z)  # stone, dirt, grass with short grass on it, a log, stone bricks: a clone keeps them
            for x, y, blk in ((X + 3, Y, "stone"), (X + 4, Y, "dirt"), (X + 5, Y, "grass_block"), (X + 5, Y + 1, "short_grass"),
                              (X + 3, Y + 1, "oak_log"), (X + 4, Y + 1, "stone_bricks")):
                rc(f"setblock {x} {y} {Z} minecraft:{blk}")
            if unstable_at(rc, ok_box):
                fails.append(f"stable box refused: {unstable_at(rc, ok_box)}")
            try:
                snap_clone(rc, ok_box, 20, "selftest")
                if not _t(rc, f"execute if blocks {X + 3} {Y} {Z} {X + 5} {Y + 3} {Z} {X + 3} {Y + 20} {Z} all"):
                    fails.append("stable snapshot differs from its original")
            except SystemExit as e:
                fails.append(f"stable snapshot refused: {e}")
        finally:
            rc(f"fill {area} minecraft:air")
            rc(f"kill @e[type=minecraft:falling_block,x={X - 2},y={Y - 1},z={Z - 2},dx=9,dy=28,dz=5]")
            rc(f"kill @e[type=minecraft:item,x={X - 2},y={Y - 1},z={Z - 2},dx=9,dy=28,dz=5]")
            rc(f"forceload remove {X - 2} {Z - 2} {X + 6} {Z + 2}")
        print(f"SNAP_SELFTEST {'PASS' if not fails else 'FAIL'}: {len(UNSTABLE)} unstable ids each placed and refused, a stable box "
              f"kept by its snapshot; failures {fails}")
        sys.exit(0 if not fails else 5)
    if which in (["precheck"], ["site_search"]):  # O75c, read-only (execute-if tests only): no fixture, nothing changed
        if not lane_lock("acquire", f"body read-only {which[0]}"):  # O43: reads too go under the lane's lock (eve-watch, I28)
            sys.exit("lane is held by someone else: not running")
        try:  # every exit below releases the lock first
            rc = arena.Rcon(arena.SERVER_PROPS)
            if which == ["precheck"]:  # --stand x,y,z (y = her walk level inside) --door x,z [--room: the room is not built yet]
                sx, sy_, sz = (int(v) for v in arg("--stand", "").split(","))
                sc = site_check(rc, tuple(int(v) for v in arg("--door", "").split(",")), sy_, (sx, sz), room="--room" in sys.argv)
                print(json.dumps(sc, indent=1))
                print("PRECHECK", "PASS" if sc["ok"] else "FAIL: " + "; ".join(sc["why"]))
                sys.exit(0 if sc["ok"] else 4)
            cands, _ = site_search(rc, tuple(int(v) for v in arg("--box", "").split(",")),  # --box x1,z1,x2,z2 --avoid "x1,z1,x2,z2;..."
                                   [tuple(int(v) for v in a.split(",")) for a in arg("--avoid", "").split(";") if a])
            for c in cands[:15]:
                print(json.dumps(c))
            print(f"SITES {len(cands)} (coarse; site_check each before use)")
            sys.exit(0)
        finally:
            lane_lock("release")
    if not lane_lock("acquire", f"body reflex fixtures: {','.join(which)}"):
        sys.exit("lane is held by someone else: not running")
    if which == ["restore_inventory"]:  # O59: a crashed run's stash back (she is parked first, the arena chunks loaded for it)
        rc = arena.Rcon(arena.SERVER_PROPS)
        try:
            park(PLAYER)
            rc("forceload add -32 -32 32 32")
            time.sleep(2.0)
            ok, note = stash_restore(rc, PLAYER)
            print(note)
            rc("forceload remove -32 -32 32 32")
        finally:
            lane_lock("release")
        sys.exit(0 if ok is not False else 3)
    if STASH_FILE.exists():
        lane_lock("release")
        sys.exit(f"O59: a previous run's inventory stash is still out ({STASH_FILE}): run `laneb.py restore_inventory --lane {LABEL}` "
                 "first (nothing was changed)")
    run, run_start = LaneB(arg("--cp", "CP-20"), PLAYER), datetime.now()
    try:
        reflex.setup(run)
        for w in which:
            reflex.SUITES[w](run)
        cand = export_candidates(run.dir, run_start)
        print(cand)
        run.finish(reflex.CRITERIA, f"Lane `{LABEL}`. Suites run: {', '.join(which)}. Thresholds as tools/reflex.py. O94f {cand}.")
    except BaseException as e:
        run.finish(reflex.CRITERIA, f"**ABORTED** after {len(run.rows)} attempts: {e!r}. O94f {export_candidates(run.dir, run_start)}.")
        raise
    finally:
        print("restore:", run.restore())
        lane_lock("release")
    if getattr(run, "o59_ok", True) is False:
        sys.exit("O59 FAIL: her inventory is not back as it was (see inventory-o59.json)")
