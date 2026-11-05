"""CP-53 planner side (O40): `bridge <x y z>`.

1. Ask sense for void edges around Eve (link v1.8 query.resources{kind: void_edge}): standable blocks with a
   neighbour she could walk off into and nothing solid within 8 below it.
2. Pick the nearest edge that FACES the target: on the target's line (same x or same z), at the target's floor
   height, with `toward` pointing at it. None -> refuse ("no edge faces the target"); nothing is dispatched.
3. Depth: the edge's `depth` (blocks to the first solid below, null = none within 64). Deeper than a safe fall
   (3) needs a recovery item in her inventory (ender pearl or a slow falling potion) -> otherwise refuse.
4. Walk onto the edge block, dispatch builder's bridge{to} (registry 0.11.0), and cross-check its evidence against
   the void_edge answer (start = the edge she stood on, length = the cells to the target, depth if reported).
A mismatch is reported, never smoothed over. Every step goes through Mind.do().
"""
import asyncio
import json
import time

SAFE_FALL = 3
RECOVERY = ("ender_pearl", "slow_falling")
DIRS = {"east": (1, 0), "west": (-1, 0), "south": (0, 1), "north": (0, -1)}


def facing(edge, to):
    """The direction from the edge block to `to` if the edge faces it on a straight x/z line at floor height."""
    p = edge["pos"]
    if p["y"] != to["y"]:
        return None
    dx, dz = to["x"] - p["x"], to["z"] - p["z"]
    if dx and dz or not (dx or dz):
        return None
    want = ("east" if dx > 0 else "west") if dx else ("south" if dz > 0 else "north")
    return want if want in (edge.get("toward") or []) else None


class Bridge:
    def __init__(self, mind):
        self.mind, self.task, self.last = mind, None, None

    def start(self, to, source="cli"):
        if self.task and not self.task.done():
            return {"ok": False, "error": "busy", "detail": "a bridge is running"}
        try:
            to = {a: int(to[a]) for a in "xyz"}
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": "bad_args", "detail": "bridge needs to: {x, y, z}"}
        self.last = {"to": to, "status": "running", "notes": []}
        self.task = asyncio.get_running_loop().create_task(self.run(self.last, source))
        return {"ok": True, "status": "running", "to": to}

    def running(self):
        return self.task is not None and not self.task.done()

    def end(self, why):
        """O120: his new order or chat "stop" ends a running bridge (its step's action is the caller's to cancel)."""
        if not self.running():
            return False
        self.task.cancel()
        self.finish(self.last, "cancelled", why)
        return True

    def status(self):
        return {"ok": True, **(self.last or {"status": "none"})}

    def finish(self, r, status, reason, **extra):
        r.update(status=status, reason=reason, **extra)
        self.mind.event("mind", "bridge", {k: v for k, v in r.items() if k != "notes"})
        return r

    def has_recovery(self, st, depth):
        """Aligned with builder's bridge (after b47803c): an ender pearl or a slow falling potion (any potion kind,
        recognised from its slot contents); a water bucket only when there is ground under the gap (depth known)."""
        out = [s["item"] for s in st.get("inventory", []) if any(k in json.dumps(s) for k in RECOVERY)]
        if depth is not None:
            out += [s["item"] for s in st.get("inventory", []) if s.get("item") == "minecraft:water_bucket"]
        return out

    async def run(self, r, source):
        m, to = self.mind, r["to"]
        st = m.fresh_state()
        if st is None:
            return self.finish(r, "failed", "no fresh state from the body")
        me = {a: int(st["pos"][a] // 1) for a in "xyz"}
        ans = await m.hub.request("sense", "query.resources", {"kind": "void_edge", "dim": st["dim"],
                                                               "center": me, "radius": 32}, timeout=5)
        if not isinstance(ans, dict) or not isinstance(ans.get("items"), list):
            return self.finish(r, "failed", "sense gave no void_edge answer")
        cands = [(e, facing(e, to)) for e in ans["items"]]
        cands = [(e, d) for e, d in cands if d]
        if not cands:
            return self.finish(r, "refused", f"no edge faces the target {to['x']} {to['y']} {to['z']} "
                                             "(same line, same floor height); nothing dispatched")
        dist = lambda e: abs(e["pos"]["x"] - me["x"]) + abs(e["pos"]["z"] - me["z"])  # noqa: E731
        edge, d = min(cands, key=lambda c: dist(c[0]))
        depth = edge.get("depth")
        r.update(edge=edge["pos"], toward=d, depth=depth)
        if depth is None or depth > SAFE_FALL:
            rec = self.has_recovery(st, depth)
            if not rec:
                return self.finish(r, "refused", f"the drop there is {'over 64' if depth is None else depth} blocks "
                                                 "and I carry no ender pearl or slow falling"
                                                 f"{'' if depth is None else ' or water bucket'}; not stepping out")
            r["recovery"] = rec
        p = edge["pos"]
        w = await self.step("walk_to", {"pos": {"x": p["x"] + .5, "y": p["y"] + 1, "z": p["z"] + .5}}, source)
        if w["status"] != "ok":
            return self.finish(r, "failed", f"could not reach the edge at {p['x']} {p['y']} {p['z']} ({w['code']})")
        res = await self.step("bridge", {"to": to}, source)
        ev = res["evidence"]
        cells = abs(to["x"] - p["x"]) + abs(to["z"] - p["z"])
        problems = []
        if isinstance(ev.get("start"), dict) and any(int(ev["start"][a]) != p[a] for a in "xyz"):
            problems.append(f"bridge started at {ev['start']} not at the edge {p}")
        if res["status"] == "ok" and isinstance(ev.get("length"), int) and ev["length"] != cells:
            problems.append(f"bridge length {ev['length']} != {cells} cells to the target")
        if isinstance(ev.get("depth"), int) and depth is not None and ev["depth"] != depth:
            problems.append(f"measured depth {ev['depth']} != sense's {depth}")
        if res["status"] != "ok":
            return self.finish(r, "failed", f"bridge {res['status']} ({res['code']})", evidence=ev, problems=problems)
        return self.finish(r, "done" if not problems else "done_with_mismatch",
                           "; ".join(problems) or "bridged to the target", evidence=ev, problems=problems)

    async def step(self, skill, params, source):
        m = self.mind
        res = await m.do(skill, params, source=source)
        aid = res.get("action_id")
        if not aid or not res.get("ok"):
            return {"status": "refused", "code": res.get("code") or res.get("error"), "evidence": {}}
        deadline = time.monotonic() + m.registry.skills[skill]["timeout_ms"] / 1000 + m.cfg.result_grace_s + 1
        while time.monotonic() < deadline and not m.stopped:
            if m.status_of(aid) not in ("pending", "sent", "accepted", "running"):
                break
            await asyncio.sleep(0.1)
        row = m.action(aid)
        ev = json.loads(row["evidence"]) if row["evidence"] else {}
        return {"status": row["status"], "code": row["code"], "evidence": ev if isinstance(ev, dict) else {}}
