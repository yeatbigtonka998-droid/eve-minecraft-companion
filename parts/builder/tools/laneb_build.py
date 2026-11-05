"""eve-builder fixtures on lane B (eve-watch's Shark stack: C:\\eve-shark, real mind on 8955, real eve-sense, dev account EveDev).
The build goes through the mind's /cli (`eve do build_schematic`, the owner-command path); the judge is the server: RCON
`execute if block` for every cell of the design (block-for-block) and `data get` of Eve's inventory before/after (no double
spending). Every attempt is counted and written down. Development evidence, not qualification.

    py -3.12 parts/builder/tools/laneb_build.py build|resume|restart|cp51|cp52|cp53 [--lane B|C|45L] [--attempt N] [--plot X,Y,Z] [--design test3] [--deploy]

--lane: B (C:/eve-shark, EveDev, mind 8955; default) or C (C:/eve-shark-c, EveDev2, mind 8956). The lane builds the provider
jars from its own src on refresh; --deploy swaps in this checkout's jar instead.

  build    clear the plot, give exactly the design's items, build once.
  resume   build, `eve stop` after --cut seconds, `eve resume`, dispatch again: judged as build, plus the second run placed
           only what the first left (placed + already_present == cells_total) and items spent == items in place.
  restart  as resume, but the cut is a client restart (restart-client.ps1 -By eve-builder) instead of a stop.

--plot is the min corner of the plot's floor (the floor block itself). Default: eve-watch's zone `build_plot` (x 20..36,
y 150..175, z -48..-32, break/place free, no grants), floor min corner 20,150,-48 -> test3 origin 23,151,-45.
"""
import asyncio, base64, hashlib, json, os, subprocess, sys, time
from datetime import datetime
from pathlib import Path

_LANES = {"B": (r"C:\eve-shark", "EveDev", 8955), "C": (r"C:\eve-shark-c", "EveDev2", 8956), "D": (r"C:\eve-shark-d", "EveDev3", 8958),
          "45L": (r"C:\eve", "FED_Chairman", 8951)}  # the 45L: foundation runs this from a git-archive extract (O19)
LANE_ID = sys.argv[sys.argv.index("--lane") + 1] if "--lane" in sys.argv else "B"
LANE = Path(_LANES[LANE_ID][0])
SERVER = LANE / "server-dev"
QUAL = LANE_ID == "45L"
DIM = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--dim=")), "minecraft:overworld")
# O50/O60: on the 45L foundation owns the End forceload for the whole series; --as-45L runs that code path on a lane
# (the lane's fixture owner forceloads by hand) so it is proven before the real account
OWN_FORCELOAD = not (QUAL or "--as-45L" in sys.argv)
FORCE_WALK = "--force-walk-failure" in sys.argv  # O88: capture proof on a lane (cp53tall), never the 45L
if FORCE_WALK and QUAL:
    sys.exit("--force-walk-failure is a lane capture proof (O88); never the 45L")
# --no-gate[=<ruling>]: an orchestrator's lane-only fixture ruling (O61a: CP-50's hut; O94m: cp72-cabin-tree, never on the
# 45L without Hunter's per-piece approval); the ruling is recorded in the run folder
NO_GATE = any(a == "--no-gate" or a.startswith("--no-gate=") for a in sys.argv)
RULING = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--no-gate=")), "O61a")
RULING_TEXT = {"O61a": "lane test-fixture ruling, as pillar_tall",
               "O94m": "orchestrator's lane-only ruling for organic lane proofs (cp72-cabin-tree; the horse, whose approval.json is a "
                       "relay record, not a CP-65 approval); never built on the 45L without Hunter's per-piece approval"}
# --margin=N: cells between the plot edge and the design (default 3); a design whose scaffold reaches farther needs more
# (O94m: cp72-cabin-tree's scaffold reaches 6). origin = plot + N; the cleared box grows to match
_m = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--margin=")), "3").split(",")
MX, MZ = int(_m[0]), int(_m[-1])  # --margin=N or --margin=X,Z (C3's horse: scaffold 6 west of the design, 3 is enough in z)
MARGIN = MX
# --wait=S: how long the harness follows one build action (default 1500 s; cp72-cabin-tree is ~50 min of building)
WAIT = int(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--wait=")), "1500"))
if NO_GATE and QUAL:
    sys.exit("--no-gate is a lane fixture ruling (O61a); the 45L uses design's category-\"schematic\" gate files")
# O89c dev fixture: the design laid as built, scaffold still in place (RCON setblock), no kit: the run is the removal phase alone
AS_BUILT = "--as-built" in sys.argv
if AS_BUILT and QUAL:
    sys.exit("--as-built is a lane dev fixture (O89c); never the 45L")
# --cage-stand: O94ah lane proof. One planned ground stand outside the design is caged in barriers (its 4 sides at feet and
# head height, a lid over its head) so no walk reaches it: she must build from other stands and never walk_to it
CAGE = "--cage-stand" in sys.argv
if CAGE and QUAL:
    sys.exit("--cage-stand is a lane proof (O94ah); never the 45L")
LABEL = "45L (Eve's real account)" if QUAL else f"lane {LANE_ID} (`shark`)" + (" as-45L" if "--as-45L" in sys.argv else "")
EVID = "Qualification lane (45L)." if QUAL else "Development evidence, not qualification."
TAG = "45L" if QUAL else "shark" + ("" if LANE_ID == "B" else LANE_ID.lower())  # evidence label
_REPO = Path(__file__).resolve().parents[3]
# designs: the lane's committed copy (eve-watch); on the 45L the extract's own parts/design/examples/out (same commit)
DESIGNS = (_REPO if QUAL else LANE / "src") / next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--design-root=")),
                                                   "parts/design/examples/out")  # --design-root=<repo-relative>: e.g. organic examples
STAGED = LANE / "mind" / "designs"  # the lane mind's designs dir (approvals)
# --version=N (default 1): the design version dispatched, approved and checked. --design-dir=<dir>: read the design from
# <dir>/<name>/v<N>/ (a mind designs dir, e.g. the 45L's C:\eve\mind\designs for O175's nether_portal, deployed there
# from 3d915efd and not on main) instead of <design-root>/<name>/
VER = int(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--version=")), "1"))
DESIGN_DIR = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--design-dir=")), None)
LIGHT = "--light-portal" in sys.argv  # O175: after the build is judged, light the portal frame by hand through the mind
# --keep-site (O175, foundation): leave the built site standing and Eve where she is at the end (no final clear, no park);
# the O59 restore and the difficulty restore still run. Whoever asked clears the site and parks her by hand afterwards.
KEEP_SITE = "--keep-site" in sys.argv
# --wrong-log=X,Y,Z (O202): before the build, Eve places ONE oak_log with the wrong axis (x) at that design-local cell by a
# separate place_block dispatch (outside any build grant: sense's H-trees then denies breaking it, placed_block); the cell
# under it is set first by RCON to its design block so the log has support. The build's FIX break of it must be refused and
# the cell go manual ("sense H-trees"), the rest of the build matching.
_wl = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--wrong-log=")), None)
WRONG = tuple(int(v) for v in _wl.split(",")) if _wl else None


def light_portal(origin, d):
    """O175: light the built portal frame through the mind, as 'light the portal' will (not mapped yet): flint and steel in
    hotbar slot 8, walk in front of the frame (2 clear), select slot 8, use it on the top face of the bottom inside obsidian;
    then every inside cell must hold minecraft:nether_portal. Before the run's final clear, which removes it. Returns checks."""
    ox, oy, oz = origin
    sx, sy = d["size"]["x"], d["size"]["y"]
    out = []
    r(f"item replace entity {P} hotbar.8 with minecraft:flint_and_steel")
    for skill, params in (("walk_to", {"pos": {"x": ox + 1.5, "y": oy, "z": oz - 1.5}, "tolerance": 0.8}),
                          ("select_hotbar", {"slot": 8}),
                          ("use_item", {"target_pos": {"x": ox + 1, "y": oy, "z": oz}, "face": "up"})):
        res = eve("do", {"skill": skill, "params": params, "wait": 60}, 90)
        out.append((res.get("status") == "ok", f"light: {skill} {json.dumps(params)} -> {res.get('status')}/{res.get('code') or res.get('error')} "
                                                 f"{res.get('note') or ''}".strip()))
    time.sleep(1.0)
    inside = [(ox + x, oy + y, oz) for x in range(1, sx - 1) for y in range(1, sy - 1)]
    lit = [c for c in inside if test(c, "minecraft:nether_portal")]
    out.append((len(lit) == len(inside), f"light: portal blocks {len(lit)}/{len(inside)} inside the frame"))
    return out


def ddir(name):
    """The folder holding this design's design.json, build_order.json, scaffold.json, bom.json and gate.json."""
    return Path(DESIGN_DIR) / name / f"v{VER}" if DESIGN_DIR else DESIGNS / name
DB = LANE / "mind" / "eve.db"
sys.path.insert(0, str(LANE / "ops"))
import rcon  # noqa: E402  foundation's RCON client, lane-B copy
from websockets.asyncio.client import connect  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
TOKEN = (LANE / ("mind/config/eve-link-token.txt" if QUAL else "secrets/eve-link-token.txt")).read_text().strip()
CLI = f"ws://127.0.0.1:{_LANES[LANE_ID][2]}/cli"
P = _LANES[LANE_ID][1]
MODS = LANE / "src" / "parts" / "body" / "run" / "mods"
CANDIDATES = MODS.parent / "eve-builder" / "candidates.jsonl"  # O94f: the client's stand-choice rows (eve-candidates/1)
CONFIG = LANE / "src" / "parts" / "body" / "run" / "config" / "eve-builder.json"
# --deploy installs this jar; --jar=<path> names another (O89h: an approved jar rebuilt from an older commit)
BUILT = Path(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--jar=")), REPO / "parts" / "builder" / "build" / "libs" / "eve-builder-0.1.0.jar"))
LOCK = str(LANE / "ops" / "lane-lock.ps1")
DERIVED = {"north", "east", "south", "west", "up", "shape", "moisture", "age"}


def r(cmd):
    return rcon.command(SERVER, cmd)


async def _cli(cmd, args, timeout):
    async with connect(CLI, open_timeout=3, max_size=4 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"token": TOKEN, "cmd": cmd, "args": args}))
        return json.loads(await asyncio.wait_for(ws.recv(), timeout))


def eve(cmd, args=None, timeout=60):
    try:
        return asyncio.run(_cli(cmd, args or {}, timeout))
    except Exception as e:  # mind restarting: recorded as the trial's result, never a crash
        return {"ok": False, "error": "cli_exception", "detail": f"{type(e).__name__}: {e}"}


# ---- the design, decoded exactly as the mod does ----

def load(name):
    d = json.loads((ddir(name) / "design.json").read_text(encoding="utf-8"))
    bo = json.loads((ddir(name) / "build_order.json").read_text(encoding="utf-8"))
    doc = {"blocks": d["blocks"], "origin": [d["origin"][k] for k in "xyz"], "palette": d["palette"], "roles": d["roles"],
           "size": [d["size"][k] for k in "xyz"]}
    h = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    raw = base64.b64decode(d["blocks"])
    idx = [raw[i] | raw[i + 1] << 8 for i in range(0, len(raw), 2)]
    sx, sy, sz = doc["size"]
    cells = {(x, y, z): d["palette"][idx[x + sx * (z + sz * y)]] for y in range(sy) for z in range(sz) for x in range(sx)}
    if "steps" in bo:  # verbose shape
        steps = [{"layer": s["layer"], "pos": s["pos"], "state": s["state"], "items": s["items"]} for s in bo["steps"]]
    else:  # compact shape: state from the grids, items from bom.json items_per_state (rows are design-local)
        ips = json.loads((ddir(name) / "bom.json").read_text(encoding="utf-8"))["items_per_state"]
        sc = json.loads((ddir(name) / "scaffold.json").read_text(encoding="utf-8")) if (ddir(name) / "scaffold.json").exists() else None
        steps = []
        for layer, x, y, z, *_ in bo["rows"]:
            if layer == "final":
                state = cells[(x, y, z)]
            elif layer == "scaffold":
                raw2 = base64.b64decode(sc["blocks"])
                k = [sc["origin"][a] - d["origin"][a] for a in "xyz"]
                lx, ly, lz = x - k[0], y - k[1], z - k[2]
                i2 = lx + sc["size"]["x"] * (lz + sc["size"]["z"] * ly)
                state = sc["palette"][raw2[2 * i2] | raw2[2 * i2 + 1] << 8]
            else:
                continue
            steps.append({"layer": layer, "pos": [x, y, z], "state": state, "items": ips.get(state, [])})
    items = {}  # given: final + scaffold items (scaffold blocks come back when broken)
    for s in steps:
        if s["layer"] in ("final", "scaffold"):
            for i in s["items"]:
                items[i["item"]] = items.get(i["item"], 0) + i["count"]
    return d, h, cells, items, [s for s in steps if s["layer"] == "final"]


def scaffold_cells(name, d, origin, states=False):
    """World positions of scaffold.json's non-air cells (its origin is its own min corner; shifted with the build origin);
    states=True: (pos, state) pairs."""
    f = ddir(name) / "scaffold.json"
    if not f.exists():
        return []
    sc = json.loads(f.read_text(encoding="utf-8"))
    raw = base64.b64decode(sc["blocks"])
    idx = [raw[i] | raw[i + 1] << 8 for i in range(0, len(raw), 2)]
    sx, sy, sz = (sc["size"][k] for k in "xyz")
    off = [origin[i] + sc["origin"][k] - d["origin"][k] for i, k in enumerate("xyz")]
    out = [((off[0] + x, off[1] + y, off[2] + z), sc["palette"][idx[x + sx * (z + sz * y)]]) for y in range(sy) for z in range(sz)
           for x in range(sx) if sc["palette"][idx[x + sx * (z + sz * y)]] != "minecraft:air"]
    return out if states else [c for c, _ in out]


def cage_pick(name, d, cells, origin, box):
    """O94ah: (stand, cage cells, uses, covered) in world coordinates: the planned ground stand outside the design that most
    steps use whose cage cells stay out of the design's box and off every scaffold cell, inside the cleared box (x1, y1, z1,
    x2, y2, z2); ties go to the cage covering fewer other stands (covered = their uses: those steps search their own). None
    when no stand qualifies."""
    bo = json.loads((ddir(name) / "build_order.json").read_text(encoding="utf-8"))
    si = bo["columns"].index("stand")
    at = lambda v: tuple(v["pos"][k] for k in "xyz") if isinstance(v, dict) else tuple(v)
    uses = {}
    for row in bo["rows"]:
        if row[si]:
            uses[at(row[si])] = uses.get(at(row[si]), 0) + 1
    sx, sy, sz = (d["size"][k] for k in "xyz")
    sc = {tuple(c[i] - origin[i] for i in range(3)) for c in scaffold_cells(name, d, origin)}
    best = None
    for (x, y, z), n in uses.items():
        if y != 0 or (0 <= x < sx and 0 <= z < sz) or (x, y, z) in sc:
            continue
        cage = [(x + dx, k, z + dz) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)) for k in (0, 1)] + [(x, 2, z)]
        w = [tuple(origin[i] + c[i] for i in range(3)) for c in cage]
        if any(c in sc or (0 <= c[0] < sx and 0 <= c[1] < sy and 0 <= c[2] < sz) for c in cage)                 or not all(box[i] <= c[i] <= box[i + 3] for c in w for i in range(3)):
            continue
        cov = sum(m for t, m in uses.items() if t in cage or (t[0], t[1] + 1, t[2]) in cage)
        if best is None or (n, -cov) > (best[2], -best[3]):
            best = ((origin[0] + x, origin[1] + y, origin[2] + z), w, n, cov)
    return best


def cage_checks(log, cage):
    """O94ah pass conditions from the client log slice of the run: no walk_to the caged stand, the planned walks to it skipped
    as unreachable, never stranded."""
    c = f"{cage[0]}, {cage[1]}, {cage[2]}"
    walks = [l for l in log if f"walk to design's planned spot {c} " in l or f"eve-builder: walk to {c} to click" in l]
    skips = [l for l in log if f"design's planned spot {c} for" in l and "is not reachable" in l]
    strand = [l for l in log if "eve-builder: stranded at" in l]
    return [(not walks, f"O94ah: walks to the caged stand {cage}: {len(walks)}"),
            (len(skips) > 0, f"O94ah: design's walks to it skipped as unreachable (Walk): {len(skips)}"),
            (not strand, f"O94ah: stranded stops: {len(strand)}")]


def parse(state):
    if "[" not in state:
        return state, {}
    i, rest = state.split("[", 1)
    return i, dict(kv.split("=") for kv in rest[:-1].split(","))


def fmt(i, props):
    return i + ("[" + ",".join(f"{k}={v}" for k, v in sorted(props.items())) + "]" if props else "")


