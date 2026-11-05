"""CP-09 / CP-12 (client side) runs on the local dev fixture ("Shark local fixture"). No model anywhere: fixed commands.

    py -3.12 tools/arena.py cp09|cp12 [--player EveDev]

Needs: `gradlew runServer` (RCON on, run-server/server.properties), `gradlew runClient` joined as --player, EVE_LINK_TOKEN set
to the client's token. This script IS the mind for the run (it hosts the stub Hub on 8952; the real mind owns 8951), so stub_mind.py must not run too.
Server truth comes from RCON (`data get entity`, predicates), never from the body's own report. Every attempt is written to
evidence/CP-NN/<stamp>-attempt-N/ (attempts.jsonl, frames.jsonl, RESULT.md); RESULT.md is copied to evidence-summaries/.
"""
import importlib.util, json, math, os, re, socket, struct, subprocess, sys, time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stub_mind import Hub  # noqa: E402

BODY = Path(__file__).resolve().parents[1]
REPO = BODY.parents[1]
# Arena floor (players stand at this y). -60 = body's flat dev world; on another world set EVE_ARENA_Y to an open height and the
# fixture builds the floor there. EVE_SERVER_PROPS points RCON at another server (e.g. the 45L dev server).
FLOOR = float(os.environ.get("EVE_ARENA_Y", "-60"))
# Every kill stays in the arena: a world-wide kill on a shared lane takes other teams' fixtures (eve-watch 2026-10-21: lane E's
# chest minecarts would drop and despawn). laneb's own clear_entities uses the same 60-block sphere.
ARENA_KILL = f"kill @e[type=!minecraft:player,x=0,y={FLOOR:.0f},z=0,distance=..60]"
SERVER_PROPS = Path(os.environ.get("EVE_SERVER_PROPS", str(BODY / "run-server" / "server.properties")))


def lane_guard(props):
    """I28a: on a Shark lane, eve-watch's <lane>/ops/lane_guard.py check for every command an Rcon on that lane's server sends (a
    world-changing one needs the lane's lock; reads pass), else None (the body's own run-server has none). I28: a dev selftest
    wrote to lane B without its lock."""
    lane = Path(props).resolve().parent.parent
    if not (lane / "ops" / "lane_guard.py").exists():
        return None
    spec = importlib.util.spec_from_file_location("lane_guard", lane / "ops" / "lane_guard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return lambda cmd: mod.check(lane, cmd)


class Rcon:
    def __init__(self, props: Path):
        p = dict(l.split("=", 1) for l in props.read_text().splitlines() if "=" in l and not l.startswith("#"))
        self.guard = lane_guard(props)
        self.s = socket.create_connection(("127.0.0.1", int(p["rcon.port"])), timeout=10)
        self._send(1, 3, p["rcon.password"])
        if self._recv()[0] == -1:
            raise PermissionError("rcon login refused")

    def _send(self, rid, kind, body):
        data = struct.pack("<ii", rid, kind) + body.encode() + b"\0\0"
        self.s.sendall(struct.pack("<i", len(data)) + data)

    def _recv(self):
        def exact(n):
            b = b""
            while len(b) < n:
                c = self.s.recv(n - len(b))
                if not c:
                    raise ConnectionError("rcon closed")
                b += c
            return b
        (n,) = struct.unpack("<i", exact(4))
        d = exact(n)
        return struct.unpack("<i", d[:4])[0], d[8:-2].decode("utf-8", "replace")

    def __call__(self, cmd):
        # Long answers (a full Inventory) arrive split into 4096-byte packets; a full-size packet means more follow. (Minecraft's
        # RCON reads one packet per socket read, so a pipelined sentinel command breaks the connection: CP-08 attempt 1.)
        if self.guard:
            self.guard(cmd)  # PermissionError, nothing sent, when the lane's lock is not held
        self._send(2, 2, cmd)
        out = [self._recv()[1]]
        while len(out[-1].encode("utf-8")) >= 4096:
            out.append(self._recv()[1])
        return "".join(out)


def nums(text):
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?(?:E-?\d+)?", text.split(":", 1)[-1])]


def code_rev():
    """(last commit touching body code/tools, uncommitted changes in them) for the RESULT stamp."""
    code = ["parts/body/src", "parts/body/build.gradle", "parts/body/gradle.properties", "parts/body/tools"]
    rev = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%h", "--", *code], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", *code], capture_output=True, text=True).stdout.strip()
    return rev, dirty


