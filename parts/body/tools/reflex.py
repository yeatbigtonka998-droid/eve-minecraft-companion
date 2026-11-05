"""Phase 3 reflex fixtures on the Shark local fixture. Each reflex has its own fixture; server truth by RCON, never the body's
own word. The stub hub is the mind; it sends nothing during a trial except where a variant says so.

    py -3.12 tools/reflex.py [archer_doorway,eat,retreat,shield_arrow,shield_melee,surrounded,upkeep,hazards,lava_walk,dodge,edge,fall,arbiter] [--n 20] [--player EveDev]

Baselines run the same threat with Eve STOPPED (no reflexes) to prove the threat is real: a baseline that does not hurt her
makes the fixture invalid, not the reflex good.
"""
import json, math, os, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from arena import FLOOR, Run, evetest, nums  # noqa: E402

F = int(FLOOR)
N = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 20

CRITERIA = f"""Each reflex has its own fixture; >= 19/20 of its positive trials must pass before it is claimed. Server truth by RCON.
- eat: food drained by a hunger effect until the server reads food <= 14 (then the effect is cleared); pass = the server's food rises
  and the bread count drops by 1 within 4 s of that read, the selected hotbar slot is back where it was, no damage, and the body
  logged event reflex eat start+end(ate). Unsafe (a hostile 5 blocks away): she must not eat for 3 s; once it is gone she eats.
  Nothing edible (only rotten flesh, excluded): no meal, no event.
- retreat: a NoAI zombie 3 blocks away at 10 angles; /damage takes her to 5 HP (floor 6). Pass = the server sees >= 16 blocks between
  them within 6 s, health still 5 (no fall or other damage), event retreat start+end(safe). Half the trials have a 5x5, 6-deep pit
  right behind her (the straight way away): she must not fall in (y never below the floor). Cornered (glass pen): she ends
  `cornered` within 5 s and never leaves the pen.
- shield_arrow: shield in the off hand, sword in the main hand, a random facing; an arrow (1.6 b/tick) from 16 blocks at 10 angles,
  aimed by the fixture's own ballistic solution. Pass = health unchanged 2 s later and event shield start. Baseline: the same
  arrows while STOPPED must hurt her (>= 2 of 3) or the fixture is void.
- shield_melee: a zombie with AI (difficulty easy, night) walks in from 6 blocks; 8 s per trial. Pass = health unchanged and at
  least one blocked attack (zombie in reach); baseline without a shield must take damage.
- dodge: no shield; arrows as above. Pass = health unchanged 2 s later, she never fell, event dodge start. A quarter of the trials
  have a wall on one side. Baseline STOPPED as above.
- edge: Eve idle at the east edge of a 6-high pillar; a NoAI cow summoned overlapping her pushes her east. Pass = the server keeps
  her on the pillar (y >= top - 0.01) for 3 s and event edge_sneak start. Baseline STOPPED: the same push must drop her.
- fall: tp 12 blocks up: pass = event fall_rescue detected (predicted damage > 0, no_qualified_item) before she lands (before the
  body's damage event). Negative: a 3-block drop: no event. Detection only; no rescue item is qualified.
- fall_bucket (water bucket in the hotbar): tp 8/12/16/24/32 blocks up over stone. Pass = health unchanged and no body damage
  event, she lands, the bucket is a water bucket again (no empty bucket), no water left at the landing spot, and event
  fall_rescue end `rescued`. Baseline: the 12-block fall without a bucket must hurt her.
- creeper (CP-29): an AI creeper (mob_griefing off for the suite) 5 blocks away at 8 angles while idle, 6 blocks ahead on a Baritone
  walk (x6), 5 blocks away during a mine (x6). Pass = no damage over 10 s, event creeper_flee start, and if it blew up she was >= 7
  from it at the blast (last server positions); a mine ends interrupted. Baseline STOPPED x3 must hurt her. Trapped in a glass pen
  with a shield (x3, a separate group, never a pass of CP-29): the reflex must raise the shield (end cornered_shielded).
- surrounded: shield up, 3 NoAI zombies W/N/S, HP 20 -> 5: the melee shield yields to retreat (`yield_retreat`), 16 blocks within 8 s.
- upkeep (CP-25): armour empty/better/nearly broken/worse-kept, worn pickaxe/sword replaced from the main inventory or reported
  `worn`, food moved to the hotbar when hungry, `no_food` once, starving with a hostile near -> retreat then eat, a meal abandoned
  when a zombie appears. Judged by RCON `execute if items` and the server's food/bread counts, plus the upkeep events.
- hazards (CP-30): pushed at a lava pool's rim (edge-sneak keeps her out; baseline STOPPED goes in), a step one block into lava with
  water near (out, then into the water), fire at her feet, a block in her head cell, drowning in a 5-deep glass tank (roofed but
  one corner, or open; air limit 150/300). Pass = hazard start + end `escaped`, out of it, alive, and no death event.
- lava_walk (CP-30): Baritone walk_to straight across a 3x5 lava pool: ends ok, never in lava, never burning.
- archer_doorway (O98): a 5x5 lit stone-brick room, a skeleton (slowness 255) 1.3 outside its doorway, midnight, Eve at the back
  facing away. archer_shield: shield, HP 20, 15 s: health never drops, a shield start with cause drawn_bow, >= 1 arrow shot.
  archer_cover: no shield, HP 5: alive, a ranged retreat whose end carries covered_at, no damage in the last 8 s. Baselines
  STOPPED (x3 shield, x2 HP 5) must hurt her (a baseline ends at its first hit). INVALID (tested nothing): the skeleton holds no bow
  (I37), never acted (no arrow left and no damage: a hit arrow is consumed), or she was not at the back of the room when it was summoned (server position).
- arbiter: a Baritone walk_to 24 blocks; at 1 s hunger is forced: pass = the eat reflex pauses it (event names the action), she eats,
  the walk resumes and ends ok within 1.0 of the goal. A mine_block interrupted by an arrow (shield): it ends failed interrupted, the
  block is intact or broken but never both claimed and denied."""


def heal_full(r):
    if "Health: 0.0f" in r.rcon(f"data get entity {r.player} Health"):
        # a death ends the run (lane B 2026-10-20 attempt 3 ran 10 trials on a dead player): the lane owner respawns, re-kits, re-parks
        raise RuntimeError(f"{r.player} is dead; stopping the run (death counted in the trial that killed her)")
    r.rcon(f"effect give {r.player} minecraft:instant_health 1 4 true")
    r.rcon(f"effect give {r.player} minecraft:saturation 1 20 true")
    time.sleep(0.6)


def srv(r, path):
    return nums(r.rcon(f"data get entity {r.player} {path}"))


def health(r):
    return srv(r, "Health")[0]


def health_watch(r, seconds, floor=10.0):
    """Wait `seconds`, but end the trial at once (every hostile in the arena killed) if her health drops below `floor`: a failed
    trial must not become a death on a true-survival account (45L attempt 1: HP 20 -> 5 four times)."""
    end, h = time.time() + seconds, health(r)
    while time.time() < end:
        h = health(r)
        if h < floor:
            r.rcon(f"kill @e[type=!minecraft:player,{near()}]")
            break
        time.sleep(0.2)
    return h


def food(r):
    return int(srv(r, "foodLevel")[0])


def count(r, item):
    out = r.rcon(f"clear {r.player} {item} 0")
    return int(nums(out)[0]) if "Found" in out else 0


def mark(r):
    """An opaque point in the body's event stream (stub: frame index; lane B: the mind DB's time)."""
    return r.mark() if hasattr(r, "mark") else len(r.hub.frames)


def body_events(r, since, kind):
    if hasattr(r, "events"):
        return r.events(since, kind)
    return [{**f["msg"]["payload"]["detail"], "t": f["t"]} for f in r.hub.frames[since:]
            if f["dir"] == "in" and f["msg"]["type"] == "event" and f["msg"]["payload"]["kind"] == kind]


def reflex_events(r, since, name=None, phase=None):
    return [d for d in body_events(r, since, "reflex")
            if (name is None or d.get("reflex") == name) and (phase is None or d.get("phase") == phase)]


def damage_events(r, since):
    return body_events(r, since, "damage")


def ran(res):
    """O46: the action actually ran on the body (a result with a status other than refused/rejected); else the trial is INVALID."""
    return bool(res) and res.get("status") in ("ok", "failed", "uncertain") and "rejected" not in str(res.get("code") or "")


def request(r, skill, params):
    return r.request(skill, params) if hasattr(r, "request") else r.hub.request(skill, params)


def result(r, handle, timeout, since):
    return r.result(handle, timeout) if hasattr(r, "result") else r.hub.wait_result(handle, timeout, since)


def stop(r):
    r.owner_stop() if hasattr(r, "owner_stop") else evetest(r, "stop")
    time.sleep(0.3)


def resume(r):
    r.owner_resume() if hasattr(r, "owner_resume") else r.hub.send("control.resume", {"source": "reflex-fixture"})
    time.sleep(0.3)


def near():
    """Entity selector limit: only the arena's entities (lane B's world has other fixtures that must survive)."""
    return f"x=0,y={F},z=0,distance=..60"


def setup(r):
    r.fixture()
    p = r.player
    for c in [f"difficulty {os.environ.get('EVE_DIFFICULTY', 'easy')}", "gamerule natural_health_regeneration false", f"clear {p}", f"effect clear {p}",
              f"item replace entity {p} hotbar.0 with minecraft:stone_sword", f"item replace entity {p} hotbar.3 with minecraft:bread 16"]:
        r.rcon(c)
    r.act("select_hotbar", {"slot": 0})
    heal_full(r)


def arrow_motion(src, dst, speed=1.6):
    """Initial velocity (speed 1.6 b/tick horizontally) whose drag-0.99 / gravity-0.05 arc passes through dst."""
    dx, dz = dst[0] - src[0], dst[2] - src[2]
    h = math.hypot(dx, dz)
    ux, uz = dx / h, dz / h

    def y_at(vy):
        x, y, vh = 0.0, src[1], speed
        while x < h:
            step = min(vh, h - x)
            frac = step / vh
            x += step
            y += vy * frac
            vh *= 0.99
            vy = vy * 0.99 - 0.05
        return y

    lo, hi = -1.0, 2.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if y_at(mid) < dst[1]:
            lo = mid
        else:
            hi = mid
    return speed * ux, (lo + hi) / 2, speed * uz


def fire_arrow(r, angle_deg, dist=16.0):
    me = r.pos()
    a = math.radians(angle_deg)
    src = (me[0] + math.cos(a) * dist, me[1] + 1.5, me[2] + math.sin(a) * dist)
    vx, vy, vz = arrow_motion(src, (me[0], me[1] + 1.0, me[2]))
    r.rcon(f"summon minecraft:arrow {src[0]:.3f} {src[1]:.3f} {src[2]:.3f} {{Motion:[{vx:.5f}d,{vy:.5f}d,{vz:.5f}d],pickup:0b,damage:2.0d}}")
    return src


# ---------------------------------------------------------------- eat

def drain_to(r, limit=14, timeout=20):
    """Hunger until the server reads food <= limit; returns (time of that read, food read)."""
    end = time.time() + timeout
    while time.time() < end:
        f = food(r)
        if f <= limit:
            r.rcon(f"effect clear {r.player} minecraft:hunger")
            return time.time(), f
        r.rcon(f"effect give {r.player} minecraft:hunger 1 255 true")
        time.sleep(0.1)
    r.rcon(f"effect clear {r.player} minecraft:hunger")
    return None, food(r)


def eat(r):
    p = r.player
    for i in range(N):
        r.tp(0.5, FLOOR, 0.5, 36 * i)
        r.rcon(f"item replace entity {p} hotbar.3 with minecraft:bread 16")
        heal_full(r)
        since, b0, h0 = mark(r), count(r, "minecraft:bread"), health(r)
        t0, f0 = drain_to(r)
        ok_t, f1 = None, f0
        while t0 and time.time() < t0 + 4:
            f1 = food(r)
            if f1 > f0:
                ok_t = time.time()
                break
            time.sleep(0.1)
        time.sleep(0.8)
        b1, slot, h1 = count(r, "minecraft:bread"), srv(r, "SelectedItemSlot"), health(r)
        ev = reflex_events(r, since, "eat")
        ended = [e for e in ev if e["phase"] == "end"]
        checks = {"crossed": t0 is not None, "ate<=4s": ok_t is not None, "bread-1": b1 == b0 - 1, "slot_restored": slot == [0],
                  "no_damage": h1 >= h0, "event": bool(ended) and ended[0]["result"] == "ate"}
        r.record(f"eat#{i}", "reflex_eat", {}, all(checks.values()),
                 f"food {f0}->{f1} in {ok_t and t0 and round(ok_t - t0, 2)}s bread {b0}->{b1} slot={slot} "
                 + " ".join(k for k, v in checks.items() if not v), events=ev, latency_s=ok_t and t0 and ok_t - t0)
    for i in range(5):  # unsafe: a hostile 5 blocks away; then it goes and she eats
        r.tp(0.5, FLOOR, 0.5, 0)
        heal_full(r)
        r.rcon(f"summon minecraft:zombie 5.5 {F} 0.5 {{NoAI:1b,PersistenceRequired:1b,Silent:1b}}")
        time.sleep(0.5)
        since, b0 = mark(r), count(r, "minecraft:bread")
        t0, f0 = drain_to(r)
        time.sleep(3.0)
        f_unsafe, b_unsafe = food(r), count(r, "minecraft:bread")
        r.rcon(f"kill @e[type=minecraft:zombie,{near()}]")
        end, f_after = time.time() + 5, f_unsafe
        while time.time() < end and f_after <= f_unsafe:
            f_after = food(r)
            time.sleep(0.1)
        starts = reflex_events(r, since, "eat", "start")
        ok = t0 is not None and f_unsafe <= f0 and b_unsafe == b0 and f_after > f_unsafe and len(starts) >= 1 and starts[0]["t"] > t0 + 2.9
        r.record(f"eat_unsafe#{i}", "reflex_eat", {}, ok, f"food {f0} unsafe 3s -> {f_unsafe} (bread {b0}->{b_unsafe}); zombie gone -> {f_after}; "
                 f"eat starts after crossing: {[round(e['t'] - t0, 2) for e in starts] if t0 else None}")
    for i in range(3):  # nothing a reflex may eat
        r.tp(0.5, FLOOR, 0.5, 0)
        r.rcon(f"clear {p} minecraft:bread")
        r.rcon(f"item replace entity {p} hotbar.3 with minecraft:rotten_flesh 8")
        heal_full(r)
        since = mark(r)
        t0, f0 = drain_to(r)
        time.sleep(3.0)
        f1, rf = food(r), count(r, "minecraft:rotten_flesh")
        ev = reflex_events(r, since, "eat")
        r.record(f"eat_nothing#{i}", "reflex_eat", {}, f1 <= f0 and rf == 8 and not ev, f"food {f0}->{f1} rotten_flesh={rf} events={ev}")
        r.rcon(f"clear {p} minecraft:rotten_flesh")
    r.rcon(f"item replace entity {p} hotbar.3 with minecraft:bread 16")
    heal_full(r)


# ---------------------------------------------------------------- retreat

def retreat(r):
    p = r.player
    pit = (f"fill -6 {F - 6} -2 -2 {F - 1} 2 minecraft:air", f"fill -6 {F - 6} -2 -2 {F - 1} 2 minecraft:smooth_stone")
    for i in range(N):
        with_pit = i % 2 == 1
        a = 0 if with_pit else math.radians(36 * i)  # pit trials: zombie east, so straight away is west, into the pit
        r.tp(0.5, FLOOR, 0.5, 0)
        if with_pit:
            r.rcon(pit[0])
        heal_full(r)
        zx, zz = 0.5 + 3 * math.cos(a), 0.5 + 3 * math.sin(a)
        r.rcon(f"summon minecraft:zombie {zx:.2f} {F} {zz:.2f} {{NoAI:1b,PersistenceRequired:1b,Silent:1b}}")
        time.sleep(0.5)
        since = mark(r)
        r.rcon(f"damage {p} 15 minecraft:generic")
        t0, far_t, min_y, dmax = time.time(), None, 999.0, 0.0
        while time.time() < t0 + 6:
            me = r.pos()
            min_y = min(min_y, me[1])
            dmax = max(dmax, math.hypot(me[0] - zx, me[2] - zz))
            if dmax >= 16:
                far_t = time.time()
                break
            time.sleep(0.05)
        time.sleep(1.0)
        h = health(r)
        ev = reflex_events(r, since, "retreat")
        ended = [e for e in ev if e["phase"] == "end"]
        checks = {"16_blocks<=6s": far_t is not None, "health==5": h == 5.0, "never_fell": min_y >= FLOOR - 0.01,
                  "event": bool(ended) and ended[0]["result"] == "safe"}
        r.record(f"retreat{'_pit' if with_pit else ''}#{i}", "reflex_retreat", {}, all(checks.values()),
                 f"16 blocks in {far_t and round(far_t - t0, 2)}s max_dist={dmax:.1f} hp={h} min_y={min_y:.2f} "
                 + " ".join(k for k, v in checks.items() if not v), events=ev)
        r.rcon(f"kill @e[type=minecraft:zombie,{near()}]")
        if with_pit:
            r.rcon(pit[1])
    pen = ("fill -3 {0} -3 3 {1} 3 minecraft:glass hollow".format(F - 1, F + 3), "fill -3 {0} -3 3 {1} 3 minecraft:air".format(F, F + 3),
           "fill -3 {0} -3 3 {0} 3 minecraft:smooth_stone".format(F - 1))
    for i in range(3):  # cornered: a glass pen, zombie inside with her
        r.tp(0.5, FLOOR, 0.5, 0)
        r.rcon(pen[0])
        heal_full(r)
        r.rcon(f"summon minecraft:zombie 2.5 {F} 0.5 {{NoAI:1b,PersistenceRequired:1b,Silent:1b}}")
        time.sleep(0.5)
        since = mark(r)
        r.rcon(f"damage {p} 15 minecraft:generic")
        t0, out = time.time(), False
        while time.time() < t0 + 6:
            me = r.pos()
            out |= abs(me[0] - 0.5) > 3 or abs(me[2] - 0.5) > 3 or me[1] < FLOOR - 0.01
            time.sleep(0.1)
        ended = [e for e in reflex_events(r, since, "retreat") if e["phase"] == "end"]
        ok = bool(ended) and ended[0]["result"] == "cornered" and ended[0]["t"] - t0 <= 5 and not out and health(r) == 5.0
        r.record(f"retreat_cornered#{i}", "reflex_retreat", {}, ok, f"end={ended and (ended[0]['result'], round(ended[0]['t'] - t0, 2))} left_pen={out}")
        r.rcon(f"kill @e[type=minecraft:zombie,{near()}]")
        r.rcon(pen[1])
        r.rcon(pen[2])
    heal_full(r)


# ---------------------------------------------------------------- shield / dodge (projectiles)

def arrows(r, shield, name):
    p = r.player
    r.rcon(f"item replace entity {p} weapon.offhand with {'minecraft:shield' if shield else 'minecraft:air'}")
    wall = ("fill -1 {0} 2 1 {1} 2 minecraft:stone".format(F, F + 2), "fill -1 {0} 2 1 {1} 2 minecraft:air".format(F, F + 2))
    for base in range(3):  # baselines: same arrows, STOPPED (no reflexes): must hurt her
        r.tp(0.5, FLOOR, 0.5, 90)
        heal_full(r)
        stop(r)
        h0 = health(r)
        fire_arrow(r, 120 * base)
        time.sleep(2.0)
        h1 = health(r)
        r.record(f"{name}_baseline_stopped#{base}", f"reflex_{name}", {}, h1 < h0, f"hp {h0}->{h1} (an arrow that hurts a STOPPED Eve)")
        resume(r)
    for i in range(N):
        walled = not shield and i % 4 == 3
        r.tp(0.5, FLOOR, 0.5, (137 * i) % 360 - 180)
        if walled:
            r.rcon(wall[0])
        heal_full(r)
        time.sleep(0.3)
        since, h0 = mark(r), health(r)
        angle = 180 if walled else 36 * i  # walled: arrow from the west, wall on her south side
        fire_arrow(r, angle)
        t0, min_y = time.time(), 999.0
        while time.time() < t0 + 2:
            min_y = min(min_y, r.pos()[1])
            time.sleep(0.05)
        h1 = health(r)
        ev = reflex_events(r, since, "shield" if shield else "dodge")
        starts = [e for e in ev if e["phase"] == "start"]
        checks = {"no_damage": h1 >= h0, "event": bool(starts), "never_fell": min_y >= FLOOR - 0.01}
        r.record(f"{name}{'_walled' if walled else ''}#{i}", f"reflex_{name}", {"angle": angle}, all(checks.values()),
                 f"hp {h0}->{h1} start={starts and starts[0].get('trigger')} end={[e.get('result') for e in ev if e['phase'] == 'end']} "
                 + " ".join(k for k, v in checks.items() if not v), events=ev)
        if walled:
            r.rcon(wall[1])
    r.rcon(f"item replace entity {p} weapon.offhand with minecraft:air")


def shield_arrow(r):
    arrows(r, True, "shield_arrow")


def dodge(r):
    arrows(r, False, "dodge")


def shield_melee(r):
    p = r.player
    r.rcon("time set midnight")
    for base in range(3):
        r.tp(0.5, FLOOR, 0.5, 90)
        r.rcon(f"item replace entity {p} weapon.offhand with minecraft:air")
        heal_full(r)
        h0 = health(r)
        r.rcon(f"summon minecraft:zombie -5.5 {F} 0.5 {{PersistenceRequired:1b,Silent:1b}}")
        h1 = health_watch(r, 8.0)  # the baseline only has to show it hurts; stop it well above a death
        r.rcon(f"kill @e[type=minecraft:zombie,{near()}]")
        r.record(f"shield_melee_baseline_no_shield#{base}", "reflex_shield_melee", {}, h1 < h0, f"hp {h0}->{h1} (the zombie does hit her)")
    for i in range(N):
        r.tp(0.5, FLOOR, 0.5, (137 * i) % 360 - 180)
        # a fresh shield every trial: blocking costs durability, and on the 45L (normal) it broke at trial 15 (foundation attempt 1)
        r.rcon(f"item replace entity {p} weapon.offhand with minecraft:shield")
        heal_full(r)
        a = math.radians(36 * i)
        since, h0 = mark(r), health(r)
        r.rcon(f"summon minecraft:zombie {0.5 + 6 * math.cos(a):.2f} {F} {0.5 + 6 * math.sin(a):.2f} {{PersistenceRequired:1b,Silent:1b}}")
        h1 = health_watch(r, 8.0)
        r.rcon(f"kill @e[type=minecraft:zombie,{near()}]")
        ev = reflex_events(r, since, "shield")
        starts = [e for e in ev if e["phase"] == "start"]
        ok = h1 >= h0 and bool(starts)
        r.record(f"shield_melee#{i}", "reflex_shield_melee", {}, ok, f"hp {h0}->{h1} starts={len(starts)} first={starts and starts[0].get('trigger')}", events=ev)
    r.rcon(f"item replace entity {p} weapon.offhand with minecraft:air")
    r.rcon("time set noon")



def surrounded(r):
    """Shield up against 3 zombies (W, N, S; east open), then HP 20 -> 5: the melee shield must hand over to retreat (combat CP-28
    lane C attempt 6: she held the shield 417 ticks while flanked and died). Half the trials drop HP before the shield rises."""
    p = r.player
    for i in range(N):
        shield_first = i % 2 == 0
        r.tp(0.5, FLOOR, 0.5, 90)
        r.rcon(f"item replace entity {p} weapon.offhand with minecraft:shield")
        heal_full(r)
        since = mark(r)
        if not shield_first:
            r.rcon(f"damage {p} 15 minecraft:generic")
        for zx, zz in ((-1.5, 0.5), (0.5, -1.5), (0.5, 2.5)):
            r.rcon(f"summon minecraft:zombie {zx} {F} {zz} {{NoAI:1b,PersistenceRequired:1b,Silent:1b}}")
        if shield_first:
            time.sleep(1.0)
            r.rcon(f"damage {p} 15 minecraft:generic")
        t0, far_t, dmax = time.time(), None, 0.0
        while time.time() < t0 + 8:
            me = r.pos()
            dmax = max(dmax, math.hypot(me[0] - 0.5, me[2] - 0.5))
            if dmax >= 16:
                far_t = time.time()
                break
            time.sleep(0.05)
        time.sleep(1.0)
        ev = reflex_events(r, since)
        shields = [e for e in ev if e.get("reflex") == "shield" and e["phase"] == "end"]
        rets = [e for e in ev if e.get("reflex") == "retreat" and e["phase"] == "end"]
        checks = {"16_blocks<=8s": far_t is not None, "retreat_safe": any(e["result"] == "safe" for e in rets),
                  "shield_yielded": (not shield_first and not shields) or any(e["result"] == "yield_retreat" for e in shields),
                  "alive": health(r) > 0}
        r.record(f"surrounded{'_shield_first' if shield_first else '_hurt_first'}#{i}", "reflex_retreat", {}, all(checks.values()),
                 f"16 blocks in {far_t and round(far_t - t0, 2)}s max_dist={dmax:.1f} shield_ends={[e['result'] for e in shields]} "
                 f"retreat_ends={[e['result'] for e in rets]} " + " ".join(k for k, v in checks.items() if not v), events=ev)
        r.rcon(f"kill @e[type=minecraft:zombie,{near()}]")
    r.rcon(f"item replace entity {p} weapon.offhand with minecraft:air")


