"""Placement simulation: can Eve's builder place every block of the build order, in order, against the world as it is
at that moment? Modelled 1:1 on eve-builder's Build.standable / stand / clickable (a52d11c, cabfe99, 56cd732):

- a standing spot (feet F) has a full sturdy top under it (full cubes, top/double slabs, upside-down stairs, scaffold,
  the ground; feet at F.y), or a bottom slab (F.y - 0.5) or bottom stair (either F.y - 0.5 or F.y: the click must work
  from both), F and F+1 have no collision now, and neither is the cell being placed (design cells not yet built
  are allowed: builder cabfe99); under the "walk_surfaces" policy (O94ag, the default for eve-organic designs) what
  the feet rest on must also be the ground, scaffold, or the design's own floor, foundation, stair or ground, never
  the design's roof, wall, trim, support, window or furniture tops (lane C C2: Baritone found no route to 64 of those);
- it must be reachable on foot from the ground around the plot (1-block steps up, drops of up to 3, no breaking, no
  placing, no pillaring), and a walk must lead back down from it (builder: Baritone refuses longer drops); doors and
  gates are walked through (Baritone opens them; builder re-closes them at the end), a door only on a level move:
  never a step up into it or a drop out of it (builder, lane C hut5_tall a12/a13);
- scaffold blocks are placed by the same rules, and removed top-down from spots with a way down (standing eye, the
  block hit first, never standing on it);
- feet y at most target y + 2; the eye is the feet's bottom centre + 1.27 (sneaking while placing);
- the clicked point is the centre of the support's face toward the target, 0.49 out from the support's centre; it is
  within 4.3 of the eye, the eye is in front of that face, the first block a ray from the eye hits is that support
  through that face, and for blocks whose facing comes from the look the horizontal look matches it;
- stairs, trapdoors and single slabs take their half from the click: only the face that gives the wanted half counts
  (a support's top face gives bottom, its underside top); on a side face any of 3 x 3 points inside the wanted half
  (builder 94d32ed, as the body's place_block searches the face) whose ray is clear from the stand centre and its
  +-0.35 corners (builder ed4da1e).
Every step records the feet cell used (`stand`, design-local) for build_order.json (contract Amendments 3); she
stays at her last stand while it still works, so walks between steps are few.
Reachability is recomputed at each layer, at each new foothold, when a placed block takes a move away, and when one
fills a walked cell whose neighbours no longer reach one another nearby (a door, the last gap of a wall); when they
still do and the finished plan has a stale stand, it is replayed dropping the spots that lost their way in or their
way down (O94ag-b: plan_order, stale_stands). She walks only on what Baritone
walks on (O94ag-b: full-collision blocks, stairs, slabs, chests, glass, leaves, farmland, paths).
"""
from __future__ import annotations

import math
import time
from collections import deque

import numpy as np

from .gamedata import GameData, parse_state
from .grid import AIR, ROLE, Design
from .validate import PAD, Grid, attachment, classes

POLICY = "unbuilt"   # where Eve may stand: "unbuilt" cells (proposed to builder) or "never_design" (builder a52d11c)
POLICIES = ("unbuilt", "never_design", "walk_surfaces")
WALK_ROLES = ("floor", "foundation", "stair", "ground")   # O94ag: the design's own walking surfaces


def organic(g: Design) -> bool:
    """An eve-organic design (its pipeline is eve-organic/N): sculpture and combined pieces."""
    return str(((g.meta or {}).get("provenance") or {}).get("pipeline", "")).startswith("eve-organic")


def stand_policy(g: Design) -> str:
    """O94ag: provenance.stand_policy when a design selects one (compile --stand-policy); otherwise "walk_surfaces" for
    an eve-organic design and POLICY for every other."""
    chosen = ((g.meta or {}).get("provenance") or {}).get("stand_policy")
    return chosen or ("walk_surfaces" if organic(g) else POLICY)
REACH = 4.3
EYE = 1.27            # sneaking while placing (builder 56cd732)
# O80h (CP-50 b29ba34, reproduced by body on dev): from low stands only a sliver of the support showed and the aim
# point sat on its edge; the exact ray cleared, but the game's crosshair lands within ~0.5 deg of the aim and hit air
# or the neighbour. A click counts only if rays turned by +-AIM_TOL about two axes across it still land on the same
# face (inside the wanted half) and hit that block first. Calibrated on the live results (scratchpad tol_sweep): at 0.5
# deg the replay still accepted the ground stands that failed (b29ba34 (4,4,2), (4,4,3)); 1.0 deg rejects every failed
# click and keeps pillar_tall's proven plan (108/108); it is stricter than the 25-dirt plan that passed (6c6a106 (4,4,2)
# and the west roof stairs), where the planner adds scaffold instead. ponytail: one scalar for the crosshair's error;
# per-distance calibration when more live misses are known.
AIM_TOL = math.radians(1.0)
# Builder lane C (CP-50 a30/a31, both body jars): a click on a support stair's side with ~1.6 deg to spare landed on that
# stair's FRONT (the crosshair on the support, another face), where the same click against a full block passed live. For a
# stair support, rays turned this far must not reach its front; a wider AIM_TOL instead rejected clicks that passed live.
AIM_TOL_SAME = math.radians(2.0)
# O68c (h06 on the 45L: 28 min on one core in _free < _ok < _moves, the walking searches): one replay may spend at most
# SIM_SECONDS and SIM_NODES walking-search expansions (a 1476-block walled farmhouse: ~5.3M in ~23 s); past either, the
# steps not yet tried are reported "no stand" with the budget as the reason, instead of searching on.
SIM_SECONDS = 240.0
SIM_NODES = 40_000_000
EYE_BREAK = 1.62      # standing, for breaking (scaffold removal) and using
# O193 (builder Build.clickableAround, nether_portal v2's corner): a click counts only if it works, reach included,
# from the stand centre and 4 landings 0.35 off along x and z; designs opt in with provenance.click_eyes = 5
EYES5 = ((0, 0), (0.35, 0), (-0.35, 0), (0, 0.35), (0, -0.35))
REACH5 = 4.2   # builder's hand check of v3: 4.30 from one landing is no slack; 0.1 in hand for where she lands
SWAY = ((0, 0), (0.35, 0.35), (0.35, -0.35), (-0.35, 0.35), (-0.35, -0.35))   # walk_to ends within +-0.35 of the stand
#        centre; body's mine_block aims at the block centre: the break must be seen from all of these (builder a4)
OFFS = {0: (0.0,), 1: (-0.5,), 2: (-0.5, 0.0)}   # feet on a sturdy top / bottom slab / bottom stair (either step)
FALL = 3
DIRS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))   # (dx, dy, dz)
HSIDE = {(1, 0): "east", (-1, 0): "west", (0, 1): "south", (0, -1): "north"}
OPP = {"east": "west", "west": "east", "north": "south", "south": "north"}
FACING_V = {v: k for k, v in HSIDE.items()}   # "east" -> (1, 0)
LOOK = ("_stairs", "_door", "_fence_gate", "_bed")                     # facing = where Eve looks
LOOK_BACK = ("chest", "furnace", "smoker", "barrel", "lectern", "carved_pumpkin", "jack_o_lantern", "beehive", "loom")
NOT_CUBE = ("pane", "bars", "lantern", "chain", "torch", "rod", "pot", "head", "skull", "cake", "carpet", "anvil",
            "chest", "lectern", "bell", "stonecutter", "grindstone", "brewing", "cauldron", "hopper", "composter",
            "dripstone", "amethyst_cluster", "bud", "kelp", "coral", "scaffolding", "button", "lever", "ladder", "sign",
            "banner", "bed", "candle", "campfire", "rail", "pressure_plate", "snow", "farmland", "dirt_path", "conduit")
