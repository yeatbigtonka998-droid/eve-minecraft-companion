"""O98 (CP-31 a16 night 1 death): fight_melee against a skeleton just outside a shelter doorway, engaged from inside.

    py -3.12 parts/combat/tools/cp98_doorway.py --lane e [--n 10] --expect-jar <sha256> --expect-body <sha256> [--stub] [--next <chat>]
    --stub (O102 b): drive body's stub_mind Hub in-process (dev client built with the 0.16.0 draft; EVE_REGISTRY, EVE_HUB_PORT,
    EVE_LINK_TOKEN set as body's rig does) instead of the lane mind, which on 0.15.0 cannot pass doorway.

Needs a registry whose fight_melee takes `doorway` (combat-fight_melee-doorway.py, assembled by the orchestrator).
Each trial: a stone room (7x7 inside, roofed) in the arena with a 1-wide doorway in its south wall; Eve inside straight back from
the door (where a16 died: in the line through the opening), iron sword + iron armour + a shield; a skeleton (AI on, a bow put in its hand: summon with NBT gives none)
1.3 outside the door's centre; normal difficulty. fight_melee {entity_id, doorway, health_floor 6}.
Pass = skeleton dead within 60 s, no death, Eve never past the door cell (RCON 0.1 s), at most 1.0 s in the line of fire after
the first 2 s while the skeleton is outside, min HP >= 8, I29e max_state_gap_s <= 1.0.
"""
import json, sqlite3, sys, threading, time, uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import laneb_combat as L  # noqa: E402

r, P, X0, X1, Z0, Z1, Y, CX, CZ = L.r, L.P, L.X0, L.X1, L.Z0, L.Z1, L.Y, L.CX, L.CZ
TAG = "evedr"
DOOR = {"x": CX, "y": Y + 1, "z": CZ + 4}  # the door cell; outside = +z
DOORWAY = {"pos": DOOR, "out": "south"}  # fight_melee's doorway param
MIND_DB = L.LANE / "mind" / "eve.db"


def num_list(out):
    return [float(v.rstrip("dfF")) for v in out.split("[", 1)[-1].rstrip("]").split(", ")] if "[" in out else None


def health(sel):
    out = r(f"data get entity {sel} Health")
    try:
        return float(out.rsplit(" ", 1)[-1].rstrip("f"))
    except ValueError:
        return None


def signed(x, z):  # past the door cell's centre toward the outside (+z)
    return z - (DOOR["z"] + 0.5)


def in_line_of_fire(t, e):  # the line skeleton -> Eve crosses the door plane inside the opening (+ 0.3 slack)
    st, se = signed(t[0], t[2]), signed(e[0], e[2])
    if st <= 0 or se >= 0:
        return False
    f = st / (st - se)
    lat = (t[0] - (DOOR["x"] + 0.5)) + ((e[0] - t[0]) * f)
    return abs(lat) <= 0.8


def build_room():
    x0, x1, z0, z1 = CX - 4, CX + 4, CZ - 4, CZ + 4
    r(f"fill {x0} {Y + 1} {z0} {x1} {Y + 4} {z1} minecraft:stone")
    r(f"fill {x0 + 1} {Y + 1} {z0 + 1} {x1 - 1} {Y + 3} {z1 - 1} minecraft:air")
    r(f"fill {DOOR['x']} {Y + 1} {DOOR['z']} {DOOR['x']} {Y + 2} {DOOR['z']} minecraft:air")  # the 1x2 doorway
    r(f"fill {x0} {Y + 4} {z0} {x1} {Y + 4} {z1} minecraft:glowstone")  # lit roof: the room is not a spawn spot


STUB = "--stub" in sys.argv  # O102 (b): a dev client built with the 0.16.0 draft, driven through body's stub_mind Hub


LINK = None


