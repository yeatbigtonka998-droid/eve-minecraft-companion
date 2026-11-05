r"""CP-44 [K] against an eve-watch fact fixture, read in place (set 1, training: parts/watch/laneb/cp44_fixture.json;
held-out set 2: C:\eve-shark\holdout\cp44_set2.json). The mind runs on fake links with the REAL fact extractor
(Ollama qwen3:8b). Phases: statements + chat traps -> a job + the generated journal row -> restart, recall 1 ->
corrections, recall 2 -> world B, recall 3 -> back to A + restart, recall 4.
    python tools\live_cp44.py --fixture <json> --out <evidence dir>

GENERIC MATCHER, frozen before the held-out run (built only from set 1's vocabulary):
an expected fact E is CURRENT when some current recalled fact F satisfies
  1. predicate: F.predicate in pred_set(E.predicate) (PRED_MAP for set 1's names, keyword rules for others);
  2. coordinates: every "x y z" in E.object appears in F.object exactly;
  3. item ids: every minecraft:<id> in E.object appears in F (the id itself, or all of its words in F.subject/object);
  4. scope: world-specific E -> F.world_id == the bound world; otherwise F.world_id is null;
  5. confidence: E "uncertain" <-> F.confidence < 1;
  6. meaning: a content word of E.subject/E.object occurs in F.subject/F.object. Candidates passing 1-5 but not 6
     are listed under "review" for a human meaning check and count as misses until judged in RESULT.md.
SUPERSEDED E (rev. 2026-10-20 evening, after set 2, before set 3): E is still current when a current F satisfies 1-6
  and F was created before the correction message that supersedes E was sent (status and taste corrections have no
  coordinates, so "satisfies 1-5" alone cannot tell the old fact from its correction). ABSENT statement E: no current F satisfies 1-3 and 5 with a world id.
TRAP sent as chat: that message adds no fact (per-message fact diff); a trap's coordinates appear in no fact.
TRAP sense data (sign/book): never sent as owner chat. GENERATED JOURNAL trap: its text never in history.
JOB: a current `job completed` fact from observation whose source_ref is the walk's action id.
"""
import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

MIND = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(MIND / "tests"), str(MIND / "tools"), str(MIND / "src")]
from conftest import envelope, mind_env, until  # noqa: E402
from eve_mind import dialogue  # noqa: E402
from eve_mind.memory import extract_facts  # noqa: E402

PRED_MAP = {"location": {"location"}, "possible_location": {"possible_location"},
            "preferred_build_materials": {"preference"}, "favourite_colour": {"preference"},
            "harvest_policy": {"rule", "access", "ownership"}, "usual_play_time": {"schedule"},
            "forbidden_item": {"rule"}, "is": {"identity", "rule"}, "container_policy": {"access", "ownership"}}
ALL = {"location", "possible_location", "preference", "rule", "schedule", "identity", "access", "ownership", "note",
       "status"}            # rev.: the mind's predicate list since the CP-44 rebuild (status added)
STOPW = {"the", "and", "for", "with", "owner", "owners", "eve", "its", "his", "her", "that", "this", "from", "only",
         "when", "not", "can", "may", "minecraft", "planks", "block", "blocks", "keep", "use", "any", "all"}
SENT = {}                    # message id -> ms it was sent (supersession: facts older than their correction)
COORD = re.compile(r"-?\d+ -?\d+ -?\d+")
IDS = re.compile(r"minecraft:[a-z0-9_]+")


def pred_set(p):
    if p in PRED_MAP:
        return PRED_MAP[p]
    rules = [("location", {"location", "possible_location"}), ("prefer", {"preference"}), ("favourite", {"preference"}),
             ("like", {"preference"}), ("policy", {"rule", "access", "ownership"}), ("rule", {"rule"}),
             ("forbid", {"rule"}), ("allow", {"rule", "access"}), ("never", {"rule"}), ("time", {"schedule"}),
             ("schedule", {"schedule"}), ("when", {"schedule"}), ("name", {"identity"}), ("own", {"ownership", "access"}),
             ("belong", {"ownership", "access"}), ("access", {"access", "ownership"})]
    out = set().union(*[v for k, v in rules if k in p]) if any(k in p for k, _ in rules) else set()
    return out or ALL


def text_of(o):
    return ", ".join(o) if isinstance(o, list) else str(o)


def words(s):
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]{3,}", s.lower())} - STOPW