class Run:
    def __init__(self, cp, player):
        self.cp, self.player = cp, player
        root = REPO / "evidence" / cp
        root.mkdir(parents=True, exist_ok=True)
        n = 1 + sum(1 for d in root.iterdir() if d.is_dir() and re.fullmatch(r"\d{4}-\d\d-\d\d-\d{4}-attempt-\d+", d.name))  # not sense's runs
        self.stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
        self.dir = root / f"{self.stamp}-attempt-{n}"
        self.dir.mkdir()
        self.rows, self.t0 = [], time.time()
        self.rcon = Rcon(SERVER_PROPS)
        self.hub = Hub(self.dir / "frames.jsonl", verbose=False).start()
        print("waiting for the body to connect ...")
        if not self.hub.wait(lambda m: m["type"] == "state", 120):
            sys.exit("no state from the body within 120 s (client joined? token set?)")

    # --- server truth ---
    def pos(self):
        return nums(self.rcon(f"data get entity {self.player} Pos"))

    def rot(self):
        return nums(self.rcon(f"data get entity {self.player} Rotation"))

    def test(self, pred):
        out = self.rcon(f"execute as {self.player} if predicate {pred}")
        if out not in ("Test passed", "", "Test failed"):
            raise RuntimeError(f"predicate not evaluated: {out!r}")  # attempt 1 counted a parse error as False
        return out == "Test passed"

    def clear_entities(self):
        # Nothing may push her or catch her crosshair during a trial. A killed mob stays as a pickable corpse for 20 ticks and drops
        # xp when it goes (CP-10 attempt 1 aimed at a corpse), so: kill, wait the death out, kill again (loot, xp, stragglers).
        if "No entity" not in self.rcon(ARENA_KILL):
            time.sleep(1.2)
            self.rcon(ARENA_KILL)

    def tp(self, x, y, z, yaw=0, pitch=0):
        self.clear_entities()
        self.rcon(f"tp {self.player} {x} {y} {z} {yaw} {pitch}")
        time.sleep(1.0)

    def tp_drop(self, x, y, z, yaw=0, pitch=0):
        """A deliberate fall from (x, y, z): stand her on a one-block glass platform there (a normal, checked teleport, O39),
        then take the platform away. Never a teleport into open air."""
        bx, by, bz = math.floor(x), math.floor(y) - 1, math.floor(z)
        self.rcon(f"setblock {bx} {by} {bz} minecraft:glass")
        self.tp(x, y, z, yaw, pitch)
        self.rcon(f"setblock {bx} {by} {bz} minecraft:air")

    def state(self):
        f = self.hub.wait(lambda m: m["type"] == "state", 5, since=len(self.hub.frames) - 1)
        return f and f["msg"]["payload"]

    def act(self, skill, params, timeout=30):
        since = len(self.hub.frames)
        t = time.time()
        aid = self.hub.request(skill, params)
        acc = self.hub.wait(lambda m: m["type"] in ("action.accepted", "action.rejected") and m["payload"].get("action_id") == aid, 2, since)
        res = self.hub.wait_result(aid, timeout, since)
        return aid, (acc["t"] - t) * 1000 if acc else None, res

    def state_times(self, t0, t1):
        """Arrival times (s) of the body's state frames between t0 and t1: the stub hub's own frames (dev)."""
        return [f["t"] for f in self.hub.frames if f.get("dir") == "in" and f["msg"].get("type") == "state" and t0 <= f["t"] <= t1]

    def record(self, variant, skill, params, ok, why, **kw):
        # O46: invalid=True = the trial's action never ran (refused / never started): a harness fault, not a pass or a fail
        inv = bool(kw.pop("invalid", False))
        # I29f: the longest gap between the body's state frames since the previous row (setup included), every row. A client
        # stall shows here first (dev a88: 3.7 s, a creeper's fuse ran inside it); over 1.0 s makes the trial INVALID (I29f).
        t1, t0 = time.time(), getattr(self, "_row_t0", self.t0)
        ts = self.state_times(t0 - 1.0, t1)
        g = max(((b - a, a) for a, b in zip(ts, ts[1:])), default=None)
        gap = round(g[0], 2) if g else None
        # eve-watch (CP-31 a20, a lone 1.126 s gap): when the longest gap began, local time to 0.1 s, so the next one localises itself
        gap_at = time.strftime("%H:%M:%S", time.localtime(g[1])) + f".{int(g[1] * 10) % 10}" if g else None
        self._row_t0 = t1
        row = {"n": len(self.rows) + 1, "variant": variant, "skill": skill, "params": params, "pass": ok and not inv, "invalid": inv, "why": why,
               "max_state_gap_s": gap, "max_state_gap_at": gap_at, **kw}
        self.rows.append(row)
        with open(self.dir / "attempts.jsonl", "a") as f:
            f.write(json.dumps(row) + "\n")
        print(("INVALID " if inv else "PASS " if ok else "FAIL ") + f"#{row['n']} {variant} {skill} {why}")

    def fixture(self):
        r = self.rcon
        F = int(FLOOR)
        self.hub.send("control.resume", {"source": "arena-setup"})  # a previous aborted run may have left her STOPPED (persisted)
        # Wandering traders spawn even on peaceful; one pushed a STOPPED Eve in CP-12 attempt 6. Clear everything that is not a player.
        for c in ["gamerule spawn_wandering_traders false", "gamerule spawn_mobs false", ARENA_KILL,
                  "difficulty peaceful", "time set noon", "weather clear", f"gamemode survival {self.player}", f"clear {self.player}",
                  f"fill -24 {F} -24 24 {F + 10} 24 minecraft:air", f"fill -24 {F - 1} -24 24 {F - 1} 24 minecraft:smooth_stone",
                  f"fill -25 {F - 1} -25 25 {F - 1} -25 minecraft:lime_wool", f"fill -25 {F - 1} 25 25 {F - 1} 25 minecraft:lime_wool",
                  f"fill -25 {F - 1} -25 -25 {F - 1} 25 minecraft:lime_wool", f"fill 25 {F - 1} -25 25 {F - 1} 25 minecraft:lime_wool",
                  f"fill -2 {F} 13 2 {F} 19 minecraft:stone", f"give {self.player} minecraft:stone_sword", f"give {self.player} minecraft:bread 8",
                  # full food: the eat reflex (Phase 3) must not fire in the middle of another suite's trials
                  f"effect give {self.player} minecraft:saturation 1 20 true"]:
            r(c)

    def finish(self, criteria, notes=""):
        valid = [r for r in self.rows if not r.get("invalid")]
        passed, invalid = sum(r["pass"] for r in valid), len(self.rows) - len(valid)
        groups = {}
        for r in valid:
            g = groups.setdefault(r["skill"] + ":" + r["variant"].split("#")[0], [0, 0])
            g[0] += r["pass"]
            g[1] += 1
        rev, dirty = code_rev()
        rev0, dirty0 = getattr(Run, "REV0", (rev, dirty))  # the code this process loaded, read at import (CP-33 lane D a1:
        if (rev0, dirty0) != (rev, dirty):                  # a commit landed mid-run and the end-of-run read stamped it)
            rev = f"{rev0}{' + UNCOMMITTED changes' if dirty0 else ''} at start (tree at the end: {rev}{' + uncommitted' if dirty else ''})"
            dirty = ""
        gpf = BODY / "gradle.properties"  # a staged copy of tools/ alone (45L) has none: versions then read "?"
        gp = dict(l.split("=", 1) for l in gpf.read_text().splitlines() if "=" in l and not l.startswith("#")) if gpf.exists() else {}
        gp = {k: gp.get(k, "?") for k in ("minecraft_version", "loader_version", "fabric_api_version", "mod_version")}
        fps = sorted(f["msg"]["payload"]["fps"] for f in getattr(self.hub, "frames", []) if f["dir"] == "in" and f["msg"]["type"] == "metrics")
        fps_line = f"Client fps (body metrics every 30 s): min {fps[0]}, median {fps[len(fps) // 2]}, n {len(fps)}." if fps else "Client fps: no metrics frames."
        desc = getattr(self, "fixture_desc", None) or ("Fixture: **Shark local fixture** (dev server via `gradlew runServer`, flat world, peaceful, "
                                                        f"offline dev account `{self.player}`, normal tick speed). Mind = tools/stub_mind.Hub driven by tools/arena.py; no model.")
        md = [f"# {self.cp} — {self.dir.name}", "", desc, "",
              f"Versions: MC {gp['minecraft_version']}, Loader {gp['loader_version']}, Fabric API {gp['fabric_api_version']}, eve-body "
              f"{gp['mod_version']}, body code at commit {rev}{' + UNCOMMITTED changes in body code/tools' if dirty else ''}, registry {self.hub.hash[:12]}.", "",
              fps_line, "", "## Frozen criteria (set before the run)", "", criteria, "",
              f"## Result: {passed}/{len(valid)} valid attempts passed, {invalid} invalid (O46: the action never ran); elapsed {time.time() - self.t0:.0f} s; deaths 0; human interventions 0", "",
              "| group | pass/total |", "|---|---|"] + [f"| {k} | {v[0]}/{v[1]} |" for k, v in groups.items()] + [
              "", "## Attempts", "", "| # | variant | skill | pass | detail |", "|---|---|---|---|---|"] + [
              f"| {r['n']} | {r['variant']} | {r['skill']} | {'INVALID' if r.get('invalid') else 'PASS' if r['pass'] else 'FAIL'} | {r['why']} |" for r in self.rows] + [
              "", notes, "", "Raw: attempts.jsonl (every attempt with server truth), frames.jsonl (every link frame, token redacted)."]
        (self.dir / "RESULT.md").write_text("\n".join(md), encoding="utf-8")
        try:  # on the 45L the body's summaries folder is not ours to write; the RESULT.md above is the record
            (BODY / "evidence-summaries").mkdir(exist_ok=True)
            (BODY / "evidence-summaries" / f"{self.cp}-{self.dir.name}.md").write_text("\n".join(md), encoding="utf-8")
        except OSError as e:
            print("evidence summary not written:", e)
        print(f"\n{passed}/{len(valid)} passed ({invalid} invalid) -> {self.dir}")