def net_id(tag, timeout=5.0):
    out = r(f"data get entity @e[tag={tag},limit=1] UUID")
    ints = [int(v) for v in out.split("[I;", 1)[-1].rstrip("]").split(",")] if "[I;" in out else None
    if not ints:
        return None
    u = str(uuid.UUID(bytes=b"".join(i.to_bytes(4, "big", signed=True) for i in ints)))
    if STUB:
        return LINK.net_id(u, timeout)
    t0 = time.time()
    while time.time() - t0 < timeout:
        con = sqlite3.connect(f"file:{MIND_DB.as_posix()}?mode=ro", uri=True, timeout=5)
        row = con.execute("SELECT payload FROM observations WHERE kind='state' ORDER BY ts DESC LIMIT 1").fetchone()
        con.close()
        for e in (json.loads(row[0]).get("nearby_entities") or []) if row else []:
            if e.get("uuid") == u:
                return e["id"]
        time.sleep(0.3)
    return None


class Watch(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop, self.out, self.lof, self.min_hp, self.t0 = threading.Event(), [], 0.0, 20.0, time.time()

    def run(self):
        while not self.stop.is_set():
            e = num_list(r(f"data get entity {P} Pos"))
            s = num_list(r(f"data get entity @e[tag={TAG},limit=1] Pos"))
            hp = health(P)
            if hp is not None:
                self.min_hp = min(self.min_hp, hp)
            if e and signed(e[0], e[2]) > 0.5:
                self.out.append([round(v, 2) for v in e])
            if e and s and time.time() - self.t0 > 2.0 and in_line_of_fire(s, e):
                self.lof += 0.1
            time.sleep(0.1)


def trial(k):
    L.clear_arena()
    r(f"kill @e[tag={TAG}]")
    build_room()
    L.kit()
    r(f"item replace entity {P} weapon.offhand with minecraft:shield")
    L.tp(DOOR["x"] + 0.5, Y + 1, DOOR["z"] - 6 + 0.5, 0, 0)  # straight back from the door, facing it (yaw 0 = +z)
    time.sleep(1.0)
    r(f'summon minecraft:skeleton {DOOR["x"] + 0.5} {Y + 1} {DOOR["z"] + 0.5 + 1.3} {{Tags:["{TAG}"],PersistenceRequired:1b}}')
    L.SPAWNS.clear()
    L.armed("skeleton", TAG)  # its bow (summon with NBT gives none) + I37: read back and recorded, unarmed = run INVALID
    sid = net_id(TAG)
    if sid is None:
        r(f"kill @e[tag={TAG}]")
        return {"case": "d_door", "trial": k + 1, "ok": False, "valid": False, "detail": "FAIL (fixture): skeleton not in the body's state"}
    w = Watch()
    w.start()
    t0_ms = int(time.time() * 1000)
    d0 = L.deaths()
    params = {"entity_id": sid, "health_floor": 6, "doorway": DOORWAY}
    res = LINK.do("fight_melee", params, 90) if STUB else L.eve("do", {"skill": "fight_melee", "params": params, "wait": 60}, 90)
    w.stop.set(); w.join()
    died = L.deaths() - d0
    dead = health(f"@e[tag={TAG},limit=1]") is None
    gap = LINK.gap(t0_ms / 1000) if STUB else L.state_gap(t0_ms)
    checks = [(dead, f"skeleton dead {dead} ({res.get('status')}/{res.get('code') or res.get('error')})"),
              (died == 0, f"deaths {died}"),
              (not w.out, f"Eve past the door cell {len(w.out)} samples {w.out[:3]}"),
              (w.lof <= 1.0, f"in the line of fire {w.lof:.1f} s after the first 2 s"),
              (w.min_hp >= 8, f"min HP {w.min_hp}"),
              (gap is not None and gap <= 1.0, f"max_state_gap_s {gap}")]
    ok = all(c for c, _ in checks)
    detail = "; ".join(d for _, d in checks) if ok else "FAIL: " + "; ".join(d for c, d in checks if not c)
    r(f"kill @e[tag={TAG}]")
    ev = res.get("evidence") or {}
    return {"case": "d_door", "trial": k + 1, "t_end": round(time.time(), 1), "spawned": list(L.SPAWNS), "max_state_gap_s": gap, "ok": ok, "valid": True,
            "detail": detail, "door_hold_ticks": ev.get("door_hold_ticks"), "lof_s": round(w.lof, 1), "min_hp": w.min_hp,
            "params": params, "result": res}


def main():
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 10
    global LINK
    if STUB:
        LINK = L.StubLink("fight_melee", "doorway")
    else:
        st = L.eve("status")
        if not st.get("body"):
            sys.exit(f"lane {L.LANE_ID} body is not up: {st}")
    L.lane_lock("acquire", 15 + 2 * n)
    try:
        L.check_jar()
    except SystemExit:
        L.lane_lock("release")
        raise
    root = L.EVIDENCE / "CP-31"
    root.mkdir(parents=True, exist_ok=True)
    num = 1 + sum(1 for d in root.iterdir() if d.is_dir() and "-doorway-attempt-" in d.name)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    out = root / f"{stamp}-{L.LABEL}-doorway-attempt-{num}"
    out.mkdir()
    rows, t0, aborted = [], time.time(), None
    was = r("difficulty").rsplit(" ", 1)[-1].strip().lower()
    L.build_arena()
    L.stash_save()
    try:
        r("difficulty normal")
        for k in range(n):
            row = trial(k)
            rows.append(row)
            with open(out / "rows.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            print(f"{'PASS' if row['ok'] else 'FAIL'} d_door #{k + 1}: {row['detail']}", flush=True)
            if "deaths 1" in row["detail"]:
                raise RuntimeError(f"death in d_door #{k + 1}")
    except Exception as e:  # always summarise
        aborted = f"{type(e).__name__}: {e}"
    finally:
        r(f"kill @e[tag={TAG}]")
        r("difficulty peaceful")  # I20: the next normal is a real change
        r(f"difficulty {was}")
        teardown = L.safe_teardown(L.clear_arena)
        L.stash_restore()
        r(f"forceload remove {X0} {Z0} {X1} {Z1}")
        L.lane_lock("release")
    passed = sum(x["ok"] for x in rows)
    (out / "RESULT.md").write_text("\n".join([
        f"# O98 doorway — lane {L.LANE_ID.upper()} {stamp} attempt {num}: {passed}/{len(rows)}", "",
        "Hypothesis: with doorway, fight_melee never follows a skeleton out of the door; it holds beside the door wall out of the "
        "line through the opening, then kills the skeleton when it comes into the doorway cell.",
        f"Fixture: room 7x7 at the arena centre, doorway {DOORWAY}; Eve 6 straight back from the door; skeleton 1.3 outside; normal "
        "difficulty; kit iron sword + iron armour + shield. Judge: RCON 0.1 s positions, health, deathCount; I29e state gaps.",
        f"Versions: repo {L.git_head()}, lane src {L.lane_src()}, eve-combat jar {L.jar_sha()}, eve-body jar {L.body_sha()}"
        + (f", via body's stub_mind Hub, registry {LINK.registry[:12]} (the 0.16.0 draft)" if STUB else ", via the lane mind") + ". "
        f"Elapsed {time.time() - t0:.0f} s. Teardown: {teardown}." + (f" ABORTED: {aborted}" if aborted else "")
        + " Development evidence, not qualification.", "",
        "| trial | ok | detail |", "|---|---|---|"] + [f"| {x['trial']} | {'PASS' if x['ok'] else 'FAIL'} | {x['detail']} |" for x in rows]) + "\n",
        encoding="utf-8")
    print(L.export_candidates(out, t0), flush=True)
    print(f"{passed}/{len(rows)} -> {out}")


if __name__ == "__main__":
    main()