def test(pos, state):
    return "passed" in r(f"execute in {DIM} if block {pos[0]} {pos[1]} {pos[2]} {state}").lower()


def classify(pos, want):
    """Server truth for one cell: match | derived_mismatch | wrong_state | wrong_block | missing | extra."""
    if test(pos, want):
        return "match"
    i, props = parse(want)
    if i == "minecraft:air":
        return "extra"
    if test(pos, i):
        return "derived_mismatch" if test(pos, fmt(i, {k: v for k, v in props.items() if k not in DERIVED})) else "wrong_state"
    return "missing" if test(pos, "minecraft:air") else "wrong_block"


def observed_state(pos, want):
    """For the report: the design id with each property value the server confirms, '?' where it differs."""
    i, props = parse(want)
    if not test(pos, i):
        return "air" if test(pos, "minecraft:air") else "other block"
    return fmt(i, {k: (v if test(pos, f"{i}[{k}={v}]") else "?") for k, v in props.items()})


def judge_cells(origin, cells):
    out = []
    for (x, y, z), want in sorted(cells.items(), key=lambda t: (t[0][1], t[0][2], t[0][0])):
        pos = (origin[0] + x, origin[1] + y, origin[2] + z)
        c = classify(pos, want)
        out.append({"pos": pos, "local": (x, y, z), "want": want, "class": c,
                    **({"observed": observed_state(pos, want)} if c not in ("match", "extra") else {})})
    return out


# ---- inventory ----

def stacks(snbt):
    """(id, count, has_components) per top-level compound of an SNBT item list. Brace depth, not a flat regex: a damaged or
    named item nests `components: {...}`, which the old regex mistook for the item (CP-52 45L: damaged hoes never counted).
    ponytail: braces inside quoted strings (a custom name with "{") would confuse the depth count."""
    import re
    out, depth, top = [], 0, []
    for ch in snbt[snbt.find("[") + 1:] if "[" in snbt else "":
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                t = "".join(top)
                i, c = re.search(r'id: "([^"]+)"', t), re.search(r'count: (\d+)', t)
                if i:
                    out.append((i.group(1), int(c.group(1)) if c else 1, "components:" in t))
                top = []
        elif depth == 1:
            top.append(ch)
    return out


def inv(detail=False):
    s = stacks(r(f"data get entity {P} Inventory"))
    out = {}
    for i, c, _ in s:
        out[i] = out.get(i, 0) + c
    return (out, {i for i, _, comp in s if comp}) if detail else out


def give(stacks):
    """Adds items; never /clear: on the 45L that would destroy her own items' data (enchantments, durability, names)."""
    for it, n in stacks:
        while n > 0:
            k = min(n, 64)
            r(f"give {P} {it} {k}")
            n -= k
    time.sleep(0.3)


# O59 exact inventory restore, as combat's harness does: every slot copied with its components into two barrels next to her
# park spot (`item replace ... from entity`), her inventory cleared so the run starts from the kit alone, and at the end
# `clear` + every slot copied back. The park spot is loaded at both ends (she is parked there). A crashed run leaves the
# record file and the barrels; the next run refuses until `laneb_build.py restore --lane X` has put them back.
SLOTS = [f"hotbar.{i}" for i in range(9)] + [f"inventory.{i}" for i in range(27)] + \
    ["armor.head", "armor.chest", "armor.legs", "armor.feet", "weapon.offhand"]
STASH_FILE = REPO / "evidence" / f"builder-stash-{LANE_ID}.json"


def inv_hash():
    """Her whole inventory (main + equipment) as the server prints it, hashed: start == end is a pass condition (O59)."""
    return hashlib.sha256(inv_text().encode()).hexdigest()[:16]


def dry_inv_hash():
    """inv_hash for the dry runs, which must also work while the lane's server is down."""
    try:
        return inv_hash()
    except OSError as e:
        return f"unavailable ({e.__class__.__name__}: server down?)"


def inv_text():
    """Inventory + equipment as printed, an absent field as its empty value: after a dimension change the server prints no
    `equipment` at all where it printed `{}` before (proof 3, cp53tall End -> overworld; same items, different hash)."""
    out = []
    for k, empty in (("Inventory", "[]"), ("equipment", "{}")):
        t = r(f"data get entity {P} {k}")
        out.append(t.split("entity data:", 1)[1].strip() if "entity data:" in t else empty)
    return "".join(out)


def park():
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LANE / "ops" / "park-eve.ps1"),
                    "-Root", str(SERVER), "-Player", P], capture_output=True)
    time.sleep(1.0)


def client_log(root=None, ops=None):
    """The client's log, as the lane's own ops scripts name it (lanes: <lane>\\logs\\client.log in restart-client.ps1; the 45L:
    C:\\eve\\client\\logs\\latest.log in foundation's check-client.ps1). The first quoted client.log / latest.log path under
    the lane root, an existing one first; else the old lane layout. (cp53tall on the 45L looked for <lane>\\logs\\client.log.)"""
    import re
    root, found = Path(root or LANE), []
    for f in sorted(Path(ops or root / "ops").glob("*.ps1")):
        for m in re.findall(r"'([A-Za-z]:\\[^']*?\\(?:client|latest)\.log)'", f.read_text(encoding="utf-8", errors="replace")):
            q = Path(m)
            if str(q).lower().startswith(str(root).lower()) and q not in found:
                found.append(q)
    found.append(root / "logs" / "client.log")  # the lane layout, always a candidate (lane C's scripts name it unquoted)
    return next((q for q in found if q.exists()), found[0])


def map_forbidden(dim):
    """Boxes from <lane>/ops/fixture-map-45l.json the stash must avoid: every laid[] box whose laid_if holds now, and every
    other part's runs[] box (builder's own run boxes, its declared stash strip among them, are not forbidden to it). No map
    file = none; an unreadable map = refuse."""
    f = LANE / "ops" / "fixture-map-45l.json"
    if not f.exists():
        return []
    try:
        m = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        sys.exit(f"I26a: fixture map {f} unreadable ({e}); inventory not stashed, nothing changed")
    same = lambda e: e.get("dim") in ("*", dim.split(":")[-1], dim)
    out = []
    for e in m.get("laid", []):
        cond = str(e.get("laid_if", "always"))
        if cond.startswith("file:"):
            held = Path(cond[5:]).exists()
        elif cond.startswith("block:"):
            x, y, z, bid = cond[6:].strip("'\" ").split()
            held = "passed" in r(f"execute in {dim} if block {x} {y} {z} {bid}").lower()
        else:
            held = True
        if held and same(e):
            out.append(tuple(e["box"]))
    for part, rs in m.get("runs", {}).items():
        if part != "builder":
            out += [tuple(e["box"]) for e in rs if same(e)]
    return out


def in_box(c, b, margin=1):
    """Cell c inside fill box b = (x1, y1, z1, x2, y2, z2), grown by margin."""
    return all(min(b[i], b[i + 3]) - margin <= c[i] <= max(b[i], b[i + 3]) + margin for i in range(3))


def stash_save():
    """Returns her inventory hash before the run. Leaves her with an empty inventory (the kit comes next)."""
    import math, re
    if STASH_FILE.exists():
        sys.exit(f"a previous run's inventory stash is still out ({STASH_FILE}): run `laneb_build.py restore --lane {LANE_ID}` first "
                 "(nothing was changed)")
    park()
    pos, _ = eve_state()
    if pos is None:
        sys.exit("O59: Eve's position unknown; inventory not stashed, nothing changed")
    dim = re.search(r'"([a-z0-9_:]+)"', r(f"data get entity {P} Dimension")).group(1)
    fx, fy, fz = (math.floor(v) for v in pos)
    ex = f"execute in {dim} "
    # OUTSIDE the park shelter: park-eve.ps1 rebuilds it with `fill ... hollow` (interior to air) on every park, which destroyed
    # barrels inside it with their contents (O59 lane C proof 03:00, EveDev2's items lost). Its box is her spot ±3, so ±5.
    # ponytail: west of the park spot only (I26a: 5 south lands on the shelter's doorway line z -43, which body's pre-check and
    # stair need); foundation's 45L fixture map, when it exists, replaces this with "not in any declared box"
    offs = [(-5, dz) for dz in range(-2, 3)]  # x -21 from the park spot: foundation's declared west strip (fixture map)
    free = [(fx + dx, fy + dy, fz + dz) for dx, dz in offs for dy in range(6)  # y 99..104: above terrain where the ground is high
            if "passed" in r(f"{ex}if block {fx + dx} {fy + dy} {fz + dz} minecraft:air").lower()]
    # I19: never inside a box the park script fills (read from the lane's own park-eve.ps1, so a different shelter on the 45L
    # is covered too); no fill box readable = refuse rather than guess
    pk = LANE / "ops" / "park-eve.ps1"
    boxes = [tuple(map(int, m)) for m in re.findall(r"fill (-?\d+) (-?\d+) (-?\d+) (-?\d+) (-?\d+) (-?\d+)", pk.read_text(encoding="utf-8", errors="replace"))] if pk.exists() else []
    if not boxes:
        sys.exit(f"I19: no fill box found in {pk}; cannot prove the stash is outside the park shelter; nothing changed")
    free = [c for c in free if not any(in_box(c, b) for b in boxes)]
    fmap = map_forbidden(dim)  # foundation's fixture map (I26a): never on a laid box or another part's run box
    free = [c for c in free if not any(in_box(c, b, margin=0) for b in fmap)]
    if len(free) < 2:
        sys.exit(f"O59: no two free air cells next to her park spot {pos} outside the park fill box(es) {boxes}; nothing changed")
    h0, spots = inv_hash(), free[:2]
    for x, y, z in spots:
        r(f"{ex}run setblock {x} {y} {z} minecraft:barrel")
    STASH_FILE.parent.mkdir(parents=True, exist_ok=True)
    STASH_FILE.write_text(json.dumps({"dim": dim, "spots": spots, "hash": h0, "player": P,  # snbt: what she had, if all else fails
                                      "snbt": [r(f"data get entity {P} {k}") for k in ("Inventory", "equipment")]}), encoding="utf-8")
    for i, s in enumerate(SLOTS):
        x, y, z = spots[i // 27]
        r(f"{ex}run item replace block {x} {y} {z} container.{i % 27} from entity {P} {s}")
    held = sum(len(re.findall(r'id: "', r(f"{ex}run data get block {x} {y} {z} Items"))) for x, y, z in spots)
    mine = len(stacks(r(f"data get entity {P} Inventory"))) + len(re.findall(r'id: "', r(f"data get entity {P} equipment")))
    if held < mine:  # never clear her if the copy is short; the record file makes the next run restore first
        sys.exit(f"O59: stash holds {held} stacks, she has {mine}; inventory NOT cleared (restore with `laneb_build.py restore`)")
    r(f"clear {P}")
    return h0


def stash_restore():
    """Copy every slot back exactly (an empty stash slot empties hers), remove the barrels. Returns (hash_after, note)."""
    if not STASH_FILE.exists():
        return None, "O59: no stash to restore"
    st = json.loads(STASH_FILE.read_text(encoding="utf-8"))
    ex = f"execute in {st['dim']} "
    park()  # the barrels sit beside her park spot: loaded once she is there
    if not all("passed" in r(f"{ex}if block {x} {y} {z} minecraft:barrel").lower() for x, y, z in st["spots"]):
        return None, f"O59: stash barrels {st['spots']} not readable (unloaded?); her inventory left as is, restore later"
    r(f"clear {P}")  # first, so a slot that was empty before ends empty even if copying air is refused
    for i, s in enumerate(SLOTS):
        x, y, z = st["spots"][i // 27]
        r(f"{ex}run item replace entity {P} {s} from block {x} {y} {z} container.{i % 27}")
    for x, y, z in st["spots"]:
        r(f"{ex}run data merge block {x} {y} {z} {{Items:[]}}")
        r(f"{ex}run setblock {x} {y} {z} minecraft:air")
    STASH_FILE.unlink()
    h1 = inv_hash()
    if h1 != st["hash"]:  # keep both raw texts to see what differs
        (STASH_FILE.parent / f"builder-stash-{LANE_ID}-mismatch-{datetime.now():%Y%m%d-%H%M%S}.json").write_text(json.dumps(
            {"start": st.get("snbt"), "end": [r(f"data get entity {P} {k}") for k in ("Inventory", "equipment")]}, indent=1), encoding="utf-8")
    return h1, (MEM_NOTE + "; " if MEM_NOTE else "") + f"O59 inventory {'restored exactly' if h1 == st['hash'] else 'MISMATCH'}: hash {st['hash']} -> {h1}"


MEM_NOTE = ""


def mem_state():
    """I29: free RAM, commit and paging on this machine (Shark), for the record and the start gate."""
    ps = (r"$o = Get-CimInstance Win32_OperatingSystem; $p = (Get-Counter '\Memory\Pages/sec' -SampleInterval 1 -MaxSamples 1)"
          ".CounterSamples[0].CookedValue; '{0} {1} {2} {3}' -f $o.FreePhysicalMemory, ($o.TotalVirtualMemorySize - $o.FreeVirtualMemory), "
          "$o.TotalVirtualMemorySize, [int]$p")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60).stdout.split()
    free, used, total, pages = (int(x) for x in out[:4])
    return {"free_gb": round(free / 2**20, 1), "commit_gb": round(used / 2**20, 1), "commit_limit_gb": round(total / 2**20, 1), "pages_s": pages}


def start_run():
    """I29 memory gate (lanes: start only above 8 GB free; always recorded), then stash_save, releasing the lane lock if
    either refuses (neither changes anything when it refuses)."""
    global MEM_NOTE
    try:
        m = mem_state()
        MEM_NOTE = f"I29 memory at start: {m['free_gb']} GB free, commit {m['commit_gb']}/{m['commit_limit_gb']} GB, {m['pages_s']} pages/s"
        print(MEM_NOTE, flush=True)
        if not QUAL and m["free_gb"] < 8:
            sys.exit(f"I29: only {m['free_gb']} GB free (gate 8 GB); nothing run")
        return stash_save()
    except SystemExit:
        lane_lock("release")
        raise


def take_back(pre, item_ids=None):
    """Back to the inventory she had before the run (foundation, CP-52 45L: drops she picked up and damaged tools stayed):
    every id she gained is cleared by the count gained. Exact when she had none of it before; when she did and some stack of
    it has components (damage, enchantments, names), a count-clear could take her own item instead, so it is left and
    reported. Losses (eaten, used) are reported, never re-given. Returns a note ('' when the inventory is as before).
    item_ids: ignored (kept for the callers)."""
    now, comp = inv(detail=True)
    left = []
    for it, n in now.items():
        extra = n - pre.get(it, 0)
        if extra <= 0:
            continue
        if pre.get(it, 0) and it in comp:
            left.append(f"{it} +{extra} (she had some before; components, not auto-cleared)")
            continue
        r(f"clear {P} {it} {extra}")
    lost = [f"{it} -{c - now.get(it, 0)}" for it, c in pre.items() if now.get(it, 0) < c]
    after = inv()
    left += [f"{it} still +{after[it] - pre.get(it, 0)}" for it in after if after[it] > pre.get(it, 0) and not any(x.startswith(it + " +") for x in left)]
    return "; ".join(x for x in (f"inventory not restored: {left}" if left else "", f"items used up: {lost}" if lost else "") if x)


def spent_expected(results, steps):
    """Items that should have left the inventory for what the server shows in place: the step's items for a cell whose block id
    is right, except a double slab that is a single slab (one item)."""
    want = {}
    by_local = {tuple(s["pos"]): s for s in steps}
    for c in results:
        s = by_local.get(tuple(c["local"]))
        if s is None or c["class"] in ("missing", "wrong_block", "extra"):
            continue
        for i in s["items"]:
            n = i["count"]
            if "type=double" in s["state"] and c["class"] == "wrong_state":
                n = 1
            want[i["item"]] = want.get(i["item"], 0) + n
    return want


# ---- lane plumbing (eve-watch's lock and standing permission) ----

def lane_lock(op, minutes=0):
    if QUAL:
        return  # the 45L: foundation schedules every run itself
    if op == "release" and os.environ.get("EVE_SERIES"):
        return  # tools/series.py holds the lock across the whole series and releases it at the end
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", LOCK, op, "-By", "eve-builder"]
    if op == "acquire":
        args += ["-Minutes", str(minutes), "-What", "laneb_build"]
    p = subprocess.run(args, capture_output=True, text=True)
    if p.returncode != 0:
        if op == "release":  # never let a failed release hide the error that got us here
            print(f"WARNING lane lock release: {p.stdout.strip()} {p.stderr.strip()}", file=sys.stderr)
            return
        if not (op == "acquire" and "by=eve-builder " in p.stdout):  # already ours (taken ahead, e.g. to go first after a release)
            sys.exit(f"lane lock {op}: {p.stdout.strip()} {p.stderr.strip()}")
    if op == "acquire":
        own_lock("right after acquire")


def own_lock(when):
    if QUAL:
        return
    """Re-read the lock: the acquire exit code alone let a run start on a lane another part held (2026-10-20 16:36, lane C).
    Every phase that touches the world checks this first; a lost lock ends the run before the next RCON write."""
    p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", LOCK, "show"], capture_output=True, text=True)
    held = p.stdout.strip()
    if "by=eve-builder " not in held + " " or "expired" in held:
        raise SystemExit(f"lane lock not ours {when}: {held!r}; touching nothing more")


def restart_client(respawn=False):
    p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LANE / "ops" / "restart-client.ps1"),
                        "-By", "eve-builder"] + (["-Respawn"] if respawn else []), capture_output=True, text=True, timeout=300)
    return p.returncode == 0, (p.stdout[-400:] + p.stderr[-400:]).strip()


