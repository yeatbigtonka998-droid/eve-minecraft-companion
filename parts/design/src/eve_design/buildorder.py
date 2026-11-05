"""Placement order for building a design (the rules in BUILD_SCHEMATIC.md §4), as data the mind can hand to body.

1. layer by layer (y ascending). Each layer starts with its scaffold blocks (a pillar block goes in as soon as what
   it stands on exists — some pillars stand on the building's own floors), then the design's blocks: those with
   a `facing` (set by Eve's look) first while the look is clear, then the rest; each by role (foundation, support, wall,
   floor, stair, window, door, roof, trim, furniture, light), then z, then x;
2b. a block waits until it has something to be placed against (the ground or a placed neighbour);
3. a block that rests on / hangs from / is fixed to another (validate.attachment) waits until that block is placed —
   a hanging lantern therefore follows the layer above;
4. second halves (door tops, bed heads, tall-plant tops) are not steps: they appear with their lower half / foot;
5. doors and fence gates after every layer (a placed door shuts the way in: builder CP-49), before
6. scaffold removal, top-down, last.
A block that never gets something to place against is still listed, last, with a note; the placement simulation
then reports it, so `buildable` fails instead of the order raising.
"""
from __future__ import annotations

from .bom import items_for_state
from .gamedata import GameData, parse_state
from .grid import AIR, Design
from .validate import attachment, classes

ROLE_ORDER = ["foundation", "support", "wall", "floor", "stair", "window", "door", "roof", "trim", "furniture", "light",
              "ground", "none"]
NEIGHBOURS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
BED_STEP = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}


def _second_half(g: Design, x, y, z, bid, p):
    """Position of the half that appears by itself when this block is placed, or None."""
    if p.get("half") == "lower" and g.in_bounds(x, y + 1, z) and parse_state(g.get(x, y + 1, z))[0] == bid:
        return (x, y + 1, z)
    if bid.endswith("_bed") and p.get("part") == "foot":
        dx, dz = BED_STEP[p["facing"]]
        return (x + dx, y, z + dz)
    return None


def order(g: Design, gd: GameData, scaffold: Design | None = None, slots: dict | None = None) -> list[dict]:
    """`slots` moves doors from the end: {pos: y} places that door at the end of layer y, {pos: "first"} first in its
    own layer (with the other facing blocks); {pos: "late"} moves any other block to the end of its layer."""
    slots = slots or {}
    c = classes(gd)
    steps: list[dict] = []

    def add(pos, state, role, layer, after=None, also=None, note=None):
        steps.append({"step": len(steps) + 1, "layer": layer, "pos": list(pos), "state": state, "role": role,
                      "items": [{"item": i, "count": n} for i, n in items_for_state(state, gd)],
                      "after": list(after) if after else None, "also_appears": [list(also)] if also else [],
                      **({"note": note} if note else {})})

    scaf = []
    if scaffold is not None:
        off = [scaffold.origin[i] - g.origin[i] for i in range(3)]
        scaf = sorted(((x + off[0], y + off[1], z + off[2]), s) for x, y, z, s, _ in scaffold.cells())

    skip = set()
    for x, y, z, s, _ in g.cells():
        bid, p = parse_state(s)
        h = _second_half(g, x, y, z, bid, p)
        if h:
            skip.add(h)
    # facing blocks first while the look is clear, except roof stairs: they need the wall behind them (eaves)
    first = lambda x, y, z, s, r: 2 if slots.get((x, y, z)) == "late" else 0 if "facing" in parse_state(s)[1] and r != "roof" else 1
    todo = sorted([((y, first(x, y, z, s, r), ROLE_ORDER.index(r) if r in ROLE_ORDER else 99, z, x),
                    s, r) for x, y, z, s, r in g.cells() if (x, y, z) not in skip] +
                  [((y, -1, -1, z, x), s, "scaffold") for (x, y, z), s in scaf])
    placed = set(skip & set())            # second halves count as placed once their lower half is
    waiting = []

    loose = [False]                          # last resort: ignore the log axis (the simulation then reports it)

    def ready(x, y, z, s, role=None):
        if role == "scaffold":               # stands on the ground or on something already placed
            return y <= 0 or (x, y - 1, z) in placed, (x, y - 1, z) if y > 0 else None
        bid, p = parse_state(s)
        a = attachment(bid, p, c)
        if not a:                            # something to place it against: the ground or a placed neighbour
            k = "xyz".index(p["axis"]) if "axis" in p and not loose[0] else None   # logs: only along their axis
            return (y == 0 and k in (None, 1)) or any((x + n[0], y + n[1], z + n[2]) in placed for n in NEIGHBOURS
                                                    if k is None or n[k] != 0), None
        sup = (x + a[0][0], y + a[0][1], z + a[0][2])
        if not g.in_bounds(*sup) or g.get(*sup) == AIR:
            return True, None             # validated designs never get here; outside supports are terrain
        return sup in placed, sup

    def place(x, y, z, s, r, sup):
        if r == "scaffold":
            add((x, y, z), s, "support", "scaffold", sup)
            placed.add((x, y, z))
            return
        bid, p = parse_state(s)
        h = _second_half(g, x, y, z, bid, p)
        note = None
        if p.get("type") == "double" and bid.endswith("_slab"):
            note = "two slabs: place one, then the second onto it"
        elif p.get("open") == "true":
            note = "use (right-click) after placing: the design wants it open"
        add((x, y, z), s, r, "final", sup, h, note)
        placed.add((x, y, z))
        if h:
            placed.add(h)

    def flush():
        progress = True
        while progress:
            progress = False
            for w in list(waiting):
                ok, sup = ready(*w[0], w[1], w[2])
                if ok:
                    waiting.remove(w)
                    place(*w[0], w[1], w[2], sup)
                    progress = True

    last_y, doors = None, []

    def doors_after(y):
        for d in [d for d in doors if slots.get(d[0]) == y]:
            doors.remove(d)
            place(*d[0], d[1], d[2], ready(*d[0], d[1], d[2])[1])
        flush()

    for (y, _, _, z, x), s, r in todo:
        if last_y is not None and y != last_y:
            flush()
            doors_after(last_y)
        last_y = y
        if r != "scaffold" and parse_state(s)[0] in c.doors and slots.get((x, y, z)) != "first":
            doors.append(((x, y, z), s, r))       # a placed door shuts the way in: doors and gates go last
            continue
        ok, sup = ready(x, y, z, s, r)
        if ok:
            place(x, y, z, s, r, sup)
        else:
            waiting.append(((x, y, z), s, r))
    flush()
    doors_after(last_y)
    for pos, s, r in doors:
        place(*pos, s, r, ready(*pos, s, r)[1])
    flush()                                   # blocks that had only a door top to go against
    if waiting:
        loose[0] = True
        flush()
    for w in waiting:                         # nothing ever to place against: listed last, the simulation fails them
        (x, y, z), s, r = w                   # (45L attempt 5 crashed here when this raised)
        add((x, y, z), s, r, "final", None, None, "nothing to place it against")
    for pos, s in sorted(scaf, key=lambda t: (-t[0][1], t[0][2], t[0][0])):
        steps.append({"step": len(steps) + 1, "layer": "scaffold_removal", "pos": list(pos), "state": AIR, "role": "none",
                      "items": [], "after": None, "also_appears": [], "note": f"break the {s.split('[')[0]} (scaffold)"})
    return steps