# O94ag-b (builder's O94ah stand list): cp72's 18 loft stands were reached over a lantern, a bed and a glass pane. She
# walks only on what her Baritone walks on (MovementHelper.canWalkOn, read from the body's jar by builder; builder's
# run-time Walk uses the same predicate): full-collision blocks but magma, honey, bamboo stalks and shulker boxes;
# stairs, slabs, chests, glass, leaves, farmland, paths and soul sand. Never panes, bars, lanterns, beds, trapdoors,
# candles, pots, fences or other partial shapes. ponytail: ladders stay passable (no climbing modelled), no water.
# The name tests below cannot see collision shapes (the game data has none), so the exceptions are listed: every 26.3
# block was checked (review of O94ag-b) against isShapeFullBlock.
WALK_ON = {"chest", "trapped_chest", "ender_chest", "farmland", "dirt_path", "bedrock", "sea_lantern", "jack_o_lantern",
           "budding_amethyst", "azalea", "flowering_azalea"}
NO_WALK = {"magma_block", "honey_block", "bamboo", "bamboo_sapling", "bubble_column", "water", "lava", "mud", "cactus",
           "chorus_plant", "cocoa", "comparator", "repeater", "daylight_detector", "dragon_egg", "enchanting_table",
           "end_gateway", "end_portal", "end_portal_frame", "heavy_core", "lily_pad", "nether_wart", "sea_pickle",
           "sniffer_egg", "turtle_egg", "frogspawn", "sugar_cane", "sweet_berry_bush", "tripwire_hook", "big_dripleaf",
           "small_dripleaf", "firefly_bush", "pale_hanging_moss", "shelf_mushroom", "brown_mushroom", "red_mushroom",
           "crimson_fungus", "warped_fungus", "sculk_sensor", "calibrated_sculk_sensor", "sculk_shrieker", "sculk_vein",
           "sulfur_spike"}
NO_WALK_ENDS = ("shulker_box", "_shelf", "golem_statue")
# S93 (validate case valid-contained-pool): fluids are no floor, but she stands and swims in them and clicks through
# them (builder's ray: ClipContext fluid NONE); the replay had water solid, a floor to stand on and a wall to rays
FLUIDS = {"minecraft:water", "minecraft:lava", "minecraft:bubble_column"}


def _walk_on(bid: str, c) -> bool:
    n = bid.split(":")[1]
    if n in NO_WALK or n.endswith(NO_WALK_ENDS):
        return False
    if bid in c.stairs or bid.endswith("_slab") or bid in c.leaves or bid in c.impermeable or n in WALK_ON:
        return True
    return not (bid in c.passable or bid in c.partial) and (n.endswith("_block") or not any(k in n for k in NOT_CUBE))


def _sturdy_top(state: str, c) -> bool:
    bid, p = parse_state(state)
    if bid in FLUIDS:
        return False
    n = bid.split(":")[1]
    if bid.endswith("_slab"):
        return p.get("type") in ("top", "double")
    if bid.endswith("_stairs"):
        return p.get("half") == "top"
    if bid in c.passable or bid in c.partial or any(k in n for k in NOT_CUBE):
        return False
    return True


