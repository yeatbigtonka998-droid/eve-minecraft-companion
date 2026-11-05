"""Scaffold plan (scaffold.json): dirt staircases Eve's builder can climb (1-high steps), added
greedily until the placement simulation (eve_design.placecheck) finds a standing spot for every block. Each staircase
ends in the spot that lets the first failing block be placed; its steps run outward and down to walkable ground.
Scaffold never occupies a design cell; it is placed layer by layer with the build and removed last.
ponytail: greedy, one failing block at a time, each candidate checked by a full simulation (cabin ~2 s, a two-storey
house minutes); generate.py therefore plans only the design it writes. Faster: simulate from the first changed layer.
"""
from __future__ import annotations

import time
from itertools import islice

import numpy as np

from .gamedata import GameData
from .grid import Design
from .placecheck import POLICY, World, can_place, simulate, stale_stands
from .validate import classes

MAX_STAIRS = 150
TRIES = 6           # candidate staircases simulated per failing block
MORE_TRIES = 3      # checked passes: further batches of TRIES when a round's are all rejected
CHECKS = 60         # checked passes: candidates pre-checked per round at most (each a walk of the world)
CHECKED_SHARE = 2 / 3   # of TIME the checked passes may use together: the earlier planner always gets the rest
# simulated build steps one planning pass may spend: a backstop for callers without a clock (run_jobs has its
# per-brief wall clock, O68). wing_house (934 blocks) needs ~49k, a 1476-block walled farmhouse ~90k (19 min on Shark);
# the 45L h06 (up to 2200 blocks) ran for hours. Out of budget = not buildable, with that reason.
WORK = 200_000
# ... and wall-clock seconds one plan() may spend (O68/O80: h06 looped again on the 45L; one simulation of a 1476-block
# walled farmhouse is ~23 s, 3/4 of it in the walking searches). Out of either budget, plan() returns its best partial
# plan with the reason; FINISH_TRIES x TIME stays inside run_jobs' 45-minute brief limit.
# ponytail: a wall clock makes a big plan machine-dependent at the margin (O87h made the replay ~3-5x faster and the
# planner waste far fewer replays; incremental walking searches would be the next step).
TIME = 600.0
# O87y (45L h08: a 3-storey watch-house's best design ran out of planning at 600 s): the clock above is per storey,
# at most TIME_CAP; the work backstop scales with it. generate's finishing plans share one TIME_CAP between them.
TIME_CAP = 1800.0


def budget(g: Design) -> float:
    """O87y: this design's planning wall clock: TIME per storey (its rooms' storeys; 1 without), at most TIME_CAP."""
    return min(TIME * (max((r.get("storey", 0) for r in g.rooms), default=0) + 1), TIME_CAP)
# O89: no scaffold inside the design's rooms by default, until the route model into rooms matches Baritone (doors,
# interior steps, no break, no place) and hut5_tall has passed on a lane. examples/hut5_tall.py opts in.
INTERIOR = False
BOX = 4


def _design(cells: set, g: Design, gd: GameData, P: int, stairs: int) -> Design | None:
    if not cells:
        return None
    xs, ys, zs = zip(*cells)
    mn = (min(xs), min(ys), min(zs))
    s = Design((max(xs) - mn[0] + 1, max(ys) - mn[1] + 1, max(zs) - mn[2] + 1),
               (g.origin[0] + mn[0] - P, g.origin[1] + mn[1], g.origin[2] + mn[2] - P))
    for X, Y, Z in cells:
        s.set(X - mn[0], Y - mn[1], Z - mn[2], "minecraft:dirt", "support")
    s.canonicalize(gd)
    s.meta = {"project_id": g.meta.get("project_id"), "version": g.meta.get("version"),
              "provenance": {"scaffold_for": g.hash(), "plan": "greedy dirt staircases (eve_design.scaffold)",
                             "staircases": stairs}}
    return s


def _floors(w: World):
    """What dirt may rest on besides the ground and other dirt: the design's own sturdy tops; under the walk_surfaces
    policy (O94ag) only its floors, foundation, stairs and ground, so no column starts on a roof Eve cannot walk onto."""
    return (w.walk_surface if w.policy == "walk_surfaces" else w.design) & w.walk_f