def write_config(approval_gate=None):
    """approval_gate (O94q-flag): None leaves the key out (the mod's default: off); True/False writes it."""
    if QUAL:
        return  # the 45L: foundation deploys the client config (design_roots = C:\eve\mind\designs)
    # the lane's own committed copies only (eve-watch 2026-10-20): its designs/ (the mind's approvals) first, then examples
    cfg = {"design_roots": [str(STAGED), str(LANE / "src" / "parts" / "design" / "examples" / "out")]}
    if approval_gate is not None:
        cfg["approval_gate"] = approval_gate
    CONFIG.write_text(json.dumps(cfg, indent=1), encoding="utf-8")


def gatecheck():
    """O94q-flag lane case: a hold-gated design (default the relay horse, --design-root=parts/design/organic/examples)
    staged with its gate.json and approved through the mind, then dispatched twice with her inventory stashed (nothing to
    place even if a gate wrongly passed: the deficit check comes next): approval_gate off, then on. PASS = both end
    failed/gate_refused, off for gate.json's hold (the pre-O94q rule), on for the approval.json check; nothing placed, her
    inventory unchanged. Lanes only."""
    if QUAL:
        sys.exit("gatecheck is a lane case (O94q-flag); never the 45L")
    name = sys.argv[sys.argv.index("--design") + 1] if "--design" in sys.argv else "cp69-horse-triposr"
    d, d_hash, cells, items, steps = load(name)
    gate = json.loads((ddir(name) / "gate.json").read_text(encoding="utf-8"))
    if gate.get("decision") != "hold" or gate.get("design_hash") != d_hash:
        sys.exit(f"gatecheck needs a hold gate for this design: {gate}")
    if NO_GATE:
        sys.exit("gatecheck stages the design with its gate.json: no --no-gate")
    st = eve("status")
    if not st.get("body") or st.get("open_action"):
        sys.exit(f"lane {LANE_ID} not ready: {st}")
    lane_lock("acquire", 20)
    appr = stage_and_approve(name)
    if not appr.get("ok"):
        lane_lock("release")
        sys.exit(f"mind approval refused, nothing run: {appr}")
    root = REPO / "evidence" / "CP-49"
    num = int(sys.argv[sys.argv.index("--attempt") + 1]) if "--attempt" in sys.argv else 1 + sum(1 for x in root.iterdir() if x.is_dir() and f"-{TAG}-builder-gate-attempt-" in x.name)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    out = root / f"{stamp}-{TAG}-builder-gate-attempt-{num}"
    out.mkdir(parents=True)
    # never reached (both dispatches end at the gate); z differs per dispatch and attempt so the mind's CP-22 retry_limit
    # (3 failures of the same params in 10 min) cannot refuse a rerun (pre-window review); inside build_plot (z -48..-32)
    origin = lambda flag: (23, 151, -45 + 2 * (num % 6) + int(flag))
    notes = [f"fixture approval through the mind: `eve approve {name} v{VER}` -> approval {appr.get('approval_id')} channel {appr.get('channel')}; "
             f"staged {STAGED / name / 'v1'} with gate.json (decision hold, category {gate.get('category')}) and the mind's approval.json"]
    h0 = start_run()  # O59: her items into the stash, she holds nothing
    t0, runs, feet = time.time(), [], []
    import threading
    stop_f = threading.Event()  # I29f: state samples every 0.25 s across both dispatches (watch, 09:40: the gate had no gap)
    th_f = threading.Thread(target=sample_feet, args=(stop_f, feet), daemon=True)
    try:
        own_lock("before the dispatches")
        i0 = inv()
        th_f.start()
        for flag in (False, True):
            write_config(approval_gate=flag)
            res = build(name, d_hash, origin(flag), 120)
            res["_approval_gate"] = flag
            runs.append(res)
        stop_f.set()
        th_f.join(2)
        i1 = inv()
    finally:
        stop_f.set()
        write_config()  # the lane's config back to the default (no approval_gate key: off)
        own_lock("before restoring Eve")
        h1, inv_note = stash_restore()
        notes.append(inv_note)
        lane_lock("release")
    vals = [ran(x) for x in runs]
    valid = all(v for v, _ in vals)
    off, on = runs
    ev = lambda x: x.get("evidence") or {}
    checks = [
        (off.get("status") == "failed" and off.get("code") == "gate_refused" and "approval_gate off" in str(off.get("note")),
         f"approval_gate off: {off.get('status')}/{off.get('code')}: {off.get('note')}"),
        (on.get("status") == "failed" and on.get("code") == "gate_refused" and "approval.json" in str(on.get("note")),
         f"approval_gate on: {on.get('status')}/{on.get('code')}: {on.get('note')}"),
        (ev(off).get("approval_gate") is False and ev(on).get("approval_gate") is True,
         f"evidence approval_gate: off run {ev(off).get('approval_gate')}, on run {ev(on).get('approval_gate')}"),
        (not any(ev(x).get("placed") for x in runs) and i0 == i1, f"nothing placed (placed {[ev(x).get('placed') for x in runs]}), inventory {i0} -> {i1}"),
        (h1 == h0, inv_note),
        (len(feet) > 0, f"state samples {len(feet)}, rcon_sample_gap_s {max_gap(feet)} (server-side; I29e's state-stream gap is watch's mind-DB poll)"),
    ]
    passed = valid and all(ok for ok, _ in checks)
    (out / "result.json").write_text(json.dumps({"valid": valid, "validity": [w for _, w in vals], "runs": runs, "notes": notes,
                                                 "feet": feet, "rcon_sample_gap_s": max_gap(feet)}, indent=1), encoding="utf-8")
    lines = [f"# CP-49 — eve-builder lane {LANE_ID} (`shark`) {stamp} gate attempt {num} (O94q-flag): "
             + ("PASS" if passed else ("FAIL" if valid else "INVALID (O46: " + "; ".join(w for _, w in vals) + ")")), "",
             f"Hypothesis: the hold-gated `{name}` (hash {d_hash[:12]}) is refused with approval_gate off for gate.json's hold (the "
             "pre-O94q rule) and with it on for the approval.json check; nothing moves or is placed.",
             f"Versions: repo {git_head()} (client: {jar_sha()}). Elapsed {time.time() - t0:.0f} s. Development evidence, not qualification.", "",
             *[f"- {n}" for n in notes], "", "| check | ok |", "|---|---|", *[f"| {d_} | {'PASS' if ok else 'FAIL'} |" for ok, d_ in checks]]
    text = "\n".join(lines) + "\n"
    (out / "RESULT.md").write_text(text, encoding="utf-8")
    summ = REPO / "parts" / "builder" / "evidence-summaries" / f"CP-49-{stamp}-{TAG}-builder-gate-attempt-{num}.md"
    summ.write_text(text, encoding="utf-8")
    print(text)


def deploy():
    """Swap our jar (exactly one eve-builder jar in mods), restart the client."""
    import shutil
    task = "Eve Shark Client" + ("" if LANE_ID == "B" else " " + LANE_ID)
    stop = (f"Stop-ScheduledTask '{task}'; Get-CimInstance Win32_Process | Where-Object {{ $_.Name -in 'java.exe','javaw.exe'"
            f" -and $_.CommandLine -like '*{LANE.name}\\src\\parts\\body*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}")
    subprocess.run(["powershell", "-NoProfile", "-Command", stop], capture_output=True, timeout=60)
    time.sleep(3)
    for j in MODS.glob("eve-builder-*.jar"):
        j.unlink()
    shutil.copy2(BUILT, MODS / BUILT.name)
    ok, out = restart_client()
    if not ok:
        lane_lock("release")
        sys.exit(f"restart-client failed: {out}")


def jar_sha():
    """The eve-builder jar the client loads, named with its sha256 (O35: RESULT.md names the exact jar). On the 45L it is
    looked for under the lane's client folder (foundation deploys it), on the Shark lanes in the lane client's mods."""
    js = sorted(MODS.glob("eve-builder-*.jar")) if not QUAL else sorted(
        j for j in (LANE / "client").rglob("eve-builder-*.jar") if "mods" in j.parts)
    return ", ".join(f"{j.name} {hashlib.sha256(j.read_bytes()).hexdigest()} ({j.parent})" for j in js) or "none"


def eve_state():
    """Eve's server position and health (RCON `data get`), or (None, None)."""
    import re
    m = re.search(r"\[([-\d.]+)d, ([-\d.]+)d, ([-\d.]+)d\]", r(f"data get entity {P} Pos"))
    h = re.search(r"([-\d.]+)f", r(f"data get entity {P} Health"))
    return (tuple(float(v) for v in m.groups()) if m else None), (float(h.group(1)) if h else None)


def park_safe(box, why):
    """O35 (after a near-death on the 45L): before removing any fixture block, park Eve (shelter, peaceful) and check her HP
    and that she is neither inside nor above the fixture volume box = (x0, y0, z0, x1, y1, z1). Otherwise remove nothing."""
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LANE / "ops" / "park-eve.ps1"),
                    "-Root", str(SERVER), "-Player", P], capture_output=True)
    r("difficulty peaceful")
    time.sleep(1.0)
    pos, hp = eve_state()
    x0, y0, z0, x1, y1, z1 = box
    over = pos is not None and x0 - 1 <= pos[0] <= x1 + 2 and z0 - 1 <= pos[2] <= z1 + 2 and pos[1] >= y0 - 1
    if pos is None or over or hp is None or hp < 10:
        raise SystemExit(f"O35 stop before {why}: Eve at {pos}, HP {hp}; fixture box {box}; no block removed")
    return f"O35 before {why}: parked at {tuple(round(v, 1) for v in pos)}, HP {hp:g}, peaceful"


def safe_tp(x, y, z, yaw=0, pitch=30):
    """O39: every player teleport goes through the lane's shared helper (<lane>/ops/safe_tp.py: solid floor, air at feet and
    head, inside an envelope zone; logged in <lane>/safe-tp.log). A refusal stops the run with her not moved. (This replaced
    the harness's own floor check from I9, CP-52 lane B attempt 1.)"""
    try:
        from safe_tp import safe_tp as shared
    except ImportError:
        raise SystemExit(f"O39: no shared safe_tp.py in {LANE / 'ops'}; no teleport, run stopped")
    shared(SERVER, P, x, y, z, yaw, pitch, dim=DIM)  # raises SafeTpRefused (a SystemExit) without moving her


def ran(res):
    """O46: a trial counts only when the mind accepted the command and the action ran (an action id and a terminal status from
    the body). A mind refusal (retry_limit, busy, not_approved...), a CLI failure or a still-running action is INVALID: a
    harness fault, neither a pass nor a behavioural fail. Returns (valid, why)."""
    if not isinstance(res, dict) or res.get("ok") is False or res.get("error"):
        return False, f"not run: {res.get('code') or res.get('error')} {res.get('detail') or ''}".strip()
    if res.get("status") in ("refused", None, "pending", "sent", "accepted", "running"):
        return False, f"not run / not finished: status {res.get('status')} {res.get('code') or ''}".strip()
    if not res.get("action_id"):
        return False, "no action id"
    if res.get("code") == "zone_denied" and "limit:" in str(res.get("note")):  # the envelope's hourly cap tested nothing
        return False, f"server cap: action {str(res['action_id'])[:8]} {res.get('note')}"[:200]
    return True, f"action {str(res['action_id'])[:8]} {res['status']}"


def loop_ran(rep):
    """O46 for the mind's loops (eve build / eve repair): invalid when the loop itself was refused, or when it ended because
    the mind refused its own dispatch (e.g. retry_limit) rather than because an action ran."""
    text = json.dumps(rep, default=str)
    if not isinstance(rep, dict) or rep.get("ok") is False or rep.get("status") in (None, "running"):
        return False, f"loop not run / not finished: {rep.get('status') if isinstance(rep, dict) else rep}"
    if __import__("re").search(r"refused \(", text):  # any mind refusal of its own dispatch (retry_limit, busy, bad_params...)
        return False, "the mind refused its own dispatch (no action ran)"
    return True, f"loop {rep.get('status')}"


def cap_denied(skill, since):
    """The first server cap denial ("limit:...") in this run's actions of `skill`, or None (mind DB, read-only)."""
    import sqlite3
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute("SELECT note FROM actions WHERE skill=? AND sent_ts>=?", (skill, int(since * 1000))).fetchall()
    finally:
        con.close()
    return next((n for (n,) in rows if n and "limit:" in n), None)


def summary_path(name):
    """parts/builder/evidence-summaries/<name>, created if missing (a tools-only extract on the 45L has no such folder)."""
    d = REPO / "parts" / "builder" / "evidence-summaries"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def git_head():
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()


def check_approval(name, d_hash):
    """The 45L: no self-approval (O23 is lanes only). Foundation approves with the owner CLI first; this only checks, read-only,
    that the mind holds a current approval of exactly this hash and that the staged folder is the same design."""
    import sqlite3
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    try:
        row = con.execute("SELECT approval_id, design_hash, decision, invalidated_ts, decided_by, channel FROM approvals "
                          "WHERE project_id=? AND version=? ORDER BY ts DESC LIMIT 1", (name, VER)).fetchone()
    finally:
        con.close()
    staged = STAGED / name / f"v{VER}" / "design.json"
    ok = (row and row[1] == d_hash and row[2] == "approved" and row[3] is None and staged.exists()
          and json.loads(staged.read_text(encoding="utf-8")).get("hash") == d_hash)
    if not ok:
        return {"ok": False, "error": "not_approved_by_foundation", "detail": f"approval {row}; staged {staged} exists={staged.exists()}"}
    return {"ok": True, "approval_id": row[0], "decided_by": row[4], "channel": row[5], "design_hash": row[1],
            "warnings": "owner approval by foundation (labelled fixture step, orchestrator-authorised)"}


def stage_and_approve(name):
    """Fixture self-approval (lane, dev world), allowed by the orchestrator 2026-10-20 for lanes B/C only, never the 45L:
    copy design's committed folder into the lane mind's designs dir and approve it through the mind's own CP-65 path
    (hash re-verify, trusted_players, channel unchanged)."""
    import shutil
    dst = STAGED / name / f"v{VER}"
    src = ddir(name)
    if dst.exists() and (dst / "design.json").read_bytes() != (src / "design.json").read_bytes():
        shutil.rmtree(dst)  # a different design under the same version: never keep its approval
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_file() and not (NO_GATE and f.name == "gate.json"):
            shutil.copy2(f, dst / f.name)
    if NO_GATE:  # O61a: a builder test fixture staged without its gate, as pillar_tall (lanes only)
        (dst / "gate.json").unlink(missing_ok=True)
    return eve("approve", {"project_id": name, "version": f"v{VER}", "by": None})


def action_row(action_id):
    """The mind's record of the action, read-only (status, code, note, evidence)."""
    import sqlite3
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    try:
        row = con.execute("SELECT status, code, note, evidence FROM actions WHERE action_id=?", (action_id,)).fetchone()
    finally:
        con.close()
    return row and {"status": row[0], "code": row[1], "note": row[2], "evidence": json.loads(row[3]) if row[3] else None}


def build(name, d_hash, origin, wait):
    """Dispatch through /cli; the mind waits at most 600 s, so a longer build is followed in its action row."""
    params = {"project_id": name, "version": VER, "design_hash": d_hash, "origin": dict(zip("xyz", origin))}
    t = time.time()
    res = eve("do", {"skill": "build_schematic", "params": params, "wait": min(wait, 600)}, min(wait, 600) + 30)
    while res.get("status") in ("pending", "sent", "running", "accepted") and res.get("action_id") and time.time() - t < wait:
        time.sleep(3)
        row = action_row(res["action_id"])
        if row:
            res = {**res, **row}
    res["_elapsed_s"] = round(time.time() - t, 1)
    return res