class World:
    """Per-cell classes of the final design + scaffold (padded [y, z, x] like validate.Grid) and what is placed now."""

    def __init__(self, g: Design, gd: GameData, scaffold: Design | None, policy: str | None = None,
                 site: dict | None = None):
        c = classes(gd)
        self.policy = policy or stand_policy(g)
        if self.policy not in POLICIES:
            raise ValueError(f"unknown stand policy {self.policy!r} (one of {', '.join(POLICIES)})")
        G = Grid(g, gd)
        self.P, self.shape, self.g, self.c = PAD, G.shape, g, c
        self.nodes = 0                           # walking-search expansions (O68c budget)
        H, D, W = self.shape
        pal = g.palette
        ids = [parse_state(s)[0] for s in pal]
        lut = lambda f: np.array([f(i, s) for i, s in zip(ids, pal)], bool)
        pad = lambda a: _pad(a, self.shape, self.P)
        self.design = pad(g.blocks != 0)
        # Baritone opens doors and gates as it walks (builder re-closes them at the end): they never block the way
        self.body_f = pad(lut(lambda b, s: s != AIR and b not in c.passable and b not in FLUIDS)[g.blocks])   # collision for feet/head
        self.walk_f = pad(lut(lambda b, s: s != AIR and _walk_on(b, c))[g.blocks])          # O94ag-b
        self.sturdy_f = pad(lut(lambda b, s: s != AIR and _sturdy_top(s, c))[g.blocks])
        self.ray_f = pad(lut(lambda b, s: s != AIR and b not in FLUIDS and (b not in c.passable or b in c.doors))[g.blocks])  # stops the ray
        self.halfstep = pad(lut(lambda b, s: b in c.stairs or (b.endswith("_slab") and "type=bottom" in s))[g.blocks])
        self.slab_b = pad(lut(lambda b, s: b.endswith("_slab") and "type=bottom" in s)[g.blocks])
        self.stair_b = pad(lut(lambda b, s: b in c.stairs and "half=bottom" in s)[g.blocks])
        # builder, lane C (hut5_tall a12/a13): Baritone opens a door only on a level move; it never steps up into a door
        # cell or drops out of one (the room on its foundation was closed to her)
        self.door_b = pad(lut(lambda b, s: b in c.two_high_doors)[g.blocks])
        self.eyes5 = ((g.meta or {}).get("provenance") or {}).get("click_eyes") == 5      # O193
        self.walk_surface = pad(np.isin(g.roles, [ROLE[r] for r in WALK_ROLES]) & (g.blocks != 0))   # O94ag
        self.scaf = np.zeros(self.shape, bool)
        if scaffold is not None:
            off = [scaffold.origin[i] - g.origin[i] for i in range(3)]
            for x, y, z, s, _ in scaffold.cells():
                X, Y, Z = x + off[0] + self.P, y + off[1], z + off[2] + self.P
                if 0 <= Y < H and 0 <= Z < D and 0 <= X < W:
                    self.scaf[Y, Z, X] = True
        for a in (self.body_f, self.walk_f, self.sturdy_f, self.ray_f):
            a |= self.scaf
        self.placed = np.zeros(self.shape, bool)
        self.area = None                         # site: where Eve may walk and scaffold may go (the plot); blocks there
        if site:
            H, D, W = self.shape
            ox, oy, oz = g.origin
            plot = site.get("plot")
            if plot:
                xs = np.arange(W) - self.P + ox
                zs = np.arange(D) - self.P + oz
                self.area = ((zs[:, None] >= plot["min"]["z"]) & (zs[:, None] <= plot["max"]["z"])
                             & (xs[None, :] >= plot["min"]["x"]) & (xs[None, :] <= plot["max"]["x"]))
            for x, y, z in site.get("obstacles", []):   # blocks already in the world (a chest, a kit)
                X, Y, Z = x - ox + self.P, y - oy, z - oz + self.P
                if 0 <= Y < H and 0 <= Z < D and 0 <= X < W:
                    for a in (self.body_f, self.walk_f, self.ray_f, self.placed):
                        a[Y, Z, X] = True

    def state_at(self, X, Y, Z) -> str | None:
        """The design block at grid cell (X, Y, Z); None for scaffold dirt or a world block (full cubes)."""
        x, z = X - self.P, Z - self.P
        sx, sy, sz = self.g.size
        if self.scaf[Y, Z, X] or not (0 <= x < sx and 0 <= Y < sy and 0 <= z < sz):
            return None
        return self.g.get(x, Y, z)

    def put(self, X, Y, Z):
        self.placed[Y, Z, X] = True

    def now(self, a):
        return a & self.placed

    # --- standing ---------------------------------------------------------------------------------------------
    def _ok(self, X, Y, Z) -> bool:
        H, D, W = self.shape
        if not (0 <= Z < D and 0 <= X < W and 0 <= Y < H - 1):
            return False
        if self.area is not None and not self.area[Z, X]:
            return False                         # off the plot (a void, a neighbour's land)
        return self._free(X, Y, Z) and self._free(X, Y + 1, Z) and (Y == 0 or (self.placed[Y - 1, Z, X] and self.walk_f[Y - 1, Z, X]))

    def _free(self, X, Y, Z) -> bool:
        return Y >= self.shape[0] or not (self.placed[Y, Z, X] and self.body_f[Y, Z, X])

    def walk(self, seeds=None, seen=None, adj: dict | None = None) -> set:
        """Feet cells reachable on foot from the ground ring (or from extra seeds, extending `seen` in place). `adj`, if
        given, receives each expanded cell's moves (escape() reuses them instead of searching every cell again)."""
        H, D, W = self.shape
        moves, ok = self._mover()
        if seeds is None:
            edge = lambda X, Z: X in (0, W - 1) or Z in (0, D - 1) if self.area is None else self.area[Z, X]
            seeds = [(X, 0, Z) for Z in range(D) for X in range(W) if edge(X, Z) and ok(X, 0, Z)]   # she arrives on the plot
        seen = set() if seen is None else seen
        seeds = [s for s in seeds if s not in seen]
        seen.update(seeds)
        q = deque(seeds)
        while q:
            c = q.popleft()
            ms = moves(*c)
            if adj is not None:
                adj[c] = ms
            for n in ms:
                if n not in seen:
                    seen.add(n)
                    q.append(n)
        return seen

    def _mover(self):
        """(moves, ok) over a flat snapshot of the world as it stands: a walking search only reads it, and numpy scalar
        indexing per cell was the replay's hot spot (O87h profile: 93.5M moves, 80 % of scaffold planning). The same
        moves, in the same order, as the cell-by-cell rules (_ok, _free, _step) give."""
        H, D, W = self.shape
        blk = self.placed & self.body_f
        okm = np.zeros(self.shape, bool)                         # feet fit here: _ok for every cell at once
        okm[:H - 1] = ~blk[:H - 1] & ~blk[1:]
        okm[1:H - 1] &= (self.placed & self.walk_f)[:H - 2]
        if self.area is not None:
            okm &= self.area[None]
        blocked, okb = blk.tobytes(), okm.tobytes()              # index (Y * D + Z) * W + X
        step = (self.placed & self.halfstep).tobytes()
        door = (self.placed & self.door_b).tobytes()
        DW, FALL_ = D * W, FALL
        dirs = ((1, 0), (-1, 0), (0, 1), (0, -1))

        def ok(X, Y, Z):
            return 0 <= Z < D and 0 <= X < W and 0 <= Y < H and okb[Y * DW + Z * W + X]

        def moves(X, Y, Z):
            """Feet cells one walking move away (a step up, level, or a drop of up to FALL), for a feet cell."""
            self.nodes += 1
            out = []
            i = Y * DW + Z * W + X
            up = Y + 2 >= H or not blocked[i + 2 * DW]                    # head room for a jump
            down_here = Y >= 1 and step[i - DW]                           # standing on a stair
            in_door = door[i]                                             # in a doorway: level moves only
            for dx, dz in dirs:
                nx, nz = X + dx, Z + dz
                if not (0 <= nz < D and 0 <= nx < W):
                    continue
                j = Y * DW + nz * W + nx
                if (up or step[j]) and Y + 1 < H and okb[j + DW] and not in_door and not door[j + DW]:
                    out.append((nx, Y + 1, nz))                           # one up: a jump, or walked up a stair
                if okb[j]:
                    out.append((nx, Y, nz))
                if blocked[j] or in_door:
                    continue                                              # nothing to step off into
                head = Y + 1 >= H or not blocked[j + DW]
                stair_down = down_here or (Y >= 2 and step[j - 2 * DW])  # walked down a stair, no head room needed
                for d in range(1, FALL_ + 1):                             # step off, fall through free cells
                    if Y - d < 0 or blocked[j - (d - 1) * DW] or not (head or (d == 1 and stair_down)):
                        break
                    if okb[j - d * DW] and not door[j - d * DW]:
                        out.append((nx, Y - d, nz))
            return out
        return moves, ok

    def still_joined(self, gone: list, feet: set, limit: int = 400) -> bool:
        """After removing feet cells `gone`: do their former walked neighbours still reach one another? (A local
        search; False sends the caller back to a full walk.)"""
        nb = {m for g in gone for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)) for dy in range(-FALL, FALL + 1)
              if (m := (g[0] + dx, g[1] + dy, g[2] + dz)) in feet}
        if len(nb) <= 1:
            return True
        moves = self._mover()[0]
        start = next(iter(nb))
        seen, q = {start}, deque([start])
        while q and len(seen) < limit:
            for n in moves(*q.popleft()):
                if n in feet and n not in seen:
                    seen.add(n)
                    q.append(n)
            if nb <= seen:
                return True
        return nb <= seen

    def escape(self, feet: set, adj: dict | None = None) -> set:
        """The walked cells from which a walk leads back down to the ground (drops of at most FALL). `adj`: the moves
        walk() just found for these cells (the world unchanged since)."""
        rev: dict = {}
        moves = None
        for n in feet:
            if adj is not None and n in adj:
                ms = adj[n]
                self.nodes += 1                  # counted as the search it replaces (the SIM_NODES budget)
            else:
                moves = moves or self._mover()[0]
                ms = moves(*n)
            for m in ms:
                if m in feet:
                    rev.setdefault(m, []).append(n)
        out = {n for n in feet if n[1] == 0}
        q = deque(out)
        while q:
            for n in rev.get(q.popleft(), ()):
                if n not in out:
                    out.add(n)
                    q.append(n)
        return out

    def _step(self, X, Y, Z) -> bool:
        """A placed stair or bottom slab at (X, Y, Z): walked onto without jumping."""
        return 0 <= Y < self.shape[0] and self.placed[Y, Z, X] and self.halfstep[Y, Z, X]

    def joins(self, n, seen) -> bool:
        """Can feet cell n (newly standable) be stepped onto from a walked neighbour?"""
        X, Y, Z = n
        if not self._ok(*n):
            return False
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            m = (X + dx, Y - 1, Z + dz)
            if m in seen and (self._free(m[0], Y + 1, m[2]) or self._step(X, Y - 1, Z)):   # up a block or a stair
                return True
            for k in range(0, FALL + 1):
                m = (X + dx, Y + k, Z + dz)
                if m in seen and all(self._free(X, Y + j, Z) for j in range(1, k + 2)):
                    return True
        return False

    def spots(self, feet: set) -> np.ndarray:
        """Rows (X, Y, Z, kind): kind 0 feet on a sturdy top (or the ground), 1 on a bottom slab, 2 on a bottom stair."""
        sturdy, slab, stair = self.now(self.sturdy_f), self.now(self.slab_b), self.now(self.stair_b)
        H = self.shape[0]
        avoid = self.design if self.policy == "never_design" else np.zeros(self.shape, bool)
        surface = self.scaf | self.walk_surface if self.policy == "walk_surfaces" else None   # O94ag
        out = []
        for X, Y, Z in feet:
            if avoid[Y, Z, X] or (Y + 1 < H and avoid[Y + 1, Z, X]):
                continue
            if surface is not None and Y > 0 and not surface[Y - 1, Z, X]:
                continue                         # on the design's roof, wall, trim, support ... top: not a stand
            k = 0 if Y == 0 or sturdy[Y - 1, Z, X] else 1 if slab[Y - 1, Z, X] else 2 if stair[Y - 1, Z, X] else None
            if k is not None:
                out.append((X, Y, Z, k))
        return np.array(out, float).reshape(-1, 4)

    # --- clicking -----------------------------------------------------------------------------------------------
    def first_block(self, e, q, length: float):
        """(cell, outward normal of the face entered, t) of the first placed ray-stopping block along e + t*q, t <= length
        (q a unit vector); None if nothing within it (the ground counts as nothing here)."""
        H, D, W = self.shape
        cell = [math.floor(e[0]), math.floor(e[1]), math.floor(e[2])]
        step = [1 if q[i] > 0 else -1 for i in range(3)]
        tmax = [((cell[i] + (step[i] > 0)) - e[i]) / q[i] if q[i] else math.inf for i in range(3)]
        tdel = [abs(1 / q[i]) if q[i] else math.inf for i in range(3)]
        while True:
            i = min(range(3), key=lambda k: tmax[k])
            t = tmax[i]
            if t > length:
                return None
            tmax[i] += tdel[i]
            cell[i] += step[i]
            X, Y, Z = cell
            if Y < 0:
                return None
            if 0 <= Y < H and 0 <= Z < D and 0 <= X < W and self.ray_f[Y, Z, X] and self.placed[Y, Z, X]:
                face = [0, 0, 0]
                face[i] = -step[i]
                return (X, Y, Z), tuple(face), t

    def ray_first(self, e, p, n_cell, n_face) -> bool:
        """True if the ray from eye e to point p first enters a blocking cell at n_cell through face n_face."""
        H, D, W = self.shape
        ray = self.ray_f
        cell = [math.floor(e[0]), math.floor(e[1]), math.floor(e[2])]
        d = [p[i] - e[i] for i in range(3)]
        step = [1 if d[i] > 0 else -1 for i in range(3)]
        tmax, tdel = [], []
        for i in range(3):
            if d[i] == 0:
                tmax.append(math.inf)
                tdel.append(math.inf)
            else:
                nb = cell[i] + (1 if d[i] > 0 else 0)
                tmax.append((nb - e[i]) / d[i])
                tdel.append(abs(1 / d[i]))
        for _ in range(64):
            i = min(range(3), key=lambda k: tmax[k])
            if tmax[i] > 1.0001:
                return False
            tmax[i] += tdel[i]
            cell[i] += step[i]
            X, Y, Z = cell
            face = [0, 0, 0]
            face[i] = -step[i]                                              # outward normal of the entered face
            if (X, Y, Z) == n_cell:
                return n_face is None or tuple(face) == n_face
            if Y < 0:
                return False                                                # the ground
            if 0 <= Y < H and 0 <= Z < D and 0 <= X < W and ray[Y, Z, X] and self.placed[Y, Z, X]:
                return False
        return False