# ---------------------------------------------------------------- CP-25 upkeep (gear, worn tools, food supply, unsafe meal)

def has_item(r, slot, item):
    return r.rcon(f"execute if items entity {r.player} {slot} {item}").startswith("Test passed")


def kit(r, *cmds):
    p = r.player
    r.rcon(f"clear {p}")
    r.rcon(f"item replace entity {p} hotbar.0 with minecraft:stone_sword")
    for c in cmds:
        out = r.rcon(c)
        if any(w in out for w in ("Unknown", "Incorrect", "Expected", "Invalid", "Failed")):
            raise RuntimeError(f"fixture command failed: {c!r} -> {out!r}")


def upkeep(r):
    p = r.player
    ARM = {"head": "helmet", "chest": "chestplate", "legs": "leggings", "feet": "boots"}
    cases = []  # (name, kit commands, check(): bool, expect upkeep phase or None)
    for slot, piece in ARM.items():
        cases.append((f"armor_empty_{slot}", [f"item replace entity {p} inventory.4 with minecraft:iron_{piece}"],
                      lambda s=slot, pc=piece: has_item(r, f"armor.{s}", f"minecraft:iron_{pc}"), "equip_armor"))
    cases += [
        ("armor_better", [f"item replace entity {p} armor.chest with minecraft:leather_chestplate",
                          f"item replace entity {p} inventory.7 with minecraft:diamond_chestplate"],
         lambda: has_item(r, "armor.chest", "minecraft:diamond_chestplate") and has_item(r, "inventory.7", "minecraft:leather_chestplate"), "equip_armor"),
        ("armor_nearly_broken", [f"item replace entity {p} armor.head with minecraft:iron_helmet[damage=163]",
                                 f"item replace entity {p} inventory.2 with minecraft:leather_helmet"],
         lambda: has_item(r, "armor.head", "minecraft:leather_helmet") and has_item(r, "inventory.2", "minecraft:iron_helmet"), "equip_armor"),
        ("armor_worse_kept", [f"item replace entity {p} armor.chest with minecraft:diamond_chestplate",
                              f"item replace entity {p} inventory.3 with minecraft:leather_chestplate"],
         lambda: has_item(r, "armor.chest", "minecraft:diamond_chestplate") and has_item(r, "inventory.3", "minecraft:leather_chestplate"), None),
        ("tool_worn_replaced", [f"item replace entity {p} hotbar.1 with minecraft:iron_pickaxe[damage=248]",
                                f"item replace entity {p} inventory.9 with minecraft:iron_pickaxe"],
         lambda: has_item(r, "hotbar.1", "minecraft:iron_pickaxe[damage=0]") and has_item(r, "inventory.9", "minecraft:iron_pickaxe[damage=248]"), "replace_tool"),
        ("sword_worn_replaced", [f"item replace entity {p} hotbar.0 with minecraft:iron_sword[damage=247]",
                                 f"item replace entity {p} inventory.12 with minecraft:stone_sword"],
         lambda: has_item(r, "hotbar.0", "minecraft:stone_sword") and has_item(r, "inventory.12", "minecraft:iron_sword"), "replace_tool"),
        ("tool_worn_no_spare", [f"item replace entity {p} hotbar.1 with minecraft:iron_pickaxe[damage=248]"],
         lambda: has_item(r, "hotbar.1", "minecraft:iron_pickaxe[damage=248]"), "worn"),
    ]
    for rep in range(max(1, N // 5)):
        for name, cmds, check, phase in cases:
            r.tp(0.5, FLOOR, 0.5, 0)
            heal_full(r)
            since = mark(r)
            kit(r, *cmds)
            end, ok_t = time.time() + 5, None
            while time.time() < end:
                if check() and (phase is None or reflex_events(r, since, "upkeep", phase)):
                    ok_t = time.time()
                    break
                time.sleep(0.2)
            if phase is None:  # nothing may happen: watch the full 3 s
                time.sleep(3)
            ev = reflex_events(r, since, "upkeep")
            ok = check() and (phase is None and not ev or phase is not None and ok_t is not None and len(ev) == 1)
            r.record(f"{name}#{rep}", "reflex_upkeep", {}, ok, f"events={[e['phase'] for e in ev]} check={check()}", events=ev)
    # food supply: hungry, no food in the hotbar, bread in the main inventory -> moved to the hotbar, then eaten
    for i in range(max(1, N // 5)):
        r.tp(0.5, FLOOR, 0.5, 0)
        heal_full(r)
        kit(r, f"item replace entity {p} inventory.20 with minecraft:bread 4")
        since = mark(r)
        t0, f0 = drain_to(r)
        end, f1 = time.time() + 6, f0
        while time.time() < end and f1 <= f0:
            f1 = food(r)
            time.sleep(0.2)
        ev = reflex_events(r, since)
        moved = [e for e in ev if e.get("reflex") == "upkeep" and e["phase"] == "food_to_hotbar"]
        ate = [e for e in ev if e.get("reflex") == "eat" and e["phase"] == "end" and e.get("result") == "ate"]
        r.record(f"food_to_hotbar#{i}", "reflex_upkeep", {}, t0 is not None and bool(moved) and bool(ate) and f1 > f0 and count(r, "minecraft:bread") == 3,
                 f"food {f0}->{f1} moved={bool(moved)} ate={bool(ate)} bread={count(r, 'minecraft:bread')}", events=ev)
    # nothing to eat anywhere: one `no_food` report per hungry spell, no loop, nothing eaten
    for i in range(max(1, N // 10)):
        r.tp(0.5, FLOOR, 0.5, 0)
        heal_full(r)
        kit(r, f"item replace entity {p} inventory.5 with minecraft:rotten_flesh 4")
        since = mark(r)
        t0, f0 = drain_to(r)
        time.sleep(5)
        ev = reflex_events(r, since)
        nf = [e for e in ev if e.get("reflex") == "upkeep" and e["phase"] == "no_food"]
        eats = [e for e in ev if e.get("reflex") == "eat"]
        r.record(f"no_food#{i}", "reflex_upkeep", {}, t0 is not None and len(nf) == 1 and not eats and count(r, "minecraft:rotten_flesh") == 4,
                 f"no_food events={len(nf)} eat events={len(eats)}", events=ev)
    # starving with a hostile near: retreat first (cause unsafe_meal), then eat once clear
    for i in range(max(1, N // 5)):
        r.tp(0.5, FLOOR, 0.5, 90)
        heal_full(r)
        kit(r, f"item replace entity {p} hotbar.3 with minecraft:bread 4")
        r.rcon(f"summon minecraft:zombie -4.5 {F} 0.5 {{NoAI:1b,PersistenceRequired:1b,Silent:1b}}")
        time.sleep(0.5)
        since = mark(r)
        t0, f0 = drain_to(r, 6, 40)
        end, f1 = time.time() + 10, f0
        while time.time() < end and f1 <= f0:
            f1 = food(r)
            time.sleep(0.2)
        ev = reflex_events(r, since)
        ret = [e for e in ev if e.get("reflex") == "retreat" and e["phase"] == "start" and e.get("trigger", {}).get("cause") == "unsafe_meal"]
        ate = [e for e in ev if e.get("reflex") == "eat" and e["phase"] == "end" and e.get("result") == "ate"]
        me = r.pos()
        r.record(f"unsafe_meal_retreat#{i}", "reflex_upkeep", {}, t0 is not None and bool(ret) and bool(ate) and f1 > f0,
                 f"food {f0}->{f1} retreat={bool(ret)} ate={bool(ate)} dist={math.hypot(me[0] + 4.5, me[2] - 0.5):.1f}", events=ev)
        r.rcon(f"kill @e[type=minecraft:zombie,{near()}]")
    # combat during a meal: a zombie appears while she eats -> the meal is abandoned (aborted_unsafe), nothing eaten twice
    for i in range(max(1, N // 5)):
        r.tp(0.5, FLOOR, 0.5, 0)
        heal_full(r)
        kit(r, f"item replace entity {p} hotbar.3 with minecraft:bread 4")
        since = mark(r)
        t0, f0 = drain_to(r)
        end = time.time() + 3
        while time.time() < end and not reflex_events(r, since, "eat", "start"):
            time.sleep(0.05)
        t_z = time.time()
        r.rcon(f"summon minecraft:zombie 3.5 {F} 0.5 {{NoAI:1b,PersistenceRequired:1b,Silent:1b}}")
        time.sleep(2.5)
        ends = [e for e in reflex_events(r, since, "eat", "end") if e["t"] > t_z]  # an earlier meal (previous trial's hunger) is not this one
        r.record(f"meal_interrupted#{i}", "reflex_eat", {}, bool(ends) and ends[0].get("result") == "aborted_unsafe" and count(r, "minecraft:bread") == 4,
                 f"eat ends={[e.get('result') for e in ends]} bread={count(r, 'minecraft:bread')}", events=reflex_events(r, since))
        r.rcon(f"kill @e[type=minecraft:zombie,{near()}]")
    kit(r, f"item replace entity {p} hotbar.3 with minecraft:bread 16")


# ---------------------------------------------------------------- CP-30 hazards (lava, fire, suffocation, drowning, lava walk)

def park(r):
    """O35: before any fixture teardown she stands clear of every fixture volume (and never above one): (-12.5, FLOOR, 10.5)."""
    r.tp(-12.5, FLOOR, 10.5, 0)


def extinguish(r):
    """Still burning from an earlier trial (fire outlasts the lava): a moment in water, so no trial inherits fire ticks."""
    if int(srv(r, "Fire")[0]) > 0:
        r.tp(0.5, FLOOR, -5.5, 0)  # O39: onto dry floor, then the water around her
        r.rcon(f"setblock 0 {F} -6 minecraft:water")
        time.sleep(0.6)
        r.rcon(f"setblock 0 {F} -6 minecraft:air")
        r.tp(0.5, FLOOR, 0.5, 0)
        time.sleep(0.3)


def hazard_trial(r, name, build, place, restore, limit_s=6.0, check_extra=None, after=(), either=None):
    """build -> tp in -> wait for the hazard reflex to end; pass = start + end escaped, out of the hazard, alive (no death event:
    a respawn restores health, so health alone cannot show it, attempt 28)."""
    p = r.player
    r.tp(0.5, FLOOR, 0.5, 0)
    extinguish(r)
    heal_full(r)
    for c in build:
        r.rcon(c)
    since = mark(r)
    r.tp(*place)
    for c in after:
        r.rcon(c)
    end, ended = time.time() + limit_s, []
    while time.time() < end and not ended:
        ended = reflex_events(r, since, "hazard", "end")
        time.sleep(0.1)
    time.sleep(0.5)
    r.rcon(f"effect give {p} minecraft:fire_resistance 10 0 true")  # she may still burn after stepping out: not the reflex's test
    h, me = health(r), r.pos()
    starts = reflex_events(r, since, "hazard", "start")
    extra = check_extra(me) if check_extra else True
    died = bool(body_events(r, since, "death"))
    by_reflex = bool(starts) and bool(ended) and ended[0].get("result") == "escaped"
    ok = (by_reflex or (either is not None and not starts and either(me))) and h > 0 and extra and not died
    # the trial's true low: HP climbs back with regen (45L), so the end read alone hid it (I14)
    hp_min = min([d.get("health", h) for d in body_events(r, since, "damage")] + [h])
    r.record(name, "reflex_hazard", {}, ok, f"start={[s.get('trigger', {}).get('kind') for s in starts]} end={[e.get('result') for e in ended]} "
             f"hp={h} hp_min={hp_min} died={died} pos={[round(v, 1) for v in me]} extra={extra}", events=reflex_events(r, since))
    park(r)  # O35: no teardown with her inside or above the fixture
    for c in restore:
        r.rcon(c)
    extinguish(r)  # I14: lava's 15 s of fire outlasts the trial; clearing fire resistance first burned her 3 HP between trials (45L 7 -> 4)
    r.rcon(f"effect clear {p}")
    time.sleep(0.3)


def hazards(r, lava_only=False):
    p = r.player
    floor = f"minecraft:smooth_stone"
    # stone under the pool: the sky arena's floor is one block thick, and lava poured into it falls through into the space under
    # the arena, where it outlasts the trial (lane B a2-a3: lava under the restored rim kept her edge-sneak on across trials)
    pool = (f"fill 1 {F - 2} -1 3 {F - 2} 1 minecraft:stone", f"fill 1 {F - 1} -1 3 {F - 1} 1 minecraft:lava",
            f"fill 1 {F - 1} -1 3 {F - 1} 1 {floor}", f"fill 1 {F - 2} -1 3 {F - 2} 1 minecraft:air")
    pool_on, pool_off = [pool[0], pool[1]], [pool[2], pool[3]]

    def rim_push(stopped_):
        """Eve on the rim of a lava pool sunk into the floor (x 1..3), a NoAI cow overlapping her pushes her toward it."""
        r.tp(-8.5, FLOOR, 0.5, 0)  # well off the rim for over 10 ticks: each trial's edge-sneak is a fresh start (lane B a2: one
        time.sleep(1.2)            # continuous sneak across all 20 back-to-back trials, so no start event to count)
        r.tp(0.5, FLOOR, 0.5, 0)
        extinguish(r)
        heal_full(r)
        since = mark(r)  # before the pool: her edge-sneak starts the moment it appears (lane B attempt 1: mark after it, 0/20 events)
        for c in pool_on:
            r.rcon(c)
        r.tp(0.85, FLOOR, 0.5, 90)
        time.sleep(0.5)
        if stopped_:
            stop(r)
        r.rcon(f"summon minecraft:cow 0.45 {F} 0.5 {{NoAI:1b,PersistenceRequired:1b,Silent:1b}}")
        t0, max_x, min_y, fire = time.time(), -9.0, 999.0, 0
        while time.time() < t0 + 3:
            me = r.pos()
            max_x, min_y = max(max_x, me[0]), min(min_y, me[1])
            fire = max(fire, int(srv(r, "Fire")[0]))
            if me[1] < F - 0.2:  # in the lava: out at once, the trial is decided
                r.tp(-2.5, FLOOR, 0.5, 0)
                r.rcon(f"effect give {p} minecraft:fire_resistance 10 0 true")
                break
            time.sleep(0.05)
        r.rcon(f"kill @e[type=minecraft:cow,{near()}]")
        if stopped_:
            resume(r)
        park(r)  # O35
        for c in pool_off:
            r.rcon(c)
        extinguish(r)  # I14: before the fire resistance goes
        r.rcon(f"effect clear {p}")
        return max_x, min_y, fire, reflex_events(r, since)

    skip_in = "--skip-lava-in" in sys.argv  # 45L: when eve-guard cannot exempt the trials that put her into the pool on purpose
    for base in range(0 if skip_in else 3):
        max_x, min_y, fire, ev = rim_push(True)
        r.record(f"lava_rim_baseline_stopped#{base}", "reflex_hazard", {}, min_y < F - 0.2, f"min_y={min_y:.2f} max_x={max_x:.2f} (pushed in while STOPPED)")
    for i in range(N):
        max_x, min_y, fire, ev = rim_push(False)
        sneak = [e for e in ev if e.get("reflex") == "edge_sneak" and e["phase"] == "start"]
        r.record(f"lava_rim_push#{i}", "reflex_hazard", {}, min_y >= F - 0.01 and fire <= 0 and bool(sneak) and not any(e.get("reflex") == "hazard" for e in ev),
                 f"min_y={min_y:.2f} max_x={max_x:.2f} fire_ticks={fire} edge_sneak={bool(sneak)}", events=ev)
    # an accidental step in, one block inside the pool next to the rim, with water 3 blocks behind the rim (x -4..-3): out of the
    # lava, then into the water (vanilla keeps her burning 15 s after lava, which kills from the HP a step-in leaves)
    water = (f"fill -4 {F - 2} 0 -3 {F - 2} 1 minecraft:stone", f"fill -4 {F - 1} 0 -3 {F - 1} 1 minecraft:water",
             f"fill -4 {F - 1} 0 -3 {F - 1} 1 {floor}", f"fill -4 {F - 2} 0 -3 {F - 2} 1 minecraft:air")  # a floor under the water
    for i in range(0 if skip_in else N // 2):
        hazard_trial(r, f"lava_step_in_water_near#{i}", [water[0], water[1]], (1.5, F, 0.5, 90 + 36 * i), pool_off + [water[2], water[3]], limit_s=10.0,
                     check_extra=lambda me: int(srv(r, "Fire")[0]) <= 0, after=pool_on)  # O39: the pool opens under her
    for i in range(0 if skip_in else 3):  # no water anywhere near: measured, never claimed (vanilla's burn is the limit, not the reflex)
        hazard_trial(r, f"lava_step_in_no_water_measured#{i}", [], (1.5, F, 0.5, 90), pool_off,
                     check_extra=lambda me: me[1] >= F - 0.01, after=pool_on)
    if lava_only:  # `lava`: the rim and step-in cases alone (I14 lane rerun)
        return
    for i in range(N // 2):
        hazard_trial(r, f"fire#{i}", [f"setblock 0 {F - 1} 0 minecraft:netherrack"], (0.5, F, 0.5, 36 * i),
                     [f"setblock 0 {F} 0 minecraft:air", f"setblock 0 {F - 1} 0 {floor}"], after=[f"setblock 0 {F} 0 minecraft:fire"])
    for i in range(N // 2):
        hazard_trial(r, f"suffocation#{i}", [], (0.5, F, 0.5, 36 * i), [f"fill -1 {F} -1 1 {F + 2} 1 minecraft:air"],
                     after=[f"setblock 0 {F + 1} 0 minecraft:stone"],  # a block in her head cell
                     # vanilla's client also nudges a player out of a solid block, sometimes before the reflex starts (lane B
                     # a3-a4: 3 of 20): out of the block, alive and >= 18 HP within the window passes either way; the event is recorded
                     either=lambda me: not (-0.3 < me[0] < 1.3 and -0.3 < me[2] < 1.3) or health(r) >= 18)
    # drowning: a glass tank 5x5, 5 deep, on the floor at x 10..14; roofed except one corner, or open
    tank = [f"fill 9 {F - 1} -3 15 {F + 5} 3 minecraft:glass hollow"]  # its bottom is the floor layer: her feet cell stays air (O39)
    flood = [f"fill 10 {F} -2 14 {F + 4} 2 minecraft:water"]  # after she is in (O39: never a teleport into water)
    untank = [f"fill 9 {F} -3 15 {F + 5} 3 minecraft:air", f"fill 9 {F - 1} -3 15 {F - 1} 3 minecraft:smooth_stone"]
    for i in range(N):
        roofed = i % 2 == 0
        build = tank + ([f"setblock 14 {F + 5} 2 minecraft:air"] if roofed else [f"fill 10 {F + 5} -2 14 {F + 5} 2 minecraft:air"])
        hazard_trial(r, f"drowning_{'roofed' if roofed else 'open'}#{i}", build, (12.5, F, 0.5, 0), untank, limit_s=30.0, after=flood)
    # baselines (STOPPED, no reflexes): each hazard must hurt her, else the fixture is void
    for name, build, place, restore, wait, after in [
        ("drowning", tank, (12.5, F, 0.5, 0), untank, 18.0, flood)]:
        r.tp(0.5, FLOOR, 0.5, 0)
        heal_full(r)
        h0 = health(r)
        for c in build:
            r.rcon(c)
        stop(r)
        r.tp(*place)
        for c in after:
            r.rcon(c)
        end, h1 = time.time() + wait, h0
        while time.time() < end:
            h1 = health(r)
            if h1 < h0:
                break
            time.sleep(0.2)
        park(r)  # O35 (was: back to the arena centre)
        r.rcon(f"effect give {p} minecraft:fire_resistance 10 0 true")
        resume(r)
        for c in restore:
            r.rcon(c)
        r.rcon(f"effect clear {p}")
        r.record(f"{name}_baseline_stopped", "reflex_hazard", {}, h1 < h0, f"hp {h0}->{h1} while STOPPED (the hazard does hurt)")



def lava_walk(r):
    """CP-30 avoidance: a Baritone walk_to whose straight line crosses a 3x5 lava pool sunk into the floor (x 4..6, z -2..2).
    Pass = the walk ends ok within tolerance and the server never sees her in the lava or burning (sampled every 50 ms)."""
    floor = "minecraft:smooth_stone"
    pool = ([f"fill 4 {F - 2} -2 6 {F - 2} 2 minecraft:stone", f"fill 4 {F - 1} -2 6 {F - 1} 2 minecraft:lava"],  # stone under: no lava
            [f"fill 4 {F - 1} -2 6 {F - 1} 2 {floor}", f"fill 4 {F - 2} -2 6 {F - 2} 2 minecraft:air"])  # falling under the sky arena
    for i in range(N):
        z = (i % 5) - 2
        r.tp(0.5, FLOOR, z + 0.5, -90)
        extinguish(r)
        heal_full(r)
        for c in pool[0]:
            r.rcon(c)
        since = mark(r)
        goal = {"x": 10.5, "y": FLOOR, "z": z + 0.5}
        aid = request(r, "walk_to", {"pos": goal, "tolerance": 1.0})
        t0, min_y, fire, res = time.time(), 999.0, -1, None
        while time.time() < t0 + 25 and res is None:
            me = r.pos()
            min_y = min(min_y, me[1])
            fire = max(fire, int(srv(r, "Fire")[0]))
            res = result(r, aid, 0.05, since)
        h = health(r)
        park(r)  # O35
        for c in pool[1]:
            r.rcon(c)
        ok = bool(res) and res.get("status") == "ok" and min_y >= F - 0.01 and fire <= 0 and not reflex_events(r, since, "hazard")
        r.record(f"lava_walk#{i}", "walk_to", goal, ok, f"status={res and res.get('status')} min_y={min_y:.2f} fire_ticks={fire} hp={h}",
                 events=reflex_events(r, since), invalid=not ran(res))
        if fire > 0:
            r.rcon(f"effect give {r.player} minecraft:fire_resistance 10 0 true")



def creeper_hut(r):
    """CP-29 structures: a 3x3x3 wool hut 8-10 blocks west of Eve; a creeper 4 blocks east (straight away = into the hut, combat's
    lane C a2 case), north-east, and north; lit (Fuse 30) or AI-only. mob_griefing on for this suite only, so a blast does take
    blocks. Pass = the hut is block-for-block intact (execute if blocks against a reference copy), no damage to her, and she was
    >= 7 from the blast. Floor and hut are rebuilt after every trial."""
    p = r.player
    griefing = r.rcon("gamerule mob_griefing").rsplit(" ", 1)[-1].strip()
    hut = f"-10 {F} -1 -8 {F + 2} 1"
    ref = f"-10 {F + 30} -1 -8 {F + 32} 1"

    def rebuild():
        r.tp(-12.5, FLOOR, -14.5, 0)  # O35: clear of the hut/floor volume (x -18..10, z -10..10) while it is rebuilt
        r.rcon(f"fill -18 {F} -10 10 {F + 4} 10 minecraft:air")
        r.rcon(f"fill -18 {F - 1} -10 10 {F - 1} 10 minecraft:smooth_stone")
        r.rcon(f"fill {hut} minecraft:white_wool")

    r.rcon(f"fill {ref} minecraft:white_wool")
    r.rcon("gamerule mob_griefing true")
    try:
        sides = [("east", 0), ("north_east", -45), ("north", -90)]
        for i in range(N):
            side, ang = sides[i % 3]
            lit = (i // 3) % 2 == 0
            rebuild()
            r.tp(0.5, FLOOR, 0.5, 90)
            heal_full(r)
            since, h0 = mark(r), health(r)
            a = math.radians(ang)
            cx, cz = 0.5 + 4 * math.cos(a), 0.5 + 4 * math.sin(a)
            nbt = "{PersistenceRequired:1b,Tags:[\"cr\"]" + (",ignited:1b,Fuse:30s" if lit else "") + "}"
            r.rcon(f"summon minecraft:creeper {cx:.2f} {F} {cz:.2f} {nbt}")
            t0, last_d, exploded, last_c = time.time(), None, False, None
            while time.time() < t0 + 10:
                c = nums(r.rcon("data get entity @e[type=minecraft:creeper,tag=cr,limit=1] Pos"))
                if len(c) != 3:
                    exploded = True
                    break
                last_c, last_d = c, math.dist(c, r.pos())
                time.sleep(0.1)
            time.sleep(0.6)
            h1 = health(r)
            r.rcon(f"kill @e[type=minecraft:creeper,{near()}]")
            intact = r.rcon(f"execute if blocks {hut} -10 {F + 30} -1 all").startswith("Test passed")
            ev = reflex_events(r, since, "creeper_flee")
            starts = [e for e in ev if e["phase"] == "start"]
            ends = [e for e in ev if e["phase"] == "end"]
            hut_d = last_c and math.hypot(last_c[0] + 8.5, last_c[2] - 0.5)
            checks = {"hut_intact": intact, "no_damage": h1 >= h0, "event": bool(starts),
                      "safe_at_blast": not exploded or (last_d is not None and last_d >= 7)}
            r.record(f"creeper_hut_{side}_{'lit' if lit else 'ai'}#{i}", "reflex_creeper", {"side": side, "lit": lit}, all(checks.values()),
                     f"hp {h0}->{h1} exploded={exploded} her_dist={last_d and round(last_d, 1)} blast_to_hut={hut_d and round(hut_d, 1)} "
                     f"scores={ends and ends[0].get('detail', {}).get('flee_scores')} " + " ".join(k for k, v in checks.items() if not v), events=ev)
    finally:
        rebuild()
        r.rcon(f"fill -18 {F} -10 10 {F + 4} 10 minecraft:air")
        r.rcon(f"fill {ref} minecraft:air")
        r.rcon(f"gamerule mob_griefing {griefing}")


def creeper_walk_hut(r):
    """O57a (combat's 45L k_walk, CP-29 a2: 5 of 20 ended cornered_shielded at once, never moving, blast at 4.0, with only
    +-60/+-90 scored: 0/+-30 had no footing there). She walks past a 3x3x3 wool hut within 2 of its wall and a lit creeper
    (Fuse 30) appears 4 blocks north of her the moment she is at x <= -3.2.
    - open: hut x -8..-6, z 0..2; walk_to (1.5, F, 1.5) -> (-18.5, F, -10.5) (combat's geometry).
    - blocked: the 45L option set. She walks due west along z 0.5 ((1.5, F, 0.5) -> (-18.5, F, 0.5)), a 2-high wall at z 2
      (x -12..2) leaves no footing straight away or at +-30, and the hut is just north of her path (x -8..-6, z -3..-1).
    - tie: the 45L scores (structure on every side, +-90 equal). Same walk, the wall is wool (built) and symmetric about her
      (x -18..12, so both sides' scoring windows lie fully on it), no hut: the sides tie exactly, and the first scanned is
      behind her. Lane D a2 (wall -13..7): not exact (west 83, east 87), west won, 20/20.
    Pass = she moves >= 3 from where the creeper appeared, is >= 6 from the blast (or no blast), no damage. The hut is recorded,
    not judged: a lit creeper blows where it stands, 2-5 from the hut (dev a61: 10/10 hut hit with her 10.5+ away)."""
    p = r.player
    griefing = r.rcon("gamerule mob_griefing").rsplit(" ", 1)[-1].strip()

    def boxes(blocked):
        z0 = -3 if blocked else 0  # (the tie variant has no hut: its wool wall is the structure)
        return f"-8 {F} {z0} -6 {F + 2} {z0 + 2}", f"-8 {F + 30} {z0} -6 {F + 32} {z0 + 2}", z0

    def rebuild(blocked):
        r.tp(-12.5, FLOOR, -14.5, 0)  # O35: off the hut/floor volume while it is rebuilt
        r.rcon(f"fill -23 {F} -15 9 {F + 4} 13 minecraft:air")
        r.rcon(f"fill -23 {F - 1} -15 9 {F - 1} 13 minecraft:smooth_stone")
        r.rcon(f"fill -23 {F + 30} -15 9 {F + 32} 13 minecraft:air")
        if blocked == "tie":
            r.rcon(f"fill -18 {F} 2 12 {F + 1} 2 minecraft:white_wool")  # both side windows fully on it: an exact tie
            return
        hut, ref, _ = boxes(blocked)
        r.rcon(f"fill {hut} minecraft:white_wool")
        r.rcon(f"fill {ref} minecraft:white_wool")
        if blocked:
            r.rcon(f"fill -12 {F} 2 2 {F + 1} 2 minecraft:stone")

    r.rcon("gamerule mob_griefing true")
    try:
        for i in range(N):
            want = sys.argv[sys.argv.index("--variant") + 1] if "--variant" in sys.argv else None  # open | blocked | tie
            blocked = {"open": False, "blocked": True, "tie": "tie"}[want] if want else (False, True, "tie")[i % 3]
            hut, ref, z0 = boxes(blocked)
            rebuild(blocked)
            sz, gz = (0.5, 0.5) if blocked else (1.5, -10.5)
            r.tp(1.5, FLOOR, sz, 90)
            heal_full(r)
            since, h0 = mark(r), health(r)
            aid = request(r, "walk_to", {"pos": {"x": -18.5, "y": FLOOR, "z": gz}, "tolerance": 1.0 + 0.01 * i})
            t0, at = time.time(), None
            while time.time() < t0 + 15:
                me = r.pos()
                if me[0] <= -3.2:
                    at = me
                    break
                time.sleep(0.05)
            if at is None:
                result(r, aid, 5, since)
                r.record(f"creeper_walk_hut#{i}", "reflex_creeper", {}, False, "she never reached x -3.2", invalid=True)
                continue
            r.rcon(f"summon minecraft:creeper {at[0]:.2f} {F} {at[2] - 4:.2f} {{PersistenceRequired:1b,Tags:[\"cr\"],ignited:1b,Fuse:30s}}")
            t1, last_d, exploded = time.time(), None, False
            while time.time() < t1 + 6:
                c = nums(r.rcon("data get entity @e[type=minecraft:creeper,tag=cr,limit=1] Pos"))
                if len(c) != 3:
                    exploded = True
                    break
                last_d = math.dist(c, r.pos())
                time.sleep(0.05)
            time.sleep(0.6)
            h1, me = health(r), r.pos()
            r.rcon(f"kill @e[type=minecraft:creeper,{near()}]")
            result(r, aid, 20, since)
            intact = blocked == "tie" or r.rcon(f"execute if blocks {hut} -8 {F + 30} {z0} all").startswith("Test passed")
            ev = reflex_events(r, since, "creeper_flee")
            ends = [e for e in ev if e["phase"] == "end"]
            moved = math.hypot(me[0] - at[0], me[2] - at[2])
            checks = {"no_damage": h1 >= h0, "moved": moved >= 3, "safe_at_blast": not exploded or (last_d is not None and last_d >= 6)}
            r.record(f"creeper_walk_hut_{'tie' if blocked == 'tie' else 'blocked' if blocked else 'open'}#{i}", "reflex_creeper", {"variant": str(blocked)}, all(checks.values()),
                     f"at=({at[0]:.1f},{at[2]:.1f}) moved={moved:.1f} hp {h0}->{h1} exploded={exploded} her_dist={last_d and round(last_d, 2)} hut_intact={intact} "
                     f"end={[e.get('result') for e in ends]} scores={ends and ends[0].get('detail', {}).get('flee_scores')} "
                     + " ".join(k for k, v in checks.items() if not v), events=ev)
    finally:
        r.tp(-12.5, FLOOR, -14.5, 0)
        r.rcon(f"fill -23 {F} -15 9 {F + 4} 13 minecraft:air")
        r.rcon(f"fill -23 {F - 1} -15 9 {F - 1} 13 minecraft:smooth_stone")
        r.rcon(f"fill -23 {F + 30} -15 9 {F + 32} 13 minecraft:air")
        r.rcon(f"gamerule mob_griefing {griefing}")


def creeper_corner(r):
    """O72a (combat k_follow_shallow, 45L + lane E a28: she fled into the corner, cornered_shielded, blast at 2.6-2.8). Walls
    5 high 3.5 blocks east (x 8) and south (z 8) of her at (4.5, F, 4.5); an unlit creeper appears 6 north and follows. Straight
    away is south, into the wall; the corner is SE. Pass = no damage and no blast within 6 of her (or no blast), and she does
    not end within 1.5 of a wall.
    `tight` (combat lane E a33 #13/#19, the NW corner target): she stands 2.5 from both walls and the creeper comes from 4 west,
    so straight away (east) is into the wall, north-east the corner, and only south along the wall is open."""
    walls = [f"fill 8 {F} -14 8 {F + 4} 8 minecraft:smooth_stone", f"fill -14 {F} 8 8 {F + 4} 8 minecraft:smooth_stone"]
    cases = [("wide", (4.5, 4.5, 90), (4.5, -1.5)), ("tight", (5.5, 5.5, 90), (1.5, 5.5))]
    for i in range(N):
        name, (ex, ez, yaw), (cx, cz) = cases[i % 2]
        park(r)
        r.rcon(f"fill -20 {F} -20 20 {F + 5} 20 minecraft:air")
        for c in walls:
            r.rcon(c)
        r.tp(ex, FLOOR, ez, yaw)
        heal_full(r)
        since, h0 = mark(r), health(r)
        r.rcon(f"summon minecraft:creeper {cx} {F} {cz} {{PersistenceRequired:1b,Tags:[\"cr\"]}}")
        t0, last_d, exploded, min_d = time.time(), None, False, 99.0
        while time.time() < t0 + 14:
            c = nums(r.rcon("data get entity @e[type=minecraft:creeper,tag=cr,limit=1] Pos"))
            if len(c) != 3:
                exploded = True
                break
            last_d = math.dist(c, r.pos())
            min_d = min(min_d, last_d)
            if reflex_events(r, since, "creeper_flee", "end") and last_d >= 8:
                break
            time.sleep(0.05)
        time.sleep(0.6)
        h1, me = health(r), r.pos()
        r.rcon(f"kill @e[type=minecraft:creeper,{near()}]")
        wall_gap = min(8 - me[0], 8 - me[2])
        ev = reflex_events(r, since, "creeper_flee")
        ends = [e for e in ev if e["phase"] == "end"]
        checks = {"no_damage": h1 >= h0, "safe_at_blast": not exploded or (last_d is not None and last_d >= 6), "not_cornered_at_wall": wall_gap >= 1.5}
        r.record(f"creeper_corner_{name}#{i}", "reflex_creeper", {"variant": name}, all(checks.values()),
                 f"end_pos=({me[0]:.1f},{me[2]:.1f}) wall_gap={wall_gap:.1f} exploded={exploded} her_dist={last_d and round(last_d, 2)} min_d={min_d:.1f} "
                 f"hp {h0}->{h1} end={[e.get('result') for e in ends]} scores={ends and ends[0].get('detail', {}).get('flee_scores')} "
                 f"runs={ends and ends[0].get('detail', {}).get('flee_runs')} "
                 + " ".join(k for k, v in checks.items() if not v), events=ev)
    park(r)
    r.rcon(f"fill -20 {F} -20 20 {F + 5} 20 minecraft:air")


def thin_aim(r):
    """O65 (skills CP-40 a10/a16): mine_block on thin or offset blocks at her eye level. The block sits on a pedestal 2 blocks
    east (cell y F+1, her eye at F+1.62) with a stone backstop behind it, so a near-flat ray to the cell centre passes over a
    thin outline (or beside an offset plant) and hits the backstop. Pass = mine_block ok, the cell is air, the backstop intact."""
    cases = [("minecraft:leaf_litter", "minecraft:grass_block"), ("minecraft:white_carpet", "minecraft:stone"),
             ("minecraft:snow[layers=1]", "minecraft:stone"), ("minecraft:rail", "minecraft:stone"),
             ("minecraft:stone_pressure_plate", "minecraft:stone"), ("minecraft:poppy", "minecraft:grass_block"),
             ("minecraft:short_grass", "minecraft:grass_block")]
    r.rcon(f"item replace entity {r.player} hotbar.1 with minecraft:wooden_shovel")  # snow layers need a shovel (a68: no_tool)
    for i in range(max(1, N // 7)):
        for k, (block, under) in enumerate(cases):
            z = k - 3 + 8 * (i % 2)  # a different cell per case and round
            park(r)
            r.rcon(f"fill 1 {F} {z - 1} 4 {F + 2} {z + 1} minecraft:air")
            r.rcon(f"setblock 2 {F} {z} {under}")
            r.rcon(f"setblock 3 {F + 1} {z} minecraft:stone")  # the backstop
            r.rcon(f"setblock 2 {F + 1} {z} {block}")
            r.tp(0.5, FLOOR, z + 0.5, -90)
            time.sleep(0.5)
            since = mark(r)
            res = result(r, request(r, "mine_block", {"pos": {"x": 2, "y": F + 1, "z": z}}), 15, since)
            gone = r.rcon(f"execute if block 2 {F + 1} {z} minecraft:air").startswith("Test passed")
            back = r.rcon(f"execute if block 3 {F + 1} {z} minecraft:stone").startswith("Test passed")
            ok = bool(res) and res.get("status") == "ok" and gone and back
            r.record(f"thin_aim_{block.split(':')[1].split('[')[0]}#{i}", "mine_block", {"z": z}, ok,
                     f"status={res and (res.get('status'), res.get('code'), res.get('note'))} gone={gone} backstop_intact={back}", invalid=not ran(res))
            park(r)
            r.rcon(f"fill 1 {F} {z - 1} 4 {F + 2} {z + 1} minecraft:air")


def bow_draw(r):
    """O32: with a bow drawn (use_item hold, main hand) the shield cannot rise, so an arrow gets dodge, and at the floor retreat
    still runs. Pass = the reflex starts, the draw is released (she is not left using the item), and the threat is handled:
    no arrow damage (dodge), 16 blocks clear (retreat)."""
    p = r.player
    for i in range(N):
        retreat_case = i % 2 == 1
        r.tp(0.5, FLOOR, 0.5, 0)
        r.rcon(f"item replace entity {p} hotbar.0 with minecraft:bow")
        r.rcon(f"item replace entity {p} hotbar.2 with minecraft:arrow 16")
        r.rcon(f"item replace entity {p} weapon.offhand with minecraft:shield")
        r.act("select_hotbar", {"slot": 0})
        heal_full(r)
        since = mark(r)
        aid = request(r, "use_item", {"hold_ms": 4990 - 10 * i})  # distinct per trial: the lane mind refuses an identical repeat
        time.sleep(0.8)
        h0 = health(r)
        if retreat_case:
            r.rcon(f"summon minecraft:zombie 3.5 {F} 0.5 {{NoAI:1b,PersistenceRequired:1b,Silent:1b}}")
            time.sleep(0.3)
            r.rcon(f"damage {p} 15 minecraft:generic")
            h0 = 5.0
            t0, dmax = time.time(), 0.0
            while time.time() < t0 + 7 and dmax < 16:
                me = r.pos()
                dmax = max(dmax, math.hypot(me[0] - 3.5, me[2] - 0.5))
                time.sleep(0.05)
            ok_threat, what = dmax >= 16, f"max_dist={dmax:.1f}"
            name = "retreat"
        else:
            fire_arrow(r, 90 + 36 * i)
            time.sleep(2.0)
            ok_threat, what = health(r) >= h0, f"hp {h0}->{health(r)}"
            name = "dodge"
        res = result(r, aid, 8, since)
        ev = reflex_events(r, since)
        started = [e for e in ev if e.get("reflex") == name and e["phase"] == "start"]
        shields = [e for e in ev if e.get("reflex") == "shield"]
        # the draw ran and the reflex pre-empted it (refused = it never drew); the lane mind stores it as "unlisted:interrupted"
        # until registry 0.13.0 lists interrupted for use_item
        drew = bool(res) and str(res.get("code") or "").endswith("interrupted")
        r.record(f"bow_draw_{name}#{i}", f"reflex_{name}", {}, bool(started) and ok_threat and not shields and drew,
                 f"{what} {name}_start={bool(started)} shield_events={len(shields)} use_item={res and res.get('status')}/{res and res.get('code')}", events=ev, invalid=not ran(res))
        r.rcon(f"kill @e[type=minecraft:zombie,{near()}]")
    r.rcon(f"item replace entity {p} weapon.offhand with minecraft:air")
    r.rcon(f"item replace entity {p} hotbar.0 with minecraft:stone_sword")
    r.act("select_hotbar", {"slot": 0})



def equip(r):
    """O41 equip_best (registry 0.13.0): each case kits her by RCON, calls the skill, and judges by RCON `execute if items` (server
    truth) plus the evidence's `changed`; a case with nothing better must change nothing (idempotent)."""
    p = r.player
    iron = [f"item replace entity {p} inventory.{i} with minecraft:iron_{x}" for i, x in enumerate(["helmet", "chestplate", "leggings", "boots"])]
    cases = [
        ("full_armor", iron, {}, [("armor.head", "minecraft:iron_helmet"), ("armor.chest", "minecraft:iron_chestplate"),
                                   ("armor.legs", "minecraft:iron_leggings"), ("armor.feet", "minecraft:iron_boots")], 4),
        ("slots_head_only", iron, {"slots": ["head"]}, [("armor.head", "minecraft:iron_helmet")], None),  # idle upkeep wears the rest anyway
        ("upgrade_and_broken", [f"item replace entity {p} armor.chest with minecraft:leather_chestplate",
                                f"item replace entity {p} armor.head with minecraft:diamond_helmet[damage=360]",
                                f"item replace entity {p} inventory.5 with minecraft:diamond_chestplate",
                                f"item replace entity {p} inventory.6 with minecraft:leather_helmet"], {},
         [("armor.chest", "minecraft:diamond_chestplate"), ("armor.head", "minecraft:leather_helmet")], 2),
        ("shield", [f"item replace entity {p} inventory.8 with minecraft:shield"], {}, [("weapon.offhand", "minecraft:shield")], 1),
        ("tool_for_stone", [f"item replace entity {p} hotbar.1 with minecraft:wooden_pickaxe", f"item replace entity {p} inventory.10 with minecraft:iron_pickaxe"],
         {"for_block": "minecraft:stone", "slots": []}, [("weapon.mainhand", "minecraft:iron_pickaxe")], 1),
        ("tool_for_obsidian", [f"item replace entity {p} hotbar.1 with minecraft:iron_pickaxe", f"item replace entity {p} inventory.11 with minecraft:diamond_pickaxe"],
         {"for_block": "minecraft:obsidian", "slots": []}, [("weapon.mainhand", "minecraft:diamond_pickaxe")], 1),
        ("worn_tool_skipped", [f"item replace entity {p} inventory.12 with minecraft:diamond_pickaxe[damage=1560]",
                               f"item replace entity {p} inventory.13 with minecraft:iron_pickaxe"],
         {"for_block": "minecraft:stone", "slots": []}, [("weapon.mainhand", "minecraft:iron_pickaxe"), ("inventory.12", "minecraft:diamond_pickaxe")], 1),
        ("weapon", [f"item replace entity {p} inventory.14 with minecraft:diamond_sword"], {"weapon": True, "slots": []},
         [("weapon.mainhand", "minecraft:diamond_sword")], 1),
        ("nothing_better", [f"item replace entity {p} armor.chest with minecraft:diamond_chestplate",
                            f"item replace entity {p} inventory.3 with minecraft:iron_chestplate"], {},
         [("armor.chest", "minecraft:diamond_chestplate"), ("inventory.3", "minecraft:iron_chestplate")], 0),
    ]
    for rep in range(max(1, N // 5)):
        for name, cmds, params, expect, n_changed in cases:
            r.tp(0.5, FLOOR, 0.5, 0)
            since = mark(r)
            kit(r, *cmds)  # stone_sword in hotbar.0
            r.act("select_hotbar", {"slot": 0})
            time.sleep(0.4)
            _, _, res = r.act("equip_best", params, 10)
            time.sleep(0.4)
            got = {slot: has_item(r, slot, item) for slot, item in expect}
            ch = (res or {}).get("evidence", {}).get("changed")
            # the idle upkeep (CP-25) may have worn armour between the kit and the call: together they make the expected changes
            ups = [e for e in reflex_events(r, since, "upkeep") if e["phase"] == "equip_armor"]
            allowed = params.get("slots", ["head", "chest", "legs", "feet", "offhand"]) + ["mainhand"]
            ok = (bool(res) and res.get("status") == "ok" and all(got.values()) and ch is not None
                  and all(c.split("=")[0] in allowed for c in ch) and (n_changed is None or len(ch) + len(ups) == n_changed))
            r.record(f"equip_{name}#{rep}", "equip_best", params, ok, f"status={res and res.get('status')} changed={ch} upkeep={len(ups)} server={got} "
                     f"after={(res or {}).get('evidence', {}).get('after')}", invalid=not ran(res))
    kit(r, f"item replace entity {p} hotbar.3 with minecraft:bread 16")



def night(r):
    """CP-30 darkness and beds (for the 45L's real nights; also run on lanes). A closed 5x5x3 stone room around her (interior
    x -2..2, z -2..2): with a torch in the hotbar she places one beside her (the reflex_arena zone allows place); without one and
    no lit spot within 16 she stays put and reports no_way. A closed 1-wide corridor with a glowstone 20 blocks east (she starts
    where its light is 0): she walks to the lit part (light >= 8, from x 13). Beds: a bed
    2 blocks east at night with nothing hostile near -> she sleeps (server: sleeping_pos in her entity data); by day, or with a
    zombie 6 blocks away, she must not. Judged by RCON; reflex events recorded."""
    p = r.player
    floor = "minecraft:smooth_stone"
    room = [f"fill -3 {F - 1} -3 3 {F + 3} 3 minecraft:stone hollow"]  # closed 5x5x3, its floor the floor layer
    corridor = [f"fill -2 {F - 1} -1 21 {F + 2} 1 minecraft:stone hollow", f"setblock 20 {F} 0 minecraft:glowstone"]  # 1 wide, lamp 20 east
    unbuild = [f"fill -3 {F} -3 21 {F + 3} 3 minecraft:air", f"fill -3 {F - 1} -3 21 {F - 1} 3 {floor}"]

    def torch_beside():
        return [c for c in ((0, -1), (0, 1), (1, 0), (-1, 0)) if r.rcon(f"execute if block {c[0]} {F} {c[1]} minecraft:torch") == "Test passed"]

    for i in range(N):
        case = ("torch", "leave", "no_way")[i % 3]
        r.rcon("time set midnight" if i % 2 else "time set noon")  # enclosed darkness does not depend on the time
        r.tp(0.5, FLOOR, 0.5, 0)
        kit(r, *([f"item replace entity {p} hotbar.4 with minecraft:torch 8"] if case == "torch" else []))
        for c in (corridor if case == "leave" else room):
            r.rcon(c)
        since = mark(r)
        t0, done = time.time(), []
        while time.time() < t0 + 15 and not done:
            done = reflex_events(r, since, "darkness", "end")
            time.sleep(0.2)
        me = r.pos()
        res = done and done[0].get("result")
        if case == "torch":
            ok, what = res == "torch_placed" and bool(torch_beside()), f"torch_at={torch_beside()}"
        elif case == "leave":
            ok, what = res == "moved_to_light" and me[0] >= 12.5, f"x={me[0]:.1f}"
        else:
            ok, what = res == "no_way" and math.hypot(me[0] - 0.5, me[2] - 0.5) < 0.6, f"moved={math.hypot(me[0] - 0.5, me[2] - 0.5):.2f}"
        r.record(f"dark_{case}#{i}", "reflex_darkness", {}, ok, f"end={res} {what}", events=reflex_events(r, since))
        park(r)  # O35
        for c in unbuild:
            r.rcon(c)
    r.rcon("time set noon")
    bed = (f"setblock 2 {F} 0 minecraft:red_bed[facing=east,part=foot]", f"setblock 3 {F} 0 minecraft:red_bed[facing=east,part=head]",
           f"fill 2 {F} 0 3 {F} 0 minecraft:air")

    def sleeping():
        return "has the following entity data" in r.rcon(f"data get entity {p} sleeping_pos")  # 26.3 key (was SleepingX)

    def respawn_at_bed():  # using a bed sets her respawn point to it: server truth that outlasts a short sleep
        return "[I; 3, " in r.rcon(f"data get entity {p} respawn.pos") or "[I; 2, " in r.rcon(f"data get entity {p} respawn.pos")

    saved_spawn = r.rcon(f"data get entity {p} respawn.pos")

    for i in range(N):
        case = ("night", "day", "hostile")[i % 3]
        r.rcon("time set noon" if case == "day" else "time set 18000")
        r.tp(0.5, FLOOR, 0.5, 90)
        heal_full(r)
        r.rcon(bed[0])
        r.rcon(bed[1])
        r.rcon(f"spawnpoint {p} 0 {F} 10")  # away from the bed, so a sleep shows as the respawn point moving to it
        if case == "hostile":
            r.rcon(f"summon minecraft:zombie -5.5 {F} 0.5 {{NoAI:1b,PersistenceRequired:1b,Silent:1b}}")
        since = mark(r)
        t0, slept = time.time(), False
        while time.time() < t0 + (8 if case == "night" else 5) and not slept:
            slept = sleeping()
            time.sleep(0.15)
        slept = slept or respawn_at_bed()
        ev = reflex_events(r, since, "sleep")
        ok = slept if case == "night" else not slept
        r.record(f"bed_{case}#{i}", "reflex_sleep", {}, ok, f"server_sleeping={slept} events={[(e['phase'], e.get('result')) for e in ev]}", events=ev)
        r.rcon("time set noon")  # vanilla wakes her at day
        time.sleep(1.0)
        r.rcon(f"kill @e[type=minecraft:zombie,{near()}]")
        park(r)  # O35
        r.rcon(bed[2])
    r.rcon("time set noon")
    import re as _re
    v = _re.findall(r"-?\d+", saved_spawn.split("data:")[-1]) if "has the following" in saved_spawn else None
    if v and len(v) >= 3:
        r.rcon(f"spawnpoint {p} {v[0]} {v[1]} {v[2]}")  # her respawn point as it was before the bed trials



def nav(r, short=False):
    """CP-33: Baritone walk_to round trips across the arena, (-16.5, F, 0.5) -> (16.5, F, 0.5) -> back, each on its own terrain
    (built, then torn down with her parked, O35): flat; a hill 4 high (steps of 1); woodland (trees in the way); a cave route
    (a 5-high wall across the whole arena with a dog-leg tunnel 1 wide, 2 high, the only way through); a changed obstacle (a wall
    with one gap dropped across her path 1.5 s into the walk). Pass = both legs end ok within 1.0, no damage, blocks broken 0 and
    placed 0. Budgets: a 1-thick wall across the arena with allow_break and max_blocks_broken 2 (she breaks through, <= 2 broken)
    and with 1 (fails explicitly: unreachable/stuck within 40 s, <= 1 broken). Unreachable: a goal sealed in glass fails
    explicitly within 40 s without wandering (path < 80). Cancellation: a stop 2 s into a walk ends it cancelled and she stands."""
    p = r.player
    floor = "minecraft:smooth_stone"
    S, G = (-16.5, FLOOR, 0.5), (16.5, FLOOR, 0.5)
    W = 24  # the floor; a glass rim at 25 closes it (the lanes' sky arena has one; the flat dev world gets one here)
    rim = [f"fill -25 {F} -25 25 {F + 5} -25 minecraft:glass", f"fill -25 {F} 25 25 {F + 5} 25 minecraft:glass",
           f"fill -25 {F} -25 -25 {F + 5} 25 minecraft:glass", f"fill 25 {F} -25 25 {F + 5} 25 minecraft:glass"]

    def build(cmds):  # a fill that errors (too big, not loaded) is a fixture fault, never a silent no-op (dev a46-a48)
        for c in cmds:
            out = r.rcon(c)
            if not (out.startswith("Successfully") or out.startswith("No blocks") or out.startswith("Changed") or out.startswith("Could not set")):
                raise RuntimeError(f"fixture command failed: {c!r} -> {out!r}")
    clear = [f"fill {-W} {F} {-W} {W} {F + 5} {W} minecraft:air", f"fill {-W} {F - 1} {-W} {W} {F - 1} {W} {floor}"]  # the whole floor (rim glass at 25)
    hill = [f"fill {-8 + k} {F} {-W} {8 - k} {F + k} {W} minecraft:dirt" for k in range(4)]
    woods = []
    for x, z in ((-10, 0), (-6, -2), (-2, 1), (2, -1), (6, 2), (10, 0), (-8, 3), (4, -3), (0, 0), (8, -2)):
        woods += [f"fill {x} {F} {z} {x} {F + 3} {z} minecraft:oak_log", f"fill {x - 1} {F + 3} {z - 1} {x + 1} {F + 4} {z + 1} minecraft:oak_leaves[persistent=true] keep"]
    cave = [f"fill -3 {F} {-W} 3 {F + 4} {W} minecraft:stone", f"fill -3 {F} 0 0 {F + 1} 0 minecraft:air",
            f"fill 0 {F} 1 0 {F + 1} 1 minecraft:air", f"fill 0 {F} 2 3 {F + 1} 2 minecraft:air"]
    wall1 = [f"fill 0 {F} {-W} 0 {F + 3} {W} minecraft:stone"]  # floor-wide: no way round (a1: gaps at |z| 23-24)

    def walk(goal, extra=None, timeout=60, during=None):
        since = mark(r)
        prm = {"pos": {"x": goal[0], "y": goal[1], "z": goal[2]}, "tolerance": 1.0, **(extra or {})}
        aid = request(r, "walk_to", prm)
        if during:
            during()
        return result(r, aid, timeout, since), since

    def ok_leg(res, goal):
        ev = (res or {}).get("evidence", {})
        return bool(res) and res.get("status") == "ok" and ev.get("blocks_broken", 0) == 0 and ev.get("blocks_placed", 0) == 0

    def round_trip(name, i, terrain, during=None):
        park(r)
        build(clear + rim + terrain)
        r.tp(S[0], S[1], S[2], -90)
        heal_full(r)
        h0 = health(r)
        t0 = time.time()
        a_res, since = walk(G, during=during)
        b_res, _ = walk(S) if ok_leg(a_res, G) else (None, None)
        h1, me = health(r), r.pos()
        ok = ok_leg(a_res, G) and ok_leg(b_res, S) and h1 >= h0
        detail = (f"out={a_res and (a_res.get('status'), a_res.get('code'))} back={b_res and (b_res.get('status'), b_res.get('code'))} "
                  f"path={(a_res or {}).get('evidence', {}).get('path_length')}+{(b_res or {}).get('evidence', {}).get('path_length')} "
                  f"broken={(a_res or {}).get('evidence', {}).get('blocks_broken')} hp {h0}->{h1} {time.time() - t0:.0f}s")
        r.record(f"nav_{name}#{i}", "walk_to", {}, ok, detail, invalid=not (ran(a_res) and (b_res is None or ran(b_res))))

    if short:  # O318: a nav sanity for a jar whose Baritone path did not change: the flat round trip only
        for i in range(N):
            round_trip("flat", i, [])
        park(r)
        build(clear)
        return

    def drop_wall():
        time.sleep(1.5)
        r.rcon(f"fill 4 {F} {-W} 4 {F + 2} {W} minecraft:stone")
        r.rcon(f"fill 4 {F} 13 4 {F + 2} 15 minecraft:air")  # one gap, far to the side

    for i in range(max(1, N // 5)):
        round_trip("flat", i, [])
        round_trip("hills", i, hill)
        round_trip("woodland", i, woods)
        round_trip("cave", i, cave)
        round_trip("changed_obstacle", i, [], during=drop_wall)
    # break budget: through a 1-thick wall (2 blocks for a 2-high hole); and a budget too small for it
    for i in range(max(1, N // 5)):
        for budget, want_ok in ((2, True), (1, False)):
            park(r)
            build(clear + rim + wall1)
            r.rcon(f"item replace entity {p} hotbar.1 with minecraft:wooden_pickaxe")
            r.tp(S[0], S[1], S[2], -90)
            heal_full(r)
            t0 = time.time()
            # a different tolerance each trial: the mind refuses the same failed skill+params a 4th time in 10 min (CP-22; lane E a1)
            res, _ = walk(G, {"allow_break": True, "max_blocks_broken": budget, "tolerance": 1.0 + 0.05 * i}, timeout=70)
            ev = (res or {}).get("evidence", {})
            br = ev.get("blocks_broken")
            if want_ok:
                ok = bool(res) and res.get("status") == "ok" and br is not None and br <= budget
            else:
                ok = bool(res) and res.get("status") == "failed" and res.get("code") in ("unreachable", "stuck") and br is not None and br <= budget \
                    and time.time() - t0 <= 45
            r.record(f"nav_break_budget_{budget}#{i}", "walk_to", {"max_blocks_broken": budget}, ok,
                     f"status={res and (res.get('status'), res.get('code'))} broken={br} budget_hit={ev.get('break_budget_hit')} {time.time() - t0:.0f}s",
                     invalid=not ran(res))
    # unreachable: the goal sealed in glass
    for i in range(3):
        park(r)
        build(clear + rim + [f"fill 15 {F} -1 18 {F + 3} 2 minecraft:glass hollow"])
        r.tp(S[0], S[1], S[2], -90)
        t0 = time.time()
        res, _ = walk(G, {"tolerance": 1.2 + 0.05 * i}, timeout=70)
        ev = (res or {}).get("evidence", {})
        ok = bool(res) and res.get("status") == "failed" and res.get("code") in ("unreachable", "stuck") and time.time() - t0 <= 45 \
            and (ev.get("path_length") or 0) < 80
        r.record(f"nav_unreachable#{i}", "walk_to", {}, ok, f"status={res and (res.get('status'), res.get('code'))} path={ev.get('path_length')} {time.time() - t0:.0f}s",
                 invalid=not ran(res))
    # cancellation: a stop 2 s in
    for i in range(3):
        park(r)
        build(clear + rim)
        r.tp(S[0], S[1], S[2], -90)
        since = mark(r)
        aid = request(r, "walk_to", {"pos": {"x": G[0], "y": G[1], "z": G[2]}, "tolerance": 1.5 + 0.05 * i})
        time.sleep(2.0)
        r.act("stop", {})
        res = result(r, aid, 10, since)
        a0 = r.pos()
        time.sleep(0.5)  # vanilla's slide after the keys release (lane E a2: 0.33 when the result came through the mind first)
        a1 = r.pos()
        time.sleep(1.0)
        a2 = r.pos()
        still = math.hypot(a2[0] - a1[0], a2[2] - a1[2]) < 0.1
        ok = bool(res) and res.get("status") == "cancelled" and still
        r.record(f"nav_cancel#{i}", "walk_to", {}, ok, f"status={res and (res.get('status'), res.get('code'))} slide={math.hypot(a1[0] - a0[0], a1[2] - a0[2]):.2f} "
                 f"moved_after={math.hypot(a2[0] - a1[0], a2[2] - a1[2]):.2f}",
                 invalid=not (ran(res) or (res or {}).get("status") == "cancelled"))
    park(r)
    for c in clear:
        r.rcon(c)
    r.rcon(f"item replace entity {p} hotbar.1 with minecraft:air")



def survival(r):
    """CP-31 COMPONENTS (O54 b, never the CP-31 pass): the harness is a scripted stand-in planner. keep_inventory OFF for the suite,
    deliberate deaths on the dev account (the one place a dev death is by design). Respawn point set to the arena shelter spot
    (-12.5, F, -12.5) for the suite and put back after.
    - recover (x5): kit (iron chestplate + helmet worn, stone sword, bread 6), killed at (8.5, F, 8.5). Then the body's own respawn,
      walk_to the death pos (from the death event), equip_best {} + {weapon}, walk_to the shelter. Pass = death event with pos and
      cause, respawn event, every item back (counts) and worn again, back at the shelter, one death only, recovery time and the
      drop's Age at pickup recorded (despawn 6000 ticks).
    - inaccessible (x2): killed inside a sealed glass box: walk_to the drops fails explicitly; a bounded loss report (items listed,
      no retry storm, no second death).
    - destroyed (x2): killed in lava (drops burn): walk_to its pos fails or finds nothing; loss report; no second death.
    - nights (--nights K, default 0 here): dusk (13000) to dawn (23500) with wooden tools, mob spawning on, difficulty as the run;
      the stand-in dispatches fight_melee on a hostile within 10 when idle (if the body advertises it), else reflexes alone.
      Pass = alive at dawn."""
    p = r.player
    shelter = (-12.5, FLOOR, -12.5)
    saved_spawn = r.rcon(f"data get entity {p} respawn.pos")
    r.rcon("gamerule keep_inventory false")
    r.rcon(f"spawnpoint {p} {int(shelter[0] - 0.5)} {F} {int(shelter[2] - 0.5)}")
    kit_cmds = [f"item replace entity {p} armor.chest with minecraft:iron_chestplate", f"item replace entity {p} armor.head with minecraft:iron_helmet",
                f"item replace entity {p} hotbar.0 with minecraft:stone_sword", f"item replace entity {p} hotbar.3 with minecraft:bread 6"]
    KIT = {"minecraft:iron_chestplate": 1, "minecraft:iron_helmet": 1, "minecraft:stone_sword": 1, "minecraft:bread": 6}

    def die_at(place, pre=(), during=()):
        park(r)
        for c in pre:
            r.rcon(c)
        r.tp(*place)
        heal_full(r)
        r.rcon(f"clear {p}")
        for c in kit_cmds:
            r.rcon(c)
        r.act("select_hotbar", {"slot": 0})
        time.sleep(0.5)
        since = mark(r)
        for c in during:
            r.rcon(c)
        r.rcon(f"kill {p}")
        t0, dead, back = time.time(), None, None
        while time.time() < t0 + 12 and not back:
            dead = dead or (body_events(r, since, "death") or [None])[0]
            back = (body_events(r, since, "respawn") or [None])[0]
            time.sleep(0.2)
        return since, t0, dead, back

    def loss_report():
        return {k: KIT[k] - count(r, k) for k in KIT if count(r, k) < KIT[k]}

    def walk(goal, timeout=60, tol=1.0):
        since = mark(r)
        aid = request(r, "walk_to", {"pos": {"x": goal[0], "y": goal[1], "z": goal[2]}, "tolerance": tol})
        return result(r, aid, timeout, since)

    def collect(dp, timeout=60):
        """The drops scatter up to ~5 blocks: walk to the death pos, then to the nearest item the body's state lists within 8 of the death
        pos (at most 6 walks; attempt 56: "within 8 of her" missed one 8.1 away after the previous pickup)."""
        res, walks = walk((dp["x"], FLOOR, dp["z"]), timeout), 1
        while res and res.get("status") == "ok" and walks < 7:
            items = [e for e in (r.state() or {}).get("nearby_entities", []) if e.get("type") == "minecraft:item"
                     and math.hypot(e["pos"]["x"] - dp["x"], e["pos"]["z"] - dp["z"]) <= 8]
            if not items:
                break
            e = items[0]["pos"]
            walk((e["x"], e["y"], e["z"]), 20, tol=0.5)
            walks += 1
        return res, walks

    try:
        for i in range(5):  # recoverable drops
            since, t0, dead, back = die_at((8.5, FLOOR, 8.5, 0))
            age0 = nums(r.rcon("data get entity @e[type=minecraft:item,limit=1,sort=nearest,x=8,y=%d,z=8] Age" % F))
            dp = dead and dead.get("pos")
            res, walks = collect(dp) if dp else (None, 0)
            time.sleep(1.0)
            t_rec = time.time() - t0
            got = {k: count(r, k) for k in KIT}
            e1 = r.act("equip_best", {})[2]
            e2 = r.act("equip_best", {"weapon": True, "slots": []})[2]
            worn = has_item(r, "armor.chest", "minecraft:iron_chestplate") and has_item(r, "armor.head", "minecraft:iron_helmet")
            home = walk(shelter)
            deaths = len(body_events(r, since, "death"))
            ok = (bool(dead) and dead.get("cause") is not None and bool(back) and bool(res) and res.get("status") == "ok"
                  and got == KIT and worn and bool(home) and home.get("status") == "ok" and deaths == 1)
            r.record(f"recover_drops#{i}", "cp31_components", {}, ok,
                     f"cause={dead and dead.get('cause')} death_pos={dp} respawn={back and back.get('pos')} walk={res and res.get('status')} walks={walks} "
                     f"recovered={got} worn={worn} equip={e1 and e1.get('status')}/{e2 and e2.get('status')} home={home and home.get('status')} "
                     f"deaths={deaths} recovery_s={t_rec:.1f} drop_age_at_respawn={age0[:1]} (despawn 6000)",
                     invalid=not (ran(res) and ran(home)))
        box = [f"fill 7 {F - 1} 7 9 {F + 2} 9 minecraft:glass hollow"]
        for i in range(2):  # inaccessible: inside a sealed glass box
            since, t0, dead, back = die_at((8.5, FLOOR, 8.5, 0), pre=box)
            dp = dead and dead.get("pos")
            res = walk((dp["x"], FLOOR, dp["z"]), timeout=70) if dp else None
            time.sleep(1.0)
            lost = loss_report()
            deaths = len(body_events(r, since, "death"))
            ok = bool(dead) and bool(res) and res.get("status") == "failed" and res.get("code") in ("unreachable", "stuck") and bool(lost) and deaths == 1
            r.record(f"drops_inaccessible#{i}", "cp31_components", {}, ok, f"walk={res and (res.get('status'), res.get('code'))} loss_report={lost} deaths={deaths}",
                     invalid=not ran(res))
            park(r)
            r.rcon(f"fill 7 {F} 7 9 {F + 2} 9 minecraft:air")
            r.rcon(f"fill 7 {F - 1} 7 9 {F - 1} 9 minecraft:smooth_stone")
            r.rcon(f"kill @e[type=minecraft:item,{near()}]")
        lava = [f"fill 0 {F - 2} -2 4 {F - 2} 2 minecraft:stone"]
        for i in range(2):  # destroyed: killed in lava, the drops burn
            since, t0, dead, back = die_at((2.5, FLOOR, 0.5, 0), pre=lava, during=[f"fill 0 {F - 1} -2 4 {F - 1} 2 minecraft:lava"])
            time.sleep(2.0)
            items = r.rcon(f"execute if entity @e[type=minecraft:item,x=0,y={F - 2},z=-2,dx=4,dy=3,dz=4]")
            lost = loss_report()
            deaths = len(body_events(r, since, "death"))
            ok = bool(dead) and "passed" not in items and lost == KIT and deaths == 1
            r.record(f"drops_destroyed#{i}", "cp31_components", {}, ok, f"items_left={'yes' if 'passed' in items else 'none'} loss_report={lost} deaths={deaths}")
            park(r)
            r.rcon(f"fill 0 {F - 1} -2 4 {F - 1} 2 minecraft:smooth_stone")
            r.rcon(f"fill 0 {F - 2} -2 4 {F - 2} 2 minecraft:air")
        nights = int(sys.argv[sys.argv.index("--nights") + 1]) if "--nights" in sys.argv else 0
        for i in range(nights):
            park(r)
            r.tp(0.5, FLOOR, 0.5, 0)
            heal_full(r)
            r.rcon(f"clear {p}")
            for c in (f"item replace entity {p} hotbar.0 with minecraft:wooden_sword", f"item replace entity {p} hotbar.1 with minecraft:wooden_pickaxe",
                      f"item replace entity {p} hotbar.2 with minecraft:wooden_axe", f"item replace entity {p} hotbar.3 with minecraft:bread 8"):
                r.rcon(c)
            r.act("select_hotbar", {"slot": 0})
            r.rcon("gamerule spawn_mobs true")
            r.rcon("gamerule natural_health_regeneration true")
            r.rcon("difficulty peaceful")  # I20: 26.3 recomputes the spawn flags only on a real difficulty change
            r.rcon(f"difficulty {os.environ.get('EVE_DIFFICULTY', 'easy')}")
            r.rcon("time set 13000")
            since, t0, fights, alive, melee, dawn, seen, hp_min = mark(r), time.time(), 0, True, True, False, 0, 20.0
            probe = None
            while time.time() < t0 + 700:
                tday = nums(r.rcon("time query day"))  # 26.3: "Timeline minecraft:day is at N tick(s)" (no "daytime" any more)
                if tday and (tday[0] >= 23500 or tday[0] < 12000):  # dawn, or already wrapped past it
                    dawn = True
                    break
                if probe is None and tday and tday[0] >= 18000:  # I20/O66: the raw midnight count, once a night
                    probe = {t: r.rcon(f"execute if entity @e[type=minecraft:{t}]") for t in ("zombie", "skeleton", "creeper", "spider")}
                if body_events(r, since, "death"):
                    alive = False
                    break
                st = r.state() or {}
                # a night with no hostile near her proves only the loop (dev attempt 57: 0 within 32 all night): recorded, not hidden
                seen = max(seen, sum(1 for e in st.get("nearby_entities", []) if e.get("hostile")))
                hp_min = min(hp_min, st.get("health", 20.0))
                threats = [t for t in st.get("threats", []) if t.get("kind") in ("melee", "ranged") and t.get("danger", 0) >= 0.35]
                if melee and threats and not st.get("control", {}).get("active_action_id"):
                    m = mark(r)
                    res = result(r, request(r, "fight_melee", {"entity_id": threats[0]["entity_id"]}), 40, m)
                    fights += 1
                    melee = ran(res)  # refused (no provider) -> reflexes alone for the rest of the night
                time.sleep(1.0)
            r.rcon("gamerule spawn_mobs false")
            r.rcon(f"kill @e[type=!minecraft:player,{near()}]")
            world = sum(int(m.group(1)) for v in (probe or {}).values() for m in [re.search(r"count:\s*(\d+)", v, re.I)] if m)
            r.record(f"night#{i}", "cp31_components", {}, alive and dawn, f"alive={alive} dawn={dawn} hostiles_seen_max={seen} hp_min={hp_min} "
                     f"world_hostiles_midnight={world if probe else None} probe={probe} "
                     f"fights_dispatched={fights} melee_provider={melee} {time.time() - t0:.0f}s "
                     f"deaths={len(body_events(r, since, 'death'))}")
            r.rcon("time set 1000")
    finally:
        r.rcon("gamerule keep_inventory true")  # laneb restores the lane's own value at the end
        import re as _re
        v = _re.findall(r"-?\d+", saved_spawn.split("data:")[-1]) if "has the following" in saved_spawn else None
        if v and len(v) >= 3:
            r.rcon(f"spawnpoint {p} {v[0]} {v[1]} {v[2]}")
        # ponytail: with no respawn point before the suite, hers stays at the arena shelter; clear it by hand if that matters
        r.rcon(f"kill @e[type=minecraft:item,{near()}]")


# ---------------------------------------------------------------- edge sneak

def edge(r):
    pillar = (f"fill 6 {F} 11 8 {F + 5} 13 minecraft:stone", f"fill 6 {F} 11 8 {F + 5} 13 minecraft:air")
    top = FLOOR + 6

    def trial(name, stopped_):
        r.tp(0.5, FLOOR, 0.5, 0)  # off any edge for 1 s, so each trial's sneak is a fresh start
        time.sleep(1.0)
        r.rcon(pillar[0])
        since = mark(r)  # the sneak starts on arrival at the edge, before the push
        r.tp(8.85, top, 12.5, 90)
        heal_full(r)
        time.sleep(0.5)
        if stopped_:
            stop(r)
        r.rcon(f"summon minecraft:cow 8.45 {top} 12.5 {{NoAI:1b,PersistenceRequired:1b,Silent:1b}}")
        t0, min_y = time.time(), 999.0
        while time.time() < t0 + 3:
            min_y = min(min_y, r.pos()[1])
            time.sleep(0.05)
        r.rcon(f"kill @e[type=minecraft:cow,{near()}]")
        if stopped_:
            resume(r)
        ev = reflex_events(r, since, "edge_sneak")
        r.rcon(pillar[1])
        return min_y, ev

    for base in range(3):
        min_y, ev = trial("baseline", True)
        r.record(f"edge_baseline_stopped#{base}", "reflex_edge", {}, min_y < top - 1, f"min_y={min_y:.2f} (pushed off while STOPPED)")
    for i in range(N):
        min_y, ev = trial("edge", False)
        ok = min_y >= top - 0.01 and any(e["phase"] == "start" for e in ev)
        r.record(f"edge#{i}", "reflex_edge", {}, ok, f"min_y={min_y:.2f} top={top} events={[e['phase'] for e in ev]}", events=ev)


# ---------------------------------------------------------------- fall detection

def fall(r):
    for i in range(N + 5):
        neg = i >= N
        h = 3 if neg else 12
        r.tp(0.5 + (i % 5) * 3, FLOOR, 0.5, 0)
        heal_full(r)
        since = mark(r)
        r.tp_drop(0.5 + (i % 5) * 3, FLOOR + h, 0.5)  # O39: a platform, then away
        time.sleep(3.0)
        ev = reflex_events(r, since, "fall_rescue")
        dmg = damage_events(r, since)
        if neg:
            r.record(f"fall_negative_3#{i - N}", "reflex_fall", {}, not ev and not dmg, f"events={ev} damage={dmg}")
        else:
            ok = bool(ev) and ev[0]["predicted_damage"] > 0 and ev[0]["result"] == "no_qualified_item" and bool(dmg) and ev[0]["t"] <= dmg[0]["t"]
            r.record(f"fall_12#{i}", "reflex_fall", {}, ok, f"event={ev and {k: ev[0][k] for k in ('predicted_fall', 'predicted_damage', 'height_above_ground')}} "
                     f"lead_ms={ev and dmg and round((dmg[0]['t'] - ev[0]['t']) * 1000)} damage={dmg and dmg[0]['amount']}", events=ev)
    heal_full(r)


# ---------------------------------------------------------------- water-bucket fall rescue

def rescue_signals(r, since_s):
    """eve:rescue frames the dev server logged since since_s (None where that log does not exist, e.g. lane B)."""
    log = Path(__file__).resolve().parents[1] / "run-server" / "evetest-log.jsonl"
    if hasattr(r, "lane") or not log.exists():
        return None
    out = []
    for line in log.read_text(encoding="utf-8").splitlines():
        o = json.loads(line)
        if o.get("type") == "rescue" and o.get("ts", 0) / 1000 >= since_s:
            out.append(o)
    return out[-3:] if out else []

def fall_bucket(r):
    p = r.player
    heights = [8, 12, 16, 24, 32]
    for base in range(3):  # the same fall without a bucket must hurt her, or the heights are void
        x = 0.5 + base * 3
        r.tp(x, FLOOR, 0.5, 0)
        heal_full(r)
        h0 = health(r)
        r.tp_drop(x, FLOOR + 12, 0.5)
        time.sleep(3.0)
        r.record(f"fall_bucket_baseline_no_bucket#{base}", "reflex_fall_bucket", {"height": 12}, health(r) < h0, f"hp {h0}->{health(r)}")
    # --plan o20 (lane B, sense's ask): 12 x 32 blocks over home (task_only), 4 x 32 outside every zone (a pad at x 40), 4 mixed in
    # the free arena. Default: odd trials over home on a real lane, heights cycling 8..32.
    plan = sys.argv[sys.argv.index("--plan") + 1] if "--plan" in sys.argv else None
    if plan == "o20":
        trials = [(32, 0.5 + (i % 5) * 3, -19.5) for i in range(12)] + [(32, 40.5, 0.5)] * 4 + [(h, 0.5 + k * 3, 0.5) for k, h in enumerate([8, 16, 24, 32])]
        r.rcon(f"fill 38 {F - 1} -2 42 {F - 1} 2 minecraft:smooth_stone")  # the outside-every-zone pad (x 38..42 is beyond reflex_arena and home)
        r.rcon(f"fill 38 {F} -2 42 {F + 40} 2 minecraft:air")
    else:
        trials = [(heights[i % len(heights)], 0.5 + (i % 5) * 3, -19.5 if hasattr(r, "lane") and i % 2 else 0.5) for i in range(N)]
    for i, (h, x, z) in enumerate(trials):
        r.tp(x, FLOOR, z, 0)
        r.rcon(f"item replace entity {p} hotbar.5 with minecraft:water_bucket")
        heal_full(r)
        since, h0 = mark(r), health(r)
        t0 = time.time()
        r.tp_drop(x, FLOOR + h, z)
        landed, min_y = None, 999.0
        while time.time() < t0 + 6:
            y = r.pos()[1]
            min_y = min(min_y, y)
            if landed is None and y <= FLOOR + 0.01:
                landed = time.time()
            if landed and time.time() > landed + 2.5:
                break
            time.sleep(0.05)
        h1 = health(r)
        water_left = r.rcon(f"execute if block {int(x // 1)} {F} {int(z // 1)} minecraft:water") == "Test passed"
        buckets, empty = count(r, "minecraft:water_bucket"), count(r, "minecraft:bucket")
        sig = rescue_signals(r, t0)
        ev = reflex_events(r, since, "fall_rescue")
        ends = [e for e in ev if e["phase"] == "end"]
        checks = {"no_damage": h1 >= h0 and not damage_events(r, since), "landed": landed is not None,
                  "bucket_back": buckets == 1 and empty == 0, "no_water_left": not water_left,
                  "event_rescued": bool(ends) and ends[0].get("result") == "rescued"}
        if sig is not None:  # a server that registered eve:rescue (the dev sender, sense): place before the water, scoop, end
            ops = [x["client"]["op"] for x in sig]
            checks["signal_place_scoop_end"] = ops == ["rescue_place", "rescue_scoop", "rescue_end"] and "air" in sig[0].get("block_now", "")
        where = "_home" if z < -16 else "_nozone" if x > 30 else ""
        r.record(f"fall_bucket_{h}{where}#{i}", "reflex_fall_bucket", {"height": h, "z": z}, all(checks.values()),
                 f"hp {h0}->{h1} end={ends and (ends[0].get('result'), ends[0].get('detail'))} water_bucket={buckets} bucket={empty} "
                 f"water_left={water_left} " + " ".join(k for k, v in checks.items() if not v), events=ev)
        r.rcon(f"fill {int(x // 1) - 2} {F} {int(z // 1) - 2} {int(x // 1) + 2} {F + 1} {int(z // 1) + 2} minecraft:air")
        r.rcon(f"clear {p} minecraft:water_bucket")
        r.rcon(f"clear {p} minecraft:bucket")
    if plan == "o20":
        r.rcon(f"fill 38 {F - 1} -2 42 {F - 1} 2 minecraft:air")
    heal_full(r)


# ---------------------------------------------------------------- creeper (CP-29)

def creeper(r):
    p = r.player
    griefing = r.rcon("gamerule mob_griefing").rsplit(" ", 1)[-1].strip()
    r.rcon("gamerule mob_griefing false")  # the blast must not eat the arena floor (and change the next trial)

    def creeper_pos():
        v = nums(r.rcon(f"data get entity @e[type=minecraft:creeper,tag=cr,limit=1] Pos"))
        return v if len(v) == 3 else None

    def trial(name, start, angle, dist, during=None, shield=False, pen=None, stopped_=False):
        r.tp(*start)
        r.rcon(f"item replace entity {p} weapon.offhand with {'minecraft:shield' if shield else 'minecraft:air'}")
        if pen:
            r.rcon(pen[0])
        heal_full(r)
        aid, since = None, mark(r)  # before the request: a quick rejection must not fall before the mark
        if during == "walk":
            aid = request(r, "walk_to", {"pos": {"x": start[0] + 18 * math.cos(math.radians(angle)), "y": FLOOR, "z": start[2] + 18 * math.sin(math.radians(angle))}, "tolerance": 1.0})
            time.sleep(0.8)
        elif during == "bow":  # O53i: a bow drawn by an action at the moment the creeper reflex pre-empts
            r.rcon(f"item replace entity {p} hotbar.0 with minecraft:stone_sword")
            r.rcon(f"item replace entity {p} hotbar.2 with minecraft:bow")
            r.rcon(f"item replace entity {p} hotbar.3 with minecraft:arrow 16")
            r.act("select_hotbar", {"slot": 2})
            aid = request(r, "use_item", {"hold_ms": 4990 - 10 * angle})
            time.sleep(0.6)
        elif during == "mine":
            r.rcon(f"item replace entity {p} hotbar.1 with minecraft:wooden_pickaxe")  # attempt 22: no pickaxe -> no_tool, never mined
            r.rcon(f"fill {int(start[0]) - 1} {F} {int(start[2]) + 2} {int(start[0]) - 1} {F + 1} {int(start[2]) + 2} minecraft:deepslate")
            time.sleep(0.3)  # a130 #16: after a finished mine the block is new; asked at once, her client still saw air (wrong_block in 0.02 s)
            aid = request(r, "mine_block", {"pos": {"x": int(start[0]) - 1, "y": F, "z": int(start[2]) + 2}})
            time.sleep(0.5)
        if stopped_:
            stop(r)
        h0 = health(r)
        me = r.pos()
        a = math.radians(angle)
        cx, cz = me[0] + dist * math.cos(a), me[2] + dist * math.sin(a)
        r.rcon(f"summon minecraft:creeper {cx:.2f} {F} {cz:.2f} {{PersistenceRequired:1b,Tags:[\"cr\"]}}")
        t0, last_d, min_d, exploded = time.time(), None, 99.0, False
        while time.time() < t0 + 10:
            c, e = creeper_pos(), r.pos()
            if c is None:
                exploded = True  # gone without our kill: it blew up
                break
            last_d = math.dist(c, e)
            min_d = min(min_d, last_d)
            time.sleep(0.1)
        time.sleep(0.5)
        h1 = health(r)
        r.rcon(f"kill @e[type=minecraft:creeper,{near()}]")
        if stopped_:
            resume(r)
        res = result(r, aid, 15, since) if aid else None
        if pen:
            r.rcon(pen[1])
        ev = reflex_events(r, since, "creeper_flee")
        return {"h0": h0, "h1": h1, "exploded": exploded, "last_d": last_d, "min_d": min_d, "events": ev, "action": res,
                "all_events": reflex_events(r, since)}

    # O221/O283: defensive reflexes outrank STOPPED, so this is no longer "she is hurt": stopped she has no task, the creeper_flee
    # reflex still moves her, no blast and no damage
    only = os.environ.get("CREEPER_ONLY")  # kickoff (fb07484c pin): one plan case, e.g. "mine:180", N times; nothing else
    for base in range(0 if only else 3):
        o = trial("baseline", (0.5, FLOOR, 0.5, 0), 120 * base, 4, stopped_=True)
        moved = any(e["phase"] == "start" and not e.get("action_paused") for e in o["events"])
        r.record(f"creeper_baseline_stopped#{base}", "reflex_creeper", {}, moved and not o["exploded"] and o["h1"] >= o["h0"],
                 f"hp {o['h0']}->{o['h1']} exploded={o['exploded']} last_d={o['last_d'] and round(o['last_d'], 2)} flee_without_a_task={moved}")
    plan = [("idle", None, 45 * i) for i in range(8)] + [("walk", "walk", 60 * i) for i in range(6)] + [("mine", "mine", 90 + 30 * i) for i in range(6)]
    if only:
        plan = [x for x in plan if f"{x[0]}:{x[2]}" == only] * N
    for i, (name, during, angle) in enumerate(plan):
        # walk: the creeper appears 6 blocks ahead on her route; mine/idle: 5 blocks away at the angle
        o = trial(name, (0.5, FLOOR, 0.5, 0), angle, 6 if during == "walk" else 5, during=during)
        starts = [e for e in o["events"] if e["phase"] == "start"]
        checks = {"no_damage": o["h1"] >= o["h0"], "event": bool(starts),
                  "safe_at_blast": not o["exploded"] or (o["last_d"] is not None and o["last_d"] >= 7)}
        if during == "mine":
            checks["mine_interrupted"] = bool(o["action"]) and o["action"].get("code") == "interrupted"
            # a130 #15: this creeper stood still at 5 m for 10 s (advance 0, never hissed), so no flee is correct. ponytail: min_d as the
            # sign it never came; only the safety checks count then
            if o["min_d"] >= 4.5:
                checks["event"] = checks["mine_interrupted"] = True
        r.record(f"creeper_{name}#{i}", "reflex_creeper", {"angle": angle}, all(checks.values()),
                 f"hp {o['h0']}->{o['h1']} exploded={o['exploded']} dist_at_end={o['last_d'] and round(o['last_d'], 2)} min_d={round(o['min_d'], 2)} "
                 f"ends={[e.get('result') for e in o['events'] if e['phase'] == 'end']} action={o['action'] and (o['action'].get('status'), o['action'].get('code'))} "
                 + " ".join(k for k, v in checks.items() if not v), events=o["events"], invalid=bool(during) and not ran(o["action"]))
    pen = (f"fill -3 {F - 1} -3 3 {F + 3} 3 minecraft:glass hollow", f"fill -3 {F} -3 3 {F + 3} 3 minecraft:air")
    for i in range(0 if only else 3):  # trapped in a glass pen with the creeper: shield raised = the least-damage response (visible failure, not a pass)
        o = trial("trapped", (0.5, FLOOR, 0.5, 0), 0, 2.5, shield=True, pen=pen)
        r.rcon(f"fill -3 {F - 1} -3 3 {F - 1} 3 minecraft:smooth_stone")
        ends = [e.get("result") for e in o["events"] if e["phase"] == "end"]
        r.record(f"creeper_trapped_shield#{i}", "reflex_creeper_trapped", {}, "cornered_shielded" in ends,
                 f"hp {o['h0']}->{o['h1']} (damage {o['h0'] - o['h1']:.1f}) exploded={o['exploded']} ends={ends}")
    for i in range(0 if only else 5):  # O53i: trapped with the bow drawn: the reflex must free her hands (bow released, sword in hand) and raise the shield
        o = trial("trapped_bow", (0.5, FLOOR, 0.5, 0), 0, 2.5, during="bow", shield=True, pen=pen)
        r.rcon(f"fill -3 {F - 1} -3 3 {F - 1} 3 minecraft:smooth_stone")
        ends = [e.get("result") for e in o["events"] if e["phase"] == "end"]
        # O283, a127: the shield reflex now starts first (a calm creeper in reach, flee waits for primed) and frees the hands, so look at
        # every reflex; the action's pause lets go of the bow before freeHands (O102d), so "released" is not logged, the swap to the sword is
        freed = [e.get("hands_freed") for e in o["all_events"] if e["phase"] == "start" and e.get("hands_freed")]
        ok = "cornered_shielded" in ends and any(f.get("from") == "minecraft:bow" and "sword" in str(f.get("to")) for f in freed)
        r.record(f"creeper_trapped_bow_drawn#{i}", "reflex_creeper_trapped", {}, ok,
                 f"ends={ends} hands_freed={freed} hp {o['h0']}->{o['h1']} exploded={o['exploded']} use_item={o['action'] and (o['action'].get('status'), o['action'].get('code'))}",
                 events=o["events"], invalid=not ran(o["action"]))
    r.rcon(f"item replace entity {p} weapon.offhand with minecraft:air")
    r.rcon(f"gamerule mob_griefing {griefing}")
    heal_full(r)


# ---------------------------------------------------------------- arbiter: pause, resume, interrupt

def arbiter(r):
    p = r.player
    for i in range(10):
        r.tp(-12.5, FLOOR, -10.5 + 2 * i, 270)
        heal_full(r)
        since = mark(r)
        goal = {"x": 11.5, "y": FLOOR, "z": -10.5 + 2 * i}
        drain_to(r, 15)  # just above the threshold, so the last point drops mid-walk
        since = mark(r)
        aid = request(r, "walk_to", {"pos": goal, "tolerance": 1.0})
        time.sleep(1.0)
        drain_to(r)
        res = result(r, aid, 40, since)
        me = r.pos()
        ev = reflex_events(r, since, "eat")
        starts = [e for e in ev if e["phase"] == "start"]
        aid = getattr(aid, "action_id", None) or aid
        ok = bool(starts) and starts[0].get("action_paused") == aid and any(e.get("result") == "ate" for e in ev) and bool(res) \
            and res.get("status") == "ok" and math.dist(me, [goal["x"], goal["y"], goal["z"]]) <= 1.0
        r.record(f"walk_paused_by_eat#{i}", "arbiter", {}, ok, f"eat={[(e['phase'], e.get('result')) for e in ev]} paused={starts and starts[0].get('action_paused') == aid} "
                 f"walk={res and (res.get('status'), res.get('code'), res.get('note'))} srv_dist={math.dist(me, [goal['x'], goal['y'], goal['z']]):.2f}")
    r.rcon(f"item replace entity {p} weapon.offhand with minecraft:shield")
    r.rcon(f"item replace entity {p} hotbar.1 with minecraft:wooden_pickaxe")
    for i in range(5):
        # a different block each trial: the mind refuses the same failed skill+params a 4th time in 10 min (CP-22 retry bound)
        bx = i - 2
        r.tp(bx + 0.5, FLOOR, 0.5, 0)
        r.rcon(f"fill {bx} {F} 2 {bx} {F + 1} 2 minecraft:deepslate")  # 2.25 s with a wooden pickaxe: the arrow arrives mid-dig
        heal_full(r)
        since = mark(r)
        aid = request(r, "mine_block", {"pos": {"x": bx, "y": F, "z": 2}})
        time.sleep(0.7)
        fire_arrow(r, 180)
        res = result(r, aid, 15, since)
        block = r.rcon(f"execute if block {bx} {F} 2 minecraft:deepslate")
        ok = bool(res) and res.get("status") == "failed" and res.get("code") == "interrupted" and block == "Test passed"
        r.record(f"mine_interrupted_by_shield#{i}", "arbiter", {"x": bx}, ok, f"result={res and (res.get('status'), res.get('code'), res.get('note'))} "
                 f"block_intact={block == 'Test passed'} reflexes={[(e['reflex'], e['phase']) for e in reflex_events(r, since)]}")
        r.rcon(f"fill {bx} {F} 2 {bx} {F + 1} 2 minecraft:air")
    r.rcon(f"item replace entity {p} weapon.offhand with minecraft:air")
    r.act("select_hotbar", {"slot": 0})


# ---------------------------------------------------------------- O98: a skeleton at a doorway (CP-31 a16 night 1)
ROOM = "-3 {f1} -3 3 {f4} 3"  # stone-brick shell on the arena floor; interior x -2..2, z -2..2, doorway (0, F..F+1, 3)


def archer_room(r, build):
    if build:
        r.rcon(f"fill {ROOM.format(f1=F - 1, f4=F + 4)} minecraft:stone_bricks hollow")
        r.rcon(f"setblock 0 {F + 4} 0 minecraft:sea_lantern")  # lit: the darkness reflex must not walk her out
        r.rcon(f"fill 0 {F} 3 0 {F + 1} 3 minecraft:air")
    else:
        r.rcon(f"fill {ROOM.format(f1=F, f4=F + 4)} minecraft:air")
        r.rcon(f"fill {ROOM.format(f1=F - 1, f4=F - 1)} minecraft:smooth_stone")


def archer_trial(r, shield, hp, seconds=15.0, stop_on_hit=False):
    """One trial: her kit and HP, at the back of the room facing away; a skeleton (slowness 255: it aims and shoots, never walks) 1.3
    outside the doorway. Returns (h0, health over time [(t, hp)], arrows near at the end, mark, bow, hand, placed). A STOPPED
    baseline ends at its first hit (stop_on_hit; a96: a second arrow killed her at HP 5 between two 0.25 s reads)."""
    p = r.player
    r.rcon(f"kill @e[type=minecraft:skeleton,{near()}]")
    r.rcon(f"kill @e[type=minecraft:arrow,{near()}]")
    for _ in range(40):  # a death in the trial before: the body respawns her itself (a96 cover#0 began during a respawn)
        if health(r) > 0:
            break
        time.sleep(0.5)
    r.tp(1.0, FLOOR, -1.5, 180)
    r.rcon(f"item replace entity {p} weapon.offhand with {'minecraft:shield' if shield else 'minecraft:air'}")
    heal_full(r)
    if hp < 20:
        r.rcon(f"damage {p} {20 - hp} minecraft:generic")
        time.sleep(0.3)
    me = r.pos()
    placed = abs(me[0] - 1.0) < 0.6 and abs(me[2] + 1.5) < 0.6  # server truth: she starts at the back of the room
    hurt_log = Path(__file__).resolve().parents[1] / "run-server" / "evetest-log.jsonl"  # O98j: the testmod's server-side hurt rows
    hurt0 = hurt_log.stat().st_size if hurt_log.exists() else 0
    since, h0 = mark(r), health(r)
    r.rcon(f"summon minecraft:skeleton 0.5 {F} 4.8 {{PersistenceRequired:1b,Silent:1b,Rotation:[180f,0f]}}")
    # a summon with NBT skips finalizeSpawn, so no default bow (dev a95: 0 arrows in 35 trials); the bow makes it re-pick its ranged attack
    r.rcon(f"item replace entity @e[type=minecraft:skeleton,{near()}] weapon.mainhand with minecraft:bow")
    # I37 (O102): the server's own read of its main hand; a trial whose skeleton holds no bow is INVALID (it tested nothing)
    bow = r.rcon(f"execute if items entity @e[type=minecraft:skeleton,{near()},limit=1] weapon.mainhand minecraft:bow").startswith("Test passed")
    # I37: the server's record of its hand, kept in every row (26.3 keeps mob equipment at equipment.mainhand, not HandItems)
    hand = r.rcon(f"data get entity @e[type=minecraft:skeleton,{near()},limit=1] equipment.mainhand")[-120:]
    r.rcon(f"effect give @e[type=minecraft:skeleton,{near()}] minecraft:slowness 60 255 true")
    t0, hs = time.time(), []
    while time.time() < t0 + seconds:
        h = health(r)
        hs.append((round(time.time() - t0, 1), h))
        if h < 2.0 or (stop_on_hit and h < h0):  # a failing trial must not become a death; a baseline needs only one hit
            r.rcon(f"kill @e[type=minecraft:skeleton,{near()}]")
            break
        time.sleep(0.25)
    out = r.rcon(f"execute if entity @e[type=minecraft:arrow,{near()}]")
    arrows = int(nums(out)[0]) if "count" in out.lower() and nums(out) else 0
    r.rcon(f"kill @e[type=minecraft:skeleton,{near()}]")
    r.rcon(f"kill @e[type=minecraft:arrow,{near()}]")
    srv = []  # (server blocking, use_ticks, angle, blocked, taken) per hurt, from the dev server's own log (testmod, dev only)
    if hurt_log.exists():
        with open(hurt_log, "rb") as fh:
            fh.seek(hurt0)
            rows = [json.loads(l) for l in fh.read().decode("utf-8", "replace").splitlines() if l.strip().startswith("{")]
        pre = [x for x in rows if x.get("type") == "player_hurt" and x.get("player") == r.player]
        post = [x for x in rows if x.get("type") == "player_hurt_after" and x.get("player") == r.player]
        srv = [(a.get("blocking"), a.get("use_ticks"), a.get("source_angle_deg"), (post[k] if k < len(post) else {}).get("blocked"),
                (post[k] if k < len(post) else {}).get("taken")) for k, a in enumerate(pre)]
    return h0, hs, arrows, since, bow, hand, placed, srv


def archer_doorway(r):
    """O98 (CP-31 a16 night 1: a skeleton 1.3 outside an open doorway killed her through it. The shield rose on each arrow 3-4 ticks
    before impact but blocks only from its 5th raised tick, and retreat ran 6x into the back wall in the line of fire).
    - archer_shield (N): shield + sword, HP 20, 15 s: pass = health never drops, a shield start with cause drawn_bow, >= 1 arrow
      shot (arrows near at the end). Baseline x3, STOPPED with the same shield and skeleton: she must be hurt.
    - archer_cover (N/2): no shield, HP 5 (under the floor 6: retreat), 15 s: pass = alive (never under 2), a retreat start
      flagged ranged that ends or holds covered_at, and no damage in the last 8 s (hidden from it). Baseline x2, STOPPED at HP 5:
      she must be hurt."""
    r.rcon("time set midnight")
    archer_room(r, True)
    try:
        for base in range(3):
            stop(r)
            h0, hs, arrows, _, bow, hand, placed, srv = archer_trial(r, True, 20, 10.0, stop_on_hit=True)
            resume(r)
            r.record(f"archer_shield_baseline_stopped#{base}", "reflex_shield_archer", {}, min(h for _, h in hs) < h0,
                     f"hp {h0}->{min(h for _, h in hs)} arrows={arrows} bow={bow} placed={placed} HandItems={hand!r} (the skeleton does hit a STOPPED Eve)", invalid=not (bow and placed and (arrows or min(h for _, h in hs) < h0)))
        for i in range(N):
            h0, hs, arrows, since, bow, hand, placed, srv = archer_trial(r, True, 20)
            ev = reflex_events(r, since, "shield")
            drawn = [e for e in ev if e["phase"] == "start" and (e.get("trigger") or {}).get("cause") == "drawn_bow"]
            # O98d diagnostic: each hit's block state from the body's damage event (blocking, use_ticks, source angle off her view)
            dmg = [(d.get("amount"), d.get("cause"), d.get("blocking"), d.get("use_ticks"), d.get("source_angle_deg")) for d in damage_events(r, since)]
            low = min(h for _, h in hs)
            ok = low >= h0 and bool(drawn) and arrows >= 1
            r.record(f"archer_shield#{i}", "reflex_shield_archer", {}, ok,
                     f"hp {h0}->{low} drawn_bow_starts={len(drawn)} shield_starts={sum(e['phase'] == 'start' for e in ev)} arrows={arrows} bow={bow} placed={placed} HandItems={hand!r} hits={dmg} server_hurt={srv}",
                     events=ev, invalid=not (bow and placed and (arrows or min(h for _, h in hs) < h0)))
        for base in range(2):
            stop(r)
            h0, hs, arrows, _, bow, hand, placed, srv = archer_trial(r, False, 5, 10.0, stop_on_hit=True)
            resume(r)
            r.record(f"archer_cover_baseline_stopped#{base}", "reflex_retreat_archer", {}, min(h for _, h in hs) < h0,
                     f"hp {h0}->{min(h for _, h in hs)} arrows={arrows} bow={bow} placed={placed} HandItems={hand!r}", invalid=not (bow and placed and (arrows or min(h for _, h in hs) < h0)))
        for i in range(max(1, N // 2)):
            h0, hs, arrows, since, bow, hand, placed, srv = archer_trial(r, False, 5)
            time.sleep(0.5)  # the skeleton's kill ends the held retreat (`safe`); its end event carries covered_at
            ev = reflex_events(r, since, "retreat")
            ranged = [e for e in ev if e["phase"] == "start" and (e.get("trigger") or {}).get("ranged")]
            covered = [e for e in ev if e["phase"] == "end" and "covered_at" in (e.get("detail") or {})]
            late = [h for t, h in hs if t >= 7.0]
            ok = min(h for _, h in hs) >= 2.0 and bool(ranged) and bool(covered) and bool(late) and min(late) >= max(late)
            me = r.pos()
            r.record(f"archer_cover#{i}", "reflex_retreat_archer", {}, ok,
                     f"hp {h0}->{hs[-1][1]} min={min(h for _, h in hs)} ranged_starts={len(ranged)} covered={bool(covered)} "
                     f"last8s={late[:1]}..{late[-1:]} at=({me[0]:.1f},{me[2]:.1f}) arrows={arrows} bow={bow} placed={placed} HandItems={hand!r} ends={[e['result'] for e in ev if e['phase'] == 'end']}",
                     events=ev, invalid=not (bow and placed))
    finally:
        r.rcon(f"kill @e[type=minecraft:skeleton,{near()}]")
        archer_room(r, False)
        r.rcon(f"item replace entity {r.player} weapon.offhand with minecraft:air")
        r.rcon("time set noon")


YIELD = "--seal-yield" in sys.argv  # O98o (2): judge the shield's yield to the seal (see seal_under_reflex)
OUT = float(sys.argv[sys.argv.index("--seal-out") + 1]) if "--seal-out" in sys.argv else 2.5  # skeleton past the door centre


def seal_under_reflex(r):
    """O98o (CP-31 a22: a drawn_bow shield came 300 ms after the first seal place was accepted; the dirt had landed and the place
    still ended `interrupted`, so the mind counted the seal 1/2 and opened one cell at dawn). Run as CP-10 (place_block), N trials:
    the a16 room, Eve 2.5 inside the open doorway facing it (shield off hand, dirt 4), a skeleton with a bow 2.5 outside, 5.0 from her, past the shield's 4.5 hold (slowness
    255: it aims and shoots, never walks); 1.0-1.35 s after a shield end (the next draw ~1.5 s after it; attempt 24 on) the fixture seals as the mind
    does: place dirt at the feet cell, then the head cell, the next sent as soon as the last result is in; a place that ended
    `interrupted` with nothing placed is re-sent (up to 3 per cell, the mind's re-plan). 15 s cap.
    - pass = both doorway cells dirt (server), exactly 2 ok results, and no result that contradicts its own evidence (failed but
      block_after is the dirt and item_count_delta -1: the a22 defect).
    - valid only if the skeleton held a bow (I37), she started in place, and a drawn_bow shield start paused one of the trial's
      places (action_paused); else INVALID (it tested nothing).
    - frozen bar: every valid trial passes; >= 15 of N valid; the fix's path (a paused place that resumed and ended ok) in >= 3.
    --seal-yield (O98o (2), docs/O98o-seal-over-shield.md; bars frozen before its first run): the drawn_bow shield yields to the
    seal. --seal-out D parks the skeleton D outside the door centre (1.3: 3.8 from her, inside the shield's 4.5 hold; default 2.5).
    No phase lock: the first place goes 0.3-1.7 s after the summon (walked), so some arrive under a raised shield, some before it.
    - pass = both cells dirt (server), exactly 2 ok results, no contradiction, no drawn_bow shield start that paused a place in its
      first 1.5 s, and both cells placed within 1.5 s of the first request (first request to the second ok result, conservative).
    - valid only if the skeleton held a bow (I37), she started in place, and the shield yielded to the seal at least once in the
      trial (an event phase yield_seal, or a shield end with result yield_seal); else INVALID (no draw during the seal).
    - frozen bar: >= 19 of the valid trials pass; >= 15 of N valid. Projectile shield starts are recorded and allowed."""
    r.rcon("time set midnight")
    archer_room(r, True)
    p = r.player
    cells = [(0, F, 3), (0, F + 1, 3)]
    try:
        for i in range(N):
            r.rcon(f"kill @e[type=minecraft:skeleton,{near()}]")
            r.rcon(f"kill @e[type=minecraft:arrow,{near()}]")
            r.rcon(f"fill 0 {F} 3 0 {F + 1} 3 minecraft:air")
            r.rcon(f"item replace entity {p} weapon.offhand with minecraft:shield")
            r.rcon(f"item replace entity {p} hotbar.4 with minecraft:dirt 4")
            r.tp(0.5, FLOOR, 1.0, 0)
            heal_full(r)
            me = r.pos()
            placed = abs(me[0] - 0.5) < 0.6 and abs(me[2] - 1.0) < 0.6
            since = mark(r)
            # 6.0 = 2.5 outside the door centre, 5.0 from her: past the shield's hold radius (MELEE_RADIUS + 1 = 4.5), so the
            # drawn_bow shield ends between draws (attempt 22 at 4.8: 3.8 away held it for good and no place ever ran)
            r.rcon(f"summon minecraft:skeleton 0.5 {F} {3.5 + OUT:.1f} {{PersistenceRequired:1b,Silent:1b,Rotation:[180f,0f]}}")
            r.rcon(f"item replace entity @e[type=minecraft:skeleton,{near()}] weapon.mainhand with minecraft:bow")
            bow = r.rcon(f"execute if items entity @e[type=minecraft:skeleton,{near()},limit=1] weapon.mainhand minecraft:bow").startswith("Test passed")
            hand = r.rcon(f"data get entity @e[type=minecraft:skeleton,{near()},limit=1] equipment.mainhand")[-120:]
            r.rcon(f"effect give @e[type=minecraft:skeleton,{near()}] minecraft:slowness 60 255 true")
            # phase lock (attempt 23: 6 valid, the resumed path once - most first places arrived under a shield already up):
            # a skeleton (normal) re-draws 40 ticks after its shot and the shield ends 10 quiet ticks after it, so the next draw
            # starts ~1.5 s after a shield end; sent 1.0-1.35 s after one, the place's click lands just before the draw
            if YIELD:
                d = 0.3 + 0.2 * (i % 8)
            else:
                seen = time.time() + 8.0
                while time.time() < seen and not reflex_events(r, since, "shield", "end"):
                    time.sleep(0.05)
                d = 1.0 + 0.05 * (i % 8)
            time.sleep(d)
            t0, results, lows, sent = time.time(), [], [], {}
            for x, y, z in cells:
                for _ in range(3):
                    if time.time() > t0 + 15 or health(r) < 6:
                        break
                    tq = time.time()
                    aid = request(r, "place_block", {"pos": {"x": x, "y": y, "z": z}, "item": "minecraft:dirt"})
                    sent[aid] = tq
                    res = result(r, aid, 10, since) or {}
                    results.append((aid, y, res))
                    if res.get("status") == "ok" or (res.get("evidence") or {}).get("block_after") == "minecraft:dirt":
                        break
                lows.append(health(r))
            seal_s = round(time.time() - t0, 2)
            r.rcon(f"kill @e[type=minecraft:skeleton,{near()}]")
            both = all(r.rcon(f"execute if block {x} {y} {z} minecraft:dirt") == "Test passed" for x, y, z in cells)
            ev = reflex_events(r, since, "shield", "start")
            paused = {e.get("action_paused") for e in ev if (e.get("trigger") or {}).get("cause") == "drawn_bow"} - {None}
            oks = [a for a, _, res in results if res.get("status") == "ok"]
            liar = [(y, res.get("code")) for _, y, res in results if res.get("status") != "ok"
                    and (res.get("evidence") or {}).get("block_after") == "minecraft:dirt" and (res.get("evidence") or {}).get("item_count_delta") == -1]
            resumed_ok = [a for a in oks if a in paused]
            hit = paused & {a for a, _, _ in results}
            ok = both and len(oks) == 2 and not liar
            # O98o (2): drawn_bow starts that paused a place in its first 1.5 s (must be none), and the yields themselves
            young = [e.get("action_paused") for e in ev if (e.get("trigger") or {}).get("cause") == "drawn_bow"
                     and e.get("action_paused") in sent and e["t"] - sent[e["action_paused"]] < 1.5]
            yields = reflex_events(r, since, "shield", "yield_seal") + [e for e in reflex_events(r, since, "shield", "end") if e.get("result") == "yield_seal"]
            if YIELD:
                ok = ok and not young and seal_s <= 1.5
            r.record(f"seal_under_reflex#{i}", "place_block", {"cells": cells, "out": OUT, "yield_mode": YIELD}, ok,
                     f"both_dirt={both} ok_results={len(oks)}/2 contradictions={liar} paused_places={len(hit)} resumed_ok={len(resumed_ok)} "
                     f"young_drawn_bow_pauses={len(young)} yields={len(yields)} held_ms={[e.get('held_ms') for e in yields if 'held_ms' in e]} seal_s={seal_s} "
                     f"results={[(y, res.get('status'), res.get('code'), (res.get('evidence') or {}).get('block_after')) for _, y, res in results]} "
                     f"hp_min={min(lows, default=None)} delay={d} out={OUT} bow={bow} placed={placed} HandItems={hand!r}",
                     events=ev, resumed_ok=len(resumed_ok), yields=yields,
                     invalid=not (bow and placed and (yields if YIELD else hit)))
    finally:
        r.rcon(f"kill @e[type=minecraft:skeleton,{near()}]")
        r.rcon(f"fill 0 {F} 3 0 {F + 1} 3 minecraft:air")
        archer_room(r, False)
        r.rcon(f"item replace entity {p} weapon.offhand with minecraft:air")
        r.rcon(f"clear {p} minecraft:dirt")
        r.rcon("time set noon")


def c_rush_replay(r):
    """O102d (lane F cp27c c_rush #6, 05:23: a projectile shield over a guarded fight held 19.5 s on the zombie in reach and never
    blocked; she died at 75 s). Combat's c_rush on the dev rig, n=N: K3 (iron sword hotbar 0, iron armour, bread 16, bow hotbar
    1, 32 arrows) plus an off-hand shield; she stands at the arena centre facing +x; a skeleton with its bow (I37) 16 out and two
    zombies 10 out (z +-3), all AI on, at midnight; fight_ranged is called again and again (radius 24, max_arrows 16) until the
    three are gone, as combat's harness does (combat's provider jar in the dev client's run/mods). 110 s cap.
    - pass = no death; the three gone within 110 s; no shield reflex of 20+ ticks that never blocked (blocking_from_tick -1:
      c_rush #6's 391); no projectile-raised shield longer than 3 s (c_rush #6: 19.5 s while the fight waited).
    - valid only if the skeleton held its bow (I37), she started in place, and a shield reflex paused one of the trial's fight
      actions (the c_rush #6 shape); else INVALID.
    - frozen bar (before its first run): 0 deaths; >= 9 of the valid trials pass; >= 5 of N valid. Fix (3) (the floor hand-back)
      is not in this jar: a trial that reaches the floor is recorded, and a death there still fails it."""
    p = r.player
    sel = f"@e[tag=everush,{near()}]"
    try:
        r.rcon("time set midnight")
        for i in range(N):
            for _ in range(40):  # a death in the trial before: the body respawns her itself
                if health(r) > 0:
                    break
                time.sleep(0.5)
            r.rcon(f"kill {sel}")
            r.rcon(f"kill @e[type=minecraft:arrow,{near()}]")
            r.rcon(f"clear {p}")
            for slot, item in (("hotbar.0", "iron_sword"), ("armor.head", "iron_helmet"), ("armor.chest", "iron_chestplate"),
                               ("armor.legs", "iron_leggings"), ("armor.feet", "iron_boots"), ("hotbar.8", "bread 16"),
                               ("hotbar.1", "bow"), ("inventory.0", "arrow 32"), ("weapon.offhand", "shield")):
                r.rcon(f"item replace entity {p} {slot} with minecraft:{item}")
            r.tp(0.5, FLOOR, 0.5, -90)
            heal_full(r)
            me = r.pos()
            placed = abs(me[0] - 0.5) < 0.6 and abs(me[2] - 0.5) < 0.6
            since = mark(r)
            for kind, x, z in (("skeleton", 16.5, 0.5), ("zombie", 10.5, -2.5), ("zombie", 10.5, 3.5)):
                r.rcon(f'summon minecraft:{kind} {x} {F} {z} {{Tags:["everush"],PersistenceRequired:1b,CanPickUpLoot:0b}}')
            r.rcon(f"item replace entity @e[tag=everush,type=minecraft:skeleton,{near()}] weapon.mainhand with minecraft:bow")
            bow = r.rcon(f"execute if items entity @e[tag=everush,type=minecraft:skeleton,{near()},limit=1] weapon.mainhand minecraft:bow").startswith("Test passed")
            hand = r.rcon(f"data get entity @e[tag=everush,type=minecraft:skeleton,{near()},limit=1] equipment.mainhand")[-120:]
            t0, calls, hs, dead = time.time(), [], [], False
            while time.time() < t0 + 110 and len(calls) < 8:
                left = int(nums(r.rcon(f"execute if entity {sel}"))[0]) if "count" in r.rcon(f"execute if entity {sel}").lower() else 0
                if calls and left == 0:
                    break
                aid = request(r, "fight_ranged", {"radius": 24 + i / 100 + len(calls) / 1000, "max_arrows": 16})
                res = result(r, aid, max(5, int(t0 + 110 - time.time())) + 5, since) or {}
                calls.append((aid, res.get("status"), res.get("code"), (res.get("evidence") or {}).get("kills")))
                h = health(r)
                hs.append(h)
                if h <= 0:
                    dead = True
                    break
                if res.get("status") not in ("ok", "failed"):
                    break
            el = round(time.time() - t0, 1)
            gone = "count" not in r.rcon(f"execute if entity {sel}").lower() or int(nums(r.rcon(f"execute if entity {sel}"))[0]) == 0
            r.rcon(f"kill {sel}")
            ends = reflex_events(r, since, "shield", "end")
            starts = reflex_events(r, since, "shield", "start")
            fights = {a for a, *_ in calls}
            shaped = [s for s in starts if s.get("action_paused") in fights]
            dry = [(e.get("ticks"), (e.get("detail") or {}).get("presses")) for e in ends
                   if (e.get("ticks") or 0) >= 20 and (e.get("detail") or {}).get("blocking_from_tick", -1) < 0]
            ids = [((s.get("trigger") or {}).get("cause"), s.get("t")) for s in starts]
            long_proj = []
            for s in starts:  # pair each start with the next end (one shield at a time)
                if (s.get("trigger") or {}).get("cause") != "projectile":
                    continue
                e = next((e for e in ends if e.get("t", 0) >= s.get("t", 0)), None)
                if e and (e.get("elapsed_ms") or 0) > 3000:
                    long_proj.append(e.get("elapsed_ms"))
            dmg = damage_events(r, since)
            # O102ab: the longest single pause of a fight by one reflex (recorded only; c_rush #18 on F: a melee shield held 50 s)
            pauses = []
            for s in reflex_events(r, since, None, "start"):
                if s.get("action_paused") in fights:
                    e = next((e for e in reflex_events(r, since, s.get("reflex"), "end") if e.get("t", 0) >= s.get("t", 0)), None)
                    if e:
                        pauses.append((e.get("elapsed_ms") or 0, s.get("reflex"), (s.get("trigger") or {}).get("cause")))
            longest = max(pauses, default=(0, None, None))
            ok = not dead and gone and not dry and not long_proj
            r.record(f"c_rush_replay#{i}", "reflex_shield_guarded_fight", {}, ok,
                     f"dead={dead} gone={gone} {el}s calls={calls} hp_min={min(hs + [health(r)], default=None)} shields={len(starts)} "
                     f"pausing_a_fight={len(shaped)} longest_fight_pause_ms={longest[0]} by={longest[1]}/{longest[2]} "
                     f"non_blocking_20t={dry} projectile_shields_over_3s={long_proj} "
                     f"ends={[(e.get('result'), e.get('ticks'), (e.get('detail') or {}).get('blocking_from_tick')) for e in ends][:12]} "
                     f"hits={len(dmg)} blocked={sum(1 for d in dmg if d.get('blocking'))} causes={ids[:10]} bow={bow} placed={placed} HandItems={hand!r}",
                     events=starts + ends, invalid=not (bow and placed and shaped))
    finally:
        r.rcon(f"kill {sel}")
        r.rcon(f"kill @e[type=minecraft:arrow,{near()}]")
        r.rcon(f"clear {p}")
        r.rcon("time set noon")


def c_rush_melee(r):
    """O119-b / kickoff 12:53 (Hunter live 12:38: "attack the skeleton" -> fight_melee {entity_id} timed out at its deadline
    "while a reflex held the controls"; the drawn_bow shield fired 7x on that same skeleton, 3.6-13.7 blocks; body 596380e7,
    pre fix A). The owner's exact case on the dev rig, n=N: c_rush_replay's kit (iron sword hotbar 0, iron armour, bread 16,
    bow, 32 arrows, off-hand shield) at the arena centre facing +x; ONE skeleton with its bow (I37) 12 out, AI on, at midnight;
    fight_melee {entity_id: the skeleton's id from state} is called (again while it lives, as the mind would), 90 s cap.
    - pass = no death; the skeleton dead within 90 s; no fight_melee ended timeout; no drawn_bow (or melee) shield started
      while a fight_melee was the action (fix A: only the projectile shield); every reflex pause of a fight <= 1 s.
    - valid only if the skeleton held its bow (I37), she started in place, its id was in state, and it drew on her (a
      projectile shield, a hit, or an arrow within 16 during the trial); else INVALID.
    - frozen bar (before its first run): 0 deaths; >= 9 of the valid trials pass; >= 5 of N valid."""
    p = r.player
    sel = f"@e[tag=everush,{near()}]"
    try:
        r.rcon("time set midnight")
        for i in range(N):
            for _ in range(40):
                if health(r) > 0:
                    break
                time.sleep(0.5)
            r.rcon(f"kill {sel}")
            r.rcon(f"kill @e[type=minecraft:arrow,{near()}]")
            r.rcon(f"clear {p}")
            for slot, item in (("hotbar.0", "iron_sword"), ("armor.head", "iron_helmet"), ("armor.chest", "iron_chestplate"),
                               ("armor.legs", "iron_leggings"), ("armor.feet", "iron_boots"), ("hotbar.8", "bread 16"),
                               ("hotbar.1", "bow"), ("inventory.0", "arrow 32"), ("weapon.offhand", "shield")):
                r.rcon(f"item replace entity {p} {slot} with minecraft:{item}")
            r.tp(0.5, FLOOR, 0.5, -90)
            heal_full(r)
            me = r.pos()
            placed = abs(me[0] - 0.5) < 0.6 and abs(me[2] - 0.5) < 0.6
            since = mark(r)
            r.rcon(f'summon minecraft:skeleton 12.5 {F} 0.5 {{Tags:["everush"],PersistenceRequired:1b,CanPickUpLoot:0b}}')
            r.rcon(f"item replace entity @e[tag=everush,type=minecraft:skeleton,{near()}] weapon.mainhand with minecraft:bow")
            bow = r.rcon(f"execute if items entity @e[tag=everush,type=minecraft:skeleton,{near()},limit=1] weapon.mainhand minecraft:bow").startswith("Test passed")
            hand = r.rcon(f"data get entity @e[tag=everush,type=minecraft:skeleton,{near()},limit=1] equipment.mainhand")[-120:]  # I37 record
            sid = None
            for _ in range(10):
                sid = next((e["id"] for e in (r.state() or {}).get("nearby_entities", []) if e.get("type") == "minecraft:skeleton"), None)
                if sid is not None:
                    break
                time.sleep(0.2)
            t0, calls, dead, arrows = time.time(), [], False, False
            while sid is not None and time.time() < t0 + 90 and len(calls) < 6:
                if "count" not in r.rcon(f"execute if entity {sel}").lower():
                    break
                aid = request(r, "fight_melee", {"entity_id": sid})
                res = result(r, aid, max(5, int(t0 + 90 - time.time())) + 5, since) or {}
                calls.append((aid, res.get("status"), res.get("code")))
                arrows = arrows or "count" in r.rcon(f"execute if entity @e[type=minecraft:arrow,{near()}]").lower()
                if health(r) <= 0:
                    dead = True
                    break
                if res.get("status") not in ("ok", "failed"):
                    break
            el = round(time.time() - t0, 1)
            gone = "count" not in r.rcon(f"execute if entity {sel}").lower()
            r.rcon(f"kill {sel}")
            fights = {a for a, *_ in calls}
            starts = reflex_events(r, since, "shield", "start")
            causes = [((s.get("trigger") or {}).get("cause"), s.get("action_paused") in fights) for s in starts]
            bad_shields = [c for c, over in causes if over and c != "projectile"]
            pauses = []
            for s in reflex_events(r, since, None, "start"):
                if s.get("action_paused") in fights:
                    e = next((e for e in reflex_events(r, since, s.get("reflex"), "end") if e.get("t", 0) >= s.get("t", 0)), None)
                    pauses.append(((e or {}).get("elapsed_ms", 10 ** 6), s.get("reflex"), (s.get("trigger") or {}).get("cause")))
            longest = max(pauses, default=(0, None, None))
            timeouts = [c for c in calls if c[2] == "timeout"]
            dmg = damage_events(r, since)
            drew = arrows or bool(dmg) or any(c == "projectile" for c, _ in causes)
            ok = not dead and gone and not timeouts and not bad_shields and longest[0] <= 1000
            r.record(f"c_rush_melee#{i}", "reflex_shield_guarded_fight", {}, ok,
                     f"dead={dead} gone={gone} {el}s calls={calls} sid={sid} shields={causes[:10]} non_projectile_over_fight={bad_shields} "
                     f"longest_fight_pause_ms={longest[0]} by={longest[1]}/{longest[2]} timeouts={len(timeouts)} hits={len(dmg)} "
                     f"blocked={sum(1 for d in dmg if d.get('blocking'))} drew={drew} bow={bow} placed={placed} HandItems={hand!r}",
                     events=starts, invalid=not (bow and placed and sid is not None and drew))
    finally:
        r.rcon(f"kill {sel}")
        r.rcon(f"kill @e[type=minecraft:arrow,{near()}]")
        r.rcon(f"clear {p}")
        r.rcon("time set noon")


def archer_live(r):
    """S28 (live Eve on 596380e7 died 13:26:56 to one skeleton: drawn_bow shields ended clear between shots, arrows hit with the
    shield down or raised under 5 ticks, then at 4 HP retreat and an arrow's shield pre-empted each other until the last arrow).
    Reflexes alone, as she stood live (no action), n=N: K3 (iron sword hotbar 0, iron armour, bread 16 hotbar 8, bow hotbar 1,
    32 arrows) plus an off-hand shield, HP 20; even trials hold the sword, odd trials the BOW (a use item in the main hand);
    ONE skeleton with its bow (I37) 8-13 out (8 + i % 6), AI on, at midnight; 60 s.
    - pass = alive at 60 s.
    - valid only if the skeleton held its bow (I37), she started in place, and it shot at her (a hit, an arrow within 16, or a
      projectile shield); else INVALID.
    - frozen bar (before its first run): 0 deaths in the valid trials; >= 15 of N valid. Recorded, not judged: hits, blocked,
      shields and their causes, floor_walk, the lowest HP."""
    p = r.player
    sel = f"@e[tag=everush,{near()}]"
    try:
        r.rcon("time set midnight")
        for i in range(N):
            for _ in range(40):
                if health(r) > 0:
                    break
                time.sleep(0.5)
            r.rcon(f"kill {sel}")
            r.rcon(f"kill @e[type=minecraft:arrow,{near()}]")
            r.rcon(f"clear {p}")
            for slot, item in (("hotbar.0", "iron_sword"), ("armor.head", "iron_helmet"), ("armor.chest", "iron_chestplate"),
                               ("armor.legs", "iron_leggings"), ("armor.feet", "iron_boots"), ("hotbar.8", "bread 16"),
                               ("hotbar.1", "bow"), ("inventory.0", "arrow 32"), ("weapon.offhand", "shield")):
                r.rcon(f"item replace entity {p} {slot} with minecraft:{item}")
            m0 = mark(r)
            result(r, request(r, "select_hotbar", {"slot": i % 2}), 5, m0)  # odd trials: the bow in her main hand
            r.tp(0.5, FLOOR, 0.5, -90)
            heal_full(r)
            me = r.pos()
            placed = abs(me[0] - 0.5) < 0.6 and abs(me[2] - 0.5) < 0.6
            dist = 8 + i % 6
            since = mark(r)
            r.rcon(f'summon minecraft:skeleton {dist + 0.5} {F} 0.5 {{Tags:["everush"],PersistenceRequired:1b,CanPickUpLoot:0b}}')
            r.rcon(f"item replace entity @e[tag=everush,type=minecraft:skeleton,{near()}] weapon.mainhand with minecraft:bow")
            bow = r.rcon(f"execute if items entity @e[tag=everush,type=minecraft:skeleton,{near()},limit=1] weapon.mainhand minecraft:bow").startswith("Test passed")
            t0, dead, arrows, hp_min = time.time(), False, False, 20.0
            while time.time() < t0 + 60:
                h = health(r)
                hp_min = min(hp_min, h)
                if h <= 0:
                    dead = True
                    break
                arrows = arrows or "count" in r.rcon(f"execute if entity @e[type=minecraft:arrow,{near()}]").lower()
                time.sleep(0.5)
            el = round(time.time() - t0, 1)
            r.rcon(f"kill {sel}")
            starts = reflex_events(r, since, "shield", "start")
            ends = reflex_events(r, since, "shield", "end")
            causes = [(s.get("trigger") or {}).get("cause") for s in starts]
            walked = any((e.get("detail") or {}).get("floor_walk") for e in ends)
            dmg = damage_events(r, since)
            drew = arrows or bool(dmg) or "projectile" in causes
            r.record(f"archer_live#{i}", "reflex_shield_archer", {}, not dead,
                     f"from={time.strftime('%H:%M:%S', time.localtime(t0))} to={time.strftime('%H:%M:%S')} "  # O174: matched to sense's eve.hit lines
                     f"dead={dead} {el}s dist={dist} hand={'bow' if i % 2 else 'sword'} hp_min={hp_min} hits={len(dmg)} "
                     f"blocked={sum(1 for d in dmg if d.get('blocking'))} shields={len(starts)} causes={causes[:12]} floor_walk={walked} "
                     f"ends={[(e.get('result'), e.get('ticks')) for e in ends][:12]} bow={bow} placed={placed}",
                     events=starts + ends, invalid=not (bow and placed and drew))
    finally:
        r.rcon(f"kill {sel}")
        r.rcon(f"kill @e[type=minecraft:arrow,{near()}]")
        r.rcon(f"clear {p}")
        r.rcon("time set noon")


def build_walk(r):
    """O142 (Hunter live: "come to me" -> unreachable, no qualified route; his rule: she builds to reach him if she must). n=N
    trials, alternating two terrains on the whole arena floor (glass rim at 25): a LEDGE (a stone block 4 high, 3x3, its top the
    goal; nothing to climb) and a GAP (a trench 3 wide and 3 deep across the whole floor - the flat world ends at F - 4 - the goal 6 beyond it; once in, no way up). She holds K1
    plus 32 dirt in the MAIN inventory (inventory.0; the build walk pulls it into the hotbar). Plain walk_to first (expected
    unreachable: no qualified route), then walk_to {build: true} (max_place 32, max_break 32), tolerance 1.0, 90 s.
    - pass = the build walk ends ok within 1.0 of the goal, no damage, blocks_placed <= 32.
    - valid only if the plain walk did NOT arrive (the terrain really needs building) and she started in place; else INVALID.
    - frozen bar (before its first run): >= 9 of the valid trials pass; >= 8 of N valid. Recorded: blocks placed/broken, path."""
    p = r.player
    W = 24
    rim = [f"fill -25 {F} -25 25 {F + 5} -25 minecraft:glass", f"fill -25 {F} 25 25 {F + 5} 25 minecraft:glass",
           f"fill -25 {F} -25 -25 {F + 5} 25 minecraft:glass", f"fill 25 {F} -25 25 {F + 5} 25 minecraft:glass"]
    clear = [f"fill {-W} {F} {-W} {W} {F + 5} {W} minecraft:air", f"fill {-W} {F - 4} {-W} {W} {F - 1} {W} minecraft:smooth_stone"]
    terrains = {
        "ledge": ([f"fill 5 {F} -1 7 {F + 3} 1 minecraft:stone"], (6.5, F + 4, 0.5)),
        "gap": ([f"fill 3 {F - 3} {-W} 5 {F - 1} {W} minecraft:air"], (11.5, FLOOR, 0.5)),
    }

    def walk(goal, extra=None, timeout=90):
        since = mark(r)
        prm = {"pos": {"x": goal[0], "y": goal[1], "z": goal[2]}, "tolerance": 1.0, **(extra or {})}
        return result(r, request(r, "walk_to", prm), timeout, since)

    def lay(cmds):
        for c in cmds:
            out = r.rcon(c)
            if not (out.startswith("Successfully") or out.startswith("No blocks") or out.startswith("Changed") or out.startswith("Could not set")):
                raise RuntimeError(f"fixture command failed: {c!r} -> {out!r}")

    try:
        for i in range(N):
            name = ("ledge", "gap")[i % 2]
            cmds, goal = terrains[name]
            park(r)
            lay(clear + rim + cmds)
            r.rcon(f"clear {p}")
            r.rcon(f"item replace entity {p} hotbar.0 with minecraft:stone_sword")
            r.rcon(f"item replace entity {p} inventory.0 with minecraft:dirt 32")
            r.tp(0.5, FLOOR, 0.5, -90)
            heal_full(r)
            me = r.pos()
            placed = abs(me[0] - 0.5) < 0.6 and abs(me[2] - 0.5) < 0.6
            plain = walk(goal, timeout=45) or {}
            plain_ok = plain.get("status") == "ok"
            r.tp(0.5, FLOOR, 0.5, -90)
            h0 = health(r)
            t0 = time.time()
            res = walk(goal, {"build": True, "max_place": 32, "max_break": 32}) or {}
            ev = res.get("evidence") or {}
            h1 = health(r)
            ok = res.get("status") == "ok" and (ev.get("distance_to_goal") or 99) <= 1.0 and h1 >= h0 and (ev.get("blocks_placed") or 0) <= 32
            r.record(f"build_walk_{name}#{i}", "walk_to", {"build": True}, ok,
                     f"plain={(plain.get('status'), plain.get('code'), plain.get('note'))} build={(res.get('status'), res.get('code'), res.get('note'))} "
                     f"dist={ev.get('distance_to_goal')} placed={ev.get('blocks_placed')} broken={ev.get('blocks_broken')} "
                     f"path={ev.get('path_length')} throwaway={ev.get('throwaway_from_inventory')} hp {h0}->{h1} {time.time() - t0:.0f}s",
                     result=res, invalid=plain_ok or not placed)
    finally:
        park(r)
        lay(clear)
        r.rcon(f"clear {p}")


def s28_low_hp(r):
    """O172 (second live death 15:14:33, a phantom; kickoff's cases for c0d24455). Reflexes alone (no action unless named), K3 +
    off-hand shield, at midnight, n=N rounds of four cases (stop_reflex at HP 20; the rest at HP 5):
    - phantom: a phantom 6 above her. pass = no retreat started against it in 30 s, and a shield raised.
    - calm_creeper: a NoAI (never swelling) creeper 6.5 out and a zombie 2.5 out. pass = no creeper_flee in 20 s.
    - latch: a skeleton (bow) 6 out and a zombie 7 out the other side. pass = at most 3 shield/retreat hand-overs (a start of one
      while the other ran) in 20 s.
    - stop_reflex: HP 20, a skeleton (bow) 8 out; once a shield is up, walk_to 30 out then stop {}. pass = stop ends ok within
      1 s and no shield end within 1 s after it.
    Recorded: deaths, min HP. Valid: she started in place (and, for the skeleton cases, it held its bow).
    - frozen bar (before its first run): every case >= 4 of 5 valid pass; recorded deaths are reported, not judged."""
    p = r.player
    sel = f"@e[tag=eves28,{near()}]"

    def kit(hp20):
        r.rcon(f"kill {sel}")
        r.rcon(f"kill @e[type=minecraft:arrow,{near()}]")
        r.rcon(f"clear {p}")
        for slot, item in (("hotbar.0", "iron_sword"), ("armor.head", "iron_helmet"), ("armor.chest", "iron_chestplate"),
                           ("armor.legs", "iron_leggings"), ("armor.feet", "iron_boots"), ("hotbar.8", "bread 16"),
                           ("hotbar.1", "bow"), ("inventory.0", "arrow 32"), ("weapon.offhand", "shield")):
            r.rcon(f"item replace entity {p} {slot} with minecraft:{item}")
        r.tp(0.5, FLOOR, 0.5, -90)
        heal_full(r)
        if not hp20:
            r.rcon(f"damage {p} 15 minecraft:generic")
            time.sleep(0.5)
        me = r.pos()
        return abs(me[0] - 0.5) < 0.6 and abs(me[2] - 0.5) < 0.6

    def mob(kind, x, y, z, nbt=""):
        r.rcon(f'summon minecraft:{kind} {x} {y} {z} {{Tags:["eves28"],PersistenceRequired:1b,CanPickUpLoot:0b{nbt}}}')
        if kind == "skeleton":
            r.rcon(f"item replace entity @e[tag=eves28,type=minecraft:skeleton,{near()}] weapon.mainhand with minecraft:bow")
            return r.rcon(f"data get entity @e[tag=eves28,type=minecraft:skeleton,{near()},limit=1] equipment.mainhand")[-120:]
        return None

    def watch_for(seconds):
        t0, hp_min, dead = time.time(), 20.0, False
        while time.time() < t0 + seconds:
            h = health(r)
            hp_min = min(hp_min, h)
            if h <= 0:
                dead = True
                break
            time.sleep(0.5)
        return hp_min, dead

    try:
        r.rcon("time set midnight")
        for i in range(N):
            for _ in range(40):
                if health(r) > 0:
                    break
                time.sleep(0.5)
            # phantom
            placed = kit(False)
            since = mark(r)
            mob("phantom", 0.5, F + 6, 0.5)
            hp_min, dead = watch_for(30)
            starts = reflex_events(r, since, None, "start")
            retreats = [s for s in starts if s.get("reflex") == "retreat"]
            shields = [s for s in starts if s.get("reflex") == "shield"]
            r.record(f"s28_phantom#{i}", "reflex_low_hp", {}, not retreats and bool(shields),
                     f"retreats={len(retreats)} shields={len(shields)} hp_min={hp_min} dead={dead} placed={placed}", events=starts, invalid=not placed)
            # calm creeper + zombie in reach
            placed = kit(False)
            since = mark(r)
            mob("creeper", 6.5, F, 0.5, ",NoAI:1b")
            mob("zombie", -2.0, F, 0.5)
            hp_min, dead = watch_for(20)
            starts = reflex_events(r, since, None, "start")
            flees = [s for s in starts if s.get("reflex") == "creeper_flee"]
            r.record(f"s28_calm_creeper#{i}", "reflex_low_hp", {}, not flees,
                     f"creeper_flee={len(flees)} reflexes={[s.get('reflex') for s in starts][:10]} hp_min={hp_min} dead={dead} placed={placed}",
                     events=starts, invalid=not placed)
            # floor latch: shooter one side, walker the other
            placed = kit(False)
            since = mark(r)
            hand = mob("skeleton", 6.5, F, 0.5)
            mob("zombie", -6.5, F, 0.5)
            hp_min, dead = watch_for(20)
            evs = [e for e in reflex_events(r, since, None, None) if e.get("reflex") in ("shield", "retreat")]
            flips = sum(1 for e in evs if e.get("phase") == "end" and str(e.get("result", "")).startswith(("preempted_by_shield", "yield_retreat")))
            r.record(f"s28_latch#{i}", "reflex_low_hp", {}, flips <= 3,
                     f"hand_overs={flips} seq={[(e.get('reflex'), e.get('phase'), e.get('result')) for e in evs][:14]} hp_min={hp_min} dead={dead} HandItems={hand!r}",
                     events=evs, invalid=not (placed and hand and "bow" in hand))
            # stop under a reflex
            placed = kit(True)
            since = mark(r)
            hand = mob("skeleton", 8.5, F, 0.5)
            up = None
            for _ in range(40):
                up = next((s for s in reflex_events(r, since, "shield", "start")), None)
                if up:
                    break
                time.sleep(0.25)
            ok = False
            note = "no shield rose in 10 s"
            if up:
                walk = request(r, "walk_to", {"pos": {"x": -30.5, "y": FLOOR, "z": 0.5}})
                time.sleep(0.3)
                m = mark(r)
                t0 = time.time()
                res = result(r, request(r, "stop", {}), 3, m) or {}
                took = time.time() - t0
                time.sleep(1.0)
                ends_after = [e for e in reflex_events(r, m, "shield", "end") if (e.get("t") or 0) <= t0 + took + 1.0]
                ok = res.get("status") == "ok" and took <= 1.0 and not ends_after
                note = f"stop={res.get('status')}/{res.get('code')} in {took:.2f}s note={res.get('note')} shield_ends_within_1s={len(ends_after)}"
            r.record(f"s28_stop_reflex#{i}", "reflex_low_hp", {}, ok, f"{note} placed={placed} HandItems={hand!r}",
                     invalid=not (placed and hand and "bow" in hand and up))
            r.rcon(f"kill {sel}")
    finally:
        r.rcon(f"kill {sel}")
        r.rcon(f"kill @e[type=minecraft:arrow,{near()}]")
        r.rcon(f"clear {p}")
        r.rcon("time set noon")


def creeper_watch(r):
    """O208 (lane F x_creeper: a calm creeper holding at 6.7-7.0 m re-triggered creeper_flee every 2-3 s at full health). Reflexes
    alone, HP 20, K1 + off-hand shield, at noon, n=N rounds of two cases:
    - calm: a NoAI creeper (never swells, never moves) 6.5 out in sight, 20 s. pass = no creeper_flee start.
    - closing: a creeper with AI 12 out (it walks at her). pass = a creeper_flee starts before it is within 3, and she is alive.
    Valid: she started in place. Frozen bar (before its first run): calm >= 5/5 valid pass; closing >= 4/5 valid pass."""
    p = r.player
    sel = f"@e[tag=evecw,{near()}]"

    def kit():
        r.rcon(f"kill {sel}")
        r.rcon(f"clear {p}")
        r.rcon(f"item replace entity {p} hotbar.0 with minecraft:iron_sword")
        r.rcon(f"item replace entity {p} weapon.offhand with minecraft:shield")
        r.tp(0.5, FLOOR, 0.5, -90)
        heal_full(r)
        me = r.pos()
        return abs(me[0] - 0.5) < 0.6 and abs(me[2] - 0.5) < 0.6

    try:
        r.rcon("time set noon")
        for i in range(N):
            placed = kit()
            since = mark(r)
            r.rcon(f'summon minecraft:creeper 7.0 {F} 0.5 {{Tags:["evecw"],PersistenceRequired:1b,NoAI:1b}}')
            t0 = time.time()
            while time.time() < t0 + 20:
                time.sleep(0.5)
            flees = reflex_events(r, since, "creeper_flee", "start")
            r.record(f"creeper_watch_calm#{i}", "reflex_creeper", {}, not flees,
                     f"creeper_flee={len(flees)} placed={placed}", events=flees, invalid=not placed)
            placed = kit()
            since = mark(r)
            r.rcon(f'summon minecraft:creeper 12.5 {F} 0.5 {{Tags:["evecw"],PersistenceRequired:1b}}')
            t0, first, dead = time.time(), None, False
            while time.time() < t0 + 20:
                flees = reflex_events(r, since, "creeper_flee", "start")
                if flees and first is None:
                    first = (flees[0].get("trigger") or {}).get("distance")
                if health(r) <= 0:
                    dead = True
                    break
                time.sleep(0.25)
            ok = first is not None and first > 3 and not dead
            r.record(f"creeper_watch_closing#{i}", "reflex_creeper", {}, ok,
                     f"first_flee_at={first} dead={dead} placed={placed}", invalid=not placed)
            r.rcon(f"kill {sel}")
    finally:
        r.rcon(f"kill {sel}")
        r.rcon(f"clear {p}")


def stopped_under_attack(r):
    """O218 (third live death, 18:21:05: after an owner stop she took zombie hits standing still, HP 18 -> 7 in 7 s, because
    STOPPED cancelled the reflexes). n=N: K1 (iron sword, iron armour) + off-hand shield, HP 20, at midnight; owner stop, then two
    zombies 3 out either side, 30 s; resume after.
    - pass = alive at 30 s, a shield reflex started while STOPPED, and no action ran (she stays STOPPED: control.stopped true).
    - valid: she started in place and the stop took (state control.stopped true before the zombies).
    - frozen bar (before its first run): 0 deaths; >= 5 of 5 valid pass."""
    p = r.player
    sel = f"@e[tag=evesua,{near()}]"
    try:
        r.rcon("time set midnight")
        for i in range(N):
            for _ in range(40):
                if health(r) > 0:
                    break
                time.sleep(0.5)
            r.rcon(f"kill {sel}")
            r.rcon(f"clear {p}")
            for slot, item in (("hotbar.0", "iron_sword"), ("armor.head", "iron_helmet"), ("armor.chest", "iron_chestplate"),
                               ("armor.legs", "iron_leggings"), ("armor.feet", "iron_boots"), ("weapon.offhand", "shield")):
                r.rcon(f"item replace entity {p} {slot} with minecraft:{item}")
            r.tp(0.5, FLOOR, 0.5, -90)
            heal_full(r)
            me = r.pos()
            placed = abs(me[0] - 0.5) < 0.6 and abs(me[2] - 0.5) < 0.6
            stop(r)
            time.sleep(0.5)
            took = ((r.state() or {}).get("control") or {}).get("stopped") is True
            since = mark(r)
            for x in (3.5, -2.5):
                r.rcon(f'summon minecraft:zombie {x} {F} 0.5 {{Tags:["evesua"],PersistenceRequired:1b,CanPickUpLoot:0b}}')
            t0, hp_min, dead = time.time(), 20.0, False
            while time.time() < t0 + 30:
                h = health(r)
                hp_min = min(hp_min, h)
                if h <= 0:
                    dead = True
                    break
                time.sleep(0.5)
            still = ((r.state() or {}).get("control") or {}).get("stopped") is True
            shields = reflex_events(r, since, "shield", "start")
            kinds = [e.get("reflex") for e in reflex_events(r, since, None, "start")]
            r.record(f"stopped_under_attack#{i}", "reflex_stopped", {}, not dead and bool(shields) and still,
                     f"dead={dead} hp_min={hp_min} shields={len(shields)} reflexes={kinds[:12]} still_stopped={still} placed={placed}",
                     events=shields, invalid=not (placed and took))
            r.rcon(f"kill {sel}")
            resume(r)
            time.sleep(0.5)
    finally:
        r.rcon(f"kill {sel}")
        resume(r)
        r.rcon(f"clear {p}")
        r.rcon("time set noon")


def eat_topup(r):
    """O227 (live 18:44-19:2x: HP sat at 15 for 40+ min at food 17 with bread in the pack). n=N: bread 16 in hotbar 3, HP 15,
    food drained to 16-17, no hostiles, at noon.
    - pass = a bread is eaten within 5 s (bread count down, food up) and health is above 15 within 20 s after (regen resumed).
    - valid: food read 16 or 17 and health 15 before the wait.
    - frozen bar (before its first run): >= 4 of 5 valid pass. (a117 was a fixture fault: setup() turns the regen gamerule off.)"""
    p = r.player
    r.rcon("gamerule natural_health_regeneration true")  # the suite setup turns it off; this case is about regen resuming
    for i in range(N):
        r.tp(0.5, FLOOR, 0.5, 36 * i)
        r.rcon(f"clear {p}")
        r.rcon(f"item replace entity {p} hotbar.3 with minecraft:bread 16")
        heal_full(r)
        t_drain, f0 = drain_to(r, 17)
        r.rcon(f"damage {p} 5 minecraft:generic")
        time.sleep(0.5)
        h0, b0 = health(r), count(r, "minecraft:bread")
        since, t0 = mark(r), time.time()
        ate_at = None
        while time.time() < t0 + 5:
            if count(r, "minecraft:bread") < b0:
                ate_at = round(time.time() - t0, 2)
                break
            time.sleep(0.25)
        h1 = h0
        t1 = time.time()
        while ate_at is not None and time.time() < t1 + 20:
            h1 = health(r)
            if h1 > h0 + 0.5:
                break
            time.sleep(0.5)
        r.record(f"eat_topup#{i}", "reflex_eat", {}, ate_at is not None and h1 > h0 + 0.5,
                 f"food0={f0} hp0={h0} ate_at={ate_at}s food_now={food(r)} hp_after={h1} eats={len(reflex_events(r, since, 'eat', 'start'))}",
                 invalid=not (t_drain and f0 in (16, 17) and 14.5 <= h0 <= 15.5))
    r.rcon("gamerule natural_health_regeneration false")


def _auto_case(r, name, mobs, expect, n_trials):
    """H-autofight rig helper: she walks 40 blocks (walk_to), the mobs spawn near her path 1 s in; reflexes + combat's EngageHook
    (combat's provider jar with API v4 in run/mods). expect: 'fight' (an auto_fight start, the target dead, the walk ends ok),
    'flee' (a creeper_flee start, no auto_fight on the creeper, the walk ends ok), 'none' (no auto_fight start; the walk ends ok)."""
    p = r.player
    sel = f"@e[tag=eveaf,{near()}]"
    goal = (20.5, FLOOR, 0.5)  # a118: the arena floor ends at x 24; she walks -19.5 -> 20.5
    try:
        r.rcon("time set midnight")
        for i in range(n_trials):
            for _ in range(40):
                if health(r) > 0:
                    break
                time.sleep(0.5)
            r.rcon(f"kill {sel}")
            r.rcon(f"clear {p}")
            for slot, item in (("hotbar.0", "iron_sword"), ("armor.head", "iron_helmet"), ("armor.chest", "iron_chestplate"),
                               ("armor.legs", "iron_leggings"), ("armor.feet", "iron_boots"), ("hotbar.8", "bread 16"),
                               ("weapon.offhand", "shield")):
                r.rcon(f"item replace entity {p} {slot} with minecraft:{item}")
            r.tp(-19.5, FLOOR, 0.5, -90)
            heal_full(r)
            me = r.pos()
            placed = abs(me[0] + 19.5) < 0.6 and abs(me[2] - 0.5) < 0.6
            since = mark(r)
            aid = request(r, "walk_to", {"pos": {"x": goal[0], "y": goal[1], "z": goal[2]}, "tolerance": 1.0})
            time.sleep(1.0)
            hands = []
            for kind, dx, dz, nbt in mobs:
                x = r.pos()[0] + dx
                r.rcon(f'summon minecraft:{kind} {x} {F} {dz} {{Tags:["eveaf"],PersistenceRequired:1b,CanPickUpLoot:0b{nbt}}}')
                if kind == "skeleton":
                    r.rcon(f"item replace entity @e[tag=eveaf,type=minecraft:skeleton,{near()}] weapon.mainhand with minecraft:bow")
                    hands.append(r.rcon(f"data get entity @e[tag=eveaf,type=minecraft:skeleton,{near()},limit=1] equipment.mainhand")[-120:])
            res = result(r, aid, 150, since) or {}
            dead = health(r) <= 0
            alive_mobs = "count" in r.rcon(f"execute if entity {sel}").lower()
            starts = reflex_events(r, since, None, "start")
            fights = [s for s in starts if s.get("reflex") == "auto_fight"]
            flees = [s for s in starts if s.get("reflex") == "creeper_flee"]
            ends = [e for e in reflex_events(r, since, "auto_fight", "end")]
            walk_ok = res.get("status") == "ok"
            if expect == "fight":
                ok = bool(fights) and not alive_mobs and walk_ok and not dead
            elif expect == "flee":
                ok = bool(flees) and not any((f.get("trigger") or {}).get("type") == "minecraft:creeper" for f in fights) and walk_ok and not dead
            else:
                ok = not fights and walk_ok and not dead
            r.record(f"{name}#{i}", "reflex_auto_fight", {}, ok,
                     f"walk={(res.get('status'), res.get('code'), res.get('note'))} dead={dead} mobs_left={alive_mobs} auto_fight={len(fights)} "
                     f"triggers={[(f.get('trigger') or {}) for f in fights][:3]} ends={[(e.get('result'), (e.get('detail') or {}).get('resumes')) for e in ends][:3]} "
                     f"creeper_flee={len(flees)} HandItems={hands!r} placed={placed}",
                     events=starts + ends, invalid=not (placed and ran(res)))
            r.rcon(f"kill {sel}")
    finally:
        r.rcon(f"kill {sel}")
        r.rcon(f"clear {p}")
        r.rcon("time set noon")


def auto_walk(r):
    """H-autofight lane case: a walk_to of 40 with a zombie spawned 6 off her path, 1 s in. pass = an auto_fight starts, the zombie
    dies, the walk resumes and ends ok, no death. Valid: she started in place and the walk ran. Frozen bar: >= 19 of 20 valid."""
    _auto_case(r, "auto_walk", [("zombie", 6, 6.5, "")], "fight", N)


def auto_archer(r):
    """H-autofight: the same walk with an armed skeleton (bow, I37 HandItems logged) 12 off her path. pass = auto_fight, the skeleton
    dies, the walk ends ok, no death. Frozen bar: >= 19 of 20 valid."""
    _auto_case(r, "auto_archer", [("skeleton", 4, 12.5, "")], "fight", N)


def auto_creeper(r):
    """H-autofight: the same walk with a creeper 6 off her path. pass = a creeper_flee starts, no auto_fight on the creeper (never
    melee a creeper), the walk ends ok, no death. Frozen bar: >= 19 of 20 valid."""
    _auto_case(r, "auto_creeper", [("creeper", 6, 0.5, "")], "flee", N)


def auto_protected(r):
    """H-autofight: the same walk with a NAMED zombie (protected) 5 off her path, NoAI so it cannot attack. pass = no auto_fight
    starts at all, the walk ends ok. Frozen bar: 20 of 20 valid."""
    _auto_case(r, "auto_protected", [("zombie", 5, 3.5, ',NoAI:1b,CustomName:"Bob"')], "none", N)


def boat_follow(r):
    """H-boat (follow_boat, registry 0.15.4 draft). A lake 2 deep from x 3 to 30 (z -8..8) in the floor; the "owner" is a NoAI
    villager in an oak boat (protected: never attacked), tp'd 0.3 a tick-pair east across the lake, then set ashore at x 33.
    n=N rounds of two cases:
    - has_boat: an oak boat in her MAIN inventory; follow_boat {entity_id: the villager}. pass = ok, boat_placed, mounted,
      landed_at set, boat_recovered, she ends within 10 of the villager, the boat count back to 1, no death.
    - no_boat: the same with no boat. pass = failed no_boat within 2 s.
    - stop_afloat: has_boat, a stop {} once she sails mid-lake. pass = the stop ok at once, follow_boat ends cancelled with
      boat_recovered, she is on land, the boat count back to 1.
    Valid: she started in place and the villager rode the boat. Frozen bar (before its first run): has_boat >= 4/5, no_boat 5/5, stop_afloat >= 4/5."""
    p = r.player
    sel = f"@e[tag=evebf,{near()}]"
    lake = [f"fill 3 {F - 2} -8 30 {F - 1} 8 minecraft:water"]

    def owner_id():
        st = r.state() or {}
        return next((e["id"] for e in st.get("nearby_entities", []) if e.get("type") == "minecraft:villager"), None)

    try:
        r.rcon("time set noon")
        for i in range(N):
            for case in ("has_boat", "no_boat", "stop_afloat"):
                park(r)
                r.rcon(f"kill {sel}")
                r.rcon(f"fill -6 {F - 2} -10 40 {F - 1} 10 minecraft:smooth_stone")
                r.rcon(f"fill -6 {F} -10 40 {F + 5} 10 minecraft:air")  # a121: another suite's glass rim at x 25 stopped her boat mid-lake
                for c in lake:
                    r.rcon(c)
                r.rcon(f"clear {p}")
                r.rcon(f"item replace entity {p} hotbar.0 with minecraft:iron_sword")
                if case != "no_boat":
                    r.rcon(f"item replace entity {p} inventory.5 with minecraft:oak_boat")
                r.tp(1.5, FLOOR, 0.5, -90)
                heal_full(r)
                me = r.pos()
                placed = abs(me[0] - 1.5) < 0.6 and abs(me[2] - 0.5) < 0.6
                r.rcon(f'summon minecraft:oak_boat 4.5 {F - 0.5} 3.5 {{Tags:["evebf"],Passengers:[{{id:"minecraft:villager",NoAI:1b,Tags:["evebf"]}}]}}')
                time.sleep(1.0)
                oid = owner_id()
                riding = r.rcon(f"execute as @e[type=minecraft:villager,tag=evebf,{near()}] on vehicle if entity @s[type=minecraft:oak_boat]").startswith("Test passed")
                since = mark(r)
                aid = request(r, "follow_boat", {"entity_id": oid, "keep_min": 4, "keep_max": 8}) if oid is not None else None
                if case == "no_boat":
                    res = (result(r, aid, 5, since) or {}) if aid else {}
                    ok = res.get("status") == "failed" and res.get("code") == "no_boat"
                    r.record(f"boat_follow_no_boat#{i}", "follow_boat", {}, ok, f"res={(res.get('status'), res.get('code'), res.get('note'))}",
                             result=res, invalid=not (placed and riding and oid is not None))
                    continue
                bx = 4.5
                while bx < (16 if case == "stop_afloat" else 30) and aid:  # he paddles east
                    bx += 0.6
                    r.rcon(f"tp @e[type=minecraft:oak_boat,tag=evebf,{near()}] {bx} {F - 0.5} 3.5")
                    time.sleep(0.1)
                if case == "stop_afloat":  # mind's note 1: a stop mid-lake lands and recovers first, then ends cancelled
                    time.sleep(2.0)
                    m = mark(r)
                    sres = result(r, request(r, "stop", {}), 5, m) or {}
                    res = (result(r, aid, 45, since) or {}) if aid else {}
                    ev = res.get("evidence") or {}
                    on_land = not r.rcon(f"execute as {p} on vehicle if entity @s").startswith("Test passed")
                    ok = (sres.get("status") == "ok" and res.get("code") == "cancelled" and ev.get("boat_recovered") and on_land
                          and count(r, "minecraft:oak_boat") == 1)
                    r.record(f"boat_follow_stop_afloat#{i}", "follow_boat", {}, ok,
                             f"stop={(sres.get('status'), sres.get('note'))} res={(res.get('status'), res.get('code'), res.get('note'))} ev={ev} on_land={on_land}",
                             result=res, invalid=not (placed and riding and oid is not None))
                    continue
                r.rcon(f"ride @e[type=minecraft:villager,tag=evebf,{near()},limit=1] dismount")
                r.rcon(f"tp @e[type=minecraft:villager,tag=evebf,{near()}] 33.5 {FLOOR} 3.5")
                res = (result(r, aid, 60, since) or {}) if aid else {}  # S96: a healthy run ends inside a minute; keep the case short
                ev = res.get("evidence") or {}
                me = r.pos()
                dist = math.hypot(me[0] - 33.5, me[2] - 3.5)
                boats = count(r, "minecraft:oak_boat")
                dead = health(r) <= 0
                ok = (res.get("status") == "ok" and ev.get("boat_placed") and ev.get("mounted") and ev.get("landed_at")
                      and ev.get("boat_recovered") and dist <= 10 and boats == 1 and not dead)
                r.record(f"boat_follow_has_boat#{i}", "follow_boat", {}, ok,
                         f"res={(res.get('status'), res.get('code'), res.get('note'))} ev={ev} dist_to_owner={dist:.1f} boats={boats} dead={dead}",
                         result=res, invalid=not (placed and riding and oid is not None))
    finally:
        park(r)
        r.rcon(f"kill {sel}")
        r.rcon(f"fill -6 {F - 2} -10 40 {F - 1} 10 minecraft:smooth_stone")
        r.rcon(f"clear {p}")


def floor_pen(r):
    """O102d (3) (docs/O102d-floor-handback.md; lane F c_rush #6: at 4.8 HP, cornered by a lone skeleton, the reflexes cycled 50 s
    with fight_ranged paused). n=N: K3 (iron sword, iron armour, bow, 32 arrows) plus an off-hand shield, HP 5 (natural regen
    off, food full), in a closed 5x5 oak-fence pen (1.5 high: no jump out, no wall to hide behind; lines of sight and arrows go
    over it), a skeleton with AI and its bow (I37) 9 out (inside the retreat trigger, 10), at midnight; fight_ranged is called
    again and again (radius 24, max_arrows 16) as the mind would, until the skeleton is gone or 45 s (combat's provider jar in
    run/mods). The expected sequence: retreat (at the floor), cornered against the fence, hand_back, the fight shoots.
    - pass = alive at the end; the skeleton gone; at least one hand_back event; no Retreat start while a hand-back window was open
      (from a hand_back to its hand_back_end).
    - valid only if the skeleton held its bow (I37), she started in place at <= 6 HP, and a fight_ranged was accepted.
    - frozen bar (before its first run): 0 deaths; >= 9 of 10 pass; >= 8 of 10 valid."""
    p = r.player
    sel = f"@e[tag=evepen,{near()}]"
    walls = ((-3, -3, 3, -3), (-3, 3, 3, 3), (-3, -3, -3, 3), (3, -3, 3, 3))
    try:
        r.rcon("time set midnight")
        for i in range(N):
            for _ in range(40):  # a death in the trial before: the body respawns her itself
                if health(r) > 0:
                    break
                time.sleep(0.5)
            r.rcon(f"kill {sel}")
            r.rcon(f"kill @e[type=minecraft:arrow,{near()}]")
            for x0, z0, x1, z1 in walls:
                r.rcon(f"fill {x0} {F} {z0} {x1} {F} {z1} minecraft:oak_fence")
            r.rcon(f"clear {p}")
            for slot, item in (("hotbar.0", "iron_sword"), ("armor.head", "iron_helmet"), ("armor.chest", "iron_chestplate"),
                               ("armor.legs", "iron_leggings"), ("armor.feet", "iron_boots"), ("hotbar.8", "bread 16"),
                               ("hotbar.1", "bow"), ("inventory.0", "arrow 32"), ("weapon.offhand", "shield")):
                r.rcon(f"item replace entity {p} {slot} with minecraft:{item}")
            r.tp(0.5, FLOOR, 0.5, -90)
            heal_full(r)
            r.rcon(f"damage {p} 15 minecraft:generic")
            time.sleep(0.5)
            h0 = health(r)
            me = r.pos()
            placed = abs(me[0] - 0.5) < 0.6 and abs(me[2] - 0.5) < 0.6
            since = mark(r)
            r.rcon(f'summon minecraft:skeleton 9.5 {F} 0.5 {{Tags:["evepen"],PersistenceRequired:1b,CanPickUpLoot:0b}}')
            r.rcon(f"item replace entity @e[tag=evepen,type=minecraft:skeleton,{near()}] weapon.mainhand with minecraft:bow")
            bow = r.rcon(f"execute if items entity @e[tag=evepen,type=minecraft:skeleton,{near()},limit=1] weapon.mainhand minecraft:bow").startswith("Test passed")
            hand = r.rcon(f"data get entity @e[tag=evepen,type=minecraft:skeleton,{near()},limit=1] equipment.mainhand")[-120:]
            t0, calls, hs, dead = time.time(), [], [h0], False
            while time.time() < t0 + 45 and len(calls) < 6:
                if calls and "count" not in r.rcon(f"execute if entity {sel}").lower():
                    break
                aid = request(r, "fight_ranged", {"radius": 24 + i / 100 + len(calls) / 1000, "max_arrows": 16})
                res = result(r, aid, max(5, int(t0 + 45 - time.time())) + 5, since) or {}
                calls.append((aid, res.get("status"), res.get("code"), (res.get("evidence") or {}).get("kills")))
                hs.append(health(r))
                if hs[-1] <= 0:
                    dead = True
                    break
                if res.get("status") not in ("ok", "failed"):
                    break
            el = round(time.time() - t0, 1)
            gone = "count" not in r.rcon(f"execute if entity {sel}").lower()
            r.rcon(f"kill {sel}")
            ev = reflex_events(r, since)
            opens = [e for e in ev if e.get("phase") == "hand_back"]
            closes = [e for e in ev if e.get("phase") == "hand_back_end"]
            inside = []  # Retreat starts while a window was open
            for o in opens:
                c = next((c for c in closes if c.get("t", 0) >= o.get("t", 0)), None)
                t_end = c.get("t") if c else time.time()
                inside += [e for e in ev if e.get("reflex") == "retreat" and e.get("phase") == "start" and o["t"] < e.get("t", 0) < t_end]
            accepted = any(s in ("ok", "failed") for _, s, _, _ in calls)
            ok = not dead and gone and bool(opens) and not inside
            r.record(f"floor_pen#{i}", "reflex_hand_back", {}, ok,
                     f"dead={dead} gone={gone} {el}s calls={calls} hp0={h0} hp_min={min(hs)} hand_backs={len(opens)} "
                     f"ends={[(c.get('reason'), c.get('held_ms')) for c in closes]} retreats_inside_window={len(inside)} "
                     f"reflexes={[(e.get('reflex'), e.get('phase'), e.get('result')) for e in ev][:16]} bow={bow} placed={placed} HandItems={hand!r}",
                     events=ev, invalid=not (bow and placed and h0 <= 6 and accepted))
    finally:
        r.rcon(f"kill {sel}")
        r.rcon(f"kill @e[type=minecraft:arrow,{near()}]")
        for x0, z0, x1, z1 in walls:
            r.rcon(f"fill {x0} {F} {z0} {x1} {F} {z1} minecraft:air")
        r.rcon(f"clear {p}")
        heal_full(r)
        r.rcon("time set noon")


SUITES = {"eat": eat,"retreat": retreat, "shield_arrow": shield_arrow, "shield_melee": shield_melee, "surrounded": surrounded, "upkeep": upkeep, "hazards": hazards, "lava": lambda r: hazards(r, True), "lava_walk": lava_walk, "creeper_hut": creeper_hut, "creeper_walk_hut": creeper_walk_hut, "creeper_corner": creeper_corner, "thin_aim": thin_aim, "bow_draw": bow_draw, "equip": equip, "night": night, "nav": nav, "survival": survival, "dodge": dodge, "edge": edge,
          "fall": fall, "fall_bucket": fall_bucket, "creeper": creeper, "arbiter": arbiter, "archer_doorway": archer_doorway,
          "seal_under_reflex": seal_under_reflex, "c_rush_replay": c_rush_replay, "c_rush_melee": c_rush_melee, "archer_live": archer_live, "build_walk": build_walk, "s28_low_hp": s28_low_hp, "creeper_watch": creeper_watch, "stopped_under_attack": stopped_under_attack, "eat_topup": eat_topup, "auto_walk": auto_walk, "auto_archer": auto_archer,
          "auto_creeper": auto_creeper, "auto_protected": auto_protected, "boat_follow": boat_follow, "floor_pen": floor_pen, "nav_short": lambda r: nav(r, True)}

def shield_facing(r):
    """O325/O326 (kickoff, combat): does a raised shield block a creeper blast from the side and from behind on 26.3? A use_item (sword in
    the main hand, so the offhand shield is what is used) holds the shield up facing east; then a NoAI creeper summoned already lit (Fuse 0)
    goes off 4 out in front, at her side (south) or behind (west), before any reflex can turn her. a146: a NoAI zombie raised no shield. Recorded per trial: her health before and after, a shield up
    at the blast; a no-shield round at each angle is the baseline. Not a pass bar: the numbers are the result (pass = she is alive)."""
    p = r.player
    griefing = r.rcon("gamerule mob_griefing").rsplit(" ", 1)[-1].strip()
    r.rcon("gamerule mob_griefing false")
    try:
        for shield in (True, False):
            for name, dx, dz in (("front", 4, 0), ("side", 0, 4), ("behind", -4, 0)):
                for i in range(N):
                    park(r)
                    r.rcon(f"kill @e[tag=evesf,{near()}]")
                    r.rcon(f"fill -6 {F} -6 6 {F + 3} 6 minecraft:air")
                    r.rcon(f"item replace entity {p} weapon.offhand with {'minecraft:shield' if shield else 'minecraft:air'}")
                    r.rcon(f"item replace entity {p} hotbar.0 with minecraft:iron_sword")
                    r.tp(0.5, FLOOR, 0.5, -90)  # facing east
                    heal_full(r)
                    r.act("select_hotbar", {"slot": 0})
                    since = mark(r)
                    aid = request(r, "use_item", {"hold_ms": 4000}) if shield else None
                    time.sleep(1.0)  # raised (a shield is up after 5 ticks)
                    h0 = health(r)
                    r.rcon(f'summon minecraft:creeper {0.5 + dx} {F} {0.5 + dz} {{Tags:["evesf"],NoAI:1b,ignited:1b,Fuse:0s}}')
                    time.sleep(1.5)
                    h1 = health(r)
                    r.rcon(f"kill @e[tag=evesf,{near()}]")
                    dead = h1 <= 0
                    res = result(r, aid, 6, since) if aid else None
                    up = bool(res) and res.get("code") not in ("nothing_to_use", "precondition_failed", "busy")
                    r.record(f"shield_facing_{'shield' if shield else 'bare'}_{name}#{i}", "shield_facing", {"shield": shield, "blast": name},
                             not dead, f"hp {h0}->{h1} (damage {h0 - h1:.1f}) shield={shield} raised={up} use_item={res and (res.get('status'), res.get('code'), res.get('note'))} blast={name}", invalid=shield and not up)
                    heal_full(r)
    finally:
        r.rcon(f"kill @e[tag=evesf,{near()}]")
        r.rcon(f"item replace entity {p} weapon.offhand with minecraft:air")
        r.rcon(f"gamerule mob_griefing {griefing}")


def phantom_cover(r):
    """S105 (kickoff ruling on the 45L death 17:16): night, open floor, no shield, 32 dirt in the hotbar, a sword, bread; two phantoms
    summoned above her. Four cover types, N trials each, 60 s each (kickoff (a)): enclosed (an alcove 3 east, walls on 3 sides, a ceiling
    at head+1), roof3 (a 3x3 roof 3 up, 3 east), roof on a neighbour (a 3-high stone post beside her: one place), home (a stone hut at the
    envelope home, ~18 m, door east). Recorded per trial, from the
    phantom_cover reflex: cover type, ms to covered, hits taken while covered, blocks left behind; damage events in the 60 s. Not a pass
    bar: the numbers are for kickoff's ruling. Pass = alive."""
    p = r.player
    try:
        r.rcon("gamerule natural_health_regeneration true")
        hut = [f"fill -14 {F} 11 -11 {F + 2} 14 minecraft:stone hollow", f"fill -11 {F} 12 -11 {F + 1} 13 minecraft:air"]  # home, door east
        for kind in ("enclosed", "roof3", "home"):  # a166: the neighbour roof is gone (never placed)
            for i in range(N):
                park(r)
                r.rcon("time set noon")
                r.rcon(f"kill @e[type=minecraft:phantom,{near()}]")
                r.rcon(f"fill -8 {F} -8 8 {F + 6} 8 minecraft:air")
                r.rcon(f"clear {p}")
                r.rcon(f"item replace entity {p} hotbar.0 with minecraft:iron_sword")
                r.rcon(f"item replace entity {p} hotbar.1 with minecraft:dirt 32")
                r.rcon(f"item replace entity {p} hotbar.2 with minecraft:bread 8")
                if kind == "enclosed":  # an alcove 3 east: walls on 3 sides, a ceiling at head+1, open toward her
                    r.rcon(f"fill 3 {F} -1 4 {F + 1} 1 minecraft:stone")
                    r.rcon(f"fill 3 {F} 0 3 {F + 1} 0 minecraft:air")
                    r.rcon(f"fill 3 {F + 2} -1 4 {F + 2} 1 minecraft:stone")
                elif kind == "roof3":  # a160's "existing": a 3x3 roof 3 up, 3 east
                    r.rcon(f"fill 2 {F + 3} -1 4 {F + 3} 1 minecraft:stone")
                elif kind == "roof_on_neighbour":
                    r.rcon(f"fill 1 {F} 0 1 {F + 2} 0 minecraft:stone")
                else:
                    for c in hut:
                        r.rcon(c)
                r.tp(0.5, FLOOR, 0.5, -90)
                heal_full(r)
                r.act("select_hotbar", {"slot": 0})
                since = mark(r)
                r.rcon("time set 18000")
                for k in range(2):
                    r.rcon(f'summon minecraft:phantom {0.5 + 3 * k} {F + 12} 0.5 {{Tags:["evepc"],PersistenceRequired:1b}}')
                t0 = time.time()
                while time.time() < t0 + 60 and health(r) > 0:
                    time.sleep(1.0)
                h1 = health(r)
                r.rcon(f"kill @e[type=minecraft:phantom,{near()}]")
                r.rcon("time set noon")
                time.sleep(1.0)
                ev = [e for e in reflex_events(r, since, "phantom_cover")]
                ends = [e for e in ev if e["phase"] == "end"]
                dm = damage_events(r, since)
                d = (ends[0].get("detail") or {}) if ends else {}
                r.record(f"phantom_cover_{kind}#{i}", "phantom_cover", {"kind": kind}, h1 > 0,
                         f"starts={sum(1 for e in ev if e['phase'] == 'start')} type={d.get('cover_type')} covered_ms={d.get('covered_ms')} "
                         f"hits_while_covered={d.get('hits_while_covered')} blocks_left={d.get('blocks_left')} hits_60s={len(dm)} "
                         f"hp_end={h1} ends={[e.get('result') for e in ends]}", events=ev)
                park(r)
                r.rcon(f"fill -8 {F} -8 8 {F + 6} 8 minecraft:air")
                r.rcon(f"fill -14 {F} 11 -11 {F + 2} 14 minecraft:air")
    finally:
        r.rcon(f"kill @e[type=minecraft:phantom,{near()}]")
        r.rcon("time set noon")
        r.rcon("gamerule natural_health_regeneration false")


SUITES["shield_facing"] = shield_facing
SUITES["phantom_cover"] = phantom_cover


# O293: each suite's frozen pass bar per case, as a share of N. Only these stop early. Not here because their bar is no share of N: creeper
# (fixed trial list), archer_live (no pass bar), and the older 19/20 suites whose cases have their own trial counts.
BARS = {"seal_under_reflex": {"seal_under_reflex": 1.0}, "c_rush_replay": {"c_rush_replay": 0.9}, "c_rush_melee": {"c_rush_melee": 0.9},
        "build_walk": {"build_walk_": 0.9}, "s28_low_hp": {k: 0.8 for k in ("s28_calm_creeper", "s28_latch", "s28_phantom", "s28_stop_reflex")},
        "creeper_watch": {"creeper_watch_calm": 1.0, "creeper_watch_closing": 0.8}, "stopped_under_attack": {"stopped_under_attack": 1.0},
        "eat_topup": {"eat_topup": 0.8}, "auto_walk": {"auto_walk": 0.95}, "auto_archer": {"auto_archer": 0.95}, "auto_creeper": {"auto_creeper": 0.95},
        "auto_protected": {"auto_protected": 1.0}, "floor_pen": {"floor_pen": 0.9}, "nav_short": {"nav_flat": 1.0},
        "boat_follow": {"boat_follow_has_boat": 0.8, "boat_follow_no_boat": 1.0, "boat_follow_stop_afloat": 2 / 3}}


class BarDecided(BaseException):  # BaseException: no suite's own except Exception may swallow it; its finally blocks still run
    pass


def bar_decided(bars, n, rows):
    """'pass' once every case has its passes, 'fail: <case>' once a case can no longer get them, else None. Invalid trials count neither way."""
    done = True
    for case, share in bars.items():
        need = math.ceil(share * n - 1e-9)
        mine = [x for x in rows if x["variant"].startswith(case) and not x.get("invalid")]
        passes = sum(1 for x in mine if x["pass"])
        if len(mine) - passes > n - need:
            return f"fail: {case} {len(mine) - passes} failed, needs {need} of {n}"
        done = done and passes >= need
    return "pass" if done else None


def stop_at_bar(run, suite):
    """O293: after each row, end the suite as soon as its bar is decided; watch judges on the trials run."""
    bars, orig = BARS[suite], type(run).record
    first = len(run.rows)

    def record(variant, skill, params, ok, why, **kw):
        orig(run, variant, skill, params, ok, why, **kw)
        verdict = bar_decided(bars, N, run.rows[first:])
        if verdict:
            raise BarDecided(verdict)
    run.record = record


if __name__ == "__main__":
    which = [a for a in sys.argv[1].split(",")] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else list(SUITES)
    player = sys.argv[sys.argv.index("--player") + 1] if "--player" in sys.argv else "EveDev"
    run = Run(sys.argv[sys.argv.index("--cp") + 1] if "--cp" in sys.argv else "CP-20", player)  # O98o: seal_under_reflex runs as CP-10
    try:
        setup(run)
        early = ""
        for w in which:
            if w in BARS:
                stop_at_bar(run, w)
            try:
                SUITES[w](run)
            except BarDecided as d:
                early += f" **{w} stopped early at its bar after {len(run.rows)} rows, {d}.**"
                print(f"EARLY STOP {w}: {d}")
            run.__dict__.pop("record", None)
        run.finish(CRITERIA, f"Suites run: {', '.join(which)}.{early} Reflex thresholds: health floor 6, eat at food <= 14 (or health <= 14 "
                             "and food < 18), safe = no hostile within 8 and no projectile predicted to hit within 20 ticks."
                   + ("\n\nseal_under_reflex (frozen before the run): " + seal_under_reflex.__doc__ if "seal_under_reflex" in which else "")
                   + ("\n\nc_rush_replay (frozen before the run): " + c_rush_replay.__doc__ if "c_rush_replay" in which else "")
                   + ("\n\nc_rush_melee (frozen before the run): " + c_rush_melee.__doc__ if "c_rush_melee" in which else "")
                   + ("\n\narcher_live (frozen before the run): " + archer_live.__doc__ if "archer_live" in which else "")
                   + ("\n\nbuild_walk (frozen before the run): " + build_walk.__doc__ if "build_walk" in which else "")
                   + ("\n\ns28_low_hp (frozen before the run): " + s28_low_hp.__doc__ if "s28_low_hp" in which else "")
                   + ("\n\ncreeper_watch (frozen before the run): " + creeper_watch.__doc__ if "creeper_watch" in which else "")
                   + ("\n\nstopped_under_attack (frozen before the run): " + stopped_under_attack.__doc__ if "stopped_under_attack" in which else "")
                   + ("\n\neat_topup (frozen before the run): " + eat_topup.__doc__ if "eat_topup" in which else "")
                   + "".join(f"\n\n{n} (frozen before the run): " + SUITES[n].__doc__ for n in ("auto_walk", "auto_archer", "auto_creeper", "auto_protected", "boat_follow") if n in which)
                   + ("\n\nfloor_pen (frozen before the run): " + floor_pen.__doc__ if "floor_pen" in which else ""))
    except BaseException as e:
        run.finish(CRITERIA, f"**ABORTED** after {len(run.rows)} attempts: {e!r}")
        raise
    finally:
        run.rcon("gamerule natural_health_regeneration true")
        run.rcon("difficulty peaceful")
