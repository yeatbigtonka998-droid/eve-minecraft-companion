"""CP-29 creeper fixtures on a lane (laneb_combat's lane table: --lane c|b|45L). Body's creeper_flee reflex is the response
(it pre-empts any action); this harness checks it during real actions, judged by the server.

    py -3.12 parts/combat/tools/cp29_creeper.py [--lane c] [--n 4] [--only k_fight,k_trapped]

Each trial: arena rebuilt, kit (iron sword, pickaxe, iron armour, bread, off-hand shield), a background action through the mind's
/cli (a sword fight, mining, or a long walk standing in for field work; crop work waits for a harvest skill), and 1.5 s into it a
creeper summoned next to her: lit (`ignited`, 1.5 s fuse) at 4 blocks, or unlit at 9 walking in. A "valuable" 3x3x3 wool hut
stands 6 blocks to one side (mob_griefing ON for this suite, so a blast near it breaks blocks; read and restored after).

Server watch every 0.1 s: her and the creeper's positions and her health. The blast = the tick the creeper vanishes; the record
is her distance from it then and the health she lost in the next second. Avoidable cases PASS when body's creeper_flee started
after the summon (client log, Shark lanes), she was >= 6 blocks from the blast (vanilla damage ends at 2 x power 3) or it never
exploded, she lost no health to it, no hut block broke, and she is alive. The trapped case (walled 3x3 pocket, lit creeper inside)
is recorded apart: PASS = alive and the reflex ended cornered_shielded (the least-damage response this body has); damage is
recorded. Every trial counted; a death is a failure. Evidence: evidence/CP-29/<stamp>-<label>-attempt-N, summary in
parts/combat/evidence-summaries/.
"""
import json, math, re, sqlite3, subprocess, sys, threading, time
from datetime import datetime
from pathlib import Path

sys.argv = [sys.argv[0], "cp26"] + sys.argv[1:]  # laneb_combat parses --lane from argv at import
sys.path.insert(0, str(Path(__file__).resolve().parent))
import laneb_combat as L  # noqa: E402

r, P, X0, X1, Z0, Z1, Y, CX, CZ = L.r, L.P, L.X0, L.X1, L.Z0, L.Z1, L.Y, L.CX, L.CZ
TAG, HUT = "evecr", "minecraft:white_wool"
LOG = L.LANE / "client" / "logs" / "latest.log" if L.QUAL else L.LANE / "logs" / "client.log"  # 45L path per foundation


def pos_of(sel):
    m = re.findall(r"(-?\d+(?:\.\d+)?)d", r(f"data get entity {sel} Pos"))
    return tuple(float(v) for v in m[:3]) if len(m) >= 3 else None


def dist(a, b):
    return math.dist(a, b) if a and b else None


def hut(x, z, place):
    """A 3x3x3 wool hut at (x, z); returns its wool block count. `fill` counts CHANGED blocks, so the count swaps the wool to
    light-grey and back (attempt 1 read 0/0 by replacing wool with wool)."""
    if place:
        r(f"fill {x} {Y + 1} {z} {x + 2} {Y + 3} {z + 2} {HUT}")
    m = re.search(r"(\d+) block", r(f"fill {x} {Y + 1} {z} {x + 2} {Y + 3} {z + 2} minecraft:light_gray_wool replace {HUT}"))
    r(f"fill {x} {Y + 1} {z} {x + 2} {Y + 3} {z + 2} {HUT} replace minecraft:light_gray_wool")
    return int(m.group(1)) if m else 0