def _pad(a, shape, P):
    out = np.zeros(shape, bool)
    out[:a.shape[0], P:P + a.shape[1], P:P + a.shape[2]] = a
    return out


def _faces(state: str, c):
    bid, p = parse_state(state)
    a = attachment(bid, p, c)
    if a:
        return [a[0]]
    if "axis" in p:
        k = "xyz".index(p["axis"])
        return [d for d in DIRS if d[k] != 0]
    return list(DIRS)


def _look_rule(state: str):
    bid, p = parse_state(state)
    f = p.get("facing")
    if f not in OPP:
        return None
    n = bid.split(":")[1]
    if any(n.endswith(k) for k in LOOK):
        return f
    if any(k in n for k in LOOK_BACK):
        return OPP[f]
    return None


def _order(w: World, xyz: np.ndarray, dist: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Candidate spots outside the footprint first, then nearest."""
    sx, _, sz = w.g.size
    inside = (xyz[idx, 0] >= w.P) & (xyz[idx, 0] < w.P + sx) & (xyz[idx, 2] >= w.P) & (xyz[idx, 2] < w.P + sz)
    return idx[np.lexsort((dist[idx], inside))]


def _half(state: str) -> str | None:
    """'bottom' | 'top' for states whose half comes from where the click lands (stairs, trapdoors, single slabs)."""
    bid, p = parse_state(state)
    if bid.endswith("_slab"):
        return p.get("type") if p.get("type") in ("bottom", "top") else None
    if bid.endswith("_stairs") or bid.endswith("_trapdoor"):
        return p.get("half") if p.get("half") in ("bottom", "top") else None
    return None


def _on_outline(state: str | None, n, u) -> bool:
    """Builder, lane C (CP-50 a30, hut5_sim): is point u (block-local, on a side face whose outward normal is n) on
    block `state`'s outline? A single slab covers only its half of a side face; a straight stair covers its full half
    and, in the other half, all of its back, the facing half of its two sides and none of its front. A click into the
    missing part goes on into the block and lands on another of its faces. Top and bottom faces are unchanged."""
    if state is None or n[1] != 0:
        return True
    bid, p = parse_state(state)
    if bid.endswith("_slab"):
        t = p.get("type")
        return t not in ("bottom", "top") or (u[1] < 0.5 if t == "bottom" else u[1] > 0.5)
    if not bid.endswith("_stairs"):
        return True
    if (u[1] < 0.5) == (p.get("half") != "top"):
        return True                                  # its full half
    fx, fz = FACING_V.get(p.get("facing"), (0, 0))
    if p.get("shape", "straight") != "straight" or (fx, fz) == (0, 0):
        return False                                 # ponytail: a corner stair counts its full half only
    if (n[0], n[2]) == (fx, fz):
        return True                                  # its back: the raised half reaches it
    if (n[0], n[2]) == (-fx, -fz):
        return False                                 # its front: the raised half is set back
    k, sgn = (0, fx) if fx else (2, fz)
    return u[k] > 0.5 if sgn > 0 else u[k] < 0.5


def _front(state: str | None):
    """The outward normal of a stair's front (the side its step faces, away from its facing); None for anything else."""
    if state is None:
        return None
    bid, p = parse_state(state)
    if not bid.endswith("_stairs") or p.get("facing") not in FACING_V:
        return None
    fx, fz = FACING_V[p["facing"]]
    return (-fx, 0, -fz)


def _aims(N, d, half, state: str | None = None, eyes5: bool = False) -> list:
    """The points she may click on support N's face toward the target (d = target -> N), 0.49 out from N's centre;
    [] if that face cannot give `half`. Vanilla: a support's top face gives bottom, its underside top, a side face the
    half the hit lands in. The body's place_block v2 searches a 5x5 grid on the face and clicks the first point that
    gives the state (O80h: the centre alone grazed a neighbour where the body found a clean point), so any grid point
    may be used, centre first: offsets 0, +-0.2, +-0.4 in both face axes; a half-sensitive side click keeps to its half
    (0.1/0.25/0.4 from the centre, as builder 94d32ed checks). `eyes5` (O193, builder Build.java): the face centre
    alone, or for a half-sensitive side click u in (0, +-0.35) along the face."""
    if half and d[1] != 0 and (d[1] > 0) != (half == "top"):
        return []
    c = np.array([N[0] + 0.5 - 0.49 * d[0], N[1] + 0.5 - 0.49 * d[1], N[2] + 0.5 - 0.49 * d[2]])
    if d[1] != 0:                                # a top or bottom face: its centre (the sliver views that need the
        return [c]                               # grid and the aim margin were side faces; scope as the evidence)
    t1, t2 = np.array([abs(d[2]), 0.0, abs(d[0])]), np.array([0.0, 1.0, 0.0])   # the side face's two in-plane axes
    cols = (0.0, 0.35, -0.35) if eyes5 else sorted((0.0, 0.2, -0.2, 0.4, -0.4), key=abs)
    if eyes5 and not half:
        cols = (0.0,)
    if half and d[1] == 0:
        sign = -1 if half == "bottom" else 1
        pts = [c + t1 * u + t2 * (sign * h) for h in (0.25, 0.1, 0.4) for u in cols]
    else:
        pts = sorted((c + t1 * u + t2 * v for u in cols for v in cols), key=lambda q: float(np.abs(q - c).sum()))
    n = (-d[0], -d[1], -d[2])
    return [q for q in pts if _on_outline(state, n, q - np.array(N, float))]


def _steady(w: World, e, p, N, d, half, state: str | None = None) -> bool:
    """O80h: rays from eye e turned by +-AIM_TOL about two axes across the aim at p still land on support N's face
    toward the target (d = target -> N), inside the wanted half on a side face, within reach, and hit N first."""
    r = p - e
    r = r / np.linalg.norm(r)
    a = np.cross(r, (0.0, 1.0, 0.0) if abs(r[1]) < 0.99 else (1.0, 0.0, 0.0))
    a = a / np.linalg.norm(a)
    b = np.cross(r, a)
    n = -np.array(d, float)                                   # the face's outward normal, toward the eye
    f0 = np.array(N, float) + 0.5 + 0.5 * n                  # a point on the face plane
    cos_, sin_ = math.cos(AIM_TOL), math.sin(AIM_TOL)
    for axis in (a, b):
        for s in (1.0, -1.0):
            q = cos_ * r + s * sin_ * axis
            den = float(q @ n)
            if den >= -1e-9:
                return False
            t = float((f0 - e) @ n) / den
            h = e + t * q
            if t > REACH or any(n[i] == 0 and not (N[i] - 1e-6 <= h[i] <= N[i] + 1 + 1e-6) for i in range(3)):
                return False                                  # off the face: air or a neighbour
            if half and d[1] == 0 and (h[1] >= N[1] + 0.5 if half == "bottom" else h[1] <= N[1] + 0.5):
                return False                                  # the other half: the wrong state
            if not _on_outline(state, n, h - np.array(N, float)):
                return False                                  # into a stair's or slab's missing part: another face
            if not w.ray_first(e, h - 0.01 * n, N, (-d[0], -d[1], -d[2])):
                return False
    front = _front(state)
    if front is None or front == (-d[0], -d[1], -d[2]):
        return True                                           # not a stair, or its front is the clicked face
    cos2, sin2 = math.cos(AIM_TOL_SAME), math.sin(AIM_TOL_SAME)
    for axis in (a, b):                                       # wider: never onto the support stair's front
        for s in (1.0, -1.0):
            q = cos2 * r + s * sin2 * axis
            hit = w.first_block(e, q, REACH + 1.0)
            if hit is not None and hit[0] == tuple(N) and hit[1] == front:
                return False
    return True


def can_place(w: World, T, state: str, spots: np.ndarray, c):
    """The spot (X, Y, Z) from which a face to place against is in reach, in front, looked at along the facing and hit
    first, from every feet height the spot may leave her at; None if there is none. A half-sensitive side click must
    be clear into its half from the stand centre and its +-0.35 corners (SWAY; the ray only), as builder checks it."""
    X, Y, Z = T
    if not len(spots):
        return None
    look, half = _look_rule(state), _half(state)
    xyz, kind = spots[:, :3], spots[:, 3].astype(int)
    eyes = xyz + np.array([0.5, EYE, 0.5])
    ok_y = xyz[:, 1] <= Y + 2
    for d in _faces(state, c):
        N = (X + d[0], Y + d[1], Z + d[2])
        if N[1] < 0:
            if d != (0, -1, 0):
                continue
        elif not (0 <= N[2] < w.shape[1] and 0 <= N[0] < w.shape[2] and N[1] < w.shape[0]
                  and w.placed[N[1], N[2], N[0]] and w.ray_f[N[1], N[2], N[0]]):
            continue
        ns = w.state_at(*N) if N[1] >= 0 else None
        pts = _aims(N, d, half, ns, w.eyes5)
        if not pts:
            continue
        p0 = pts[0]
        sway = EYES5 if w.eyes5 else SWAY if half and d[1] == 0 else ((0, 0),)
        dist = np.sqrt(((eyes - p0) ** 2).sum(1))
        cand = np.nonzero(ok_y & (dist <= REACH + 0.6))[0]

        def sees(e, p, at):
            """From the stand eye e: reach, front and look; from `at` (e or one of its sway corners, where she may end
            up and searches the face from, as the body does): the ray and its steadiness (O80h)."""
            v = (at if w.eyes5 else e) - p                                  # O193: reach from each landing
            if (v * v).sum() > (REACH5 if w.eyes5 else REACH) ** 2 or -(v[0] * d[0] + v[1] * d[1] + v[2] * d[2]) <= 0.01:
                return False
            if look:
                hx, hz = p[0] - e[0], p[2] - e[2]
                if abs(hx) < 1e-6 and abs(hz) < 1e-6:
                    return False                                            # straight down: no horizontal look
                side = HSIDE[(int(np.sign(hx)), 0)] if abs(hx) >= abs(hz) else HSIDE[(0, int(np.sign(hz)))]
                if side != look:
                    return False
            return w.ray_first(at, p, N, (-d[0], -d[1], -d[2])) and (d[1] != 0 or _steady(w, at, p, N, d, half, ns))
        # From every feet height and every place in the stand she may end up (a half: its corners too; she aims from
        # wherever she stands), some point is clear AND steady. ponytail: conservative (it rejected one stand that
        # worked live, 6c6a106 (4,4,2)); a lane run lost to a missed click costs more than scaffold.
        for i in _order(w, xyz, dist, cand):
            ok = True
            for o in OFFS[kind[i]]:
                e = eyes[i] + np.array([0.0, o, 0.0])
                if not all(any(sees(e, p, e + np.array([dx, 0.0, dz])) for p in pts) for dx, dz in sway):
                    ok = False
                    break
            if ok:
                return tuple(int(v) for v in xyz[i])
    return None


def can_break(w: World, T, spots: np.ndarray):
    """The spot from which block T's centre is within reach (standing eye) and hit first, wherever in the stand she
    ends up (SWAY); never on top of it or in it."""
    if not len(spots):
        return None
    xyz, kind = spots[:, :3], spots[:, 3].astype(int)
    keep = ~((xyz[:, 0] == T[0]) & (xyz[:, 2] == T[2]) & (xyz[:, 1] >= T[1]) & (xyz[:, 1] <= T[1] + 1))
    eyes = xyz + np.array([0.5, EYE_BREAK, 0.5])
    p = np.array([T[0] + 0.5, T[1] + 0.5, T[2] + 0.5])
    dist = np.sqrt(((eyes - p) ** 2).sum(1))
    for i in _order(w, xyz, dist, np.nonzero(keep & (dist <= REACH - 0.5))[0]):
        if all(w.ray_first(eyes[i] + np.array([dx, o, dz]), p, T, None) for o in OFFS[kind[i]] for dx, dz in SWAY):
            return tuple(int(v) for v in xyz[i])
    return None


def simulate(g: Design, gd: GameData, scaffold: Design | None = None, steps: list | None = None,
             stop_at_first: bool = False, policy: str | None = None, ignore: frozenset = frozenset(),
             snaps: dict | None = None, site: dict | None = None, recheck: bool = False) -> tuple[list[dict], World]:
    """Run the build order, scaffold placement and removal included; returns (failures, world). `recheck` (O94ag-b):
    see the walked-cell branch below; plan_order turns it on only for a plan stale_stands finds stale. Each step that works
    gets `stand` (the design-local feet cell used) and `interior` (that cell is inside the footprint); a failing step is
    recorded (kind place / scaffold / remove) and then taken as done."""
    from .buildorder import order
    c = classes(gd)
    steps = steps if steps is not None else order(g, gd, scaffold)
    w = World(g, gd, scaffold, policy, site)
    P = w.P
    sx, _, sz = g.size
    fails, layer, feet, spots, cut = [], None, set(), None, set()

    adj: dict = {}

    def fresh():
        adj.clear()
        f = w.walk(adj=adj)
        return f, w.spots(f & w.escape(f, adj))   # standing spots she can also walk back down from

    last = [None]                               # the stand she is at: stay there while it still works

    def here(spots):
        """Just the current stand, if it is still a spot (fewest walks; builder pillar_tall: a walk the replay
        accepted, Baritone did not)."""
        if last[0] is None or not len(spots):
            return spots[:0]
        m = (spots[:, 0] == last[0][0]) & (spots[:, 1] == last[0][1]) & (spots[:, 2] == last[0][2])
        return spots[m]

    def note(st, spot):
        if spot:
            last[0] = spot
        st["stand"] = [spot[0] - P, spot[1], spot[2] - P] if spot else None
        st["interior"] = True if spot and P <= spot[0] < P + sx and P <= spot[2] < P + sz else None

    t0, n0 = time.monotonic(), w.nodes
    for i, st in enumerate(steps):
        over = "time" if time.monotonic() - t0 > SIM_SECONDS else "node" if w.nodes - n0 > SIM_NODES else None
        if over:                                  # O68c: bounded; what was not tried is reported, with the reason
            why = f"placement replay stopped at its {over} budget " + (
                f"({SIM_SECONDS:g} s)" if over == "time" else f"({SIM_NODES} walking-search steps)")
            for r in steps[i:]:
                r["stand"], r["interior"] = None, None
                fails.append({"pos": dict(zip("xyz", r["pos"])), "step": r["step"], "budget": True, "reason": why,
                              "kind": "remove" if r["layer"] == "scaffold_removal" else
                              "scaffold" if r["layer"] == "scaffold" else "place"})
            return fails, w
        x, y, z = st["pos"]
        T = (x + P, y, z + P)
        if st["layer"] == "scaffold_removal":
            feet, spots = fresh()                 # every removal changes the way down: walk again
            spot = can_break(w, T, here(spots)) or can_break(w, T, spots)
            note(st, spot)
            if spot is None:
                fails.append({"pos": {"x": x, "y": y, "z": z}, "step": st["step"], "kind": "remove",
                              "reason": "scaffold block cannot be broken from a spot with a way back down"})
            w.placed[T[1], T[2], T[0]] = False
            continue
        if y != layer or spots is None:
            layer = y
            feet, spots = fresh()
            cut = {(X, Y + k, Z) for X, Y, Z in feet for k in (0, 1)}
        sp = spots[~((spots[:, 0] == T[0]) & (spots[:, 2] == T[2]) & ((spots[:, 1] == T[1]) | (spots[:, 1] == T[1] - 1)))]
        scaf = st["layer"] == "scaffold"
        state = "minecraft:dirt" if scaf else st["state"]
        spot = can_place(w, T, state, here(sp), c) or can_place(w, T, state, sp, c)
        note(st, spot)
        if spot is None:
            look = None if scaf else _look_rule(st["state"])
            fails.append({"pos": {"x": x, "y": y, "z": z}, "step": st["step"], "kind": "scaffold" if scaf else "place",
                          "reason": "no reachable standing spot with a way back down sees a face to place it against "
                                    "within reach" + (f" while looking {look}" if look else "")})
            if snaps is not None and (x, y, z) not in ignore:
                snaps[(x, y, z)] = w.placed.copy()                          # the world just before this block
            if stop_at_first and (x, y, z) not in ignore:
                return fails, w
        w.put(*T)
        for a in st["also_appears"]:
            w.put(a[0] + P, a[1], a[2] + P)
        filled = [T] + [(a[0] + P, a[1], a[2] + P) for a in st["also_appears"]]
        if any(q in cut for q in filled):           # a walked cell got filled
            gone = [n for q in filled for n in ((q[0], q[1], q[2]), (q[0], q[1] - 1, q[2])) if n in feet]
            feet.difference_update(gone)
            if w.still_joined(gone, feet):          # the rest still hangs together: just drop those spots ...
                for X, Y, Z in gone:
                    spots = spots[~((spots[:, 0] == X) & (spots[:, 1] == Y) & (spots[:, 2] == Z))]
                if recheck:   # O94ag-b (builder's O94ah list): also those that lost their way in (drops are one-way:
                    a2: dict = {}  # cp69 #300) or their way down (cp70-horse20 #1001); off by default, so every plan
                    f2 = w.walk(adj=a2)  # and the scaffold planner's trials stay as they were (frozen examples)
                    if last[0] and w._mover()[1](*last[0]):   # S93: or from where she stands (inside a closed basin)
                        w.walk(seeds=[last[0]], seen=f2, adj=a2)
                    keep = f2 & w.escape(f2, a2)
                    spots = spots[[(int(X), int(Y), int(Z)) in keep for X, Y, Z, _ in spots]] if len(spots) else spots
            else:
                spots = None                        # it may have closed a room: walk again
        elif w.walk_f[T[1], T[2], T[0]] and (n := (T[0], T[1] + 1, T[2])) not in feet and w.joins(n, feet):
            spots = None                            # a new foothold: walk again (reach and the way down both change)
        elif _moves_changed(w, filled, feet, adj):
            spots = None                            # a block over a step or in a drop's path took a move away
    return fails, w


def _moves_changed(w: World, filled: list, feet: set, adj: dict) -> bool:
    """Builder, lane C (hut5_tall, c2d1312): the ceiling plank over the interior staircase's top step took the jump to
    the planned stand and the way back down from it, but it filled no feet or head cell, so the replay kept the stand
    and the plan trapped her under her own ceiling. A filled cell can take a move from a feet cell two below it (the
    jump's head room) or from one beside it, down to a drop's depth; walk again when any such cell's moves differ from
    the last walk's."""
    near = {f for X, Y, Z in filled
            for f in [(X, Y - 2, Z)] + [(X + dx, y, Z + dz) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
                                         for y in range(Y - 2, Y + FALL + 1)]
            if f in feet}
    if not near:
        return False
    moves = w._mover()[0]
    return any(moves(*f) != adj.get(f) for f in sorted(near))


def stale_stands(g: Design, gd: GameData, scaffold: Design | None, steps: list, site: dict | None = None) -> list[dict]:
    """O94ag-b (orchestrator: a stale plan must never reach a lane unnoticed): a plan's stands checked afresh with
    today's walking rules, each in the world as built just before its step: no walk to it from the ground or from the
    stand before it (where she is: builder's run-time check; S93: the contained pool is built from inside the basin it
    closes), no walk back down (cp70-horse20, step 1003) or, after a rule change, a way that is gone (cp72's loft).
    `steps`: simulate's steps or build_order.json rows as dicts (x, y, z instead of pos)."""
    w = World(g, gd, scaffold, None, site)
    P, out, prev = w.P, [], None
    for i, st in enumerate(steps):
        x, y, z = st.get("pos") or (st["x"], st["y"], st["z"])
        if st.get("stand"):
            adj: dict = {}
            feet = w.walk(adj=adj)
            if prev and w._mover()[1](*prev):
                w.walk(seeds=[prev], seen=feet, adj=adj)
            T = (st["stand"][0] + P, st["stand"][1], st["stand"][2] + P)
            if T not in feet or T not in w.escape(feet, adj):
                out.append({"step": i + 1, "stand": st["stand"], "why": "no walk to it from the ground or her last stand"
                            if T not in feet else "no walk back down"})
            else:
                prev = T                     # her last stand that held (a stand she never reached vouches for nothing)
        if st["layer"] == "scaffold_removal":
            w.placed[y, z + P, x + P] = False
        else:
            for a in [(x, y, z)] + list(st.get("also_appears") or []):
                w.put(a[0] + P, a[1], a[2] + P)
    return out
