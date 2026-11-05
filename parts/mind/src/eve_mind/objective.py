"""CP-43: run a multi-step objective — understand it once, then plan and revise from the current state, keep a ledger
of completed work, never run a step again that failed for the same reason, pause for an urgent need, stay within a
budget, and report honestly.

Design (after lane-B a1/a3: qwen3:8b could not decompose "build a column, mine the supplies if needed" step by step -
it never planned the mining and claimed the column was finished after one block):
1. GOAL: the local model turns the objective into one plan that lists every block to place (in build order) and one
   mine step per supply the objective names. It is checked by the same validator as any plan (grounded positions:
   the owner's words, or touching a block placed earlier in the goal; envelope rules); only the inventory rule is
   skipped, because the goal says what must exist at the end, not what Eve holds now.
2. EXECUTION, deterministic, re-planned after every step from observed state + ledger: gather first (mine the next
   named supply while she holds fewer blocks than still need placing), then place the lowest remaining block, standing
   on a free neighbouring block. A failed step is retried only once the situation changed (another standing spot, a
   different source); with nothing left to try the run ends partial with the reason. Every step goes through
   Mind.do() (bounds, mode, envelope pre-check, single flight).
"""
import asyncio
import json
import time
import uuid

from .planner import TRIPLE, validate_plan

BUDGET = {"max_actions": 24, "max_minutes": 6, "max_replans": 4}
FOOD_URGENT = 6          # at or below: do not start a step; let the body's eat reflex run, else eat ourselves
FOOD_OK = 15
EAT_EVERY_S = 120
GOAL_TRIES = 3
GONE = {"wrong_block", "zone_denied", "precondition_failed", "not_found", "protected",
        "retry_limit"}           # retry_limit: the mind itself refuses it for now; not tried again this run
GOAL_HINT = ("\nReturn ONE plan that lists, in build order, a place_block step for EVERY block this objective asks to "
             "place (each block of a column or wall separately), and one mine_block step for each supply position "
             "the objective names. Ignore what Eve holds now. Do not add walk_to, look_at or other steps.")


