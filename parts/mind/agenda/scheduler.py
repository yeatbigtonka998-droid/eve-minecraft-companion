r"""CP-73 [B/K]: balance needs and honour a limited agenda. A small deterministic scheduler, no model.

Inputs (one dict per decision, nothing else is read):
  time_of_day (0..23999 or any tick count), health, food, inventory {item_id: count}, threats [..],
  owner_request (text or None), unavailable [things she cannot get right now, e.g. "pickaxe", "food"]
Holds: Hunter's standing agenda (a short ordered list), a session time budget, the decision log.

Rules, first that fires wins (the order IS the spec):
  1 night      DUSK_LEAD <= t < DAWN                     -> night_survival   (outranks everything, owner included;
                                                                             STOP never comes here: it is control)
  2 danger     health <= HEALTH_LOW or a threat listed   -> retreat_home
  3 starving   food <= FOOD_URGENT                       -> eat | get_food (none carried)
  4 owner      owner_request                             -> owner_request    (the agenda item is suspended, not lost)
  5 hungry     food < FOOD_OK                            -> eat | get_food
  6 tools      the next agenda item needs a tool she lacks -> get_tool; if the tool is unavailable the item is
                                                             blocked with that reason and the next item is tried
  7 agenda     first open item, in Hunter's order, whose round budget is not spent -> that item
  8 idle       session budget spent, or every item done/blocked/out of rounds -> idle_home + a question for Hunter
Needs (2,3,5,6) always outrank the agenda (7). Anti-starvation: each item gets at most budget_s per round; when every
open item has spent its round, a new round starts, so "keep the farm" (standing, never done) cannot starve "explore".
The agenda is limited: MAX_ITEMS items, MAX_ROUNDS rounds, explore radius <= the envelope's explore_radius.
Every decide() appends {seq, ts, inputs, agenda, decision} to the log (and to log_path as JSON lines if given).
"""
import copy
import json
import math
import re
from dataclasses import asdict, dataclass, field

DAY = 24000
DUSK_LEAD, DAWN = 12000, 23000      # night runner: DUSK 13000 / DAWN 23000; start heading home a game-hour early
HEALTH_LOW = 10                     # nightsurvival FIGHT_HEALTH: below this she does not fight, she leaves
FOOD_URGENT, FOOD_OK = 6, 15        # objective.py FOOD_URGENT / FOOD_OK
FOODS = ("bread", "cooked_", "apple", "baked_potato", "carrot", "golden_carrot")   # ironkit FOODS
MAX_ITEMS, MAX_ROUNDS, EXPLORE_RADIUS_MAX = 8, 3, 128
XYZ = re.compile(r"(-?\d+)[ ,]+(-?\d+)[ ,]+(-?\d+)")


def one_line(v, n=80):
    return " ".join(str(v).split())[:n]          # owner words in reasons and the log: one line, bounded


@dataclass
class Item:
    id: str
    goal: str
    kind: str = "task"              # task (can be done) | standing (upkeep, never done) | explore (bounded)
    budget_s: int = 600             # per round
    tools: tuple = ()               # tool kinds this item needs: "pickaxe", "axe", "hoe", "sword", "shears"
    radius: int | None = None       # explore only
    spent_s: float = 0
    done: bool = False
    blocked: str | None = None


