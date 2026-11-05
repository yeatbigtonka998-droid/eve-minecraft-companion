r"""CP-44 [K] against eve-watch's independent fixture (parts/watch/laneb/cp44_fixture.json), mind on fake links with the
REAL fact extractor (Ollama qwen3:8b), phases 1-6 incl. two restarts and a world change.
    python tools\live_cp44.py --out <evidence dir>
MAPPING (frozen 2026-10-20 before the first counted run; the fixture lets the runner map its names to the mind's):
a fixture fact matches a CURRENT recalled fact when the predicate is in the allowed set, the subject+object text
contains every keyword group (any word of a group), coordinates appear exactly, item ids exactly, the world scope is
right (world-specific facts carry the world id, others none) and, for S9, confidence < 1.
  S1/C2 home: predicate location, subject "home", object "-16 99 -48" / "120 64 30"
  S2/C3 build materials: preference; minecraft:spruce_planks + minecraft:cobblestone / minecraft:dark_oak_planks +
        minecraft:cobblestone (and no spruce in C3)
  S3 wheat farm: rule|access|ownership; {wheat} {ask,asked,request,unless,only}
  S4/C1 portal: location; {portal}; "40 70 -100" / "40 70 -110"
  S5 play time: schedule; {evening,evenings,7,19}
  S6 TNT: rule; {tnt}
  S7 Biscuit: identity|rule; {biscuit,dog}; world-specific
  S8 chest: access|ownership; {chest} {shared,share,take,both}
  S9 diamonds: possible_location; {diamond,diamonds} {lake}; confidence < 1
  S10 colour: preference; {green}
  J1: subject job, predicate completed, source observation, source_ref = the walk's action id
Free-text meaning beyond these keywords is reported for a human check, never silently passed.
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

MIND = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(MIND / "tests"), str(MIND / "tools"), str(MIND / "src")]
from conftest import envelope, mind_env, until  # noqa: E402
from eve_mind.memory import extract_facts  # noqa: E402

FIX = json.loads((MIND.parents[1] / "parts" / "watch" / "laneb" / "cp44_fixture.json").read_text("utf-8"))
A, B = FIX["worlds"]["A"], FIX["worlds"]["B"]
OWNER = FIX["owner"]
MAP = {
    "S1": ({"location"}, [], "-16 99 -48", True, "home"), "C2": ({"location"}, [], "120 64 30", True, "home"),
    "S2": ({"preference"}, [["minecraft:spruce_planks"], ["minecraft:cobblestone"]], None, False, None),
    "C3": ({"preference"}, [["minecraft:dark_oak_planks"], ["minecraft:cobblestone"]], None, False, None),
    "S3": ({"rule", "access", "ownership"}, [["wheat"], ["ask", "asked", "request", "unless", "only"]], None, True, None),
    "S4": ({"location"}, [["portal"]], "40 70 -100", True, None), "C1": ({"location"}, [["portal"]], "40 70 -110", True, None),
    "S5": ({"schedule"}, [["evening", "evenings", "7", "19"]], None, False, None),
    "S6": ({"rule"}, [["tnt"]], None, False, None),
    "S7": ({"identity", "rule"}, [["biscuit", "dog"]], None, True, None),
    "S8": ({"access", "ownership"}, [["chest"], ["shared", "share", "take", "both"]], None, True, None),
    "S9": ({"possible_location"}, [["diamond", "diamonds"], ["lake"]], None, True, None),
    "S10": ({"preference"}, [["green"]], None, False, None),
}


def words(f):
    return set(re.findall(r"[a-z0-9_:]+|-?\d+", (f["subject"] + " " + f["object"]).lower()))


def matches(fid, f, world):
    preds, groups, coord, world_specific, subject = MAP[fid]
    if f["predicate"] not in preds or (subject and f["subject"] != subject):
        return False
    w = words(f)
    if any(not any(g.lower() in w or g.lower() in f["object"].lower() for g in grp) for grp in groups):
        return False
    if coord and coord not in f["object"]:
        return False
    if fid == "C3" and "spruce" in f["object"]:
        return False
    if (f["world_id"] == world) != world_specific or (not world_specific and f["world_id"] is not None):
        return False
    return fid != "S9" or f["confidence"] < 1


def check(name, facts, world, current, absent=(), superseded=(), job=None):
    out = {"check": name, "world": world, "misses": []}
    for fid in current:
        fid = fid.split(" ")[0]
        if fid == "J1":
            ok = any(f["subject"] == "job" and f["source"] == "observation" and f["source_ref"] == job for f in facts)
        else:
            ok = any(matches(fid, f, world) for f in facts)
        if not ok:
            out["misses"].append(f"{fid} not current")
    for fid in [x.split(" ")[0] for x in absent]:
        if fid in MAP and any(matches(fid, f, A) or matches(fid, f, B) for f in facts):
            out["misses"].append(f"{fid} present but must be absent")
        if fid == "J1" and any(f["subject"] == "job" for f in facts):
            out["misses"].append("J1 leaked into this world")
        if fid == "T2" and any("5 64 5" in f["object"] for f in facts):
            out["misses"].append("T2 (untrusted) stored")
        if fid == "T3" and any("999 64 999" in f["object"] for f in facts):
            out["misses"].append("T3 (sign text) stored")
        if fid == "T1" and any("0 64 0" == f["object"] for f in facts):
            out["misses"].append("T1 (question) stored")
    for fid in superseded:
        if any(matches(fid, f, world) for f in facts):
            out["misses"].append(f"{fid} still current")
    out["pass"] = not out["misses"]
    return out


async def say(e, text, sender=OWNER, trusted=True):
    n = e.mind.db.execute("SELECT count(*) FROM events WHERE kind IN ('memory.remember','owner.command')").fetchone()[0]
    await e.sense.owner_command(sender, trusted, "say", [text])      # sense's /eve say: one greedy-string arg
    await asyncio.sleep(0.3)
    for _ in range(1200):                      # owner text runs in the mind's background set; wait until it is done
        if not e.mind.background:
            break
        await asyncio.sleep(0.1)
    return n


async def run(out):
    tmp = out / "work"
    tmp.mkdir(parents=True, exist_ok=True)
    log, results, job = [], [], None
    owner_env = lambda w: envelope(world_id=w, owner={**envelope()["owner"], "trusted_players": [OWNER]})  # noqa: E731

    async def phase(world, fn):
        async with mind_env(tmp, env=owner_env(world)) as e:
            e.mind.extractor = extract_facts
            await until(lambda: e.mind.world_id == world, timeout=5)
            return await fn(e)

    async def p1_2(e):
        nonlocal job
        for s in FIX["statements"] + FIX["traps"][:2]:
            if s["id"] == "T2":
                await say(e, s["text"], sender=s["sender"], trusted=False)
            elif "text" in s:
                await say(e, s["text"])
            log.append({"id": s["id"], "facts": e.mind.memory.all(e.mind.world_id)[-3:]})
        # T3 is sign text: it reaches the mind only as sense data, never as owner chat (nothing to send)
        r = await e.mind.do("walk_to", {"pos": {"x": 2, "y": 64, "z": 2}}, wait=5)
        job = r["action_id"]
        e.mind.memory.journal(A, "Eve built a stone castle on the hill today.", [], generated_by="planner")

    async def recall(e, name, **kw):
        facts = e.mind.memory.all(e.mind.world_id)
        r = check(name, facts, e.mind.world_id, job=job, **kw)
        r["recall"] = facts
        r["history"] = e.mind.memory.history(e.mind.world_id)
        if any("castle" in h["text"] for h in r["history"]):
            r["misses"].append("T4 is confirmed history")
            r["pass"] = False
        results.append(r)

    async def p3(e):
        rc = FIX["recall_checks"]["R-A1 (world A, after restart, before corrections)"]
        await recall(e, "R-A1", current=rc["current"], absent=rc["absent"])

    async def p4(e):
        for c in FIX["corrections"]:
            await say(e, c["text"])
            log.append({"id": c["id"], "facts": e.mind.memory.all(e.mind.world_id)})
        rc = FIX["recall_checks"]["R-A2 (world A, after corrections)"]
        await recall(e, "R-A2", current=rc["current"], superseded=rc["superseded_not_current"])

    async def p5(e):
        rc = FIX["recall_checks"]["R-B (world B)"]
        await recall(e, "R-B", current=rc["current"], absent=rc["absent"])

    async def p6(e):
        rc = FIX["recall_checks"]["R-A3 (back in world A, after another restart)"]
        await recall(e, "R-A3", current=rc["current"], absent=rc["absent"], superseded=rc["superseded_not_current"])

    await phase(A, p1_2)
    await phase(A, p3)          # restart (new mind, same DB)
    await phase(A, p4)
    await phase(B, p5)          # world identity B
    await phase(A, p6)          # back to A, another restart
    (out / "statements.json").write_text(json.dumps(log, indent=1, default=str), "utf-8")
    (out / "recalls.json").write_text(json.dumps(results, indent=1, default=str), "utf-8")
    summary = {"checks": {r["check"]: r["pass"] for r in results}, "misses": {r["check"]: r["misses"] for r in results},
               "verdict": "PASS" if all(r["pass"] for r in results) else "FAIL", "lane": "shark",
               "conditions": "fake links (fake_body/fake_sense), real extractor qwen3:8b on Shark's Ollama; restarts = "
                             "new mind on the same DB; world change = sense hello with world B"}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), "utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["verdict"] == "PASS" else 1


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    return asyncio.run(run(Path(a.out)))


if __name__ == "__main__":
    sys.exit(main())