def strict(E, F, world, scope=True):
    if F["predicate"] not in pred_set(E["predicate"]):
        return False
    eo, ft = text_of(E["object"]), (F["subject"] + " " + F["object"]).lower()
    if any(c not in F["object"] for c in COORD.findall(eo)):
        return False
    for i in IDS.findall(eo):
        if i not in ft and not all(w in ft for w in i.split(":", 1)[1].split("_") if w != "planks"):
            return False
    if scope and ((F["world_id"] == world) != bool(E.get("world_specific")) or
                  (not E.get("world_specific") and F["world_id"] is not None)):
        return False
    return (E.get("confidence") == "uncertain") == (F["confidence"] < 1)


def meaning(E, F):
    return bool(words(E["subject"] + " " + text_of(E["object"])) & words(F["subject"] + " " + F["object"]))


def evaluate(fix, name, facts, world, check, job, history, per_msg):
    exp = {s["id"]: s["fact"] for s in fix["statements"] + fix["corrections"] if "fact" in s}
    if isinstance(fix.get("job"), dict):
        exp.pop(fix["job"].get("id"), None)
    traps = {t["id"]: t for t in fix.get("traps", [])}
    out = {"check": name, "world": world, "misses": [], "review": []}
    for raw in check.get("current", []):
        fid = raw.split(" ")[0]
        if fid not in exp:
            if not any(f["subject"] == "job" and f["source"] == "observation" and f["source_ref"] == job for f in facts):
                out["misses"].append(f"{fid} (job) not current")
            continue
        cands = [F for F in facts if strict(exp[fid], F, world)]
        if not cands:
            out["misses"].append(f"{fid} not current")
        elif not any(meaning(exp[fid], F) for F in cands):
            out["review"].append({"id": fid, "expected": exp[fid], "candidates": cands})
            out["misses"].append(f"{fid} needs a meaning check")
    for raw in check.get("absent", []):
        fid = raw.split(" ")[0]
        if fid in exp:
            if any(strict(exp[fid], F, world, scope=False) and F["world_id"] is not None for F in facts):
                out["misses"].append(f"{fid} present but must be absent")
        elif fid in traps:
            t = traps[fid]
            if per_msg.get(fid):
                out["misses"].append(f"{fid} (trap) stored {per_msg[fid]}")
            if any(c in F["object"] for c in COORD.findall(t.get("text", "")) for F in facts):
                out["misses"].append(f"{fid} (trap) coordinates stored")
        elif any(f["subject"] == "job" for f in facts):
            out["misses"].append(f"{fid} (job) leaked into this world")
    for fid in check.get("superseded_not_current", []):
        by = next((c["id"] for c in fix["corrections"] if c.get("corrects") == fid), None)
        cut = SENT.get(by, 10 ** 15)
        if any(strict(exp[fid], F, world) and meaning(exp[fid], F) and F["created_ts"] < cut for F in facts):
            out["misses"].append(f"{fid} still current")
    for t in fix.get("traps", []):
        jt = journal_text(t)
        if jt and any(jt.lower()[:30] in h["text"].lower() for h in history):
            out["misses"].append(f"{t['id']} (generated journal) is confirmed history")
    out["pass"] = not out["misses"]
    return out


def journal_text(t):
    m = re.search(r"text '([^']+)'", t.get("do", ""))
    return m.group(1) if m else None


