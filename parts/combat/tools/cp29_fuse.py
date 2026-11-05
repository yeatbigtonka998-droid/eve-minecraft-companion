"""O284 step 1: measure a creeper's fuse on 26.3, so the creeper-first rule is built on the game and not on memory. Server-side only:
Eve held still at the arena centre by zero movement attributes (STOPPED does not turn creeper_flee off, and it would move her), no shield, Resistance V, mob_griefing off, a live creeper walking in from 8 blocks. RCON cannot read
the swell, so what counts is when the creeper blows relative to when it crossed 3 blocks:
    approach      she stands still: where and how soon after crossing 3 it blows
    tp_<d>        0.3 s after it crosses 3 she is moved to d blocks from it, same line: does the fuse still end on time?
    wall          0.3 s after crossing, an opaque wall goes up between them: does losing sight cancel it?
    band_<d>      she is kept d blocks from it for 6 s (the edge of sword reach): does it prime at all?
Blown on time (about 1.5 s after crossing) = not cancelled. Development evidence, no pass/fail.

    py -3.12 parts/combat/tools/cp29_fuse.py --lane d [--n 3] [--only approach,tp_7.5] [--expect-jar <sha>] [--expect-body <sha>] [--next eve-watch]
"""
import json, re, shutil, sys, time
from datetime import datetime
from pathlib import Path

sys.argv = [sys.argv[0], "cp26"] + sys.argv[1:]  # laneb_combat parses --lane from argv at import
sys.path.insert(0, str(Path(__file__).resolve().parent))
import laneb_combat as L  # noqa: E402

r, P, Y, CX, CZ = L.r, L.P, L.Y, L.CX, L.CZ
TAG, HOME, WATCH_S, BAND_S = "evefu", (CX + 0.5, Y + 1, CZ + 0.5), 12, 6
CASES = ["approach", "tp_4", "tp_5", "tp_6.5", "tp_7.5", "tp_10", "wall", "band_3.3", "band_3.6"]


def pos(sel):
    m = re.findall(r"(-?\d+(?:\.\d+)?)d", r(f"data get entity {sel} Pos"))
    return tuple(float(v) for v in m[:3]) if len(m) >= 3 else None