# ---- CP-51: the mind's supply loop (`eve build`) around build_schematic ----
# A container row inside build_plot on lane B, reachable on foot (eve-watch's envelope tags): shared chests A/B/C, private
# chest P, crafting table T at y=151, z=-35 (CP-51 attempt 1: eve-skills' floating pad was unreachable from the plot).
# Contents are set per case and emptied again at the end. Builds stay at z <= -40.
# The 45L: no standing row there, so the harness lays one next to its reflex_arena site (floor y=190) and removes it after;
# foundation tags A/B/C `shared` in the 45L envelope (P stays private, the refusal case).
PADS = {"B": {"A": (24, 151, -35), "B": (26, 151, -35), "C": (28, 151, -35), "P": (30, 151, -35), "T": (32, 151, -35)},
        "45L": {"A": (-24, 191, 15), "B": (-22, 191, 15), "C": (-20, 191, 15), "P": (-18, 191, 15), "T": (-16, 191, 15)}}
PAD = PADS.get(LANE_ID, PADS["B"])
JUNK = "minecraft:gravel"


def fill_box(pos, stacks):
    x, y, z = pos
    nbt = ",".join(f'{{Slot:{i}b,id:"{it}",count:{n}}}' for i, (it, n) in enumerate(stacks))
    r(f"data merge block {x} {y} {z} {{Items:[{nbt}]}}")


def box(pos):
    import re
    out = {}
    for body in re.findall(r'\{([^{}]*?)\}', r(f"data get block {pos[0]} {pos[1]} {pos[2]} Items")):
        i, c = re.search(r'id: "([^"]+)"', body), re.search(r'count: (\d+)', body)
        if i:
            out[i.group(1)] = out.get(i.group(1), 0) + int(c.group(1) if c else 1)
    return out


def cp51_cases(items):
    """(name, inventory part, {chest: {item: n}}, fill the inventory with junk, expected supply status)."""
    def without(keys):
        return {k: v for k, v in items.items() if k not in keys}, {k: v for k, v in items.items() if k in keys}
    a_inv, a_box = without({"minecraft:oak_slab", "minecraft:oak_planks", "minecraft:oak_stairs", "minecraft:cobblestone"})
    b_inv, _ = without({"minecraft:oak_planks"})
    c_inv, c_box = without({"minecraft:oak_slab"})
    return [("a_half_in_chest", a_inv, {"A": a_box}, False, "done"),
            ("b_craft_planks", b_inv, {"B": {"minecraft:oak_log": 2}}, False, "done"),
            ("c_inventory_full", c_inv, {"A": c_box}, True, "done"),
            ("d_private_only", c_inv, {"P": c_box}, False, "short")]


def cp51(name, d, d_hash, cells, items, plot):
    if QUAL and plot[1] + 2 + d["size"]["y"] >= 199:
        sys.exit("the cleared box would reach body's sky arena (y 199); nothing run")
    """CP-51 cases through the mind's `cli build` (mind f3c9493+), judged by the server: cells, scaffold, chests."""
    if LANE_ID not in PADS:
        sys.exit("cp51 runs on lane B (eve-watch's row on build_plot) or the 45L (row laid by the harness)")
    sx, sy, sz = (d["size"][k] for k in "xyz")
    for k, pos in ({} if QUAL else PAD).items():  # the 45L row is laid inside the run (and removed in its finally)
        want = "minecraft:crafting_table" if k == "T" else "minecraft:chest"
        if not test(pos, want):
            sys.exit(f"pad {k} at {pos} is not a {want}: ask eve-watch / eve-skills for the pad first")
    st = eve("status")
    if not st.get("body") or st.get("open_action"):
        sys.exit(f"lane B not ready: body {bool(st.get('body'))}, open action {st.get('open_action')}")
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    cases = [c for c in cp51_cases(items) if not only or c[0] in only]
    lane_lock("acquire", 15 + 12 * len(cases))
    appr = check_approval(name, d_hash) if QUAL else stage_and_approve(name)
    if not appr.get("ok"):
        lane_lock("release")
        sys.exit(f"fixture self-approval refused, nothing run: {appr}")
    root = REPO / "evidence" / "CP-51"
    root.mkdir(parents=True, exist_ok=True)
    num = int(sys.argv[sys.argv.index("--attempt") + 1]) if "--attempt" in sys.argv else 1 + sum(1 for x in root.iterdir() if x.is_dir() and f"-{TAG}-builder-attempt-" in x.name)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    out = root / f"{stamp}-{TAG}-builder-attempt-{num}"
    out.mkdir()
    write_config()
    h0 = start_run()  # O59: her inventory into the stash, the run starts from the kit alone
    pre, rows, t0, difficulty0, notes = inv(), [], time.time(), None, []
    ids = set(items) | {JUNK, "minecraft:oak_log", "minecraft:stick"}
    try:
        own_lock("before fixture setup")
        m = __import__("re").search(r"difficulty is (\w+)", r("difficulty"))
        difficulty0 = m.group(1).lower() if m else None
        r("difficulty peaceful")
        r(f"forceload add {plot[0]} {plot[2]} {plot[0] + 12 + sx} {plot[2] + 16}")
        if QUAL:  # lay the row (chests facing south, table) on a floor strip with standing room south of it
            # the strip joins the build sites' floor (z 2..10): the mind walks from the site to the chests (CP-51 45L attempt 1:
            # a strip floating 4 blocks away left every walk "unreachable")
            notes.append(park_safe((-26, 190, 9, -14, 193, 17), "laying the chest row"))
            r("fill -26 190 9 -14 190 17 minecraft:stone")
            r("fill -26 191 11 -14 193 17 minecraft:air")
            for k, pos in PAD.items():
                r(f"setblock {pos[0]} {pos[1]} {pos[2]} " + ("minecraft:crafting_table" if k == "T" else "minecraft:chest[facing=south]"))
        for idx, (cname, inv_part, boxes, junk, expect) in enumerate(cases):
            own_lock(f"before case {cname}")
            # each case builds one block further east: a different site is a different build (the mind's retry_limit
            # keys on identical params, and every supply run starts with a deliberate no_item)
            x0, y0, z0 = plot[0] + idx, plot[1], plot[2]
            origin = (x0 + 3, y0 + 1, z0 + 3)
            notes.append(park_safe((plot[0], y0, z0, plot[0] + 8 + sx, y0 + 2 + sy, z0 + 5 + sz), f"case {cname}'s site reset"))
            r(f"fill {x0} {y0} {z0} {x0 + 5 + sx} {y0} {z0 + 5 + sz} minecraft:stone")
            r(f"fill {x0} {y0 + 1} {z0} {x0 + 5 + sx} {y0 + 2 + sy} {z0 + 5 + sz} minecraft:air")
            r(f"kill @e[type=item,x={x0},y={y0},z={z0},dx={5 + sx},dy={4 + sy},dz={5 + sz}]")
            for k in ("A", "B", "C", "P"):
                fill_box(PAD[k], sorted(boxes.get(k, {}).items()))
            take_back(pre, ids)  # back to what she had before, then this case's part
            give(sorted(inv_part.items()))
            if junk:  # /give fills every free slot; the rest drops and is killed
                give([(JUNK, 64 * 40)])
                time.sleep(0.5)
                r(f"kill @e[type=item,distance=..6,x={origin[0]},y={origin[1]},z={origin[2]}]")
            safe_tp(origin[0] + sx // 2 + 0.5, origin[1], origin[2] - 2 + 0.5)
            time.sleep(1.5)
            i0, b0 = inv(), {k: box(PAD[k]) for k in ("A", "B", "C", "P")}
            start = eve("build", {"project_id": name, "version": VER, "design_hash": d_hash, "origin": dict(zip("xyz", origin))})
            rep, t1 = start, time.time()
            while start.get("ok") and time.time() - t1 < 900:
                time.sleep(5)
                rep = eve("build", {"status": start["supply_id"]})
                if rep.get("status") not in ("running", None):
                    break
            i1, b1 = inv(), {k: box(PAD[k]) for k in ("A", "B", "C", "P")}
            res = judge_cells(origin, cells)
            counts = {}
            for c in res:
                counts[c["class"]] = counts.get(c["class"], 0) + 1
            left = [c for c in scaffold_cells(name, d, origin) if not test(c, "minecraft:air")]
            if expect == "done":
                checks = [(rep.get("status") == "done", f"supply run {rep.get('status')}: {rep.get('report')}"),
                          (counts.get("match", 0) == len(cells), f"cells {counts}"),
                          (not left, f"scaffold removed ({len(left)} left)")]
            else:
                placed = [c for c in res if c["want"] != "minecraft:air" and c["class"] != "missing"]
                checks = [(rep.get("status") == "short" and "minecraft:oak_slab" in json.dumps(rep.get("short")),
                           f"supply run {rep.get('status')}: {rep.get('report')}"),
                          (not placed, f"nothing placed ({len(placed)} cells hold blocks)"),
                          (b1["P"] == b0["P"], f"private chest untouched {b1['P']}")]
            valid, vwhy = loop_ran(rep)
            ok = valid and all(c for c, _ in checks)
            row = {"case": cname, "ok": ok, "valid": valid, "validity": vwhy, "checks": checks, "supply": rep, "inv_before": i0, "inv_after": i1,
                   "boxes_before": b0, "boxes_after": b1}
            rows.append(row)
            with (out / "rows.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            print(f"{'PASS' if ok else 'FAIL'} {cname}: " + "; ".join(dd for _, dd in checks), flush=True)
    finally:
        own_lock("before restoring Eve")
        h1, inv_note = stash_restore()  # O59: first, before anything else can stop the finally
        notes.append(inv_note)
        for k in ("A", "B", "C", "P"):
            fill_box(PAD[k], [])
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LANE / "ops" / "park-eve.ps1"),
                        "-Root", str(SERVER), "-Player", P], capture_output=True)
        r(f"forceload remove {plot[0]} {plot[2]} {plot[0] + 12 + sx} {plot[2] + 16}")
        if QUAL:  # give the sky back: build sites, floors and the row (chests emptied above), with her parked first (O35)
            try:
                notes.append(park_safe((plot[0], plot[1], plot[2], plot[0] + 8 + sx, plot[1] + 2 + sy, 17), "the final cleanup"))
                r(f"fill {plot[0]} {plot[1]} {plot[2]} {plot[0] + 8 + sx} {plot[1] + 2 + sy} {plot[2] + 5 + sz} minecraft:air")
                r("fill -26 190 9 -14 193 17 minecraft:air")
            except SystemExit as e:
                notes.append(f"SITE NOT CLEARED, tell foundation: {e}")
        if difficulty0 and difficulty0 != "peaceful":
            r(f"difficulty {difficulty0}")
        lane_lock("release")
    passed = sum(x["ok"] for x in rows) if h1 == h0 else 0  # O59: an unrestored inventory fails the harness
    invalid = [x["case"] for x in rows if not x.get("valid", True)]
    rows_valid = [x for x in rows if x.get("valid", True)]
    reg = st.get("registry") or {}
    where = "the 45L (`45L`, foundation's qualification lane, Eve's real account)" if QUAL else f"lane {LANE_ID} (`shark`)"
    lines = [f"# CP-51 — eve-builder + mind supply loop, {'45L' if QUAL else 'lane ' + LANE_ID + ' (`shark`)'} {stamp} attempt {num}: {passed}/{len(rows_valid)} valid" + (f" ({len(invalid)} INVALID (O46): {invalid})" if invalid else "") + ("" if h1 == h0 else f" — O59 FAIL: {inv_note}"), "",
             "Hypothesis: the mind's `eve build` gathers exactly what build_schematic reports missing (withdraw from shared "
             "chests, craft, make room) and the design then completes block-for-block; a material only in a private chest "
             "ends `short`, naming it, with nothing placed.",
             f"Fixture: {where}, design `{name}` (hash {d_hash[:12]}), origin ({plot[0] + 3}+case, {plot[1] + 1}, {plot[2] + 3}); "
             f"container row {PAD}: chests A/B/C (shared), P (private), table T; "
             + ("owner approval by foundation (labelled fixture step)." if QUAL else "fixture self-approval (lane, dev world)."),
             "Judge: RCON `execute if block` per cell and scaffold cell, chests and inventory by `data get` before/after.",
             f"Versions: repo {git_head()} (client: {jar_sha()}), registry {reg.get('version')} {str(reg.get('hash'))[:12]}. "
             f"Elapsed {time.time() - t0:.0f} s. " + ("Qualification lane." if QUAL else "Development evidence, not qualification."), "",
             *[f"- {n}" for n in notes], "",
             "| case | ok | detail |", "|---|---|---|"]
    lines += [f"| {x['case']} | {'PASS' if x['ok'] else 'FAIL'} | " + "; ".join(dd for _, dd in x["checks"]) + " |" for x in rows]
    text = "\n".join(lines) + "\n"
    (out / "RESULT.md").write_text(text, encoding="utf-8")
    summary_path(f"CP-51-{stamp}-{TAG}-builder-attempt-{num}.md").write_text(text, encoding="utf-8")
    print(text)


# ---- CP-52: the mind's repair loop (`eve repair`) around repair_zone, on sense's fixture (parts/server-helper/tools/cp52_fixture.py) ----
CP52_CHEST_A = ((-6, 161, -74), {"minecraft:bread": 5, "minecraft:iron_ingot": 3})  # intact in the crater: never touched
CP52_BOX = (-10, 160, -76, -2, 166, -68)  # the fixture's site (O35: Eve parked before sense's reset rebuilds it)
SEED = {"minecraft:wheat": "minecraft:wheat_seeds", "minecraft:carrots": "minecraft:carrot", "minecraft:potatoes": "minecraft:potato",
        "minecraft:beetroots": "minecraft:beetroot_seeds"}


def cp52_items(steps, skipped):
    """What Eve is given: the placements' items (farmland = dirt, minus the trampled dirt kept in place), seeds, a hoe."""
    out, removed_dirt = {}, {tuple(s["pos"].values()) for s in steps if s["op"] == "remove" and s["block"].startswith("minecraft:dirt")}
    for s in steps:
        if s["op"] != "place":
            continue
        bid = parse(s["block"])[0]
        if bid == "minecraft:farmland":
            if tuple(s["pos"].values()) in removed_dirt:
                continue  # the trampled dirt is tilled where it is
            bid = "minecraft:dirt"
        out[bid] = out.get(bid, 0) + 1
    for k in skipped:
        if k.get("reason") == "crop_cycle" and parse(k.get("block", ""))[0] in SEED and parse(k.get("actual", "minecraft:air"))[0] == "minecraft:air":
            sd = SEED[parse(k["block"])[0]]
            out[sd] = out.get(sd, 0) + 1
    out["minecraft:iron_hoe"] = 1
    return out


CP52_CHEST_B = (-5, 161, -72)  # destroyed by the crater: sense lists it under manual, never a step (fixture docstring)
CP52_LANTERN = (-7, 162, -71)  # the owner's edit after the snapshot: skipped owner_edit, kept


def parse_evetest_reply(text):
    """The last `[evetest] query -> {...}` reply in a slice of the server log, or None."""
    import re
    m = None
    for m in re.finditer(r"\[evetest\] query -> (\{.*\})\s*$", text, re.M):
        pass
    return json.loads(m.group(1)) if m else None


def sense_repair_query(zone_id, snapshot_id, bx, timeout=20):
    """sense's zone.repair for a box, asked server-side (devtools `evetest query repair`, the reply in the server log): the
    diff of EVERY cell in the box against the snapshot, read by the server. None when no reply came."""
    log = SERVER / "logs" / "latest.log"
    start = log.stat().st_size if log.exists() else 0
    r("evetest query repair " + json.dumps({"zone_id": zone_id, "snapshot_id": snapshot_id, "box": bx}, separators=(",", ":")))
    t = time.time()
    while time.time() - t < timeout:
        time.sleep(0.5)
        try:
            with open(log, "rb") as f:
                f.seek(start)
                got = parse_evetest_reply(f.read().decode("utf-8", "replace"))
        except OSError:
            got = None
        if got is not None:
            return got
    return None


def whole_box_check(q, box6):
    """REQUAL bar (CP-52): after the repair sense finds nothing left inside the box: no step, the destroyed chest B as the only
    manual cell, the owner's lantern still skipped as owner_edit. Returns (ok, note)."""
    if q is None:
        return False, "whole box: no sense reply (devtools `evetest query repair` missing?): UNJUDGED, not a pass"
    inb = lambda pos: all(box6[i] <= pos[k] <= box6[i + 3] for i, k in enumerate("xyz"))  # noqa: E731
    steps = [x for x in q.get("steps", []) if inb(x["pos"])]
    manual = sorted(tuple(x["pos"][k] for k in "xyz") for x in q.get("manual", []) if inb(x["pos"]))
    owner = [x for x in q.get("skipped", []) if x.get("reason") == "owner_edit" and tuple(x["pos"][k] for k in "xyz") == CP52_LANTERN]
    ok = not steps and manual == [CP52_CHEST_B] and bool(owner)
    return ok, (f"whole box (sense zone.repair after the repair, every cell server-read): {len(steps)} step(s) left, manual {manual} "
                f"(want [{CP52_CHEST_B}]), owner's lantern {'still skipped as owner_edit' if owner else 'NOT reported'}")