async def run(fix_path, out):
    fix = json.loads(Path(fix_path).read_text("utf-8"))
    A, B, owner = fix["worlds"]["A"], fix["worlds"]["B"], fix["owner"]
    tmp = out / "work"
    tmp.mkdir(parents=True, exist_ok=True)
    log, results, per_msg, state = [], [], {}, {"job": None}
    env_for = lambda w: envelope(world_id=w, owner={**envelope()["owner"], "trusted_players": [owner]})  # noqa: E731
    checks = list(fix["recall_checks"].items())          # in phase order: A1, A2, B, A3

    async def phase(world, fn):
        async with mind_env(tmp, env=env_for(world)) as e:
            e.mind.extractor, e.mind.talker = extract_facts, dialogue.answer
            e.mind.intent_fn = "model"          # O30: the production intent routing (fact vs command vs talk)
            await until(lambda: e.mind.world_id == world, timeout=5)
            return await fn(e)

    async def send(e, item):
        sender = item.get("sender") or owner
        if "text" not in item or re.search(r"\b(sign|book|relayed)\b", sender + " " + item.get("kind", "")):
            log.append({"id": item["id"], "sent": False, "why": "not owner chat (sense data / generated row)"})
            return
        before = {f["fact_id"] for f in e.mind.memory.all(e.mind.world_id)}
        SENT[item["id"]] = int(time.time() * 1000)
        await e.sense.owner_command(sender, sender == owner, "say", [item["text"]])
        await asyncio.sleep(0.3)
        for _ in range(1200):
            if not e.mind.background:
                break
            await asyncio.sleep(0.1)
        new = [f for f in e.mind.memory.all(e.mind.world_id) if f["fact_id"] not in before]
        per_msg[item["id"]] = [f"{f['subject']}|{f['predicate']}|{f['object']}" for f in new]
        log.append({"id": item["id"], "text": item["text"], "sender": sender, "new_facts": new})

    async def recall(e, i):
        name, check = checks[i]
        facts, hist = e.mind.memory.all(e.mind.world_id), e.mind.memory.history(e.mind.world_id)
        r = evaluate(fix, name, facts, e.mind.world_id, check, state["job"], hist, per_msg)
        r["recall"], r["history"] = facts, hist
        r["questions"] = await ask(e, check)      # after the facts are judged: what Eve SAYS when asked (gate rule)
        short = name.split(" ")[0]                # set 5 "dialogue_checks": the exact question, judged on the reply
        r["dialogue"] = [{"id": k, **(await ask_one(e, d["ask"])), "expect": d.get("expect")}
                         for k, d in fix.get("dialogue_checks", {}).items()
                         if isinstance(d, dict) and short in d.get("in", [])]
        results.append(r)

    async def ask(e, check):
        items = {x["id"]: x for x in fix["statements"] + fix["corrections"] + fix.get("traps", []) +
                 ([fix["job"]] if isinstance(fix.get("job"), dict) else [])}
        ids = [x.split(" ")[0] for k in ("current", "superseded_not_current", "absent") for x in check.get(k, [])]
        subs = dict.fromkeys(re.sub(r"\s*\(.*?\)|<.*?>", "", items[i]["fact"]["subject"]).split("/")[0].strip()
                             for i in ids if isinstance(items.get(i, {}).get("fact"), dict))
        return [await ask_one(e, "What do you know about the owner?" if sub == "owner" else
                              f"What do you know about the {sub}?") for sub in subs if sub]

    async def ask_one(e, q):
        """q as trusted owner chat (the /eve say path); every line Eve says until the mind is idle again."""
        said, orig = [], e.mind.say

        async def say(text):
            said.append(text)
            return await orig(text)
        e.mind.say = say
        try:
            await e.sense.owner_command(owner, True, "say", [q])
            await asyncio.sleep(0.3)
            for _ in range(1200):
                if not e.mind.background and said:
                    break
                await asyncio.sleep(0.1)
        finally:
            e.mind.say = orig
        return {"asked": q, "said": said}

    async def p1(e):
        for item in fix["statements"] + [t for t in fix.get("traps", []) if "text" in t]:
            await send(e, item)
        r = await e.mind.do("walk_to", {"pos": {"x": 2, "y": 64, "z": 2}}, wait=5)
        state["job"] = r["action_id"]
        for t in fix.get("traps", []):
            if journal_text(t):
                e.mind.memory.journal(A, journal_text(t), [], generated_by="planner")

    async def p4(e):
        for c in fix["corrections"]:
            await send(e, c)
        await recall(e, 1)

    await phase(A, p1)
    await phase(A, lambda e: recall(e, 0))
    await phase(A, p4)
    await phase(B, lambda e: recall(e, 2))
    await phase(A, lambda e: recall(e, 3))
    (out / "messages.json").write_text(json.dumps(log, indent=1, default=str), "utf-8")
    (out / "recalls.json").write_text(json.dumps(results, indent=1, default=str), "utf-8")
    summary = {"fixture": str(fix_path), "checks": {r["check"]: r["pass"] for r in results},
               "misses": {r["check"]: r["misses"] for r in results},
               "needs_meaning_check": sum(len(r["review"]) for r in results),
               "verdict": "PASS" if all(r["pass"] for r in results) else "FAIL", "lane": "shark",
               "conditions": "fake links, real extractor qwen3:8b on Shark; restarts = new mind on the same DB; world "
                             "change = sense hello with world B; generic matcher frozen in this file's docstring"}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), "utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["verdict"] == "PASS" else 1


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default=str(MIND.parents[1] / "parts" / "watch" / "laneb" / "cp44_fixture.json"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    return asyncio.run(run(a.fixture, Path(a.out)))


if __name__ == "__main__":
    sys.exit(main())