class Watch(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop, self.samples, self.blast, self.seen = threading.Event(), [], None, False

    def run(self):
        last = None
        while not self.stop.is_set():
            try:
                e, h, c = pos_of(P), L.health(), pos_of(f"@e[tag={TAG},limit=1]")
            except Exception:
                time.sleep(0.1)
                continue
            t = time.time()
            self.samples.append((t, e, h, c))
            self.seen |= c is not None
            if last and last[3] and not c and self.blast is None:
                self.blast = {"t": t, "creeper": last[3], "eve": last[1], "hp_before": last[2], "distance": round(dist(last[1], last[3]), 2)}
            last = (t, e, h, c)
            time.sleep(0.1)


MIND_DB = L.LANE / "mind" / "eve.db"


def follow_events(since_ms, kind="creeper_follow"):
    """O53c evidence: the mind's `kind` events since since_ms (creeper_follow; dispatch_refused: a14 lost every shot to busy
    refusals), read-only from the lane mind's DB."""
    try:
        con = sqlite3.connect(f"file:{MIND_DB.as_posix()}?mode=ro", uri=True, timeout=5)
        rows = con.execute("SELECT ts, payload FROM events WHERE kind=? AND ts >= ? ORDER BY ts", (kind, since_ms)).fetchall()
        con.close()
        return [{"ts": ts, **json.loads(p)} for ts, p in rows]
    except sqlite3.Error as e:
        return [{"error": str(e)}]


def check_mind():
    """--expect-mind <commit> (O53c): the mind must report its own commit in status (mind_commit) and contain <commit>."""
    if "--expect-mind" not in sys.argv:
        return None
    want = sys.argv[sys.argv.index("--expect-mind") + 1]
    st = L.eve("status")
    have = st.get("mind_commit")
    if not have:
        sys.exit(f"--expect-mind {want}: the mind's status has no mind_commit; not running")
    if (L.REPO / ".git").exists():
        ok = subprocess.run(["git", "-C", str(L.REPO), "merge-base", "--is-ancestor", want, have], capture_output=True).returncode == 0
    else:  # a git-archive extract (the 45L) cannot check ancestry: accept only the exact commit (foundation, CP-29 45L prep)
        ok = have.startswith(want) or want.startswith(have)
    if not ok:
        sys.exit(f"--expect-mind {want}: the lane mind runs {have}, which does not contain it; not running")
    return have


def log_size():
    return LOG.stat().st_size if LOG.exists() else None


def log_since(offset):
    if offset is None or not LOG.exists():
        return None
    with open(LOG, "rb") as f:
        f.seek(offset)
        return f.read().decode("utf-8", "replace")


def kit():
    L.kit()
    r(f"item replace entity {P} hotbar.1 with minecraft:iron_pickaxe")
    r(f"item replace entity {P} weapon.offhand with minecraft:shield")


def facing():
    m = re.findall(r"(-?\d+(?:\.\d+)?)f", r(f"data get entity {P} Rotation"))
    return float(m[0]) if m else 0.0


def behind(dist_, side_deg=180):
    """A point dist_ blocks from Eve, side_deg clockwise from her facing (180 = behind)."""
    e, yaw = pos_of(P), facing()
    a = math.radians(yaw + side_deg)
    return e[0] - math.sin(a) * dist_, e[1], e[2] + math.cos(a) * dist_


def away_from_hut(d, hx, hz):
    """A point d blocks from Eve, in whichever of 8 directions lies farthest from the hut box. 45L CP-29 a2: 'far side from
    the hut's centre' still put a lit creeper ~4.5 from the hut's corner on the 45L geometry, and its blast took a block."""
    e = pos_of(P)
    def hut_gap(x, z):  # distance from (x, z) to the 3x3 hut box
        return math.hypot(max(hx - x, 0, x - (hx + 3)), max(hz - z, 0, z - (hz + 3)))
    pts = [(e[0] + math.cos(a) * d, e[2] + math.sin(a) * d) for a in (k * math.pi / 4 for k in range(8))]
    x, z = max(pts, key=lambda q: hut_gap(*q))
    return x, e[1], z


BLAST_REACH = 6.0  # an uncharged creeper (power 3) damages entities only within 2 x power blocks


def attribute(distance, drop):
    """O91: an HP drop in the second after a blast is blast damage only when the blast was within BLAST_REACH; beyond it the
    drop is something else (k_fight's zombie: a48 #4 lost 0.62 to a 1.38 zombie hit 0.39 s after a blast 8.28 away).
    Returns (blast_damage, other_damage)."""
    if distance is not None and distance > BLAST_REACH:
        return 0.0, drop
    return drop, 0.0


def creeper(at, lit):
    nbt = f'Tags:["{TAG}"],PersistenceRequired:1b' + (",ignited:1b,Fuse:30s" if lit else "")
    r(f"summon minecraft:creeper {at[0]:.2f} {at[1]:.2f} {at[2]:.2f} {{{nbt}}}")


# ---- cases: setup() -> (skill, params, lit, creeper_dist, side_deg, trapped) ----
def k_fight(k):
    L.stand(0); L.zombie(*L.at(5, 0))
    return "fight_melee", {"max_targets": 1, "radius": 16 + k / 100}, True, 4, 180, False


def k_mine(k):
    L.stand(0)
    r(f"fill {CX - 2} {Y + 1} {CZ + 2} {CX + 3} {Y + 1} {CZ + 2} minecraft:stone")
    return "mine_resource", {"block": "minecraft:stone", "count": 6, "radius": 8 + k % 24}, True, 4, 180, False  # unique params per trial: mind CP-22 retry_limit refused a35 #13-16 (same params failed 3x in 10 min)


def walk_target(k):
    """The arena's north-west corner: inside the mind's explore_radius from home. a1-a8 walked toward the south edge, which is
    130+ from lane C's home (radius 128), so the mind refused every walk out_of_bounds and no action was running."""
    return {"pos": {"x": X0 + 3.5 + k % 3, "y": Y + 1, "z": Z0 + 3.5}, "tolerance": 1.5}


def k_walk(k):
    """Lit creeper at 4 during a walk, on her LEFT (east, away from the hut). a1-a7 summoned it on her right, 3.5 blocks
    from the hut: a lit creeper blows where it stands, so the hut loss was the fixture's, not her flee's (she was 10.5-11 east)."""
    L.stand(0)
    return "walk_to", walk_target(k), True, 4, None, False  # side None = on her far side from the hut


FOLLOW_AT = float(sys.argv[sys.argv.index("--follow-at") + 1]) if "--follow-at" in sys.argv else 6.0  # O72h (b): 4 allowed, recorded


def k_follow(k):
    """The structure test: an UNLIT creeper 6 east of her during a walk follows her; straight away from it is west, toward the
    hut. A flee that leads it to the hut gets the hut blown; body's structure-aware flee (4b14c6c) should pick another way.
    With a bow and arrows (as Eve carries) the mind's O53 rule cancels the resumed walk and dispatches fight_ranged on it."""
    # from the south-east corner: a ~34-block walk to the NW target, so the follower closes in while the walk is still open
    # (a26: 9/20 invalid, the ~17-block walk from the centre ended before its second approach, and O53 needs an open walk)
    L.tp(X1 - 3.5, Y + 1, Z1 - 3.5, 135, 0)
    time.sleep(1.0)
    r(f"item replace entity {P} hotbar.2 with minecraft:bow")
    r(f"item replace entity {P} inventory.0 with minecraft:arrow 32")
    return "walk_to", walk_target(k), False, FOLLOW_AT, 90, False  # at her side: it closes in during the walk (a24 ahead: 16/20; a25 behind: she outwalked it, 0 valid)


def k_follow_shallow(k):
    """O72a: k_follow unchanged, in the 45L's 47x31 box (naming this case sets laneb_combat's wide arena for the run). The walk
    from the SE corner runs shallow along the south wall, and the follower at her side pins her there: 45L a3 1/11 valid."""
    return k_follow(k)


def k_approach(k):
    L.stand(0); L.zombie(*L.at(6, 0))
    return "fight_melee", {"max_targets": 1, "radius": 16 + k / 100}, False, 9, 180, False


def k_approach_bow(k):
    """The unlit creeper walking in from 12, answered with the bow (orchestrator after a2): fight_ranged takes a visible creeper
    first and shoots it beyond 7; inside 7 body's flee pre-empts, then she shoots again. Win = no blast at all, or >= 6 away."""
    L.stand(0)
    r(f"item replace entity {P} hotbar.2 with minecraft:bow")
    r(f"item replace entity {P} inventory.0 with minecraft:arrow 32")
    return "fight_ranged", {"radius": 24 + k / 100, "max_arrows": 16}, False, 12, 180, False


def k_corner_bow(k):
    """O72d dev case for combat's piece: Eve cornered (1 from both walls of the SE corner, facing out), fight_ranged on an
    unlit creeper walking in from 9 in front. Combat's criterion: the first shot within 30 ticks of the call (shot_log tick)."""
    L.tp(X1 - 1.5, Y + 1, Z1 - 1.5, 135, 0)
    time.sleep(1.0)
    r(f"item replace entity {P} hotbar.2 with minecraft:bow")
    r(f"item replace entity {P} inventory.0 with minecraft:arrow 32")
    return "fight_ranged", {"radius": 24 + k / 100, "max_arrows": 16}, False, 9, 0, False


def k_corner_close(k):
    """mind O72i: its cornered dispatch can start fight_ranged with the creeper already inside 7.5. Same corner as k_corner_bow,
    creeper from 5 in front: the skill must have the sword (shield) out, never a drawn bow at the blast."""
    skill, params, lit, _, side, trapped = k_corner_bow(k)
    return skill, params, lit, 5, side, trapped


def k_trapped(k):
    """A walled 3x3 pocket (walls 3 high, roofed) with a lit creeper inside: no escape; the shield is the only answer."""
    x, z = CX - 1, CZ - 1
    r(f"fill {x - 1} {Y + 1} {z - 1} {x + 3} {Y + 4} {z + 3} minecraft:stone")
    r(f"fill {x} {Y + 1} {z} {x + 2} {Y + 3} {z + 2} minecraft:air")
    L.tp(x + 0.5, Y + 1, z + 0.5, -45, 0)
    time.sleep(1.0)
    return "walk_to", {"pos": {"x": x + 2.5, "y": Y + 1, "z": z + 2.5}, "tolerance": 0.5}, True, 2, 0, True


def k_follow_idle(k):
    """O53l: Eve idle (no action at all) with a bow and arrows; an unlit creeper starts 6 at her side and follows. The mind's
    idle creeper rule must dispatch fight_ranged (O53c: creeper_follow event + Eve's kill or a clear hold)."""
    L.stand(0)
    r(f"item replace entity {P} hotbar.2 with minecraft:bow")
    r(f"item replace entity {P} inventory.0 with minecraft:arrow 32")
    return None, {}, False, 6, 90, False


CASES = [k_fight, k_mine, k_walk, k_follow, k_follow_shallow, k_follow_idle, k_approach, k_approach_bow, k_corner_bow, k_corner_close, k_trapped]


def open_end():
    """A 3-wide gate in the east wall onto a glass-rimmed platform 10 long (lane C/B only; on the 45L it would leave
    reflex_arena): 'cornered' is then her choice, not the fixture's shape (orchestrator after a2)."""
    if L.QUAL:
        return
    r(f"fill {X1} {Y + 1} {CZ - 1} {X1} {Y + 3} {CZ + 1} minecraft:air")
    r(f"fill {X1 + 1} {Y} {CZ - 4} {X1 + 10} {Y} {CZ + 4} minecraft:smooth_stone")
    r(f"fill {X1 + 1} {Y + 1} {CZ - 4} {X1 + 10} {Y + 2} {CZ - 4} minecraft:glass")
    r(f"fill {X1 + 1} {Y + 1} {CZ + 4} {X1 + 10} {Y + 2} {CZ + 4} minecraft:glass")
    r(f"fill {X1 + 10} {Y + 1} {CZ - 4} {X1 + 10} {Y + 2} {CZ + 4} minecraft:glass")


def open_end_remove():
    if not L.QUAL:
        r(f"fill {X1 + 1} {Y} {CZ - 4} {X1 + 10} {Y + 2} {CZ + 4} minecraft:air")


def main():
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 4
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    cases = [c for c in CASES if not only or c.__name__ in only]
    st = L.eve("status")
    if not st.get("body"):
        sys.exit(f"lane {L.LANE_ID} body is not up: {st}")
    mind_commit = check_mind()
    L.lane_lock("acquire", 15 + len(cases) * n)
    if "--deploy" in sys.argv:  # standing permission (eve-watch): swap our own jar under our lock, restart the client
        L.deploy()
        st = L.eve("status")
    try:
        L.check_jar()
    except SystemExit:  # never exit holding the lane
        L.lane_lock("release")
        raise
    root = L.EVIDENCE / "CP-29"
    root.mkdir(parents=True, exist_ok=True)
    num = 1 + sum(1 for d in root.iterdir() if d.is_dir() and f"-{L.LABEL}-attempt-" in d.name)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    out = root / f"{stamp}-{L.LABEL}-attempt-{num}"
    out.mkdir()
    rows, t0, aborted = [], time.time(), None
    L.stash_save()
    was = {g: r(f"gamerule {g}").rsplit(" ", 1)[-1].strip() for g in ("spawn_mobs", "mob_griefing")}
    was["difficulty"] = r("difficulty").rsplit(" ", 1)[-1].strip().lower()
    try:
        r("gamerule spawn_mobs false")
        r("difficulty peaceful")  # 26.3 recomputes spawn flags only on a real difficulty change (I20): the next "normal" is one
        r("gamerule mob_griefing true")  # blasts break blocks: the hut's block count is the structure evidence
        cap_s = 60 * float(sys.argv[sys.argv.index("--max-minutes") + 1]) if "--max-minutes" in sys.argv else None  # S96: per case, checked at a trial boundary
        spent, last = {}, None
        for k in range(n):
            for case in cases:
                if last:
                    spent[last[0]] = spent.get(last[0], 0) + time.time() - last[1]
                last = None
                if cap_s and spent.get(case.__name__, 0) >= cap_s:
                    print(f"{case.__name__}: --max-minutes reached, trial {k + 1} skipped", flush=True)
                    continue
                last = (case.__name__, time.time())
                if not L.QUAL:  # S96: noon every trial on a lane, so the mind's dusk routine cannot cut a trial
                    r("time set 6000")
                L.build_arena(); L.clear_arena(); open_end(); kit()
                r(f"kill @e[tag={TAG}]")
                r("difficulty normal")
                d0 = L.deaths()
                skill, params, lit, cdist, side, trapped = case(k)
                hx, hz = CX - 9, CZ + 6  # hut south-west, off her north-west walk path (45L a2: 1.8 from the path, blasts took blocks)
                hut0 = hut(hx, hz, True)
                off = log_size()
                kc0 = L.creeper_kills()
                t0_ms = int(time.time() * 1000)
                res_box = {}
                th = threading.Thread(target=lambda: res_box.update(L.eve("do", {"skill": skill, "params": params, "wait": 25}, 60)), daemon=True)
                pre = skill == "fight_ranged"  # its target must exist first (a4-a8 failed no_target and never shot)
                L.eve("cancel", {"reason": "cp29: next trial"})  # a9: a still-running walk made the next trial refused/busy
                time.sleep(0.5)
                spot = (lambda: away_from_hut(cdist, hx, hz)) if side is None else (lambda: behind(cdist, side))
                if pre:
                    at = spot()
                    w = Watch(); w.start()
                    t_summon = time.time()
                    creeper(at, lit)
                    time.sleep(0.5)
                if skill:  # k_follow_idle: no action at all
                    th.start()
                if not pre:
                    time.sleep(1.5)
                    at = spot() if not trapped else (CX + 1.5, Y + 1, CZ + 1.5)
                    w = Watch(); w.start()
                    t_summon = time.time()
                    creeper(at, lit)
                end = time.time() + (6 if lit else 20)
                while time.time() < end and (w.blast is None):
                    time.sleep(0.2)
                time.sleep(1.2)  # the blast's damage and block updates
                w.stop.set(); w.join()
                r(f"kill @e[tag={TAG}]")
                if skill:
                    th.join(timeout=30)
                L.eve("cancel", {"reason": "cp29: trial over"})
                r(f"kill @e[tag={L.MOBTAG}]")
                res = res_box or ({"status": "idle"} if not skill else {"ok": False, "error": "no_result"})
                died = L.deaths() - d0
                hp_after = min([s[2] for s in w.samples if w.blast and s[0] >= w.blast["t"] and s[0] <= w.blast["t"] + 1.0 and s[2] is not None] or [None]) \
                    if w.blast else None
                drop = round(max(0.0, w.blast["hp_before"] - hp_after), 2) if w.blast and hp_after is not None and w.blast["hp_before"] is not None else 0.0
                blast_dmg, other_dmg = attribute(w.blast and w.blast["distance"], drop)
                hut1 = hut(hx, hz, False)
                text = log_since(off)
                fled = None if text is None else ("reflex creeper_flee start" in text)
                ends = re.findall(r"reflex creeper_flee end (\w+)", text or "")
                checks = [(died == 0, f"deaths {died}"), (hut1 == hut0, f"hut blocks {hut1}/{hut0}")]
                if skill != "fight_ranged":  # the bow case may kill it before it is ever close enough to flee from
                    checks.append((fled is not False, f"creeper_flee started: {fled}"))
                if trapped:
                    checks.append(("cornered_shielded" in ends, f"reflex ends {ends} (want cornered_shielded)"))
                elif case.__name__ == "k_corner_close":  # cornered with it at 5: no escape, so 0 damage (shield up) or a kill is the win
                    checks.append((blast_dmg == 0, f"blast damage {blast_dmg} (want 0; blast at {w.blast and w.blast['distance']})"))
                else:
                    far = w.blast is None or w.blast["distance"] >= 6
                    checks += [(far, f"distance at blast {w.blast and w.blast['distance']} (want >= 6, or no blast)"),
                               (blast_dmg == 0, f"blast damage {blast_dmg}")]
                ran = res.get("status") not in ("refused", None) and (res.get("code") not in ("no_target",)
                                                                       or (case.__name__ == "k_corner_close" and w.blast is not None))  # a40: pre-empted at 5, resumed after the blast
                if not trapped and skill:  # trapped / idle cases have no action to check; trapped tests only the cornered response (its walk target is outside explore_radius; no action is needed)
                    checks.append((ran, f"action ran: {res.get('status')}/{res.get('code') or res.get('error')}"))
                if skill == "fight_ranged" and case.__name__ != "k_corner_close":  # k_corner_close: a shielded blast is the win
                    killed = L.creeper_kills() > kc0
                    checks.append((killed, f"creeper killed by Eve: {killed}"))
                if case.__name__ == "k_corner_bow":  # O72d: a cornered fight_ranged fires within its first 30 ticks
                    log = (res.get("evidence") or {}).get("shot_log") or []
                    first = log[0].get("tick") if log else None
                    checks.append((first is not None and first <= 30, f"first shot tick {first} (want <= 30)"))
                follows, valid = None, True
                if case.__name__ in ("k_follow", "k_follow_shallow", "k_follow_idle"):  # O53c: a pass needs the mind's creeper_follow and the matching outcome
                    follows = follow_events(t0_ms)
                    if case.__name__ == "k_follow_idle":  # O53l logs each idle dispatch as self_defence {skill, params, source}
                        follows += [{**e, "plan": "shoot"} for e in follow_events(t0_ms, "self_defence") if "error" not in e]
                    escaped = (case.__name__ != "k_follow_idle" and w.blast is None and blast_dmg == 0 and bool(ends)
                               and all(e.startswith("safe") for e in ends) and res.get("status") == "ok")
                    if not [e for e in follows if "error" not in e] and escaped:  # O72h: fled to open ground, no re-approach,
                        checks.append((True, "ESCAPED (O72h): no creeper_follow, flee ended safe, no blast, walk completed"))  # walk done
                    elif not [e for e in follows if "error" not in e]:
                        valid = False
                        checks.append((False, f"INVALID (O53c): no creeper_follow event {follows or ''}"))
                    else:
                        plan = follows[0].get("plan")
                        killed = L.creeper_kills() > kc0
                        if plan == "shoot":
                            checks.append((killed, f"creeper_follow plan shoot: killed by Eve {killed}"))
                        else:
                            checks.append((w.blast is None or w.blast["distance"] >= 6, f"creeper_follow plan {plan}: held clear"))
                ok = all(c for c, _ in checks)
                detail = "; ".join(d for _, d in checks) if ok else "FAIL: " + "; ".join(d for c, d in checks if not c)
                row = {"case": case.__name__, "trial": k + 1, "t_end": round(time.time(), 1), "max_state_gap_s": L.state_gap(t0_ms), "ok": ok, "detail": detail, "skill": skill, "lit": lit,
                       "creeper_spawn": at, "blast": w.blast, "blast_damage": blast_dmg, "other_damage": other_dmg, "hut": [hut0, hut1], "reflex_ends": ends,
                       "deaths": died, "action": {"status": res.get("status"), "code": res.get("code") or res.get("error")},
                       "summon_to_blast_s": round(w.blast["t"] - t_summon, 2) if w.blast else None,
                       "creeper_follow": follows, "valid": valid,
                       "dispatch_refused": follow_events(t0_ms, "dispatch_refused") if follows is not None else None}
                rows.append(row)
                with open(out / "rows.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")
                print(f"{'PASS' if ok else 'FAIL'} {case.__name__} #{k + 1}: {detail}", flush=True)
                if died:
                    if L.QUAL:
                        raise RuntimeError(f"DEATH in {case.__name__} #{k + 1} on the 45L: run stopped")
                    good, msg = L.restart_client(respawn=True)
                    raise RuntimeError(f"death in {case.__name__} #{k + 1}" + ("" if good else f"; respawn failed: {msg}"))
    except Exception as e:  # a11 died on an unexpected error after 16 trials and wrote no summary: always summarise
        aborted = f"{type(e).__name__}: {e}"
    finally:
        r(f"kill @e[tag={TAG}]")
        teardown = L.safe_teardown(lambda: (L.clear_arena(), open_end_remove(), r(f"fill {X0} {Y} {Z0} {X1} {Y + 6} {Z1} minecraft:air") if L.QUAL else None))
        L.stash_restore()
        for g in ("spawn_mobs", "mob_griefing"):
            r(f"gamerule {g} {was[g]}")
        r(f"difficulty {was['difficulty']}")
        r(f"forceload remove {X0} {Z0} {X1} {Z1}")
        L.lane_lock("release")
    passed = sum(x["ok"] for x in rows)
    reg = st.get("registry") or {}
    where = "the 45L (`45L`)" if L.QUAL else f"lane {L.LANE_ID.upper()} (`shark`)"
    lines = [f"# CP-29 — creepers, {where} {stamp} attempt {num}: {passed}/{len(rows)}", "",
             "Hypothesis: a creeper (lit at 4, or unlit walking in) during a fight, mining or a walk interrupts the action through "
             "body's creeper_flee; she is >= 6 from the blast (or it never goes off), takes no blast damage, breaks no block of the "
             "nearby hut, and lives. Trapped (walled pocket): she lives and raises the shield (cornered_shielded).",
             f"Fixture: {where}, {P}, mind {L.PORT} via /cli; arena x {X0}..{X1} y {Y} z {Z0}..{Z1}; wool hut 3x3x3 at x {CX - 9} z {CZ + 6}; follower spawn {FOLLOW_AT} at her side; "
             f"normal difficulty, mob_griefing ON for the run (restored to {was['mob_griefing']}); kit iron sword + pickaxe + iron armour "
             "+ shield. Judge: RCON every 0.1 s (positions, health), hut block count, deathCount; reflex lines from the client log.",
             f"Versions: repo {L.git_head()}, lane src (mind/body) {L.lane_src()}, mind_commit (status) {mind_commit or 'not checked'}, eve-combat jar {L.jar_sha()}, eve-body jar {L.body_sha()}, registry {reg.get('version')} {str(reg.get('hash'))[:12]}. "
             f"Elapsed {time.time() - t0:.0f} s. Teardown: {teardown}." + (f" ABORTED: {aborted}" if aborted else "")
             + (" Qualification lane." if L.QUAL else " Development evidence, not qualification."), "",
             f"Invalid trials (O53c: follow without a creeper_follow event; never passes): {sum(1 for x in rows if x.get('valid') is False)}.", "",
             "O72h follow gate per case (>= 19/20 pass-or-escaped with 0 damage AND >= 10 trials with a creeper_follow event): "
             + "; ".join(f"{c}: pass {sum(x['ok'] for x in rows if x['case'] == c)}/{sum(x['case'] == c for x in rows)}, escaped "
                         f"{sum('ESCAPED' in x['detail'] for x in rows if x['case'] == c)}, follow events "
                         f"{sum(bool(x.get('creeper_follow')) for x in rows if x['case'] == c)}, damage "
                         f"{sum(x['blast_damage'] for x in rows if x['case'] == c)}"
                         for c in sorted({x['case'] for x in rows if x['case'].startswith('k_follow')})) + ".", "",
             "| case | trial | ok | blast dist | blast dmg | hut | reflex ends | action | detail |", "|---|---|---|---|---|---|---|---|---|"]
    lines += [f"| {x['case']} | {x['trial']} | {'PASS' if x['ok'] else 'FAIL'} | {x['blast'] and x['blast']['distance']} | {x['blast_damage']} | "
              f"{x['hut'][1]}/{x['hut'][0]} | {','.join(x['reflex_ends'])} | {x['action']['status']}/{x['action']['code']} | {x['detail']} |" for x in rows]
    text = "\n".join(lines) + "\n"
    (out / "RESULT.md").write_text(text, encoding="utf-8")
    print(L.export_candidates(out, t0), flush=True)
    summ = L.REPO / "parts" / "combat" / "evidence-summaries"
    summ.mkdir(parents=True, exist_ok=True)
    (summ / f"CP-29-{stamp}-{L.LABEL}-attempt-{num}.md").write_text(text, encoding="utf-8")
    print(f"{passed}/{len(rows)} -> {out}")


if __name__ == "__main__":
    main()