def gap(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def put_her(x, z):
    r(f"tp {P} {x:.2f} {Y + 1} {z:.2f}")  # ponytail: raw tp, the arena floor is solid and x, z stay inside its walls (checked by trial())


def trial(case):
    """One creeper, walking in along +x. Returns when it blew or the watch ran out."""
    L.clear_arena()
    r(f"effect give {P} minecraft:resistance 60 4 true")
    r(f"effect give {P} minecraft:instant_health 1 10 true")
    L.tp(*HOME)
    time.sleep(0.3)
    r(f'summon minecraft:creeper {HOME[0] + 8} {Y + 1} {HOME[2]} {{Tags:["{TAG}"],PersistenceRequired:1b}}')
    t0 = time.time()
    crossed, acted, min_gap, last, c = None, None, 99.0, None, None
    while time.time() - t0 < WATCH_S:
        c, e = pos(f"@e[tag={TAG},limit=1]"), pos(P)
        now = time.time()
        if c is None:
            break
        last = (c, e)
        d = gap(c, e)
        if crossed is None and d < 3:
            crossed = now
        min_gap = min(min_gap, d) if crossed else min_gap
        if case.startswith("band_"):  # kept at d from the moment it comes within d + 1, so it never has to cross 3
            want = float(case.split("_")[1])
            if d < want + 1:
                if acted is None:
                    acted = now
                x = c[0] - want
                if x < L.X0 + 2:
                    return {"case": case, "blew": False, "stopped": "ran out of arena", "held_s": round(now - acted, 2), "crossed_3": crossed is not None, "min_gap": round(min_gap, 2)}
                put_her(x, c[2])
                min_gap = min(min_gap, d)
            if acted and now - acted > BAND_S:
                break
        elif crossed and acted is None and case != "approach" and now - crossed >= 0.3:
            acted = now
            if case.startswith("tp_"):
                put_her(max(L.X0 + 2, c[0] - float(case.split("_")[1])), c[2])
            elif case == "wall":
                x = int((c[0] + e[0]) // 2)
                r(f"fill {x} {Y + 1} {L.Z0 + 1} {x} {Y + 5} {L.Z1 - 1} minecraft:smooth_stone")
        elif crossed and case != "approach" and now - crossed > 5:
            break
        time.sleep(0.05)
    end = time.time()
    blew = c is None and last is not None
    out = {"case": case, "blew": blew, "gap_at_end": round(gap(*last), 2) if last else None, "crossed_3": crossed is not None, "min_gap": round(min_gap, 2),
           "blew_after_cross_s": round(end - crossed, 2) if blew and crossed else None, "s_from_summon": round(end - t0, 2), "acted_after_cross_s": round(acted - crossed, 2) if acted and crossed else None,
           "held_s": round(end - acted, 2) if case.startswith("band_") and acted else None}
    r(f"kill @e[tag={TAG}]")
    return out


def main():
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 3
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else CASES
    L.lane_lock("acquire", 15)
    rows, aborted = [], None
    try:
        L.check_jar()
    except SystemExit:
        L.lane_lock("release")
        raise
    root = L.EVIDENCE / "CP-29"
    num = 1 + sum(1 for d in root.iterdir() if d.is_dir() and f"-{L.LABEL}-fuse-attempt-" in d.name)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    out = root / f"{stamp}-{L.LABEL}-fuse-attempt-{num}"
    out.mkdir()
    L.stash_save()
    was = {g: r(f"gamerule {g}").rsplit(" ", 1)[-1].strip() for g in ("spawn_mobs", "mob_griefing")}
    was["difficulty"] = r("difficulty").rsplit(" ", 1)[-1].strip().lower()
    try:
        r("gamerule spawn_mobs false")
        r("gamerule mob_griefing false")
        r("difficulty peaceful")
        L.build_arena()
        r(f"clear {P}")  # no shield for a cornered flee to raise; stash_save holds what she carried
        r(f"attribute {P} minecraft:movement_speed base set 0")  # a flee presses keys and goes nowhere; tp still moves her
        r(f"attribute {P} minecraft:jump_strength base set 0")
        for k in range(n):
            for case in only:
                r("time set 6000")
                r("difficulty normal")
                d0 = L.deaths()
                row = {**trial(case), "trial": k + 1, "deaths": L.deaths() - d0}
                rows.append(row)
                with open(out / "rows.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")
                print(json.dumps(row), flush=True)
                if row["deaths"]:
                    raise RuntimeError(f"death in {case} #{k + 1}")
    except Exception as e:
        aborted = f"{type(e).__name__}: {e}"
    finally:
        r(f"kill @e[tag={TAG}]")
        r(f"attribute {P} minecraft:movement_speed base reset")
        r(f"attribute {P} minecraft:jump_strength base reset")
        r(f"effect clear {P}")
        teardown = L.safe_teardown(L.clear_arena)
        L.stash_restore()
        r(f"gamerule spawn_mobs {was['spawn_mobs']}")
        r(f"gamerule mob_griefing {was['mob_griefing']}")
        r(f"difficulty {was['difficulty']}")
        r(f"forceload remove {L.X0} {L.Z0} {L.X1} {L.Z1}")
        L.lane_lock("release")
    lines = [f"# CP-29 fuse measurement, lane {L.LANE_ID.upper()} (`shark`) {stamp} attempt {num}", "",
             "Development evidence, no pass/fail (O284 step 1). Eve held still by zero movement attributes, no shield, Resistance V, mob_griefing off. RCON cannot read the swell; a blast about 1.5 s after crossing 3 blocks means the fuse ran on.",
             f"Versions: repo {L.git_head()}, eve-combat {L.jar_sha()}, eve-body {L.body_sha()}. Teardown: {teardown}." + (f" ABORTED: {aborted}" if aborted else ""), "",
             "| case | trials | blew | crossed 3 | blew after crossing, s | min gap after crossing | held, s |", "|---|---|---|---|---|---|---|"]
    for case in dict.fromkeys(x["case"] for x in rows):
        xs = [x for x in rows if x["case"] == case]
        col = lambda key: ", ".join(str(x.get(key)) for x in xs)
        lines.append(f"| {case} | {len(xs)} | {sum(bool(x['blew']) for x in xs)} | {sum(bool(x['crossed_3']) for x in xs)} | {col('blew_after_cross_s')} | {col('min_gap')} | {col('held_s')} |")
    text = "\n".join(lines) + "\n"
    (out / "RESULT.md").write_text(text, encoding="utf-8")
    shutil.copy2(out / "RESULT.md", Path(__file__).resolve().parents[1] / "evidence-summaries" / f"CP-29-{stamp}-{L.LABEL}-fuse-attempt-{num}.md")
    print(text, flush=True)


if __name__ == "__main__":
    main()