def yaw_pitch(eye, t):
    dx, dy, dz = t[0] - eye[0], t[1] - eye[1], t[2] - eye[2]
    return math.degrees(math.atan2(dz, dx)) - 90, -math.degrees(math.atan2(dy, math.hypot(dx, dz)))


def wrap(a):
    return (a + 180) % 360 - 180


CP09_CRITERIA = """- walk_to (tolerance 1.0; 5 layouts x 4, plus a wall-detour layout x 4 when run with --baritone): result `ok` with the expected
  walker; **server** position within 1.0 of the goal; path_length <= 1.3 x shortest route (straight line, or the hand-measured
  detour) + 1.0 (route budget); elapsed <= 1.5 x (distance / 4.317 b/s) + 3 s; server position moves <= 0.05 in the 1 s after the result
  (no drift); body's final_pos within 0.15 of the server position.
- look_at (10 targets incl. behind/up/down): result `ok`; **server** Rotation within 2 deg of the yaw/pitch computed from the
  server position to the target.
- select_hotbar (slots 0-8): result `ok`; **server** SelectedItemSlot equals the slot.
- sneak (on/off x 4): result `ok`; **server** predicate is_sneaking equals the request.
- jump (x 5): result `ok` with left_ground; **server** sees y above the floor within 0.5 s after the result (polled; changed
  after attempt 1, where a single read raced the first airborne movement packet; threshold unchanged).
- sneak: checked with a `type:` inline predicate (attempt 1's `condition:` form does not parse on 26.3; unparseable answers now abort).
- cancel (x 5): walk_to 20 blocks, `stop` skill after 1 s: walk ends `cancelled`, stop ends `ok` with inputs_cleared read back
  from the client; server position moves <= 0.05 between +0.5 s and +1.5 s after the stop.
- Fixture: every non-player entity is killed before each trial (added after CP-12 attempt 6 found a wandering trader in the arena).
- link loss (x 3): walk_to 20 blocks, stub drops the connection after 1 s: server position moves <= 0.05 between +1 s and +2 s;
  after reconnect re-sending the same action_id returns the stored terminal result (not re-executed) and Eve does not move."""