def _staircase(w: World, F, d, have: set, budget: int = 3000, turns: bool = True) -> tuple | None:
    """(dirt cells, landing feet cell) of a staircase whose top step carries feet F, 1-high steps down to the local
    floor (the ground, a floor of the design, or an existing scaffold step). It starts in direction d and may turn (a
    tall build on a small plot needs switchbacks); depth-first, straight on before turning, no column used twice."""
    H, D, W = w.shape
    solid_f = _floors(w)                                   # the design's own floors and tops
    left = [budget]

    def column(cx, cz, h):
        """(dirt for this step, landed) or None if no step can stand here."""
        if not (0 <= cx < W and 0 <= cz < D) or h < 0 or h + 1 >= H or (w.area is not None and not w.area[cz, cx]):
            return None                                    # off the grid or off the plot
        if w.placed[h, cz, cx] or w.placed[h + 1, cz, cx]:
            return None                                    # feet and head free now (unbuilt design cells allowed)
        floor = lambda y: solid_f[y, cz, cx] or (cx, y, cz) in have      # scaffold steps are floors too
        b = h
        while b > 0 and not floor(b - 1) and not w.design[b - 1, cz, cx]:
            b -= 1                                         # dirt goes down to a floor or the ground
        if b > 0 and w.design[b - 1, cz, cx] and not floor(b - 1):
            return None                                    # would stand on something that is not a floor
        if any(w.placed[y, cz, cx] and (cx, y, cz) not in have for y in range(b, h)):
            return None                                    # something already there (an obstacle)
        return [(cx, y, cz) for y in range(b, h) if (cx, y, cz) not in have], b == h

    def walk(cx, cz, h, dx, dz, used, cells):
        left[0] -= 1
        if left[0] < 0:
            return None
        r = column(cx, cz, h)
        if r is None:
            return None
        dirt, landed = r
        if landed:
            return cells, (cx, h, cz)                      # reached walkable floor: the landing
        for nd in ((dx, dz), (dz, -dx), (-dz, dx))[:3 if turns else 1]:   # straight on, then the two turns
            n = (cx + nd[0], cz + nd[1])
            if n not in used:
                found = walk(n[0], n[1], h - 1, nd[0], nd[1], used | {n}, cells + dirt)
                if found:
                    return found
        return None
    X, Y, Z = F
    r = column(X, Z, Y)
    if r is None:
        return None
    if r[1]:
        return [], F                                       # already standing on a floor: nothing to build
    n = (X + d[0], Z + d[1])
    return walk(n[0], n[1], Y - 1, d[0], d[1], {(X, Z), n}, r[0])


def _best(w: World, T, state: str, have: set, todo: np.ndarray, c, P: int, sx: int, sz: int, turns: bool = True,
          ranked: bool = False) -> list:
    """Candidate staircases for block T, distinct, cheapest first: landing walkable now (there and back), outside the
    footprint, fewest blocks per failing block in reach. `ranked` (O87h): fewest blocks per failing block in reach,
    inside or out (the tower's top storey: a few steps on its floor, not a staircase from the ground)."""
    adj: dict = {}
    feet = w.walk(adj=adj)
    ok = feet & w.escape(feet, adj)
    found, seen = [], set()
    for Y in range(1, min(T[1] + 3, w.shape[0] - 2)):
        for Z in range(T[2] - BOX, T[2] + BOX + 1):
            for X in range(T[0] - BOX, T[0] + BOX + 1):
                if not can_place(w, T, state, np.array([[X, Y, Z, 0]], float), c):
                    continue
                eye = np.array([X + 0.5, Y + 1.27, Z + 0.5])
                cover = int((np.sqrt(((todo - eye) ** 2).sum(1)) <= 4.3).sum())
                for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    r = _staircase(w, (X, Y, Z), d, have, turns=turns)
                    if not r or not r[0] or frozenset(r[0]) in seen:
                        continue
                    cells, landing = r
                    seen.add(frozenset(cells))
                    inside = 0 if ranked else sum(1 for cx, _, cz in cells if P <= cx < P + sx and P <= cz < P + sz)
                    found.append(((landing not in ok, inside, len(cells) / max(cover, 1)), cells))
    found.sort(key=lambda t: t[0])
    return [cells for _, cells in found]


def _reachable(w: World, cells: list, upto: int) -> bool:
    """Is the candidate's stand (on its highest dirt) walkable, with a way back down, in the world the replay will have
    when it gets to the block: this snapshot plus the candidate's dirt up to the block's layer? O87h (45L h04): a
    staircase stacked on an earlier one's step cut that one's path, its own stand was out of reach, and each such
    candidate cost a full replay that fixed nothing."""
    top = max(cells, key=lambda q: q[1])
    stand = (top[0], top[1] + 1, top[2])
    put = [(y, z, x) for x, y, z in cells if y <= upto]
    if top[1] > upto:
        return False                             # its stand's dirt comes after the block
    idx = tuple(np.array(put).T)
    saved = [a[idx].copy() for a in (w.placed, w.body_f, w.walk_f)]
    try:                                         # w.placed may be the planner's own snapshot: always put it back
        for a in (w.placed, w.body_f, w.walk_f):
            a[idx] = True
        adj: dict = {}
        feet = w.walk(adj=adj)
        return stand in feet and stand in w.escape(feet, adj)
    finally:
        for a, v in zip((w.placed, w.body_f, w.walk_f), saved):
            a[idx] = v