@dataclass
class Agenda:
    items: list
    session_budget_s: float = 7200
    explore_radius_max: int = EXPLORE_RADIUS_MAX
    home: tuple = (0, 0)            # envelope home.center x, z
    log_path: str | None = None
    round: int = 1
    used_s: float = 0
    log: list = field(default_factory=list)

    def __post_init__(self):
        self.items = [i if isinstance(i, Item) else Item(**i) for i in self.items]
        if not 1 <= len(self.items) <= MAX_ITEMS:
            raise ValueError(f"agenda needs 1..{MAX_ITEMS} items, got {len(self.items)}")
        if len({i.id for i in self.items}) != len(self.items):
            raise ValueError("agenda item ids must be unique")
        for i in self.items:
            if i.kind not in ("task", "standing", "explore"):
                raise ValueError(f"{i.id}: unknown kind {i.kind!r}")
            if i.kind == "explore" and not (i.radius and 0 < i.radius <= self.explore_radius_max):
                raise ValueError(f"{i.id}: explore needs 0 < radius <= {self.explore_radius_max}")
            if i.budget_s <= 0:
                raise ValueError(f"{i.id}: budget_s must be > 0")
            for x, _, z in XYZ.findall(i.goal):     # a place named in a goal stays inside the explore bound
                if math.hypot(int(x) - self.home[0], int(z) - self.home[1]) > self.explore_radius_max:
                    raise ValueError(f"{i.id}: {x} {z} is beyond explore_radius {self.explore_radius_max}")
            i.goal = one_line(i.goal, 160)

    # bookkeeping the caller reports back -------------------------------------------------------------------------
    def charge(self, item_id, seconds):
        """Time spent on an agenda item (needs and owner requests are charged to the session only)."""
        self.used_s += seconds
        if item_id is not None:
            self.get(item_id).spent_s += seconds

    def finish(self, item_id):
        it = self.get(item_id)
        if it.kind == "standing":
            raise ValueError(f"{item_id} is standing upkeep; it is never done")
        it.done = True

    def unblock(self, item_id):
        self.get(item_id).blocked = None

    def get(self, item_id):
        return next(i for i in self.items if i.id == item_id)

    # the decision -------------------------------------------------------------------------------------------------
    def decide(self, state):
        inputs = copy.deepcopy(state)
        d = self._decide(state)
        self.log.append({"seq": len(self.log) + 1, "ts": state.get("now_ms"), "inputs": inputs,
                         "agenda": {"round": self.round, "used_s": self.used_s, "items": [asdict(i) for i in self.items]},
                         "decision": d})
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(self.log[-1]) + "\n")
        return d

    def _decide(self, s):
        t = int(s.get("time_of_day", 0)) % DAY
        hp, food = s.get("health", 20), s.get("food", 20)
        inv, threats, unavailable = s.get("inventory") or {}, s.get("threats") or [], set(s.get("unavailable") or ())
        has_food = any(n > 0 and any(f in k for f in FOODS) for k, n in inv.items())
        eat = lambda why: (dec("eat", why, "starving" if food <= FOOD_URGENT else "hungry") if has_food  # noqa: E731
                           else dec("get_food", why + "; no food carried", "starving" if food <= FOOD_URGENT else "hungry"))

        if DUSK_LEAD <= t < DAWN:
            why = f"time_of_day {t} is dusk/night ({DUSK_LEAD}..{DAWN})"
            if s.get("owner_request"):
                why += f"; owner request held until dawn: {one_line(s['owner_request'])}"
            return dec("night_survival", why, "night")
        if hp <= HEALTH_LOW or threats:
            return dec("retreat_home", f"health {hp} <= {HEALTH_LOW}" if hp <= HEALTH_LOW else
                       f"{len(threats)} threat(s) listed", "danger")
        if food <= FOOD_URGENT:
            return eat(f"food {food} <= {FOOD_URGENT}")
        if s.get("owner_request"):
            cur = self._current()
            return dec("owner_request", f"Hunter asked: {one_line(s['owner_request'])}" +
                       (f"; {cur.id} suspended" if cur else ""), "owner", text=s["owner_request"])
        if food < FOOD_OK:
            return eat(f"food {food} < {FOOD_OK}")
        if self.used_s >= self.session_budget_s:
            return dec("idle_home", f"session budget spent ({self.used_s:.0f}/{self.session_budget_s:.0f} s)", "idle",
                       question="My time budget for this session is spent. Shall I carry on?")
        while True:
            cur = self._current()
            if cur is None:
                return self._exhausted()
            missing = [k for k in cur.tools if not any(n > 0 and name.endswith("_" + k) for name, n in inv.items())]
            if not missing:
                return dec("agenda", f"{cur.id}: {cur.goal} (Hunter's #{self.items.index(cur) + 1}, round {self.round},"
                           f" {cur.spent_s:.0f}/{cur.budget_s} s)", "agenda", item=cur.id, goal=cur.goal)
            gone = [k for k in missing if k in unavailable]
            if not gone:
                return dec("get_tool", f"{cur.id} needs a {missing[0]} and she has none", "tools",
                           item=cur.id, tool=missing[0])
            cur.blocked = f"needs a {gone[0]}; none carried and none obtainable now"

    def _current(self):
        open_ = [i for i in self.items if not i.done and not i.blocked]
        while open_ and self.round <= MAX_ROUNDS:
            fresh = [i for i in open_ if i.spent_s < i.budget_s]
            if fresh:
                return fresh[0]
            self.round += 1
            if self.round > MAX_ROUNDS:
                break
            for i in open_:
                i.spent_s = 0
        return None

    def _exhausted(self):
        blocked = [f"{i.id} ({i.blocked})" for i in self.items if i.blocked]
        left = [i.id for i in self.items if not i.done and not i.blocked]
        why = "agenda exhausted: " + ", ".join(
            ([f"{len([i for i in self.items if i.done])} done"]) +
            ([f"blocked {'; '.join(blocked)}"] if blocked else []) +
            ([f"{' '.join(left)} out of rounds ({MAX_ROUNDS})"] if left else []))
        q = ("What should I do next?" if not blocked else
             f"I couldn't continue {blocked[0].split(' ')[0]}: {blocked[0].split('(', 1)[1][:-1]}. What should I do?")
        return dec("idle_home", why, "idle", question=q)


def dec(task, reason, rule, **detail):
    return {"task": task, "reason": reason, "rule": rule, **detail}