def cp09(r: Run):
    r.fixture()
    baritone = "--baritone" in sys.argv  # the client has baritone-api-fabric in its mods: walk_to must report walker=baritone
    layouts = {  # name: start (x,y,z,yaw), goal, shortest route (None = straight line), fixture blocks for this layout only
        "turn_N10": ((0.5, FLOOR, 0.5, 0), (0.5, FLOOR, -9.5), None, None),
        "diag_8": ((0.5, FLOOR, 0.5, 0), (8.5, FLOOR, 8.5), None, None),
        "east_15": ((-7.5, FLOOR, -5.5, 90), (7.5, FLOOR, -5.5), None, None),
        "ahead_3": ((0.5, FLOOR, 0.5, 0), (0.5, FLOOR, 3.5), None, None),
        "step_up": ((0.5, FLOOR, 9.5, 0), (0.5, FLOOR + 1, 16.5), None, None),
        # needs a pathfinder: a 7-wide, 3-high wall across the straight line; around its end is ~12 blocks
        "wall_detour": ((-15.5, FLOOR, -15.5, 0), (-15.5, FLOOR, -6.5), 12.2, "fill -19 -60 -11 -13 -58 -11 minecraft:stone"),
    }
    if not baritone:
        layouts.pop("wall_detour")
    for rep in range(4):
        for name, (s, g, shortest, blocks) in layouts.items():
            if blocks:
                r.rcon(blocks)
            r.tp(*s)
            straight = shortest or math.dist(s[:3], g)
            params = {"pos": dict(zip("xyz", g)), "tolerance": 1.0}
            aid, acc_ms, res = r.act("walk_to", params, 60)
            p1 = r.pos()
            time.sleep(1.0)
            p2 = r.pos()
            if blocks:
                r.rcon(blocks.replace("minecraft:stone", "minecraft:air"))
            if not res or res.get("status") != "ok":
                r.record(f"{name}#{rep}", "walk_to", params, False, f"result {res}", accept_ms=acc_ms)
                continue
            ev = res["evidence"]
            d_srv, drift = math.dist(p1, g), math.dist(p1, p2)
            agree = math.dist(p1, [ev["final_pos"][k] for k in "xyz"])
            budget_ms = 1.5 * straight / 4.317 * 1000 + 3000
            checks = {"walker": ev.get("walker") == ("baritone" if baritone else "direct"),
                      "server_dist<=1.0": d_srv <= 1.0, "route": ev["path_length"] <= 1.3 * straight + 1.0,
                      "time": res["elapsed_ms"] <= budget_ms, "drift<=0.05": drift <= 0.05, "telemetry_agrees": agree <= 0.15}
            r.record(f"{name}#{rep}", "walk_to", params, all(checks.values()),
                     f"srv_dist={d_srv:.3f} path={ev['path_length']:.2f}/{straight:.2f} t={res['elapsed_ms']}/{budget_ms:.0f}ms "
                     f"drift={drift:.3f} agree={agree:.3f} " + " ".join(k for k, v in checks.items() if not v),
                     accept_ms=acc_ms, server_pos=p1, server_pos_1s=p2, evidence=ev, elapsed_ms=res["elapsed_ms"])
    targets = [(5, 1.6, 0), (-5, 1.6, 0), (0, 1.6, -6), (0, 1.6, 6), (3, 6, 3), (-3, -2, 2), (4, 1.6, -4), (-6, 3, -1), (1, 10, 0.5), (0.5, -1.5, 1)]
    for i, (dx, dy, dz) in enumerate(targets):
        r.tp(0.5, FLOOR, 0.5, 0, 0)
        t = (0.5 + dx, FLOOR + dy, 0.5 + dz)
        params = {"pos": dict(zip("xyz", t))}
        aid, acc_ms, res = r.act("look_at", params)
        time.sleep(0.2)
        p, (yaw, pitch) = r.pos(), r.rot()
        ey, ep = yaw_pitch((p[0], p[1] + 1.62, p[2]), t)
        err = math.hypot(wrap(ey - yaw), ep - pitch)
        ok = bool(res) and res.get("status") == "ok" and err <= 2.0
        r.record(f"target{i}", "look_at", params, ok, f"server_rot=({yaw:.1f},{pitch:.1f}) expected=({wrap(ey):.1f},{ep:.1f}) err={err:.2f} "
                 f"body={res and res.get('evidence')}", accept_ms=acc_ms)
    for slot in [3, 0, 8, 5, 1, 7, 2, 6, 4]:
        params = {"slot": slot}
        aid, acc_ms, res = r.act("select_hotbar", params)
        srv = nums(r.rcon(f"data get entity {r.player} SelectedItemSlot"))
        ok = bool(res) and res.get("status") == "ok" and srv == [slot]
        r.record(f"slot{slot}", "select_hotbar", params, ok, f"server SelectedItemSlot={srv} body={res and res.get('evidence')}", accept_ms=acc_ms)
    sneak_pred = '{type:"minecraft:entity_properties",entity:"this",predicate:{flags:{is_sneaking:true}}}'
    for i, on in enumerate([True, False] * 4):
        params = {"on": on}
        aid, acc_ms, res = r.act("sneak", params)
        time.sleep(0.2)
        srv = r.test(sneak_pred)
        r.record(f"sneak{i}", "sneak", params, bool(res) and res.get("status") == "ok" and srv == on, f"server is_sneaking={srv} body={res and res.get('evidence')}")
    for i in range(5):
        r.tp(0.5, FLOOR, 0.5)
        aid, acc_ms, res = r.act("jump", {})
        y, end = FLOOR, time.time() + 0.5
        while time.time() < end:  # poll: attempt 1 raced the result frame against the first airborne movement packet
            y = max(y, r.pos()[1])
        r.record(f"jump{i}", "jump", {}, bool(res) and res.get("status") == "ok" and y > FLOOR + 0.05, f"server y right after={y:.3f} body={res and res.get('evidence')}")
        time.sleep(1.0)
    for i in range(5):
        r.tp(-10.5, FLOOR, -10.5, -90)
        params = {"pos": {"x": 10.5, "y": FLOOR, "z": -10.5}, "tolerance": 1.0}
        since = len(r.hub.frames)
        walk = r.hub.request("walk_to", params)
        time.sleep(1.0)
        _, _, stop = r.act("stop", {})
        wres = r.hub.wait_result(walk, 3, since)
        time.sleep(0.5)
        a = r.pos()
        time.sleep(1.0)
        b = r.pos()
        ok = bool(wres) and wres["status"] == "cancelled" and bool(stop) and stop["status"] == "ok" and stop["evidence"]["inputs_cleared"] and math.dist(a, b) <= 0.05
        r.record(f"cancel{i}", "stop", params, ok, f"walk={wres and wres['status']} stop={stop and stop['evidence']} drift={math.dist(a, b):.3f}")
    for i in range(3):
        r.tp(-10.5, FLOOR, 10.5 - 4 * i, -90)
        params = {"pos": {"x": 10.5, "y": FLOOR, "z": 10.5 - 4 * i}, "tolerance": 1.0}
        aid = r.hub.request("walk_to", params)
        time.sleep(1.0)
        r.hub.conn.close(1001, "stub drops link (test)")
        time.sleep(1.0)
        a = r.pos()
        time.sleep(1.0)
        b = r.pos()
        since = len(r.hub.frames)
        r.hub.wait(lambda m: m["type"] == "state", 40, since)  # body reconnected (backoff 1-2 s)
        again = r.hub.request("walk_to", params, action_id=aid)
        res = r.hub.wait_result(again, 5, since)
        time.sleep(1.0)
        c = r.pos()
        ok = math.dist(a, b) <= 0.05 and bool(res) and res["status"] in ("cancelled", "uncertain") and math.dist(b, c) <= 0.05
        r.record(f"linkloss{i}", "walk_to", params, ok, f"drift_after_loss={math.dist(a, b):.3f} resend_same_id={res and (res['status'], res['code'])} "
                 f"moved_after_resend={math.dist(b, c):.3f}")
    r.finish(CP09_CRITERIA)