def _supports(w: World, T, have: set) -> list:
    """Dirt support columns next to block T (below it first, then beside it), from the local floor up to the cell the
    block is placed against: an overhanging eave row has nothing of its own to start from (two_storey (0,8,12)). A tall
    column also comes with a staircase beside it (a lone column cannot be taken down from a neighbour)."""
    H, D, W = w.shape
    solid_f = _floors(w)
    out = []
    for dx, dy, dz in ((0, -1, 0), (1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1)):
        X, Y, Z = T[0] + dx, T[1] + dy, T[2] + dz
        if not (0 <= X < W and 0 <= Z < D and 0 <= Y < H) or w.design[Y, Z, X] or w.placed[Y, Z, X]:
            continue
        if w.area is not None and not w.area[Z, X]:
            continue
        b = Y
        while b > 0 and not (solid_f[b - 1, Z, X] or (X, b - 1, Z) in have) and not w.design[b - 1, Z, X]:
            b -= 1
        if b > 0 and w.design[b - 1, Z, X] and not solid_f[b - 1, Z, X]:
            continue                             # would stand on something that is not a floor
        if any(w.placed[y, Z, X] for y in range(b, Y + 1)):
            continue
        cells = [(X, y, Z) for y in range(b, Y + 1) if (X, y, Z) not in have]
        if not cells:
            continue
        out.append(cells)
        if Y - b >= 3:                          # a tall column also gets a staircase beside it, its top step level with the
            for ex, ez in ((1, 0), (-1, 0), (0, 1), (0, -1)):   # column top, so every block comes down from a
                F = (X + ex, Y + 1, Z + ez)       # neighbour (builder: never break the block she stands on)
                r = _staircase(w, F, (ex, ez), have | set(cells), turns=False)
                if r and r[0]:
                    out.append(cells + r[0])
    return sorted(out, key=len)


def plan(g: Design, gd: GameData, site: dict | None = None, interior: bool | None = None,
         seconds: float | None = None, report: dict | None = None) -> tuple[Design | None, list[dict]]:
    """Returns (scaffold design or None if none needed, blocks the simulation still cannot place). Greedy and not
    monotonic. O87h: the checked passes try only staircases whose stand is reachable when their block comes, and more
    of them when a round's are all rejected; the first ranks by dirt per failing block in reach (the tower), the
    second keeps the earlier order (h04 in a minute); what they leave, the earlier planner gets the rest of the clock
    (it planned medieval_house where they left one block), then straight staircases. The plan with the fewest blocks
    left is kept, with its own pass's budget reason. Each pass has its own work budget; the clock is shared, and the
    checked passes stop at CHECKED_SHARE of it so the earlier planner always gets the rest. O87y: the clock is
    `seconds`, else budget(g); `report` gets storeys, budget, seconds used and the O87i bar read against them."""
    t0 = time.monotonic()
    T = budget(g) if seconds is None else seconds
    work_cap = WORK * max(1.0, T / TIME) if TIME else WORK
    s, left, why = None, None, None
    for check, ranked, turns in ((True, True, True), (True, False, True), (False, False, True), (False, False, False)):
        if left is not None and not left:
            break
        end = t0 + T * (CHECKED_SHARE if check else 1)
        if left is not None and time.monotonic() >= end:
            continue                             # this pass's share of the clock is gone
        work = [0, t0, end, work_cap]
        s2, left2 = _plan(g, gd, site, turns=turns, work=work, check=check, ranked=ranked,
                          interior=INTERIOR if interior is None else interior)
        if left is None or len(left2) < len(left):
            s, left = s2, left2
            why = f"its work budget ({work_cap:g} simulated steps)" if work[0] >= work_cap else \
                f"its time budget ({T:g} s)" if time.monotonic() >= end else None
    left = left[:20]
    if report is not None:     # O87y: per design in the evidence; the O87i bar is "nothing left inside the budget"
        used = time.monotonic() - t0
        report.update(storeys=max((r.get("storey", 0) for r in g.rooms), default=0) + 1, budget_s=round(T, 1),
                      seconds=round(used, 1), left=len(left), stopped_at=why, o87i_bar_met=not left and used <= T)
    if left and why:
        left.insert(0, {"pos": None, "budget": True, "reason": f"scaffold planning stopped at {why}"})
    return s, left


def _over(work: list) -> bool:
    """work = [simulated steps spent, start, deadline, step cap]."""
    return work[0] >= (work[3] if len(work) > 3 else WORK) or time.monotonic() >= work[2]