def key(p):
    return tuple(int(p[a] // 1) for a in "xyz")


def where(params):
    p = params.get("pos") or params.get("target_pos")
    return f"{p['x']} {p['y']} {p['z']}" if isinstance(p, dict) else ""


def describe(skill, p):
    if skill == "place_block":
        return f"placed {p.get('item')} at {where(p)}"
    if skill == "mine_block":
        return f"mined the block at {where(p)}"
    if skill == "walk_to":
        return f"walked to {where(p)}"
    return f"{skill} {json.dumps(p, sort_keys=True)}"


def situation(st):
    p = st.get("pos") or {}
    return [int(p.get("x", 0) // 1), int(p.get("y", 0) // 1), int(p.get("z", 0) // 1), st.get("inventory_hash")]


def held(st, item):
    return sum(s.get("count", 0) for s in st.get("inventory", []) if s.get("item") == item)


class Objective:
    def __init__(self, text, budget=None, oid=None):
        self.id, self.text = oid or str(uuid.uuid4())[:8], text
        self.budget = {**BUDGET, **(budget or {})}
        self.goal = None                      # {"place": [{"pos", "item"}], "sources": [{"pos"}]}
        self.ledger, self.failed, self.rejections, self.notes = [], [], [], []
        self.actions = self.replans = 0
        self.elapsed_s, self.status, self.reason = 0.0, "running", ""

    def as_dict(self):
        return {k: getattr(self, k) for k in ("id", "text", "budget", "goal", "ledger", "failed", "rejections", "notes",
                                              "actions", "replans", "elapsed_s", "status", "reason")}

    @classmethod
    def load(cls, d):
        o = cls(d["text"], d["budget"], d["id"])
        for k, v in d.items():
            setattr(o, k, v)
        return o

    def failed_before(self, skill, params, st):
        """Failed before FOR THE SAME REASON: same skill+params while Eve stands on the same block with the same
        inventory (lane-B a3: a place failed `occupied` while she stood on the target; after moving it is fine)."""
        k = [skill, json.dumps(params, sort_keys=True)]
        return any(f["key"] == k and f.get("at") == situation(st) for f in self.failed)

    def done(self, skill, pos):
        return any(x["skill"] == skill and key(x["params"]["pos"]) == key(pos) for x in self.ledger)

    def report(self):
        placed = [x for x in self.ledger if x["skill"] == "place_block"]
        mined = [x for x in self.ledger if x["skill"] == "mine_block"]
        want = len(self.goal["place"]) if self.goal else "?"
        head = {"done": "done", "partial": "partial", "stopped": "stopped"}.get(self.status, self.status)
        parts = [f"{head}: {len(placed)}/{want} placed ({', '.join(where(x['params']) for x in placed) or 'none'})",
                 f"mined {len(mined)} ({', '.join(where(x['params']) for x in mined) or 'none'})"]
        if self.notes:
            parts.append("; ".join(dict.fromkeys(self.notes)))
        if self.reason:
            parts.append(self.reason)
        return "; ".join(parts)


class Runner:
    """One objective at a time, as an asyncio task inside the mind. State persists in meta `objective.<id>` so an
    owner stop, a resume and an explicit continue pick up the goal and the ledger (nothing redone)."""

    def __init__(self, mind):
        self.mind, self.current, self.task = mind, None, None

    def save(self, o):
        try:
            self.mind.set_meta({f"objective.{o.id}": json.dumps(o.as_dict())})
        except Exception:                       # best effort; the in-memory objective is authoritative
            self.mind.metrics["db_errors"] += 1

    def get(self, oid):
        if self.current and self.current.id == oid:
            return self.current
        raw = self.mind.meta(f"objective.{oid}")
        return Objective.load(json.loads(raw)) if raw else None

    def start(self, text, source="cli", budget=None):
        if self.task and not self.task.done():
            return {"ok": False, "error": "busy", "detail": f"objective {self.current.id} is running"}
        return self._launch(Objective(text, budget), source)

    def resume(self, oid, source="cli"):
        if self.task and not self.task.done():
            return {"ok": False, "error": "busy", "detail": f"objective {self.current.id} is running"}
        o = self.get(oid)
        if o is None:
            return {"ok": False, "error": "unknown_objective", "detail": oid}
        if o.status not in ("stopped", "partial"):
            return {"ok": False, "error": "not_resumable", "detail": f"objective is {o.status}"}
        o.status, o.reason = "running", ""
        o.notes.append("continued by the owner")
        return self._launch(o, source)

    def _launch(self, o, source):
        self.current = o
        self.save(o)
        self.task = asyncio.get_running_loop().create_task(self.run(o, source))
        return {"ok": True, "objective_id": o.id, "status": o.status}

    def status(self, oid=None):
        o = self.get(oid) if oid else self.current
        if o is None:
            return {"ok": False, "error": "unknown_objective", "detail": oid}
        return {"ok": True, **o.as_dict(), "report": o.report()}

    def finish(self, o, status, reason=""):
        o.status, o.reason = status, reason
        self.save(o)
        self.mind.event("mind", "objective", {"id": o.id, "status": status, "report": o.report()})

    async def understand(self, o, st):
        """The objective -> goal (blocks to place, named supplies), validated like any plan except the inventory rule."""
        m = self.mind
        caps = m.hub.links["body"].hello["caps"]
        r = await m.planner.plan(o.text + GOAL_HINT, m.registry, caps, st, m.env)
        if not r["ok"]:
            return r["error"]
        plan = r["plan"]
        steps = [s for s in plan.get("steps", []) if isinstance(s, dict) and s.get("skill") in ("place_block", "mine_block")]
        goal_plan = {"steps": [{**s, "depends_on": []} for s in steps], "question": None}
        errs = [e for e in validate_plan(goal_plan, m.registry, caps, m.mode, m.env, st, request=o.text, chain_place=True)
                if not e.startswith("prerequisite:")]
        m.record_decision("objective_goal", r["model"], r["prompt_version"], o.text, r["raw"], r["latency_ms"], not errs)
        place = [{"pos": s["params"]["pos"], "item": s["params"].get("item")} for s in steps if s["skill"] == "place_block"]
        stated = {tuple(int(float(v)) for v in t) for t in TRIPLE.findall(o.text)}
        seen = []
        for p in place:                       # exact: a stated block, or touching one placed earlier in the goal
            k = key(p["pos"])
            if k not in stated and not any(sum(abs(a - b) for a, b in zip(k, q)) == 1 for q in seen):
                errs.append(f"goal block {where(p)} is neither a position the objective states nor next to an earlier "
                            f"goal block")
            seen.append(k)
        for s in steps:
            if s["skill"] == "mine_block" and key(s["params"]["pos"]) not in stated:
                errs.append(f"supply {where(s['params'])} is not a position the objective states")
        if errs or not place:
            return "; ".join(errs[:2]) or (plan.get("question") or "the objective names no block to place")
        o.goal = {"place": place, "sources": [{"pos": s["params"]["pos"]} for s in steps if s["skill"] == "mine_block"]}
        o.notes.append(f"goal: {len(place)} blocks to place, {len(o.goal['sources'])} named supplies")
        return None

    def stand_spots(self, target, st, avoid):
        """Where Eve can stand to work on `target`: on her current ground level, 1-3 blocks away along an axis, never
        inside a goal block. A block 2+ above the ground is placed against a face she can only see from further back
        (lane-B a4: standing right next to a 3-high column, the top was `obstructed`), so those spots come first."""
        me = st.get("pos") or {}
        x, y, z = key(target)
        g = int(me.get("y", y) // 1)
        spots = [(x + dx * d, g, z + dz * d) for d in (1, 2, 3) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))]
        spots = [s for s in spots if s not in avoid]
        far_first = y - g >= 2
        spots.sort(key=lambda s: ({1: 2, 2: 0, 3: 1}[max(abs(s[0] - x), abs(s[2] - z))] if far_first
                                  else max(abs(s[0] - x), abs(s[2] - z)),
                                  (s[0] + .5 - me.get("x", 0)) ** 2 + (s[2] + .5 - me.get("z", 0)) ** 2))
        return spots

    def spot_failed(self, o, skill, params, spot):
        """A step that failed from this standing spot is not tried from it again (where she really stood can differ
        from the spot when a walk ended short, so the spot itself is remembered)."""
        k = json.dumps(params, sort_keys=True)
        return any(f["key"] == [skill, k] and f.get("spot") == list(spot) for f in o.failed)

    def next_steps(self, o, st):
        """The deterministic re-plan: what to do now, given the goal, the ledger, the failures and observed state.
        Returns (steps, None) or (None, reason to stop)."""
        remaining = [p for p in o.goal["place"] if not o.done("place_block", p["pos"])]
        if not remaining:
            return [], None
        item = remaining[0]["item"]
        need = sum(1 for p in remaining if p["item"] == item)
        have = held(st, item)
        goal_blocks = {key(p["pos"]) for p in o.goal["place"]}
        if have < need:
            for src in o.goal["sources"]:
                mine = {"pos": src["pos"]}
                if o.done("mine_block", src["pos"]) or any(
                        f["key"][0] == "mine_block" and json.loads(f["key"][1]).get("pos") == src["pos"]
                        and f["code"] in GONE for f in o.failed):
                    continue                    # mined already, or gone/denied: that supply is not tried again
                for s in self.stand_spots(src["pos"], st, goal_blocks | {key(src["pos"])}):
                    walk = {"pos": {"x": s[0] + .5, "y": s[1], "z": s[2] + .5}}
                    if not o.failed_before("walk_to", walk, st) and not self.spot_failed(o, "mine_block", mine, s) \
                            and not self.spot_failed(o, "walk_to", walk, s):
                        return [("walk_to", walk), ("mine_block", mine)], None
        if have < 1:
            short = need - have
            return None, (f"short {short} {item}: the named supplies are used up or gone and I know no other source; "
                          "I will not invent one")
        target = sorted(remaining, key=lambda p: key(p["pos"])[1])[0]    # lowest first: columns go up
        place = {"pos": target["pos"], "item": target["item"]}
        if any(f["key"] == ["place_block", json.dumps(place, sort_keys=True)] and f["code"] == "retry_limit"
               for f in o.failed):
            return None, f"the mind refuses placing at {where(place)} again for now (retry limit); not retrying"
        for s in self.stand_spots(target["pos"], st, goal_blocks):
            walk = {"pos": {"x": s[0] + .5, "y": s[1], "z": s[2] + .5}}
            if not o.failed_before("walk_to", walk, st) and not self.spot_failed(o, "place_block", place, s) \
                    and not self.spot_failed(o, "walk_to", walk, s):
                return [("walk_to", walk), ("place_block", place)], None
        return None, f"no reachable spot to place at {where(place)} (every approach failed)"

    async def urgent(self, o, st):
        """Pause for food. The body's own eat reflex usually handles it; the mind only waits, and eats itself
        (hotbar food + use_item) if the body has not done so within 8 s."""
        if st.get("food", 20) > FOOD_URGENT and not st["control"].get("reflex_active"):
            return True
        if time.monotonic() - getattr(o, "_ate_at", -1e9) < EAT_EVERY_S and not st["control"].get("reflex_active"):
            return True          # ate recently; low food is not dangerous by itself (easy: health stays >= 10)
        o.notes.append("paused for food")
        self.save(o)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 30:
            if self.mind.stopped:
                return False
            st = self.mind.fresh_state() or st
            if st.get("food", 20) >= FOOD_OK and not st["control"].get("reflex_active"):
                break
            if time.monotonic() - t0 > 8 and not st["control"].get("reflex_active") and st.get("food", 20) <= FOOD_URGENT:
                slot = next((s["slot"] for s in st.get("inventory", []) if isinstance(s.get("slot"), int)
                             and s["slot"] <= 8 and any(f in str(s.get("item")) for f in ("apple", "bread"))), None)
                if slot is None:
                    return False
                await self.mind.do("select_hotbar", {"slot": slot}, source="cli", wait=3)
                await self.mind.do("use_item", {"hold_ms": 2000}, source="cli", wait=5)
                o.actions += 2
                o._ate_at = time.monotonic()
                o.notes.append("ate (the mind)")
                break
            await asyncio.sleep(0.5)
        o.notes.append("resumed from the completed work")
        return True

    async def step(self, skill, params, source):
        """Dispatch one step and wait for its terminal status; an owner stop ends the wait at once (the action is
        settled by the mind's normal paths, never by the runner)."""
        m = self.mind
        sk = m.registry.skills[skill]
        res = await m.do(skill, params, source=source)
        aid = res.get("action_id")
        if not aid or not res.get("ok"):
            return res
        deadline = time.monotonic() + sk["timeout_ms"] / 1000 + m.cfg.result_grace_s + 1
        while time.monotonic() < deadline and not m.stopped:
            if m.status_of(aid) not in ("pending", "sent", "accepted", "running"):
                break
            await asyncio.sleep(0.1)
        row = m.action(aid)
        return {"ok": True, "action_id": aid, "status": row["status"], "code": row["code"]}

    async def run(self, o, source):
        m, t_start, base = self.mind, time.monotonic(), o.elapsed_s
        try:
            while True:
                o.elapsed_s = base + time.monotonic() - t_start
                self.save(o)
                if m.stopped:
                    return self.finish(o, "stopped", "owner stop; `continue` resumes from the ledger")
                if o.actions >= o.budget["max_actions"] or o.elapsed_s > o.budget["max_minutes"] * 60:
                    return self.finish(o, "partial", "budget exhausted")
                if o.replans > o.budget["max_replans"]:
                    return self.finish(o, "partial", "too many re-plans; stopping instead of guessing")
                st = m.fresh_state()
                if st is None:
                    await asyncio.sleep(0.5)
                    continue
                if not await self.urgent(o, st):
                    if m.stopped:
                        continue
                    return self.finish(o, "partial", "urgent need and no food to eat")
                st = m.fresh_state() or st
                if o.goal is None:
                    why = await self.understand(o, st)
                    if why:
                        o.rejections.append(f"goal: {why}")
                        if len(o.rejections) >= GOAL_TRIES:
                            return self.finish(o, "partial", f"could not understand the objective: {why}")
                    continue
                steps, stop = self.next_steps(o, st)
                if stop:
                    return self.finish(o, "partial", stop)
                if not steps:
                    return self.finish(o, "done")
                spot = None
                for skill, params in steps:
                    if m.stopped or o.actions >= o.budget["max_actions"]:
                        break
                    if skill == "walk_to":
                        spot = list(key(params["pos"]))
                    before = m.fresh_state() or st
                    res = await self.step(skill, params, source)
                    if res.get("action_id"):
                        o.actions += 1
                    if res.get("status") == "ok":
                        o.ledger.append({"skill": skill, "params": params, "done": describe(skill, params),
                                         "action_id": res["action_id"]})
                        self.save(o)
                        continue
                    if res.get("error") == "stopped" or res.get("code") == "stopped" or m.stopped:
                        break
                    o.failed.append({"key": [skill, json.dumps(params, sort_keys=True)], "at": situation(before),
                                     "spot": spot,
                                     "skill": skill, "where": where(params),
                                     "code": res.get("code") or res.get("error") or res.get("status")})
                    o.notes.append(f"{skill} {where(params)} failed ({o.failed[-1]['code']}); re-planned")
                    o.replans += 1
                    break
        except asyncio.CancelledError:
            self.finish(o, "stopped", "cancelled")
            raise