CP12_CRITERIA = """Client side of CP-12 only (sense owns /eve stop; this uses the dev-only /evetest sender with the same eve:control bytes).
Per stop attempt, all must hold:
- the running action ends `cancelled`; inputs read back from the client are all clear within 2 client ticks of the stop
  (client_input_empty, last_sent_input_empty, not using/destroying/sprinting, attack/use released);
- body latency receipt -> inputs cleared <= 50 ms (one tick); for eve:control the server sees the client input go empty within
  3 server ticks of sending and the player still (5 consecutive motionless server ticks) within 15 ticks, no drift after that
  (<= 0.05) [still-detector fixed after attempt 1, which fired on a single packet-less tick; thresholds unchanged];
- server position moves <= 0.05 between +0.5 s and +1.5 s after the stop;
- no automatic resume: an action.request after the stop is rejected `stopped`, also after the link drops and reconnects and
  (mind-down variant) after the mind comes back; only an explicit resume ends STOPPED.
End-to-end latency (mind send -> control.ack received; server send -> eve:control_ack received) is recorded, not thresholded.
Fixture change after attempt 6 (thresholds unchanged): every non-player entity is killed before each trial and trader/mob spawning
is off, because a wandering trader pushed a STOPPED Eve 0.2-0.5 blocks (physics, not input) in attempt 6."""


def evetest(r: Run, op, extra=""):
    """Send eve:control via the dev /evetest command and return its log lines (send, ack, watch) once the 40-tick watch ends."""
    log = BODY / "run-server" / "evetest-log.jsonl"
    start = log.stat().st_size if log.exists() else 0
    r.rcon(f"evetest control {op} {r.player} {extra}".strip())
    deadline, out = time.time() + 4, {}
    while time.time() < deadline and not ("ack" in out and ("watch" in out or op != "stop")):
        time.sleep(0.2)
        with open(log, encoding="utf-8") as f:
            f.seek(start)
            for line in f:
                o = json.loads(line)
                out[o["type"]] = o
    return out


def cp12(r: Run):
    r.fixture()
    r.hub.send("control.resume", {"source": "arena-setup"})
    time.sleep(0.5)

    def walk(i):
        r.tp(-12.5, FLOOR, -12.5 + 3 * (i % 8), -90)
        return r.hub.request("walk_to", {"pos": {"x": 12.5, "y": FLOOR, "z": -12.5 + 3 * (i % 8)}, "tolerance": 1.0})

    def after_stop(since, aid=None):
        ev = r.hub.wait(lambda m: m["type"] == "event" and m["payload"]["detail"].get("inputs_cleared") is not None, 3, since)
        time.sleep(0.5)
        a = r.pos()
        time.sleep(1.0)
        b = r.pos()
        probe = r.hub.request("look_at", {"pos": {"x": 0, "y": FLOOR + 1, "z": 30}})
        rej = r.hub.wait_result(probe, 3, since)
        res = r.hub.wait_result(aid, 3, since) if aid else None
        return ev and ev["msg"]["payload"]["detail"], math.dist(a, b), rej, res

    def resume():
        r.hub.send("control.resume", {"source": "arena"})
        time.sleep(0.3)

    variants = ["link_walk", "server_walk", "link_sneak", "server_sneak", "link_idle", "server_idle", "link_turning", "server_turning"]
    for i in range(96):
        v = variants[i % len(variants)]
        via_link = v.startswith("link")
        r.rcon(ARENA_KILL)
        since = len(r.hub.frames)
        aid = None
        if v.endswith("walk"):
            aid = walk(i)
            time.sleep(1.0)
        elif v.endswith("sneak"):
            r.act("sneak", {"on": True})
        elif v.endswith("turning"):
            r.tp(0.5, FLOOR, 0.5, 0, 0)
            aid = r.hub.request("look_at", {"pos": {"x": 0.5, "y": FLOOR + 1.6, "z": -20}})
            time.sleep(0.06)
        t_send = time.time()
        srv = {}
        if via_link:
            r.hub.send("control.stop", {"reason": f"cp12 {v}", "source": "arena"})
            ack = r.hub.wait(lambda m: m["type"] == "control.ack" and m["payload"]["op"] == "stop", 2, since)
            body_ms = ack and ack["msg"]["payload"]["latency_ms"]
            e2e_ms = ack and (ack["t"] - t_send) * 1000
        else:
            srv = evetest(r, "stop")
            body_ms = srv.get("ack", {}).get("client", {}).get("latency_ms")
            e2e_ms = srv.get("ack", {}).get("rtt_ms")
        cleared, drift, rej, res = after_stop(since, aid)
        sneak_off = not r.test('{type:"minecraft:entity_properties",entity:"this",predicate:{flags:{is_sneaking:true}}}')
        w = srv.get("watch", {})
        checks = {
            "cleared<=2ticks": bool(cleared) and cleared["inputs_cleared"] and cleared["ticks_after_clear"] <= 2,
            "body<=50ms": body_ms is not None and body_ms <= 50,
            "no_drift": drift <= 0.05,
            "no_auto_resume": bool(rej) and rej.get("code") == "stopped",
            # rejected `stopped` = the stop overtook the request on the other channel: it never started (attempt 4 crashed on this)
            "action_cancelled": aid is None or (bool(res) and (res.get("status") in ("cancelled", "ok") or res.get("code") == "stopped")),
            "sneak_released": sneak_off,
        }
        if not via_link:
            checks["server_input_empty<=3"] = 0 <= w.get("server_input_empty_after_ticks", -1) <= 3
            checks["server_still<=15"] = 0 <= w.get("still_after_ticks", -1) <= 15 and w.get("drift_after_still_blocks", 1) <= 0.05
        r.record(f"{v}#{i}", "stop", {}, all(checks.values()),
                 f"body_ms={body_ms} e2e_ms={e2e_ms and round(e2e_ms, 1)} drift={drift:.3f} action={res and (res.get('status') or res.get('code'))} "
                 f"probe={rej and rej.get('code')} watch={w and {k: w[k] for k in ('server_input_empty_after_ticks', 'still_after_ticks', 'drift_after_still_blocks')}} "
                 + " ".join(k for k, ok in checks.items() if not ok), body_ms=body_ms, e2e_ms=e2e_ms, cleared=cleared, server=srv)
        # STOPPED must survive a link drop and reconnect.
        if i % 4 == 3:
            s2 = len(r.hub.frames)
            r.hub.conn.close(1001, "cp12 link drop while stopped")
            st = r.hub.wait(lambda m: m["type"] == "state", 40, s2)
            probe = r.hub.request("look_at", {"pos": {"x": 0, "y": FLOOR + 1, "z": 30}})
            rej = r.hub.wait_result(probe, 3, s2)
            ok = bool(st) and st["msg"]["payload"]["control"]["stopped"] and bool(rej) and rej.get("code") == "stopped"
            r.record(f"reconnect_still_stopped#{i}", "stop", {}, ok, f"state.stopped={st and st['msg']['payload']['control']['stopped']} probe={rej and rej.get('code')}")
        resume()
    # Mind down: eve:control alone must stop her, and STOPPED must still hold when the mind returns.
    for i in range(4):
        mind_down(r)
        srv = evetest(r, "stop")
        st = mind_up(r)
        probe = r.hub.request("look_at", {"pos": {"x": 0, "y": FLOOR + 1, "z": 30}})
        rej = r.hub.wait_result(probe, 3)
        a = srv.get("ack", {}).get("client", {})
        ok = a.get("stopped") is True and bool(st) and st["msg"]["payload"]["control"]["stopped"] and bool(rej) and rej.get("code") == "stopped"
        r.record(f"mind_down#{i}", "stop", {}, ok, f"ack={a} rtt_ms={srv.get('ack', {}).get('rtt_ms')} state.stopped={st and st['msg']['payload']['control']['stopped']} probe={rej and rej.get('code')}")
        resume()
    # Refusals: autonomous mode, home while stopped / not stopped, resume via eve:control.
    since = len(r.hub.frames)
    r.hub.send("control.mode", {"mode": "autonomous"})
    err = r.hub.wait(lambda m: m["type"] == "error" and m["payload"]["code"] == "mode_not_qualified", 2, since)
    r.record("mode_autonomous_link", "control", {}, bool(err), f"error={err and err['msg']['payload']}")
    srv = evetest(r, "mode", "autonomous")
    r.record("mode_autonomous_server", "control", {}, srv.get("ack", {}).get("client", {}).get("error") == "mode_not_qualified", f"ack={srv.get('ack', {}).get('client')}")
    evetest(r, "stop")
    srv = evetest(r, "resume")
    st = r.hub.wait(lambda m: m["type"] == "state" and not m["payload"]["control"]["stopped"], 3, len(r.hub.frames) - 1)
    r.record("server_resume", "control", {}, srv.get("ack", {}).get("client", {}).get("stopped") is False and bool(st), f"ack={srv.get('ack', {}).get('client')}")
    r.finish(CP12_CRITERIA, "Stop while mining, attacking, using an item: tools/cp10.py cp12. Home routes: arena.py cp12home.")