def _plan(g: Design, gd: GameData, site: dict | None, turns: bool, work: list | None = None, check: bool = False,
          ranked: bool = False, interior: bool = True) -> tuple[Design | None, list[dict]]:
    """Returns (scaffold design or None if none needed, blocks the simulation still cannot place). One block at a time
    in build order (the first failure is usually what makes later ones fail); each accepted trial's simulation is the
    next round's state."""
    from .buildorder import order, plan_order
    c = classes(gd)
    P = World(g, gd, None).P
    sx, _, sz = g.size
    _, _, early = plan_order(g, gd, site=site)
    key = lambda f: (f["pos"]["x"], f["pos"]["y"], f["pos"]["z"])
    rooms = [] if interior else [(r["min"], r["max"]) for r in g.rooms]
    outside = lambda cells: not any(mn["x"] <= x - P <= mx["x"] and mn["y"] <= y <= mx["y"] and mn["z"] <= z - P <= mx["z"]
                                    for x, y, z in cells for mn, mx in rooms)   # O89: no dirt in a room

    now = time.monotonic()
    work = work if work is not None else [0, now, now + TIME, WORK]
    spent = lambda: _over(work)

    def run(cells):
        sd = _design(cells, g, gd, P, 0)
        st = order(g, gd, sd, early)
        work[0] += len(st)
        snaps: dict = {}
        fl, _ = simulate(g, gd, sd, st, snaps=snaps, site=site)
        if not fl and stale_stands(g, gd, sd, st, site):   # S93 (contained pool): a plan that only looks done, with
            snaps = {}                                     # stands she cannot get to or down from: replay re-checked,
            fl, _ = simulate(g, gd, sd, st, snaps=snaps, site=site, recheck=True)   # its misses are the planner's
        return sd, st, fl, snaps

    have: set = set()
    tried: dict = {}                             # block -> scaffold size when last tried (retried once it grew)
    n = 0
    s, steps, fails, snaps = run(have)
    while n < MAX_STAIRS and not spent():
        f = next((f for f in fails if f["kind"] == "place" and tried.get(key(f), -1) < len(have)), None)
        if f is None:
            break
        tried[key(f)] = len(have)
        T = (key(f)[0] + P, key(f)[1], key(f)[2] + P)
        w = World(g, gd, s, site=site)
        w.placed = snaps[key(f)]
        todo = np.array([[q["pos"]["x"] + P + 0.5, q["pos"]["y"] + 0.5, q["pos"]["z"] + P + 0.5] for q in fails]).reshape(-1, 3)
        failing = {key(q) for q in fails}
        more = (cl for i, cl in enumerate(_best(w, T, steps[f["step"] - 1]["state"], have, todo, c, P, sx, sz, turns,
                                                ranked))
                if outside(cl) and (not check or i < CHECKS and not spent() and _reachable(w, cl, T[1])))
        cands = list(islice(more, TRIES))
        cands += [cl for cl in _supports(w, T, have) if outside(cl)]   # nothing to stand at helps: something to place against
        for _ in range(1 + (MORE_TRIES if check else 0)):   # O87h: the tower's clean roof stand was the 7th candidate
            for cells in cands:
                if spent():
                    break
                r = run(have | set(cells))
                tk = {key(q) for q in r[2]}
                if len(tk) < len(failing):                        # fewer failures overall (new ones get their turn)
                    have = have | set(cells)
                    s, steps, fails, snaps = r
                    n += 1
                    break
            else:
                cands = list(islice(more, TRIES))   # all of them rejected: the next ones, while there are any
                if cands and not spent():
                    continue
            break
    for f in [f for f in fails if f["kind"] == "remove"]:   # a block that cannot be taken down: try without it
        X, Y, Z = f["pos"]["x"] + P, f["pos"]["y"], f["pos"]["z"] + P   # (and the dirt above it in its column)
        trial = {c for c in have if not (c[0] == X and c[2] == Z and c[1] >= Y)}
        if trial != have and not spent():
            r = run(trial)
            if len(r[2]) < len(fails):
                have = trial
                s, steps, fails, snaps = r
    s = _design(have, g, gd, P, n)
    _, fails, slots = plan_order(g, gd, s, early, site=site)
    for f in [f for f in fails if f["kind"] == "remove"]:   # the same, against the written order (its doors and late
        X, Y, Z = f["pos"]["x"] + P, f["pos"]["y"], f["pos"]["z"] + P   # blocks moved): O87h, medieval_house
        trial = {c for c in have if not (c[0] == X and c[2] == Z and c[1] >= Y)}
        if trial != have and not spent():
            s2 = _design(trial, g, gd, P, n)
            r = plan_order(g, gd, s2, early, site=site)
            if len(r[1]) < len(fails):
                have, s, (_, fails, slots) = trial, s2, r
    if s is not None:                            # build_order.json and the validator replay this same order
        s.meta["provenance"]["order_slots"] = [[*p, v] for p, v in sorted(slots.items(), key=lambda t: t[0])]
    left = [{"pos": f["pos"], "reason": "cannot be placed even with scaffold"} for f in fails]   # plan() keeps 20
    return s, left