def cp52(lane_tag):
    if QUAL and "--deploy" in sys.argv:
        sys.exit("on the 45L: no --deploy (foundation stages jars, O40)")
    st = eve("status")
    if not st.get("body") or st.get("open_action"):
        sys.exit(f"lane {LANE_ID} not ready: body {bool(st.get('body'))}, open action {st.get('open_action')}")
    lane_lock("acquire", 40)
    if "--deploy" in sys.argv:
        deploy()
    root = REPO / "evidence" / "CP-52"
    root.mkdir(parents=True, exist_ok=True)
    num = int(sys.argv[sys.argv.index("--attempt") + 1]) if "--attempt" in sys.argv else 1 + sum(1 for x in root.iterdir() if x.is_dir() and f"-{TAG}-builder-attempt-" in x.name)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    out = root / f"{stamp}-{TAG}-builder-attempt-{num}"
    out.mkdir()
    h0 = start_run()  # O59: her inventory into the stash, the run starts from the kit alone
    pre, t0, notes, checks, rep = inv(), time.time(), [], [], {}
    give_ids = set()
    try:
        own_lock("before the fixture reset")
        notes.append(park_safe(CP52_BOX, "sense's fixture reset"))
        before = {d.name for d in (LANE / "logs").glob("cp52-fixture-*")}
        t_fx = time.time() - 1
        if QUAL:  # sense's 45L steps: cp52_fixture.py + lane_tp.py + safe_tp.py copied into C:\eve\ops by foundation
            fx_cmd = [sys.executable, str(LANE / "ops" / "cp52_fixture.py"), "--lane", "45L"]
        else:
            fx_cmd = [str(LANE / "src" / "parts" / "mind" / ".venv" / "Scripts" / "python.exe"),
                      str(LANE / "src" / "parts" / "server-helper" / "tools" / "cp52_fixture.py")]
        fx = subprocess.run(fx_cmd + ["--root", str(SERVER), "--ops", str(LANE / "ops"), "--out", str(LANE / "logs"), "--reset"],
                            capture_output=True, text=True, timeout=600)
        # sense names the folder by the minute: a reset in the same minute as the last one reuses it (series attempt 26)
        res_t = lambda d: (d / "zone.repair.result.json").stat().st_mtime if (d / "zone.repair.result.json").exists() else 0
        new = sorted((d for d in (LANE / "logs").glob("cp52-fixture-*") if res_t(d) >= t_fx), key=res_t)
        if fx.returncode != 0 or not new:
            raise SystemExit(f"sense's --reset failed (exit {fx.returncode}): {(fx.stdout + fx.stderr)[-600:]}")
        ans = json.loads((new[-1] / "zone.repair.result.json").read_text(encoding="utf-8"))
        notes.append(f"sense --reset ({new[-1].name}): {len(ans['steps'])} steps, {len(ans['manual'])} manual, {len(ans['skipped'])} skipped")
        items = cp52_items(ans["steps"], ans["skipped"])
        give_ids = set(items)
        give(sorted(items.items()))
        safe_tp(-1.5, 161, -68.5, 180, 20)  # on the fixture's stone floor, outside the farm and the crater
        time.sleep(1.5)
        a0 = box(CP52_CHEST_A[0])
        # only the fixture's box: home@v1 covers the whole zone, and a whole-zone repair would undo other parts' later work
        # (eve-skills' base on lane B). sense 0.1.14 takes the box in zone.repair; the mind passes it on.
        bx = {"min": dict(zip("xyz", CP52_BOX[:3])), "max": dict(zip("xyz", CP52_BOX[3:]))}
        outside = [s["pos"] for s in ans["steps"] if not all(CP52_BOX[i] <= s["pos"][k] <= CP52_BOX[i + 3] for i, k in enumerate("xyz"))]
        if outside:
            raise SystemExit(f"sense's answer has steps outside the fixture box: {outside[:5]}; not repairing")
        import threading
        stop_f, feet = threading.Event(), []  # REQUAL: HP must not drop during the repair (the crater is deep, so feet y is no guide)
        th_f = threading.Thread(target=sample_feet, args=(stop_f, feet), daemon=True)
        th_f.start()
        start = eve("repair", {"zone_id": ans["zone_id"], "snapshot_id": ans["snapshot_id"], "box": bx})
        rid = start.get("repair_id") or start.get("id") or start.get("supply_id")
        rep, t1 = start, time.time()
        while start.get("ok") and rid and time.time() - t1 < 1800:
            time.sleep(5)
            rep = eve("repair", {"status": rid})
            if rep.get("status") not in ("running", None):
                break
        stop_f.set()
        th_f.join(2)
        hp0 = next((f[2] for f in feet if f[2] is not None), None)
        min_hp = min((f[2] for f in feet if f[2] is not None), default=None)
        # judge: every step cell at its target (the last op for a cell wins), owner edits kept, chest A intact, crops replanted
        target = {}
        for s in ans["steps"]:
            target[tuple(s["pos"].values())] = s["block"] if s["op"] == "place" else "minecraft:air"
        bad = [(p, w, classify(p, w)) for p, w in target.items() if classify(p, w) not in ("match", "derived_mismatch")]
        checks.append((rep.get("status") == "done", f"repair run {rep.get('status')}: {str(rep.get('report') or rep)[:300]}"))
        checks.append((not bad, f"step cells restored {len(target) - len(bad)}/{len(target)}" + (f"; wrong {bad[:6]}" if bad else "")))
        own = [k for k in ans["skipped"] if k.get("reason") == "owner_edit"]
        kept = [k for k in own if k.get("actual") and test(tuple(k["pos"].values()), parse(k["actual"])[0])]
        checks.append((own and len(kept) == len(own), f"owner edits kept {len(kept)}/{len(own)} ({[k.get('actual') for k in own]})"))
        a1 = box(CP52_CHEST_A[0])
        checks.append((a1 == CP52_CHEST_A[1] == a0, f"chest A untouched {a1}"))
        crops = [k for k in ans["skipped"] if k.get("reason") == "crop_cycle" and parse(k.get("actual", "minecraft:air"))[0] == "minecraft:air"]
        grown = [k for k in crops if test(tuple(k["pos"].values()), parse(k["block"])[0])]
        checks.append((len(grown) == len(crops), f"crops replanted {len(grown)}/{len(crops)}"))
        checks.append(whole_box_check(sense_repair_query(ans["zone_id"], ans["snapshot_id"], bx), CP52_BOX))
        checks.append((hp0 is None or min_hp is None or min_hp >= hp0,
                       f"HP {hp0} -> min {min_hp} during the repair, {len(feet)} samples, rcon_sample_gap_s {max_gap(feet)}"))
    finally:
        own_lock("before restoring Eve")
        h1, inv_note = stash_restore()  # O59: first, before anything else can stop the finally
        notes.append(inv_note)
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LANE / "ops" / "park-eve.ps1"),
                        "-Root", str(SERVER), "-Player", P], capture_output=True)
        lane_lock("release")
    valid, vwhy = loop_ran(rep)
    capped = cap_denied("repair_zone", t0)
    if valid and capped:  # O46: an environment limit (the lane's hourly cap) tested nothing
        valid, vwhy = False, f"server cap: {capped}"
    checks.append((h1 == h0, inv_note))  # O59 pass condition
    passed = valid and all(c for c, _ in checks) and bool(checks)
    verdict = "PASS" if passed else ("FAIL" if valid else "INVALID (O46: " + vwhy + ")")
    (out / "result.json").write_text(json.dumps({"repair": rep, "checks": checks, "notes": notes, "valid": valid, "validity": vwhy},
                                                indent=1, default=str), encoding="utf-8")
    reg = st.get("registry") or {}
    lines = [f"# CP-52 — eve-builder repair_zone + mind repair loop, {LABEL} {stamp} attempt {num}: {verdict}", "",
             "Hypothesis: the mind's `eve repair` (zone.repair -> grant -> repair_zone -> re-ask) restores sense's cratered fixture to "
             "snapshot home@v1 cell for cell, keeps the owner's later edit, leaves the intact chest alone, and replants the crops.",
             f"Fixture: sense's cp52_fixture.py --reset on lane {LANE_ID}; judge: RCON per step cell, owner-edit cells, chest A contents, crop cells.",
             f"Versions: repo {git_head()} (client: {jar_sha()}), registry {reg.get('version')} {str(reg.get('hash'))[:12]}. "
             f"Elapsed {time.time() - t0:.0f} s. {EVID}", "",
             *[f"- {n}" for n in notes], "", "| check | ok |", "|---|---|",
             *[f"| {d_} | {'PASS' if ok else 'FAIL'} |" for ok, d_ in checks]]
    text = "\n".join(lines) + "\n"
    (out / "RESULT.md").write_text(text, encoding="utf-8")
    summary_path(f"CP-52-{stamp}-{TAG}-builder-attempt-{num}.md").write_text(text, encoding="utf-8")
    print(text)


# ---- CP-53: bridge over a disposable End-style void (eve-watch's fixture on lane C) ----
def copy_candidates(out, since, mind=True):
    """O94f: the builder's eve-candidates/1 rows written since `since` (epoch s) into the run folder, for eve-learning, then
    the lane mind's rows for the same window appended by eve-watch's candidates_export.py (b4ae3597; it dedups and always
    exits 0). Returns a note. On the 45L the rows stay on Eve's client (foundation copies them)."""
    from datetime import datetime
    if QUAL:
        return "candidates: 45L rows stay on the client (eve-builder/candidates.jsonl in its game dir)"
    if not CANDIDATES.exists():
        return f"candidates: none ({CANDIDATES} absent: a jar before O94f)"
    rows = []
    for line in CANDIDATES.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            if datetime.fromisoformat(json.loads(line)["ts"]).timestamp() >= since:
                rows.append(line)
        except (ValueError, KeyError):
            continue
    (out / "candidates.jsonl").write_text("".join(r + "\n" for r in rows), encoding="utf-8")
    note = f"candidates: {len(rows)} stand-choice row(s) -> candidates.jsonl"
    if mind:
        try:
            exp = subprocess.run([sys.executable, str(REPO / "parts" / "watch" / "laneb" / "candidates_export.py"), "--lane", LANE_ID, "--run", str(out),
                                  "--since", datetime.fromtimestamp(since).astimezone().isoformat(timespec="seconds"),
                                  "--until", datetime.now().astimezone().isoformat(timespec="seconds")], capture_output=True, text=True, timeout=60)
            note += "; lane mind's rows: " + ((exp.stdout or exp.stderr).strip().splitlines() or ["(no output)"])[-1][:160]
        except Exception as e:
            note += f"; lane mind's rows not exported: {e}"
    return note


def max_gap(samples):
    """I29f: the largest gap between consecutive state samples (s); the sampler asks every 0.25 s, so a long gap is a stall."""
    return round(max((b[0] - a[0] for a, b in zip(samples, samples[1:])), default=0.0), 2)


def sample_feet(stop, out):
    """Eve's server feet y every 0.25 s (the fall judge: a fall shows as feet below the bridge's floor top)."""
    while not stop.is_set():
        pos, hp = eve_state()
        if pos:
            out.append((time.time(), pos[1], hp))
        time.sleep(0.25)