def mind_down(r: Run):
    """The mind really goes away: close the link (the body sees it at once) and stop listening, so every reconnect is refused.
    (CP-12 attempts before 2026-10-19 only stopped listening: the open link went silent, not down, for its first 15 s.)"""
    if r.hub.conn:
        r.hub.conn.close(1001, "mind down (test)")
    r.hub.server.shutdown()
    r.hub.conn = None
    time.sleep(1.0)


def mind_up(r: Run):
    r.hub = Hub(r.dir / "frames.jsonl", verbose=False).start()
    return r.hub.wait(lambda m: m["type"] == "state", 45)


HOME = (-12.5, FLOOR, 12.5)  # must equal the body's config/envelope.json home (checked at start)
ENVELOPE = Path(os.environ.get("EVE_ENVELOPE", str(BODY / "run" / "config" / "envelope.json")))  # ponytail: Shark dev client only
CP12_HOME_CRITERIA = f"""Home half of CP-12 ("home follows a qualified safe route or reports that return is blocked"). Envelope home
{HOME} in the overworld, read by the body from its config/envelope.json. 22 variants x 4, interleaved. Per attempt:
- the expected outcome: `arrived` where a walking route with no block broken or placed and no drop over 3 exists (open floor, a wall to
  go round, stairs down a pillar, home asked while walking elsewhere); `home_blocked` where none does (Eve walled in, home walled in, home
  on an island in a 3-wide lava moat, Eve on a 6-high pillar, no envelope home, home in another dimension); `refused_stopped` /
  rejected `stopped` while STOPPED; `cancelled` by a stop mid-route;
- server truth every time: Health never below its value at the start and no body damage event; Fire <= 0; every fixture block still in
  place; she stands still (<= 0.05) in the 1 s after the outcome;
- arrived: server position within 3.0 of home, within 1.5 x route / 4.317 b/s + 5 s, evidence route_kind, blocks_broken 0,
  blocks_placed 0, distance_to_home <= 3; blocked: outcome within 20 s of the request;
- STOPPED: no movement (<= 0.05 over 2 s); stop mid-route: inputs cleared (read back), drift <= 0.05 between +0.5 s and +1.5 s;
- mind down (link closed, reconnects refused): eve:control home starts nothing, server position still over 3 s, and after the mind is
  back no action is running.
Paths: control.home (link), eve:control home (/evetest, the same bytes sense sends), action.request go_home (the mind's skill)."""