def plan_order(g: Design, gd: GameData, scaffold: Design | None = None,
               slots: dict | None = None, site: dict | None = None) -> tuple[list[dict], list[dict], dict]:
    """The order written to build_order.json: `order`, with each door the placement simulation cannot place at the end
    moved to the latest slot where some standing spot still sees its support looking along its facing (builder,
    2026-10-20): the end of a lower layer, else first in its own layer. Returns (steps, simulation failures, slots)."""
    from .placecheck import simulate
    c = classes(gd)
    if slots is None and scaffold is not None:   # the order the scaffold was planned against
        slots = {tuple(q[:3]): q[3] for q in scaffold.meta.get("provenance", {}).get("order_slots", [])}
    slots = dict(slots or {})
    steps = order(g, gd, scaffold, slots)
    fails, _ = simulate(g, gd, scaffold, steps, site=site)
    key = lambda f: (f["pos"]["x"], f["pos"]["y"], f["pos"]["z"])
    doors = {(x, y, z) for x, y, z, s, _ in g.cells() if parse_state(s)[0] in c.doors and parse_state(s)[1].get("half") != "upper"}
    near = lambda q, d: abs(q[0] - d[0]) + abs(q[2] - d[2]) <= 1 and d[1] - 1 <= q[1] <= d[1] + 2
    stuck = [d for d in sorted(doors - set(slots))     # doors that fail, or leave a neighbour (a lintel) failing
             if any(f.get("kind") == "place" and (key(f) == d or near(key(f), d)) for f in fails)]
    for d in stuck:
        for slot in [*range(g.size[1] - 2, d[1] - 1, -1), "first"]:
            trial = {**slots, d: slot}
            st = order(g, gd, scaffold, trial)
            f, _ = simulate(g, gd, scaffold, st, site=site)
            if d not in {key(q) for q in f} and len(f) < len(fails):
                slots, steps, fails = trial, st, f
                break
    for _ in range(3):                       # a block placed too early to have its support: end of its layer instead
        late = {q: "late" for f in fails if f.get("kind") == "place" and key(f) not in doors and key(f) not in slots
                for q in _run(g, key(f))}
        if not late:
            break
        trial = {**slots, **late}
        st = order(g, gd, scaffold, trial)
        f, _ = simulate(g, gd, scaffold, st, site=site)
        if len(f) >= len(fails):
            break
        slots, steps, fails = trial, st, f
    from .placecheck import stale_stands
    if stale_stands(g, gd, scaffold, steps, site):   # O94ag-b: a stand that lost its way in or down: replay, re-checked
        fails, _ = simulate(g, gd, scaffold, steps, site=site, recheck=True)
    return steps, fails, slots


def _run(g: Design, p) -> set:
    """The connected run of identical blocks in p's layer (an eave row): moved late together, so it still chains."""
    st, seen, todo = g.get(*p), {p}, [p]
    while todo:
        x, y, z = todo.pop()
        for n in ((x + 1, y, z), (x - 1, y, z), (x, y, z + 1), (x, y, z - 1)):
            if n not in seen and g.in_bounds(*n) and g.get(*n) == st:
                seen.add(n)
                todo.append(n)
    return seen


COLUMNS = ["layer", "x", "y", "z", "after", "also_appears", "note", "stand", "interior"]


def compact(design_hash: str, steps: list[dict]) -> dict:
    """The form written into design folders: one row per step; the state comes from design.json/scaffold.json at that
    position and the items from bom.json items_per_state (scaffold removal rows break the block instead)."""
    return {"design_hash": design_hash, "columns": COLUMNS,
            "rows": [[s["layer"], *s["pos"], s["after"], s["also_appears"] or None, s.get("note"), s.get("stand"),
                      s.get("interior")] for s in steps]}