def cp53():
    """`--start X,Y,Z` = the floor block she starts on, `--to X,Y,Z` = the far floor block (level, straight x or z line).
    Lane C's bridge_plot (eve-watch, zone x 40..100 y 120..230 z -110..-70; a fall is 80 blocks and fatal):
      12-block gap: --start 49,200,-95 --to 62,200,-95      24-block gap: --start 49,200,-85 --to 74,200,-85
      30-block gap (CP-53 length): --start 49,200,-101 --to 80,200,-101
    The End fixture (foundation, zone end_bridge on lane C): --dim=minecraft:the_end
      30-block: --start 409,64,0 --to 440,64,0      12-block: --start 409,64,5 --to 422,64,5   (floor blocks; feet at y 65)
      re-lay: C:/eve-shark-c/ops/end-bridge.ps1 under the lock
    --variants normal,start_back,start_yaw,knockback,disconnect,stale_link  (each repeated --n times, refusals at the end)
    Reset the islands/kit with eve-watch's bridge-plot.ps1 -Lane C."""
    import threading
    kinds_ = sys.argv[sys.argv.index("--variants") + 1].split(",") if "--variants" in sys.argv else ["normal"]
    for k_, tool in (("disconnect", "restart-client.ps1"), ("stale_link", "link-pause.ps1")):
        if k_ in kinds_ and not (LANE / "ops" / tool).exists():
            sys.exit(f"variant {k_} needs {LANE / 'ops' / tool}, which is not there; nothing run")
    sxyz = tuple(int(v) for v in sys.argv[sys.argv.index("--start") + 1].split(","))
    txyz = tuple(int(v) for v in sys.argv[sys.argv.index("--to") + 1].split(","))
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 3
    gap = [(sxyz[0] + k * (txyz[0] > sxyz[0]) - k * (txyz[0] < sxyz[0]), sxyz[1], sxyz[2] + k * (txyz[2] > sxyz[2]) - k * (txyz[2] < sxyz[2]))
           for k in range(1, abs(txyz[0] - sxyz[0]) + abs(txyz[2] - sxyz[2]))]
    lo = tuple(min(sxyz[i], txyz[i]) for i in range(3))
    hi = tuple(max(sxyz[i], txyz[i]) for i in range(3))
    fx_box = (lo[0] - 1, lo[1] - 1, lo[2] - 1, hi[0] + 1, hi[1] + 3, hi[2] + 1)
    st = eve("status")
    if not st.get("body") or st.get("open_action"):
        sys.exit(f"lane {LANE_ID} not ready")
    lane_lock("acquire", 10 + 4 * (n + 2))
    root = REPO / "evidence" / "CP-53"
    root.mkdir(parents=True, exist_ok=True)
    num = int(sys.argv[sys.argv.index("--attempt") + 1]) if "--attempt" in sys.argv else 1 + sum(1 for x in root.iterdir() if x.is_dir() and f"-{TAG}-builder-attempt-" in x.name)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    out = root / f"{stamp}-{TAG}-builder-attempt-{num}"
    out.mkdir()
    h0 = start_run()  # O59: her inventory into the stash, the run starts from the kit alone
    pre, rows, notes, t0 = inv(), [], [], time.time()
    ids = {"minecraft:cobblestone", "minecraft:ender_pearl", "minecraft:water_bucket"}
    # --variants: normal (default), start_back (3 blocks back on the start island), start_yaw (facing away), knockback (a
    # snowball into her side mid-gap: a peaceful stand-in for a mob's hit), disconnect (client restart mid-gap, then bridge
    # again from where she stands). stale_link needs a link-pause tool (asked); refusals always run at the end.
    kinds = sys.argv[sys.argv.index("--variants") + 1].split(",") if "--variants" in sys.argv else ["normal"]
    cases = [(k, 64, True, "ok", k) for _ in range(n) for k in kinds] + [
        ("short_supplies", max(1, len(gap) // 2), True, "no_item", "normal"), ("no_recovery", 64, False, "no_recovery", "normal")]
    try:
        own_lock("before fixture setup")
        r("difficulty peaceful")
        if DIM != "minecraft:overworld" and OWN_FORCELOAD:  # parked in the overworld, nobody holds the fixture's chunks; removed in finally
            r(f"execute in {DIM} run forceload add {fx_box[0]} {fx_box[2]} {fx_box[3]} {fx_box[5]}")
        for idx, (cname, blocks, recover, expect, kind) in enumerate(cases):
            own_lock(f"before case {cname}")
            notes.append(park_safe(fx_box, f"clearing the bridge for {cname} #{idx + 1}"))
            for c in gap:  # the void between the two floors back to air
                r(f"execute in {DIM} run setblock {c[0]} {c[1]} {c[2]} minecraft:air")
            left = sum(1 for c in gap if not test(c, "minecraft:air"))
            if left:
                notes.append(f"INVALID setup before {cname} #{idx + 1}: {left} gap cells still solid after clearing; series stopped")
                break
            take_back(pre, ids)
            give([("minecraft:cobblestone", blocks)] + ([("minecraft:ender_pearl", 1)] if recover else []))
            back = 3 if kind == "start_back" else 0  # along the line, away from the gap (still on the start island)
            ux, uz = (txyz[0] > sxyz[0]) - (txyz[0] < sxyz[0]), (txyz[2] > sxyz[2]) - (txyz[2] < sxyz[2])
            st_x, st_z = sxyz[0] - ux * back, sxyz[2] - uz * back
            safe_tp(st_x + 0.5, sxyz[1] + 1, st_z + 0.5, 180 if kind == "start_yaw" else 0, 30)
            time.sleep(1.5)
            stop, feet = threading.Event(), []
            th = threading.Thread(target=sample_feet, args=(stop, feet), daemon=True)
            th.start()
            event = {}

            def mid_gap(action):  # once she is ~8 blocks out over the gap: knock her or cut her client
                t_end = time.time() + 120
                while time.time() < t_end and not stop.is_set():
                    pos, _ = eve_state()
                    if pos and ((pos[0] - (sxyz[0] + 0.5)) * ux + (pos[2] - (sxyz[2] + 0.5)) * uz) >= 8:
                        event["at"] = pos
                        if action == "knockback":  # from her left side, across the line
                            sx_, sz_ = pos[0] - uz * 2.5, pos[2] + ux * 2.5
                            r(f"execute in {DIM} run summon snowball {sx_} {pos[1] + 1.4} {sz_} {{Motion:[{uz * 1.2},0.0,{-ux * 1.2}]}}")
                            event["done"] = "snowball"
                        elif action == "stale_link":  # the lane's sanctioned link pause (foundation 4cd8464)
                            pp = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                                 str(LANE / "ops" / "link-pause.ps1"), "-Lane", LANE_ID, "-By", "eve-builder",
                                                 "-Seconds", "5"], capture_output=True, text=True, timeout=60)
                            event["done"] = f"link pause 5 s {'ok' if pp.returncode == 0 else 'FAILED ' + (pp.stdout + pp.stderr)[-200:]}"
                        else:
                            event["at"] = eve_state()[0] or pos  # where she logs out
                            ok_, o_ = restart_client()
                            event["done"] = f"client restart {'ok' if ok_ else 'FAILED ' + o_}"
                        return
                    time.sleep(0.2)

            ev_th = threading.Thread(target=mid_gap, args=(kind,), daemon=True) if kind in ("knockback", "disconnect", "stale_link") else None
            if ev_th:
                ev_th.start()
            res = eve("do", {"skill": "bridge", "params": {"to": dict(zip("xyz", txyz))}, "wait": 300}, 330)
            for _ in range(12):  # the mind's retry_limit (3 failures with these params in 10 min, e.g. the designed refusals): wait it out
                if res.get("code") != "retry_limit":
                    break
                notes.append(f"{cname} #{idx + 1}: mind refused retry_limit, waited 60 s and asked again (no action ran)")
                time.sleep(60)
                res = eve("do", {"skill": "bridge", "params": {"to": dict(zip("xyz", txyz))}, "wait": 300}, 330)
            if ev_th:
                ev_th.join(180)
            if kind == "disconnect" and event.get("done", "").startswith("client restart"):
                for _ in range(60):  # back and bound
                    if eve("status").get("body"):
                        break
                    time.sleep(2)
                import math  # restart-client.ps1 parks her; a real reconnect puts her back where she logged out (on her own bridge)
                ax, ay, az = event["at"]
                safe_tp(math.floor(ax) + 0.5, ay, math.floor(az) + 0.5,
                        {(1, 0): -90, (-1, 0): 90, (0, 1): 0, (0, -1): 180}[(ux, uz)], 30)
                time.sleep(2)
            if kind in ("disconnect", "knockback") and res.get("status") != "ok" and event.get("done"):
                pos, _ = eve_state()
                if pos and pos[1] >= sxyz[1] + 0.5:
                    first = res
                    res = eve("do", {"skill": "bridge", "params": {"to": dict(zip("xyz", txyz))}, "wait": 300}, 330)
                    res["first_run"] = {k: first.get(k) for k in ("status", "code", "note")}
            res["mid_gap_event"] = event
            time.sleep(1.0)
            stop.set()
            th.join(2)
            floor_top = sxyz[1] + 1
            min_y = min((f[1] for f in feet), default=None)
            min_hp = min((f[2] for f in feet if f[2] is not None), default=None)
            solid = sum(1 for c in gap if not test(c, "minecraft:air"))
            pos, hp = eve_state()
            got = (res.get("status"), res.get("code") or res.get("error"))
            falls = 0 if min_y is None or min_y >= floor_top - 0.5 else 1
            # no samples = falls and HP unmeasured: never a vacuous PASS (CP-53 a12 normal #2 had 0 samples and read "falls 0")
            unmeasured = expect == "ok" and not feet
            if expect == "ok":
                checks = [(got[0] == "ok", f"result {got}"), (solid == len(gap), f"gap cells solid {solid}/{len(gap)}"),
                          (pos is not None and abs(pos[0] - (txyz[0] + 0.5)) <= 1.2 and abs(pos[2] - (txyz[2] + 0.5)) <= 1.2, f"ends at {pos}"),
                          (falls == 0, f"falls {falls} (min feet y {min_y}, floor top {floor_top})")]
                hp_first = next((f[2] for f in feet if f[2] is not None), None)
                if kind != "knockback":  # REQUAL: no damage on a normal crossing (the knockback case's snowball may scratch)
                    checks.append((hp_first is None or min_hp is None or min_hp >= hp_first, f"HP {hp_first} -> min {min_hp}"))
            else:
                checks = [(got == ("failed", expect), f"result {got} (want failed/{expect})"), (solid == 0, f"nothing placed ({solid})"),
                          (falls == 0, f"falls {falls} (min feet y {min_y})"),
                          (pos is not None and abs(pos[0] - (sxyz[0] + 0.5)) <= 1.2 and abs(pos[2] - (sxyz[2] + 0.5)) <= 1.2, f"still on the start {pos}")]
            valid, vwhy = ran(res)  # O46: the skill's own refusals (no_item, no_recovery) ran; a mind refusal did not
            if valid and unmeasured:
                valid, vwhy = False, f"{vwhy}; INVALID: 0 state samples, falls and HP unmeasured"
            ok = valid and all(c for c, _ in checks)
            row = {"case": cname, "trial": idx + 1, "ok": ok, "valid": valid, "validity": vwhy, "checks": checks, "result": res,
                   "min_feet_y": min_y, "min_hp": min_hp,
                   "samples": len(feet), "rcon_sample_gap_s": max_gap(feet),  # I29f (watch, 09:40: a10/a11 lacked it)
                   "sample_ts": [round(f[0], 3) for f in feet]}  # the raw times, so the gap can always be recomputed
            rows.append(row)
            with (out / "rows.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
            print(f"{'PASS' if ok else ('FAIL' if valid else 'INVALID')} {cname} #{idx + 1}: " + vwhy + "; " + "; ".join(d_ for _, d_ in checks)
                  + f"; rcon_sample_gap_s {row['rcon_sample_gap_s']}" + (" (includes the client restart)" if kind == "disconnect" else ""), flush=True)
            if falls:  # fatal on bridge_plot: respawn her (the lane's own script) and stop the series
                ok_, o_ = restart_client(respawn=True)
                notes.append(f"FALL in {cname} #{idx + 1}: series stopped; restart-client -Respawn {'ok' if ok_ else 'FAILED ' + o_}")
                break
    finally:
        own_lock("before restoring Eve")
        h1, inv_note = stash_restore()  # O59: first, before anything else can stop the finally
        notes.append(inv_note)
        notes.append(park_safe(fx_box, "the final bridge clear"))
        for c in gap:
            r(f"execute in {DIM} run setblock {c[0]} {c[1]} {c[2]} minecraft:air")
        if DIM != "minecraft:overworld" and OWN_FORCELOAD:
            r(f"execute in {DIM} run forceload remove {fx_box[0]} {fx_box[2]} {fx_box[3]} {fx_box[5]}")
        lane_lock("release")
    passed = sum(x["ok"] for x in rows) if h1 == h0 else 0  # O59: an unrestored inventory fails the harness
    invalid = [f"{x['case']} #{x['trial']}" for x in rows if not x.get("valid", True)]
    n_valid = sum(1 for x in rows if x.get("valid", True))
    reg = st.get("registry") or {}
    lines = [f"# CP-53 — eve-builder bridge, {LABEL} {stamp} attempt {num}: {passed}/{n_valid} valid"
             + (f" ({len(invalid)} INVALID (O46): {invalid})" if invalid else "") + ("" if h1 == h0 else f" — O59 FAIL: {inv_note}"), "",
             "Hypothesis: bridge crosses the void gap at normal tick speed with zero falls (threshold, not a guarantee); short "
             "supplies refuse no_item and a deep gap without recovery gear refuses no_recovery, both with nothing placed.",
             f"Fixture: lane {LANE_ID} void gap from {sxyz} to {txyz} ({len(gap)} cells); judge: RCON gap cells, Eve's feet y every 0.25 s, "
             f"final position.",
             f"Versions: repo {git_head()} (client: {jar_sha()}), registry {reg.get('version')} {str(reg.get('hash'))[:12]}. "
             f"Elapsed {time.time() - t0:.0f} s. {EVID}", "", *[f"- {x}" for x in notes], "",
             "| case | trial | ok | detail |", "|---|---|---|---|"]
    lines += [f"| {x['case']} | {x['trial']} | {'PASS' if x['ok'] else ('FAIL' if x.get('valid', True) else 'INVALID')} | "
              + x.get("validity", "") + "; " + "; ".join(d_ for _, d_ in x["checks"])
              + f"; {x.get('samples')} samples, rcon_sample_gap_s {x.get('rcon_sample_gap_s')}"
              + (" (includes the client restart)" if x["case"] == "disconnect" else "") + " |" for x in rows]
    text = "\n".join(lines) + "\n"
    (out / "RESULT.md").write_text(text, encoding="utf-8")
    summary_path(f"CP-53-{stamp}-{TAG}-builder-attempt-{num}.md").write_text(text, encoding="utf-8")
    print(text)


def cp53tall():
    """CP-53 "separately test a tall scaffold": design's pillar_tall (design 0a6efa0, build order 279f2e8 = HEAD, 8f74b331: 3x3 stone_bricks column 12 high at its own
    origin 405,65,-1 on the End fixture's island A, 80 dirt of turning staircase scaffold in x 404..409 z -2..3 y 65..74)
    built with build_schematic at normal tick, feet and HP sampled every 0.25 s. Pass: every cell matches, no scaffold left,
    no fall (feet never below the floor top, no HP loss). Clears only the design + scaffold box (never the island floor).
      laneb_build.py cp53tall --lane C --dim=minecraft:the_end [--design pillar_tall]"""
    import threading
    name = sys.argv[sys.argv.index("--design") + 1] if "--design" in sys.argv else "pillar_tall"
    d, d_hash, cells, items, steps = load(name)
    assert d_hash == d["hash"], "design.json hash does not re-verify"
    origin = tuple(d["origin"][k] for k in "xyz")
    sc = scaffold_cells(name, d, origin)
    pts = [(origin[0] + x, origin[1] + y, origin[2] + z) for (x, y, z) in cells] + sc
    bx = tuple(min(p[i] for p in pts) for i in range(3)) + tuple(max(p[i] for p in pts) for i in range(3))
    floor_top = origin[1]
    st = eve("status")
    if "--dry-run" in sys.argv:  # read-only plan, as for build/resume
        import re
        sha = lambda f: hashlib.sha256(f.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:12] if f.exists() else "missing"
        files = ("design.json", "build_order.json", "scaffold.json", "bom.json", "gate.json")
        gate = json.loads((ddir(name) / "gate.json").read_text(encoding="utf-8")) if (ddir(name) / "gate.json").exists() else None
        pk = LANE / "ops" / "park-eve.ps1"
        fills = re.findall(r"fill (-?\d+ -?\d+ -?\d+ -?\d+ -?\d+ -?\d+)", pk.read_text(encoding="utf-8", errors="replace")) if pk.exists() else []
        print("\n".join([
            f"DRY RUN cp53tall --lane {LANE_ID} --design {name} {DIM}  (nothing changed)",
            f"  design {ddir(name)}  hash {d_hash[:12]}  gate {gate and gate.get('category')} -> builder accepts"
            if gate is None or gate.get("category") in ("owner_supplied", "schematic") else f"  gate {gate} -> REFUSED",
            "  files (sha256[:12], LF):  " + ", ".join(f"{f} {sha(ddir(name) / f)}" for f in files),
            "  staged for the client:   " + ", ".join(f"{f} {sha(STAGED / name / 'v1' / f)}" for f in files),
            f"  approval: {'owner approval read from the mind DB (check_approval)' if QUAL else 'fixture self-approval at run start'}",
            f"  origin {origin}  cells {len(cells)}  scaffold {len(sc)}  cleared air box {bx}  feet sampled every 0.25 s (falls: below y {floor_top - 0.5})",
            f"  End forceload: {'the harness adds/removes its own' if OWN_FORCELOAD else 'NONE by the harness (fixture owner holds it, O50)'}",
            f"  client log (walk-failure capture): {client_log()} ({'exists' if client_log().exists() else 'MISSING'})",
            f"  kit given: {dict(sorted(items.items()))}",
            f"  placements ~{sum(items.values())}, breaks ~{len(sc)} (scaffold)",
            f"  jar {jar_sha()}",
            f"  mind {('up, body ' + str(bool(st.get('body'))) + ', open action ' + str(st.get('open_action'))) if st.get('ok') else st}",
            f"  O59: inventory hash now {dry_inv_hash()}; stash west of the park spot (x -21, y 99..104), outside park-eve fills {fills} and the fixture map"]))
        return
    if not st.get("body") or st.get("open_action"):
        sys.exit(f"lane {LANE_ID} not ready")
    lane_lock("acquire", 40)
    if "--deploy" in sys.argv:
        deploy()
    appr = check_approval(name, d_hash) if QUAL else stage_and_approve(name)  # the 45L: the owner's approval, read-only
    if not appr.get("ok"):
        lane_lock("release")
        sys.exit(f"fixture self-approval refused, nothing run: {appr}")
    root = REPO / "evidence" / "CP-53"
    num = int(sys.argv[sys.argv.index("--attempt") + 1]) if "--attempt" in sys.argv else 1 + sum(1 for x in root.iterdir() if x.is_dir() and f"-{TAG}-builder-tall-attempt-" in x.name)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    out = root / f"{stamp}-{TAG}-builder-tall-attempt-{num}"
    out.mkdir(parents=True)
    write_config()
    h0 = start_run()  # O59: her inventory into the stash, the run starts from the kit alone
    pre, notes, t0, feet = inv(), [f"{'owner approval (45L, read-only)' if QUAL else 'fixture self-approval (lane, dev world)'}: approval {appr.get('approval_id')} hash {str(appr.get('design_hash'))[:12]}"], time.time(), []
    clear = f"execute in {DIM} run fill {bx[0]} {bx[1]} {bx[2]} {bx[3]} {bx[4]} {bx[5]} minecraft:air"
    clog = client_log()
    log0 = clog.stat().st_size if clog.exists() else 0
    try:
        own_lock("before fixture setup")
        r("difficulty peaceful")
        if OWN_FORCELOAD:
            r(f"execute in {DIM} run forceload add {bx[0] - 8} {bx[2] - 8} {bx[3] + 8} {bx[5] + 8}")
        notes.append(park_safe(bx, "the site clear"))
        r(clear)
        give(sorted(items.items()))
        i0 = inv()
        safe_tp(402.5, floor_top, 3.5, 0, 30)  # island A, south of the kit chest, outside the build box
        time.sleep(1.5)
        if FORCE_WALK:  # O88: block one planned stand (inside the cleared box, so the final clear removes it) -> a walk fails
            bo = json.loads((ddir(name) / "build_order.json").read_text(encoding="utf-8"))
            si = bo["columns"].index("stand")
            for row in bo["rows"]:
                if row[0] == "scaffold" and row[si]:
                    w = tuple(origin[i] + row[si][i] for i in range(3))
                    if all(bx[i] <= w[i] <= bx[i + 3] for i in range(3)):
                        r(f"execute in {DIM} run setblock {w[0]} {w[1]} {w[2]} minecraft:barrier")
                        notes.append(f"O88 forced walk failure: barrier at planned stand {w} (scaffold row {row[1:4]})")
                        break
        stop = threading.Event()
        th = threading.Thread(target=sample_feet, args=(stop, feet), daemon=True)
        th.start()
        own_lock("before dispatch")
        res = build(name, d_hash, origin, WAIT)
        time.sleep(1.0)
        stop.set()
        th.join(2)
        i1 = inv()
        results = judge_cells(origin, cells)
        left = [c for c in sc if not test(c, "minecraft:air")]
    finally:
        # before anything is cleared: the client's own record of every unreached walk goal (her position, the real blocks
        # around the goal) and Baritone's pathing output, for design's replay (orchestrator 2026-10-20 23:3x)
        try:
            with open(clog, "rb") as fh:  # only what this run appended (times of day wrap at midnight)
                fh.seek(log0)
                log = fh.read().decode("utf-8", errors="replace").splitlines()
            keep = [l for l in log if any(k in l for k in ("unreached", "ended unreachable", "[STDOUT]", "baritone"))]
            (out / "walk_failures.txt").write_text("\n".join(keep) + "\n", encoding="utf-8")
            notes.append(f"walk failures saved before the clear: {sum('unreached from' in l for l in keep)} unreached goal(s) -> walk_failures.txt")
        except Exception as e:
            notes.append(f"walk-failure capture failed: {e}")
        own_lock("before restoring Eve")
        h1, inv_note = stash_restore()  # O59: first, before anything else can stop the finally
        notes.append(inv_note)
        notes.append(park_safe(bx, "the final site clear"))
        r(clear)
        if OWN_FORCELOAD:
            r(f"execute in {DIM} run forceload remove {bx[0] - 8} {bx[2] - 8} {bx[3] + 8} {bx[5] + 8}")
        lane_lock("release")
    hp0 = feet[0][2] if feet else None
    min_y = min((f[1] for f in feet), default=None)
    min_hp = min((f[2] for f in feet if f[2] is not None), default=None)
    max_y = max((f[1] for f in feet), default=None)
    counts = {}
    for c in results:
        counts[c["class"]] = counts.get(c["class"], 0) + 1
    valid, vwhy = ran(res)
    checks = [(res.get("status") == "ok", f"result {res.get('status')} {res.get('code') or ''}".strip()),
              (all(c["class"] in ("match", "derived_mismatch") for c in results), f"cells {counts}"),
              (not left, f"scaffold removed ({len(left)} of {len(sc)} left)"),
              (min_y is not None and min_y >= floor_top - 0.5 and (hp0 is None or min_hp is None or min_hp >= hp0),
               f"falls: min feet y {min_y} (floor top {floor_top}), highest feet y {max_y}, HP {hp0} -> min {min_hp}, {len(feet)} samples, "
               f"rcon_sample_gap_s {max_gap(feet)}")]
    checks.append((h1 == h0, inv_note))  # O59 pass condition
    notes.append(copy_candidates(out, t0))
    ok = valid and all(c for c, _ in checks)
    (out / "result.json").write_text(json.dumps({"valid": valid, "validity": vwhy, "run": res, "cells": results, "scaffold_left": left,
                                                 "feet": feet, "inv_before": i0, "inv_after": i1, "notes": notes}, indent=1, default=str), encoding="utf-8")
    reg = st.get("registry") or {}
    lines = [f"# CP-53 tall scaffold — eve-builder {LABEL} {stamp} attempt {num}: {'PASS' if ok else ('FAIL' if valid else 'INVALID (O46: ' + vwhy + ')')}"
             + (" — O88 FORCED WALK-FAILURE CAPTURE PROOF, not a CP-53 trial" if FORCE_WALK else ""), "",
             f"Hypothesis: build_schematic builds `{name}` (hash {d_hash[:12]}, {len(cells)} cells, {len(sc)} scaffold) at the island edge in {DIM} "
             "at normal tick with zero falls and removes all its scaffold (threshold, not a guarantee).",
             f"Versions: repo {git_head()} (client: {jar_sha()}), registry {reg.get('version')} {str(reg.get('hash'))[:12]}. Elapsed {time.time() - t0:.0f} s. " + EVID, "", *[f"- {x}" for x in notes], "",
             *[f"- {'ok' if c else 'FAIL'}: {t}" for c, t in checks], f"- O46: {vwhy}", f"- evidence: {json.dumps(res.get('evidence'), default=str)[:800]}"]
    text = "\n".join(lines) + "\n"
    (out / "RESULT.md").write_text(text, encoding="utf-8")
    sp = summary_path(f"CP-53-{stamp}-{TAG}-builder-tall-attempt-{num}.md")
    sp.write_text(text, encoding="utf-8")
    print(text)


def dry_run(name, d, d_hash, items, plot, origin, top, mode):
    """Prints what a build/resume run would do, changes nothing (no lock, stash, fill, approval or dispatch)."""
    import re
    sx, sy, sz = (d["size"][k] for k in "xyz")
    sha = lambda f: hashlib.sha256(f.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:12] if f.exists() else "missing"
    gate = json.loads((ddir(name) / "gate.json").read_text(encoding="utf-8")) if (ddir(name) / "gate.json").exists() else None
    files = ("design.json", "build_order.json", "scaffold.json", "bom.json", "gate.json")
    sc = scaffold_cells(name, d, origin)
    st = eve("status")
    pk = LANE / "ops" / "park-eve.ps1"
    boxes = re.findall(r"fill (-?\d+ -?\d+ -?\d+ -?\d+ -?\d+ -?\d+)", pk.read_text(encoding="utf-8", errors="replace")) if pk.exists() else []
    gate_ok = gate is None or gate.get("category") in ("owner_supplied", "schematic") or gate.get("decision") == "build"
    out = [f"DRY RUN {mode} --lane {LANE_ID} --design {name}  (nothing changed)",
           f"  design {ddir(name)}  hash {d_hash[:12]}  size {sx}x{sy}x{sz}  gate {gate and gate.get('category')}/{gate and gate.get('decision')}"
           f" -> builder {'accepts' if gate_ok else 'REFUSES (gate_refused)'}",
           "  files (sha256[:12], LF):  " + ", ".join(f"{f} {sha(ddir(name) / f)}" for f in files),
           "  staged for the client:   " + ", ".join(f"{f} {sha(STAGED / name / 'v1' / f)}" for f in files),
           f"  approval: {'owner approval read from the mind DB (check_approval)' if QUAL else 'fixture self-approval (stage_and_approve) at run start'}",
           f"  plot {plot}  origin {origin}  floor y {plot[1]} (stone)  cleared air box x {plot[0]}..{plot[0] + 2 * MX - 1 + sx} y {plot[1] + 1}..{top} "
           f"z {plot[2]}..{plot[2] + 2 * MZ - 1 + sz}  (margin x {MX}, z {MZ})",
           f"  scaffold {len(sc)} cells, y {min((c[1] for c in sc), default='-')}..{max((c[1] for c in sc), default='-')}",
           f"  kit given: " + ("none (--as-built: design + scaffold laid by setblock, removal phase alone)" if AS_BUILT else f"{dict(sorted(items.items()))}"),
           f"  placements ~{sum(v for k, v in items.items())} (one click per item; double slabs 2), breaks ~{len(sc)} (scaffold); "
           "resume places each cell once across the cut",
           f"  jar {jar_sha()}",
           f"  mind {('up, body ' + str(bool(st.get('body'))) + ', open action ' + str(st.get('open_action'))) if st.get('ok') else st}",
           f"  O59: inventory hash now {dry_inv_hash()}; stash = 2 barrels 5 blocks from her park spot, outside park-eve's fill box(es) {boxes}",
           f"  cut ({mode}): " + ("`eve stop` at --cut s, `eve resume`, second dispatch" if mode == "resume" else "none (single dispatch)")]
    if CAGE:
        cg = cage_pick(name, d, load(name)[2], origin, (plot[0], plot[1] + 1, plot[2], plot[0] + 2 * MX - 1 + sx, top, plot[2] + 2 * MZ - 1 + sz))
        out.append(f"  O94ah cage: planned stand {cg and cg[0]} ({cg and cg[2]} step(s); covers stands of {cg and cg[3]} more), barriers {cg and cg[1]}")
    print("\n".join(out))


def _selftest_candidates():
    import tempfile
    global CANDIDATES
    old = CANDIDATES
    with tempfile.TemporaryDirectory() as d:
        CANDIDATES = Path(d) / "candidates.jsonl"
        CANDIDATES.write_text('{"ts": "2026-10-21T20:00:00.000-05:00"}\n{"ts": "2026-10-21T22:00:00.000-05:00"}\nnot json\n', encoding="utf-8")
        from datetime import datetime
        since = datetime.fromisoformat("2026-10-21T21:00:00-05:00").timestamp()
        note = copy_candidates(Path(d), since, mind=False)
        assert "1 stand-choice row" in note, note
        assert (Path(d) / "candidates.jsonl").read_text(encoding="utf-8").count("22:00:00") == 1
    CANDIDATES = old


def selftest():
    """laneb_build.py selftest: the inventory parser on a damaged tool (nested components) and plain stacks."""
    snbt = ('EveDev has the following entity data: [{count: 1, Slot: 0b, components: {"minecraft:damage": 15}, id: "minecraft:iron_hoe"}, '
            '{count: 60, Slot: 1b, id: "minecraft:wheat"}, {count: 1, Slot: 2b, id: "minecraft:iron_hoe"}, {count: 16, Slot: 3b, id: "minecraft:bread"}]')
    assert stacks(snbt) == [("minecraft:iron_hoe", 1, True), ("minecraft:wheat", 60, False), ("minecraft:iron_hoe", 1, False),
                            ("minecraft:bread", 16, False)], stacks(snbt)
    assert stacks("EveDev has the following entity data: []") == []
    global r
    r0, r = r, lambda c: {"Inventory": "X has the following entity data: []", "equipment": "Found no elements matching equipment"}[c.split()[-1]]
    a = inv_text()
    r = lambda c: {"Inventory": "X has the following entity data: []", "equipment": "X has the following entity data: {}"}[c.split()[-1]]
    assert a == inv_text() == "[]{}", (a, inv_text())  # absent equipment == empty equipment
    r = r0
    shelter = (-19, 98, -51, -13, 103, -45)  # park-eve.ps1's hollow fill (I19)
    assert in_box((-18, 99, -50), shelter) and in_box((-20, 99, -48), shelter) and not in_box((-21, 99, -48), shelter)
    assert not loop_ran({"ok": True, "status": "partial", "notes": ["repair_zone refused (retry_limit)"]})[0]  # O46
    assert loop_ran({"ok": True, "status": "done", "notes": []})[0]
    assert not ran({"action_id": "a", "status": "failed", "code": "zone_denied", "note": "server denied: place limit:blocks_placed_per_hour"})[0]
    assert ran({"action_id": "a", "status": "failed", "code": "zone_denied", "note": "server denied: outside any zone"})[0]
    _selftest_candidates()  # O94f: only the rows of this run go into its folder
    log = ('[12:00:00] [Server thread/INFO]: [evetest] query -> {"steps":[],"manual":[],"skipped":[]}\n'
           '[12:00:01] [Server thread/INFO]: [evetest] query -> {"steps":[],"manual":[{"pos":{"x":-5,"y":161,"z":-72}}],'
           '"skipped":[{"reason":"owner_edit","pos":{"x":-7,"y":162,"z":-71}}]}\n')
    q = parse_evetest_reply(log)
    assert q and q["manual"][0]["pos"]["x"] == -5, q  # the LAST reply in the slice
    assert whole_box_check(q, CP52_BOX)[0]
    bad = dict(q, steps=[{"pos": {"x": -4, "y": 161, "z": -72}}])
    assert not whole_box_check(bad, CP52_BOX)[0]  # a cell left
    assert not whole_box_check(dict(q, skipped=[]), CP52_BOX)[0]  # the owner's edit no longer reported (overwritten?)
    assert not whole_box_check(None, CP52_BOX)[0]  # no reply is never a pass
    lg = ["[12:00:01] eve-builder: design's planned spot 21, 151, -46 for place 22, 151, -45 is not reachable from 20, 151, -47 (Walk): own stand search instead",
          "[12:00:02] eve-builder: walk to 22, 151, -47 to click 22, 150, -45 face up for 22, 151, -45 (walk 1)"]
    assert all(ok for ok, _ in cage_checks(lg, (21, 151, -46))), cage_checks(lg, (21, 151, -46))
    assert not cage_checks(lg + ["eve-builder: walk to design's planned spot 21, 151, -46 for place 1, 2, 3"], (21, 151, -46))[0][0]
    assert not cage_checks(lg[1:], (21, 151, -46))[1][0]
    assert not cage_checks(lg + ["[W] eve-builder: stranded at 1, 2, 3 (no walk down"], (21, 151, -46))[2][0]
    print("selftest ok")


def main():
    mode = sys.argv[1]
    if QUAL and mode not in ("selftest", "restore") and not (LANE / "logs" / "suite.lock").exists():
        # the live sense's RCON write guard refuses every write outside a held suite.lock (45L O180b, 16:00: nothing changed)
        sys.exit(f"45L: hold {LANE / 'logs' / 'suite.lock'} first (foundation); the live sense refuses RCON writes without it. Nothing run.")
    if mode == "selftest":
        return selftest()
    if mode == "restore":  # put a crashed run's inventory stash back (O59)
        return print(stash_restore()[1])
    if mode == "cp52":
        return cp52(TAG)
    if mode == "cp53":
        return cp53()
    if mode == "gatecheck":
        return gatecheck()
    if mode == "cp53tall":
        return cp53tall()
    # default site: eve-watch's build_plot on the lanes; on the 45L a floor at y=190 in envelope zone reflex_arena, corner
    # x -24.., z 2.. (clear of home's overlap z<-16, below body's sky arena y 199..202 and combat's y 204..210)
    plot = tuple(int(v) for v in sys.argv[sys.argv.index("--plot") + 1].split(",")) if "--plot" in sys.argv else         ((-24, 190, 2) if QUAL else (20, 150, -48))
    if QUAL and jar_sha() == "none":
        sys.exit(f"O35: no eve-builder jar found under {LANE / 'client'} (mods): RESULT.md must name the exact jar")
    if QUAL and (mode == "restart" or "--deploy" in sys.argv):
        sys.exit("on the 45L: no --deploy (foundation deploys) and no restart case (foundation's client restart is its own)")
    name = sys.argv[sys.argv.index("--design") + 1] if "--design" in sys.argv else "test3"
    cut = float(sys.argv[sys.argv.index("--cut") + 1]) if "--cut" in sys.argv else 25.0
    d, d_hash, cells, items, steps = load(name)
    assert d_hash == d["hash"], "design.json hash does not re-verify"
    if mode == "cp51":
        return cp51(name, d, d_hash, cells, items, plot)
    origin = (plot[0] + MX, plot[1] + 1, plot[2] + MZ)
    sx, sy, sz = (d["size"][k] for k in "xyz")
    W, WZ = 2 * MX - 1, 2 * MZ - 1  # the cleared box is x plot..plot+W+sx, z plot..plot+WZ+sz (3 -> the old +5)
    # the cleared box (floor y0, air y0+1..y0+2+sy: roof + one) must hold the scaffold, and on the 45L must stay below body's
    # sky arena (y 199..202 over x -25..25): hut5_sim's old y0+3+sy reached 199
    top = plot[1] + 2 + sy
    sc_out = [c for c in scaffold_cells(name, d, origin) if not (plot[0] <= c[0] <= plot[0] + W + sx and c[1] <= top and plot[2] <= c[2] <= plot[2] + WZ + sz)]
    if sc_out:
        sys.exit(f"scaffold cells outside the cleared box {sc_out[:4]}; nothing run")
    if QUAL and top >= 199:
        sys.exit(f"the cleared box would reach y {top}, body's sky arena starts at 199; nothing run")
    if AS_BUILT and mode != "build":
        sys.exit("--as-built goes with build only")
    cg = cage_pick(name, d, cells, origin, (plot[0], plot[1] + 1, plot[2], plot[0] + W + sx, top, plot[2] + WZ + sz)) if CAGE else None
    if CAGE and (mode != "build" or AS_BUILT or not cg):
        sys.exit("--cage-stand goes with a plain build, on a design with a planned ground stand outside it whose cage is all air")
    if "--dry-run" in sys.argv:  # read-only: the plan, then exit before any lock, stash, fill or dispatch
        return dry_run(name, d, d_hash, items, plot, origin, top, mode)
    st = eve("status")
    if not st.get("body"):
        sys.exit(f"lane {LANE_ID} body is not up: {st}")
    if st.get("open_action"):
        sys.exit(f"lane {LANE_ID} has an open action, someone is using it: {st.get('open_action')}")
    lane_lock("acquire", 30)
    appr = check_approval(name, d_hash) if QUAL else stage_and_approve(name)
    if not appr.get("ok"):
        lane_lock("release")
        sys.exit(f"fixture self-approval refused, nothing run: {appr}")
    cp = "CP-50" if mode in ("resume", "restart") else "CP-49"
    root = REPO / "evidence" / cp
    root.mkdir(parents=True, exist_ok=True)
    num = int(sys.argv[sys.argv.index("--attempt") + 1]) if "--attempt" in sys.argv else 1 + sum(1 for x in root.iterdir() if x.is_dir() and f"-{TAG}-builder-attempt-" in x.name)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    out = root / f"{stamp}-{TAG}-builder-attempt-{num}"
    out.mkdir()
    write_config()  # the mod reads it at the start of every build; no restart needed
    if "--deploy" in sys.argv:
        deploy()
        st = eve("status")
    h0 = start_run()  # O59: her inventory into the stash, the run starts from the kit alone
    pre, runs, t0 = inv(), [], time.time()
    notes = ([f"{RULING}: {name} staged without gate.json ({RULING_TEXT.get(RULING, 'orchestrator lane-only ruling')})"] if NO_GATE else []) + [f"{'owner approval by foundation (owner CLI, labelled fixture step, orchestrator-authorised)' if QUAL else 'fixture self-approval (lane, dev world)'}: `eve approve {name} v{VER}` -> approval {appr.get('approval_id')} "
             f"by {appr.get('decided_by')} via {appr.get('channel')}, hash {str(appr.get('design_hash'))[:12]}, warnings {appr.get('warnings')}"]
    x0, y0, z0 = plot
    difficulty0, laid, feet, lit_checks, wl_after = None, False, [], [], None
    clog = client_log()
    log0 = clog.stat().st_size if clog.exists() else 0
    try:
        own_lock("before fixture setup")
        m = __import__("re").search(r"difficulty is (\w+)", r("difficulty"))
        difficulty0 = m.group(1).lower() if m else None  # restored on the way out (incident I3)
        r("difficulty peaceful")
        r(f"forceload add {x0} {z0} {x0 + W + 3 + sx} {z0 + WZ + 3 + sz}")
        notes.append(park_safe((x0, y0, z0, x0 + W + sx, y0 + 2 + sy, z0 + WZ + sz), "the site clear"))
        laid = True  # from the first fill on, the finally gives the sky back on the 45L (I26a)
        r(f"fill {x0} {y0} {z0} {x0 + W + sx} {y0} {z0 + WZ + sz} minecraft:stone")
        r(f"fill {x0} {y0 + 1} {z0} {x0 + W + sx} {y0 + 2 + sy} {z0 + WZ + sz} minecraft:air")
        r(f"kill @e[type=item,x={x0},y={y0},z={z0},dx={W + sx},dy={4 + sy},dz={WZ + sz}]")  # only drops on my plot
        if AS_BUILT:  # bottom-up, so a door's lower half goes in before its upper half
            for (x, y, z), want in sorted(cells.items(), key=lambda t: t[0][1]):
                if want != "minecraft:air":
                    r(f"setblock {origin[0] + x} {origin[1] + y} {origin[2] + z} {want}")
            laid_sc = scaffold_cells(name, d, origin, states=True)
            for c, st_ in laid_sc:
                r(f"setblock {c[0]} {c[1]} {c[2]} {st_}")
            notes.append(f"O89c fixture: {name} laid as built by RCON setblock, its {len(laid_sc)} scaffold cells in place (the state CP-49 "
                         f"lane C a11 reached at removal, where ground-only stands left the 15 room cells); no kit given")
        else:
            give(sorted(items.items()))
        wl_below = None
        if WRONG:  # O202: the support under the wrong log, then one extra log for her to place
            b_ = (WRONG[0], WRONG[1] - 1, WRONG[2])
            if cells.get(b_, "minecraft:air") != "minecraft:air":
                wl_below = cells[b_]
                r(f"setblock {origin[0] + b_[0]} {origin[1] + b_[1]} {origin[2] + b_[2]} {wl_below}")
            give([("minecraft:oak_log", 1)])
        for c in cg[1] if cg else []:
            r(f"setblock {c[0]} {c[1]} {c[2]} minecraft:barrier")
        if cg:
            notes.append(f"O94ah cage: planned stand {cg[0]} ({cg[2]} step(s) planned there) caged by {len(cg[1])} barriers {cg[1]}; "
                         f"they cover other planned stands used by {cg[3]} step(s) (those search their own)")
        i0 = inv()
        safe_tp(origin[0] + sx // 2 + 0.5, origin[1], origin[2] - 2 + 0.5)
        time.sleep(1.5)
        wl_ok = None
        if WRONG:
            w_ = tuple(origin[i] + WRONG[i] for i in range(3))
            # axis=x needs a click on an east/west face: a temporary dirt on its west side (outside the design), whose east
            # face looks toward her start spot (O202 a1: on the support below alone the body offered only axis=y)
            side = (w_[0] - 1, w_[1], w_[2])
            side_local = (WRONG[0] - 1, WRONG[1], WRONG[2])
            if cells.get(side_local, "minecraft:air") != "minecraft:air":
                sys.exit(f"--wrong-log: the cell west of {WRONG} is a design cell; pick another")
            r(f"setblock {side[0]} {side[1]} {side[2]} minecraft:dirt")
            res_ = eve("do", {"skill": "place_block", "params": {"pos": dict(zip("xyz", w_)), "item": "minecraft:oak_log",
                                                                 "state": {"axis": "x"}}, "wait": 60}, 90)
            wl_ok = test(w_, "minecraft:oak_log[axis=x]")
            r(f"setblock {side[0]} {side[1]} {side[2]} minecraft:air")  # gone before the build: it is no design cell
            if not wl_ok:  # O212: never build on a fixture that did not take (RCON-verified before the dispatch)
                notes.append(f"O202 fixture: the wrong log is NOT in place ({res_.get('status')}/{res_.get('code')}): build not dispatched")
                raise SystemExit(f"--wrong-log: oak_log[axis=x] not at {w_} after place_block ({res_.get('status')}/{res_.get('code')} "
                                 f"{res_.get('note') or ''}); nothing built")
            notes.append(f"O202 fixture: wrong-axis oak_log by a separate place_block at {w_} (local {WRONG}) -> {res_.get('status')}/"
                         f"{res_.get('code')} {res_.get('note') or ''}; in place: {wl_ok}; support {wl_below} set by RCON")
        import threading
        stop_f = threading.Event()  # feet + HP every 0.25 s (O87m: "no fall" is a pass condition for builds too)
        th_f = threading.Thread(target=sample_feet, args=(stop_f, feet), daemon=True)
        th_f.start()
        own_lock("before dispatch")
        if mode == "build":
            runs.append(build(name, d_hash, origin, WAIT))
        else:
            first = build(name, d_hash, origin, cut)
            runs.append(first)
            if mode == "resume":
                notes.append(f"cut: `eve stop` at {cut:.0f} s -> {eve('stop')}; `eve resume` -> {eve('resume')}")
            else:
                stop_f.set()  # the restart parks her in the shelter (a teleport, not a fall): no samples across it
                th_f.join(2)
                ok, o = restart_client()
                notes.append(f"cut: restart-client.ps1 at {cut:.0f} s -> {'ok' if ok else 'FAILED ' + o}")
            time.sleep(5)  # the cut ends the first action (cancelled / link loss); the mind records it
            notes.append(f"status after the cut: {json.dumps(eve('status'))[:600]}")
            if mode == "restart":  # restart-client.ps1 re-parks her in the O15 shelter; fixture puts her back at the plot
                safe_tp(origin[0] + sx // 2 + 0.5, origin[1], origin[2] - 2 + 0.5)
                time.sleep(1.5)
                stop_f = threading.Event()
                th_f = threading.Thread(target=sample_feet, args=(stop_f, feet), daemon=True)
                th_f.start()
            own_lock("before the second dispatch")
            runs.append(build(name, d_hash, origin, WAIT))
        time.sleep(1.0)
        stop_f.set()
        th_f.join(2)
        i1 = inv()
        results = judge_cells(origin, cells)
        scaffold_left = [c for c in scaffold_cells(name, d, origin) if not test(c, "minecraft:air")]
        if WRONG:  # read before the 45L's final clear: observed_state shows only '?' for a differing property (O202 a2)
            wl_after = test(tuple(origin[i] + WRONG[i] for i in range(3)), "minecraft:oak_log[axis=x]")
        if LIGHT:  # judged first, so the frame's cells and the items spent are the build's alone
            lit_checks = light_portal(origin, d)
    finally:
        own_lock("before restoring Eve")  # if the lane is someone else's now, leave their state alone
        h1, inv_note = stash_restore()  # O59: first, before anything else can stop the finally
        notes.append(inv_note)
        if cg and laid:  # the barriers are invisible: never left on the lane's plot
            for c in cg[1]:
                r(f"setblock {c[0]} {c[1]} {c[2]} minecraft:air")
            notes.append("O94ah cage removed (barriers back to air)")
        if KEEP_SITE:
            notes.append("--keep-site: the site is left standing and Eve is not parked; clear and park by hand afterwards")
        elif QUAL and laid:  # give the sky back on her real world (floor included), even after an error (I26a: body's clear box
            try:           # starts at y 192 over this site), with her parked first (O35)
                notes.append(park_safe((x0, y0, z0, x0 + W + sx, y0 + 2 + sy, z0 + WZ + sz), "the final site clear"))
                r(f"fill {x0} {y0} {z0} {x0 + W + sx} {y0 + 2 + sy} {z0 + WZ + sz} minecraft:air")
                notes.append("site cleared (floor and build back to air)")
            except SystemExit as e:
                notes.append(f"SITE NOT CLEARED, tell foundation: {e}")
        if not KEEP_SITE:
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LANE / "ops" / "park-eve.ps1"),
                            "-Root", str(SERVER), "-Player", P], capture_output=True)
        r(f"forceload remove {x0} {z0} {x0 + W + 3 + sx} {z0 + WZ + 3 + sz}")
        if difficulty0 and difficulty0 != "peaceful":
            r(f"difficulty {difficulty0}")
        lane_lock("release")

    spent = {k: i0.get(k, 0) - i1.get(k, 0) for k in set(i0) | set(i1) if i0.get(k, 0) != i1.get(k, 0)}
    exp = spent_expected(results, steps)
    if AS_BUILT:  # she placed nothing; each scaffold block she broke should be back in her inventory (its item = its id)
        exp = {}
        for c, st_ in laid_sc:
            if c not in scaffold_left:
                exp[st_.split("[")[0]] = exp.get(st_.split("[")[0], 0) - 1
    if WRONG:  # the RCON support was not spent; her wrong log was
        if wl_below:
            k_ = wl_below.split("[")[0]
            exp[k_] = exp.get(k_, 0) - 1
        exp["minecraft:oak_log"] = exp.get("minecraft:oak_log", 0) + 1
        exp = {k: v for k, v in exp.items() if v}
    counts = {}
    for c in results:
        counts[c["class"]] = counts.get(c["class"], 0) + 1
    last = runs[-1].get("evidence") or {}
    rest = [c for c in results if not (WRONG and tuple(c["local"]) == WRONG)]
    checks = [
        (all(c["class"] in ("match", "derived_mismatch") for c in rest), f"cells {counts}" + (f" (the wrong-log cell {WRONG} aside)" if WRONG else "")),
        (not scaffold_left, f"scaffold removed ({len(scaffold_left)} cell(s) left: {scaffold_left})"),
        (spent == exp, f"items spent {dict(sorted(spent.items()))} (expected for what is in place {dict(sorted(exp.items()))})"),
    ]
    checks.append((h1 == h0, inv_note))  # O59 pass condition
    checks += lit_checks
    if WRONG:
        w_ = tuple(origin[i] + WRONG[i] for i in range(3))
        cell = next((c for c in results if tuple(c["local"]) == WRONG), {})
        man = [m for m in last.get("manual") or [] if (m["pos"]["x"], m["pos"]["y"], m["pos"]["z"]) == w_]
        checks.append((bool(wl_ok), f"O202 fixture: the wrong log was in place before the build ({wl_ok})"))
        checks.append((cell.get("class") == "wrong_state" and bool(wl_after),
                       f"O202: the wrong log is still there after the build ({cell.get('class')}; oak_log[axis=x] by RCON: {wl_after})"))
        checks.append((any("H-trees" in m.get("why", "") for m in man), f"O202: the cell went manual for sense H-trees ({man})"))
    if cg:
        try:
            with open(clog, "rb") as fh:  # only what this run appended
                fh.seek(log0)
                log = fh.read().decode("utf-8", errors="replace").splitlines()
        except OSError as e:
            log = []
            notes.append(f"client log unreadable: {e}")
        (out / "cage_log.txt").write_text("\n".join(l for l in log if "planned spot" in l or "walk to" in l or "stranded" in l) + "\n", encoding="utf-8")
        checks += cage_checks(log, cg[0])
    hp0 = next((f[2] for f in feet if f[2] is not None), None)
    min_y = min((f[1] for f in feet), default=None)
    min_hp = min((f[2] for f in feet if f[2] is not None), default=None)
    checks.append((min_y is not None and min_y >= y0 + 1 - 0.5 and (hp0 is None or min_hp is None or min_hp >= hp0),
                   f"falls: min feet y {min_y} (site floor top {y0 + 1}), highest {max((f[1] for f in feet), default=None)}, "
                   f"HP {hp0} -> min {min_hp}, {len(feet)} samples, rcon_sample_gap_s {max_gap(feet)}"))
    if mode != "build":
        ev2 = last
        n_sc = len(scaffold_cells(name, d, origin))  # the skill counts scaffold placements in placed/already_present too
        both = (ev2.get("placed") or 0) + (ev2.get("already_present") or 0)
        checks.append((both == (ev2.get("cells_total") or 0) + n_sc and (ev2.get("already_present") or 0) > 0,
                       f"second run placed {ev2.get('placed')} + already_present {ev2.get('already_present')} == cells_total "
                       f"{ev2.get('cells_total')} + scaffold {n_sc} (each cell exactly once across the cut)"))
    vals = [ran(x) for x in runs[-1:]]  # O46: the final dispatch must have run (a cut first run is expected to end early)
    valid = all(v for v, _ in vals) and bool(runs)
    vwhy = "; ".join(w for _, w in vals) or "no run"
    passed = valid and all(ok for ok, _ in checks)
    notes.append(copy_candidates(out, t0))
    (out / "result.json").write_text(json.dumps({"valid": valid, "validity": vwhy, "runs": runs, "cells": results, "inv_before": i0, "inv_after": i1, "spent": spent,
                                                 "expected_spent": exp, "notes": notes, "feet": feet}, indent=1), encoding="utf-8")
    reg = st.get("registry") or {}
    where = "the 45L (`45L`, foundation's qualification lane, Eve's real account)" if QUAL else f"lane {LANE_ID} (`shark`, eve-watch's Shark stack)"
    lines = [f"# {cp} — eve-builder {'45L' if QUAL else 'lane ' + LANE_ID + ' (`shark`)'} {stamp} attempt {num} ({mode}): {'PASS' if passed else ('FAIL' if valid else 'INVALID (O46: ' + vwhy + ')')}", "",
             (f"Hypothesis: build_schematic, started on `{name}` (hash {d_hash[:12]}) as built with its scaffold in place, takes every "
              f"scaffold cell out (O89c: off the ground where no ground spot works, only with a walk down after the break), leaves the "
              f"design block-for-block and picks up what she breaks." if AS_BUILT else
              f"Hypothesis: build_schematic places `{name}` (hash {d_hash[:12]}) block-for-block as the server sees it, spends exactly "
              f"the items now in place" + (", and after the cut the second run places only what was left." if mode != "build" else ".")),
             f"Fixture: {where}, real mind {CLI} via /cli, real eve-sense, {P}; stone plot at {plot}, "
             f"origin {origin}; " + ("design and scaffold laid by setblock, no items given" if AS_BUILT else "exactly the design's items given") + ". Judge: RCON `execute if block` per cell (all {len(cells)} cells, "
             f"air included) and `data get` inventory before/after.",
             f"Versions: repo {git_head()} (client: {jar_sha()}), registry {reg.get('version')} {str(reg.get('hash'))[:12]}. "
             f"Elapsed {time.time() - t0:.0f} s. Deaths 0 (peaceful), interventions 0 unless noted. " + ("Qualification lane." if QUAL else "Development evidence, not qualification."),
             "", *[f"- {n}" for n in notes],
             *[f"- run {k + 1}: {x.get('status')}/{x.get('code') or x.get('error')} in {x.get('_elapsed_s')} s; note {x.get('note')}; "
               f"evidence placed {(x.get('evidence') or {}).get('placed')}, already {(x.get('evidence') or {}).get('already_present')}, "
               f"fixed {(x.get('evidence') or {}).get('fixed')}, manual {json.dumps((x.get('evidence') or {}).get('manual'))}" for k, x in enumerate(runs)],
             "", "| check | ok |", "|---|---|", *[f"| {d_} | {'PASS' if ok else 'FAIL'} |" for ok, d_ in checks],
             "", "| local | want | server | observed |", "|---|---|---|---|",
             *[f"| {c['local']} | `{c['want'].replace('minecraft:', '')}` | {c['class']} | {c.get('observed', '').replace('minecraft:', '')} |"
               for c in results if not (c["class"] == "match" and c["want"] == "minecraft:air")]]
    text = "\n".join(lines) + "\n"
    (out / "RESULT.md").write_text(text, encoding="utf-8")
    summ = REPO / "parts" / "builder" / "evidence-summaries" / f"{cp}-{stamp}-{TAG}-builder-attempt-{num}.md"
    summ.parent.mkdir(exist_ok=True)
    summ.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