def cp12home(r: Run):
    r.fixture()
    F, P = int(FLOOR), r.player
    r.rcon(f"fill -2 {F} 13 2 {F} 19 minecraft:air")  # cp09's step
    r.rcon("execute in minecraft:the_nether run fill -3 100 -3 3 104 3 minecraft:air")
    r.rcon("execute in minecraft:the_nether run fill -3 99 -3 3 99 3 minecraft:obsidian")
    env = json.loads(ENVELOPE.read_text())
    c = env["home"]["center"]
    if env["home"]["dim"] != "minecraft:overworld" or (c["x"], c["y"], c["z"]) != HOME:
        sys.exit(f"{ENVELOPE}: home {env['home']} is not the fixture's {HOME}")

    def cells(x0, y0, z0, x1, y1, z1, block, hollow=False):
        return [((x, y, z), block) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1) for z in range(z0, z1 + 1)
                if not hollow or x in (x0, x1) or y in (y0, y1) or z in (z0, z1)]

    def struct(*fills):  # each (x0, y0, z0, x1, y1, z1, block, hollow, block_to_restore)
        build = [f"fill {' '.join(map(str, f[:6]))} {f[6]}{' hollow' if f[7] else ''}" for f in fills]
        tear = [f"fill {' '.join(map(str, f[:6]))} {f[8]}" for f in reversed(fills)]
        return build, tear, fills

    S = {
        "wall": struct((-2, F, 3, -2, F + 2, 22, "minecraft:stone", False, "minecraft:air")),
        "box_eve": struct((6, F, 11, 8, F + 3, 13, "minecraft:glass", True, "minecraft:air")),
        "box_home": struct((-16, F, 9, -9, F + 3, 16, "minecraft:glass", True, "minecraft:air")),
        "moat": struct((-18, F - 1, 7, -7, F - 1, 18, "minecraft:lava", False, "minecraft:smooth_stone"),
                       (-15, F - 1, 10, -10, F - 1, 15, "minecraft:smooth_stone", False, "minecraft:smooth_stone")),
        "pillar": struct((6, F, 11, 8, F + 5, 13, "minecraft:stone", False, "minecraft:air")),
        "stairs": struct((6, F, 11, 8, F + 5, 13, "minecraft:stone", False, "minecraft:air"),
                         *[(5 - k, F, 11, 5 - k, F + 4 - k, 13, "minecraft:stone", False, "minecraft:air") for k in range(5)]),
    }

    def broken(fills):
        want = {}
        for f in fills:  # later fills overwrite earlier ones (the moat's island)
            for pos, b in cells(*f[:7], hollow=f[7]):
                want[pos] = b
        return [pos for pos, b in want.items() if r.rcon(f"execute if block {pos[0]} {pos[1]} {pos[2]} {b}") != "Test passed"]

    def health():
        return nums(r.rcon(f"data get entity {P} Health"))[0]

    def fire():
        return nums(r.rcon(f"data get entity {P} Fire"))[0]

    def damage(since):
        return [f["msg"]["payload"]["detail"] for f in r.hub.frames[since:] if f["dir"] == "in" and f["msg"]["type"] == "event"
                and f["msg"]["payload"]["kind"] == "damage"]

    def home(how):
        if how == "link":
            r.hub.send("control.home", {"reason": "cp12 home"})
        elif how == "server":
            return evetest(r, "home")
        else:
            return r.hub.request("go_home", {})

    def outcome(how, h, since, timeout=60):
        """(outcome, evidence, reason). action path: arrived | failure code | rejected:<code>; control paths: the home event's result."""
        if how == "action":
            res = r.hub.wait_result(h, timeout, since)
            if not res:
                return "none", {}, None
            if "status" not in res:
                return "rejected:" + res["code"], {}, res.get("reason")
            return {"ok": "arrived", "failed": res.get("code")}.get(res["status"], res["status"]), res.get("evidence") or {}, res.get("note")
        f = r.hub.wait(lambda m: m["type"] == "event" and m["payload"]["detail"].get("op") == "home"
                       and m["payload"]["detail"].get("result") != "started", timeout, since)
        if not f:
            return "none", {}, None
        d = f["msg"]["payload"]["detail"]
        return d["result"], d.get("evidence") or {}, d.get("reason")

    def attempt(name, start, how, expect, s=None, route=None, pre=None, post=None):
        build, tear, fills = S[s] if s else ([], [], [])
        for cmd in build:
            r.rcon(cmd)
        r.tp(*start)
        extra = pre() if pre else {}
        h0, since, t = health(), len(r.hub.frames), time.time()
        h = home(how)
        o, ev, why = outcome(how, h, since)
        el = time.time() - t
        p1 = r.pos()
        time.sleep(1.0)
        p2 = r.pos()
        bad = broken(fills)
        h1, fi, dmg = health(), fire(), damage(since)
        if post:
            extra.update(post())
        for cmd in tear:
            r.rcon(cmd)
        checks = {"outcome": o == expect, "health": h1 >= h0 and not dmg, "fire<=0": fi <= 0, "blocks_intact": not bad,
                  "still_after": math.dist(p1, p2) <= 0.05, **{k: v for k, v in extra.items() if isinstance(v, bool)}}
        if expect == "arrived":
            budget = 1.5 * route / 4.317 + 5
            checks.update({"at_home<=3": math.dist(p1, HOME) <= 3.0, "time": el <= budget,
                           "evidence": bool(ev.get("route_kind")) and ev.get("blocks_broken") == 0 and ev.get("blocks_placed") == 0
                           and ev.get("distance_to_home", 99) <= 3})
        else:
            checks["time<=20s"] = el <= 20
        r.record(f"{name}_{how}#{rep}", "go_home", {"how": how}, all(checks.values()),
                 f"outcome={o} reason={why!r} t={el:.1f}s srv_dist={math.dist(p1, HOME):.2f} hp={h0}->{h1} fire={fi} broken={len(bad)} "
                 f"drift={math.dist(p1, p2):.3f} " + " ".join(k for k, v in checks.items() if not v),
                 server_pos=p1, server_pos_1s=p2, evidence=ev, elapsed_s=el, damage=dmg, broken=bad[:10], extra=extra)

    def stopped(how):
        r.tp(7.5, FLOOR, 12.5, 90)
        evetest(r, "stop")
        time.sleep(0.3)
        a, since = r.pos(), len(r.hub.frames)
        o, ev, why = outcome(how, home(how), since, 5)
        time.sleep(2.0)
        b = r.pos()
        expect = "rejected:stopped" if how == "action" else "refused_stopped"
        st = r.state()
        ok = o == expect and math.dist(a, b) <= 0.05 and bool(st) and st["control"]["stopped"]
        r.record(f"stopped_{how}#{rep}", "go_home", {"how": how}, ok, f"outcome={o} reason={why!r} moved={math.dist(a, b):.3f}")
        r.hub.send("control.resume", {"source": "arena"})
        time.sleep(0.3)

    def stop_mid(how):
        r.tp(7.5, FLOOR, 12.5, 90)
        since = len(r.hub.frames)
        home(how)
        time.sleep(1.5)
        if how == "link":
            r.hub.send("control.stop", {"reason": "cp12 stop mid home", "source": "arena"})
        else:
            evetest(r, "stop")
        o, ev, why = outcome(how, None, since, 5)
        cl = r.hub.wait(lambda m: m["type"] == "event" and m["payload"]["detail"].get("inputs_cleared") is not None, 3, since)
        time.sleep(0.5)
        a = r.pos()
        time.sleep(1.0)
        b = r.pos()
        cleared = cl and cl["msg"]["payload"]["detail"]
        ok = o == "cancelled" and bool(cleared) and cleared["inputs_cleared"] and math.dist(a, b) <= 0.05 and math.dist(a, HOME) > 3
        r.record(f"stop_mid_{how}#{rep}", "go_home", {"how": how}, ok, f"outcome={o} reason={why!r} cleared={cleared and cleared['inputs_cleared']} "
                 f"drift={math.dist(a, b):.3f} srv_dist={math.dist(a, HOME):.2f}")
        r.hub.send("control.resume", {"source": "arena"})
        time.sleep(0.3)

    def while_walking():
        held = {}

        def pre():
            held["since"] = len(r.hub.frames)
            held["walk"] = r.hub.request("walk_to", {"pos": {"x": 7.5, "y": FLOOR, "z": -20.5}, "tolerance": 1.0})
            time.sleep(1.0)
            return {}

        def post():
            w = r.hub.wait_result(held["walk"], 3, held["since"])
            return {"walk_cancelled": bool(w) and w.get("status") == "cancelled", "walk_result": w and [w.get("status"), w.get("note")]}
        attempt("while_walking", (7.5, FLOOR, 2.5, 180), "link", "arrived", route=26, pre=pre, post=post)

    def no_envelope(how):
        bak = ENVELOPE.with_name(ENVELOPE.name + ".cp12bak")
        ENVELOPE.rename(bak)
        try:
            time.sleep(1.5)  # the body re-checks the file at most once a second
            attempt("no_envelope", (7.5, FLOOR, 12.5, 90), how, "rejected:precondition_failed" if how == "action" else "home_blocked")
        finally:
            bak.rename(ENVELOPE)
            time.sleep(1.5)

    def other_dim():
        def pre():
            r.rcon(f"execute in minecraft:the_nether run tp {P} 0.5 100 0.5")
            time.sleep(2.0)
            return {}
        attempt("other_dim", (7.5, FLOOR, 12.5, 90), "link", "home_blocked", pre=pre,
                post=lambda: {"in_nether": "the_nether" in r.rcon(f"data get entity {P} Dimension")})

    def mind_gone():
        r.tp(7.5, FLOOR, 12.5, 90)
        mind_down(r)
        a = r.pos()
        srv = evetest(r, "home")
        time.sleep(3.0)
        b = r.pos()
        st = mind_up(r)
        ctl = st and st["msg"]["payload"]["control"]
        ack = srv.get("ack", {}).get("client", {})
        ok = bool(ack) and "error" not in ack and math.dist(a, b) <= 0.05 and bool(ctl) and ctl["active_action_id"] is None and "internal_action" not in ctl
        r.record(f"mind_down_server#{rep}", "go_home", {"how": "server"}, ok, f"ack={ack} moved={math.dist(a, b):.3f} control_after={ctl}")

    E = (7.5, FLOOR, 12.5, 90)
    variants = [
        lambda: attempt("clear", E, "link", "arrived", route=20),
        lambda: attempt("clear", E, "server", "arrived", route=20),
        lambda: attempt("clear", (7.5, FLOOR, 2.5, 90), "action", "arrived", route=23),
        lambda: attempt("wall_detour", E, "link", "arrived", "wall", route=29),
        lambda: attempt("box_eve", (7.5, FLOOR + 1, 12.5, 90), "link", "home_blocked", "box_eve"),
        lambda: attempt("box_eve", (7.5, FLOOR + 1, 12.5, 90), "server", "home_blocked", "box_eve"),
        lambda: attempt("box_home", E, "link", "home_blocked", "box_home"),
        lambda: attempt("box_home", E, "action", "home_blocked", "box_home"),
        lambda: attempt("lava_moat", E, "link", "home_blocked", "moat"),
        lambda: attempt("lava_moat", E, "server", "home_blocked", "moat"),
        lambda: attempt("cliff_6", (7.5, FLOOR + 6, 12.5, 90), "link", "home_blocked", "pillar"),
        lambda: attempt("stairs_down", (7.5, FLOOR + 6, 12.5, 90), "link", "arrived", "stairs", route=25),
        lambda: stopped("link"),
        lambda: stopped("server"),
        lambda: stopped("action"),
        lambda: stop_mid("link"),
        lambda: stop_mid("server"),
        while_walking,
        lambda: no_envelope("link"),
        lambda: no_envelope("action"),
        other_dim,
        mind_gone,
    ]
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None  # smoke runs: variant indexes
    reps = int(sys.argv[sys.argv.index("--reps") + 1]) if "--reps" in sys.argv else 4
    global rep
    for rep in range(reps):
        for i, v in enumerate(variants):
            if only is None or str(i) in only:
                v()
    r.finish(CP12_HOME_CRITERIA, "Each variant's fixture is built before and torn down after its attempt; the arena is otherwise empty.")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "cp09"
    player = sys.argv[sys.argv.index("--player") + 1] if "--player" in sys.argv else "EveDev"
    run = Run({"cp09": "CP-09", "cp12": "CP-12", "cp12home": "CP-12"}[which], player)
    try:
        {"cp09": cp09, "cp12": cp12, "cp12home": cp12home}[which](run)
    except BaseException as e:  # an aborted attempt still gets its RESULT.md, counted as it stands
        run.finish({"cp09": CP09_CRITERIA, "cp12": CP12_CRITERIA, "cp12home": CP12_HOME_CRITERIA}[which], f"**ABORTED** after {len(run.rows)} attempts: {e!r}")
        raise


Run.REV0 = code_rev()  # at import: the code this process runs, whatever lands on the tree before it finishes
