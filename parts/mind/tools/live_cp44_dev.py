r"""CP-44 O42 cycle runner: eve-watch's scenario-format sets (dev: parts/watch/fixtures/cp44_dev.json; the 20-line gate
and hidden set 4 use the same format). Each scenario runs on a FRESH DB through the real owner path (sense owner.chat,
O30 intent, the fact extractor on the live qwen3:8b), with restarts = a new mind on the same DB and world switches = a
sense hello with the other world. The recall at the end (in the world current then) is judged with the frozen CP-44
matcher's rules (tools/live_cp44.py: predicate family, exact coordinates and ids, scope, confidence, content-word
meaning):
  current      some current fact matches it
  superseded   no current fact matches it unless that fact also matches one of the scenario's current expectations
               (the correction itself), i.e. the old value is gone
  absent       no current fact with a world id or owner source matches it, and no confirmed history mentions it
    python tools\live_cp44_dev.py --fixture <json> --out <dir> [--only M01,M02]
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
from live_cp44 import COORD, PRED_MAP, strict  # noqa: E402
from live_cp44 import meaning as words_meet  # noqa: E402

# the scenario sets' predicate names that would otherwise fall into the matcher's catch-all family
PRED_MAP.update({"status": {"status"}, "may_use": {"access", "rule", "ownership"}, "completed": {"completed"}})


def recall_questions(sc):
    """One recall question per subject the scenario expects something about (current, superseded or absent)."""
    subs = dict.fromkeys(re.sub(r"\s*\(.*?\)|<.*?>", "", x["subject"]).strip()
                         for k in ("current", "superseded", "absent") for x in sc["expect"].get(k, []))
    return [("What do you know about the owner?" if s == "owner" else f"What do you know about the {s}?") for s in subs if s]
NEG = re.compile(r"\b(?:not|no one|nobody|never|can'?t|cannot|no longer|anymore)\b", re.I)


def meaning(E, F):          # a one-letter name ("call me H") has no 3-letter word to share
    return words_meet(E, F) or str(E["object"]).strip().lower() == F["object"].strip().lower()


def as_e(x):
    return {"subject": x["subject"], "predicate": x["predicate"], "object": x["object"],
            "world_specific": x.get("scope", "world") == "world", "confidence": x.get("confidence", "certain")}


async def scenario(tmp, fix, sc):
    worlds, owner = fix["worlds"], fix["owner"]
    world = "A"
    state = {"history": [], "facts": [], "recall": []}

    def env_for(w):
        return envelope(world_id=worlds[w], owner={**envelope()["owner"], "trusted_players": [owner]})

    async def phase(steps, last=False):
        async with mind_env(tmp, env=env_for(world)) as e:
            m = e.mind
            m.talker, m.extractor, m.intent_fn = dialogue.answer, extract_facts, "model"
            await until(lambda: m.world_id == worlds[world], timeout=5)
            for st in steps:
                if "say" in st:
                    sender = st.get("sender", owner)
                    await e.sense.send("owner.chat", {"sender": sender, "trusted": sender == owner, "text": st["say"]})
                    await asyncio.sleep(0.3)
                    for _ in range(1200):
                        if not m.background:
                            break
                        await asyncio.sleep(0.1)
                elif "task" in st:
                    await m.do("walk_to", {"pos": {"x": 2, "y": 64, "z": 2}}, wait=5)
                elif "journal" in st:
                    j = st["journal"]
                    m.memory.journal(m.world_id, j["text"], j.get("grounded_in", []), generated_by=j.get("generated_by", "mind"))
            state["facts"] = m.memory.all(m.world_id)
            state["history"] = m.memory.history(m.world_id)
            if last:                       # the recall replies eve-watch reads, asked after the facts are taken
                orig = m.say
                for q in recall_questions(sc):
                    said = []

                    async def say(text, said=said):
                        said.append(text)
                        return await orig(text)
                    m.say = say
                    await e.sense.send("owner.chat", {"sender": owner, "trusted": True, "text": q})
                    await asyncio.sleep(0.3)
                    for _ in range(1200):
                        if not m.background and said:
                            break
                        await asyncio.sleep(0.1)
                    state["recall"].append({"asked": q, "said": said})

    chunk = []
    for st in sc["steps"]:
        if "restart" in st or "world" in st:
            await phase(chunk)
            chunk = []
            if "world" in st:
                world = st["world"]
            continue
        chunk.append(st)
    await phase(chunk, last=True)
    return state, worlds[world]


def judge(sc, facts, history, world_id, recall=()):
    exp, why = sc["expect"], []
    cur = [as_e(x) for x in exp.get("current", [])]
    for x, E in zip(exp.get("current", []), cur):
        if not any(strict(E, F, world_id) and meaning(E, F) for F in facts):
            why.append(f"not current: {x['subject']} {x['predicate']} {x['object']}")
    for x in exp.get("superseded", []):
        E = as_e(x)
        stale = [F for F in facts if strict(E, F, world_id) and meaning(E, F) and
                 bool(NEG.search(F["object"])) == bool(NEG.search(str(x["object"]))) and   # "not Priya" retracts it
                 not any(strict(C, F, world_id) and meaning(C, F) for C in cur)]
        if stale:
            why.append(f"still current: {x['subject']} {x['predicate']} {x['object']} as {stale[0]['object']!r}")
    for x in exp.get("absent", []):
        E = as_e(x)
        hit = [F for F in facts if strict(E, F, world_id, scope=False) and meaning(E, F)]
        words = [w for w in re.findall(r"[a-z]{5,}", (x["subject"] + " " + str(x["object"])).lower())]
        told = [h for h in history if any(w in h["text"].lower() for w in words)]
        if hit or told:
            seen = (f"{hit[0]['subject']}|{hit[0]['predicate']}|{hit[0]['object']}" if hit else
                    f"history: {told[0]['text'][:60]}")
            why.append(f"present: {x['subject']} {x['predicate']} {x['object']} ({seen})")
    return why + judge_recall(sc, recall)


def judge_recall(sc, recall):
    """eve-watch's gate 1 ruling: expect is checked on the recall too. A current value must be presented (its
    coordinates, else one of its words; an uncertain one as unsure); a superseded or absent value (its coordinates, or
    for absent its own words) must not be presented as true."""
    exp, why = sc["expect"], []
    said = {q["asked"]: " ".join(q["said"]).lower() for q in recall}
    every = " ".join(said.values())
    cur_text = " ".join(str(x["object"]) for x in exp.get("current", [])).lower()
    for x in exp.get("current", []):
        if x.get("source") == "observation":     # Eve's own task log, not something the owner told her
            continue
        q = next((a for a in said if base(x["subject"]) in a.lower() or
                  (base(x["subject"]) == "owner" and "owner" in a.lower())), None)
        r, o = said.get(q, every), str(x["object"]).lower()
        cs = COORD.findall(o)
        stem = lambda s: {w[:5] for w in re.findall(r"[a-z0-9]{3,}", s.replace("_", " ").replace("/", " "))}  # noqa
        ok = (all(c in r for c in cs) if cs else
              bool((stem(o) - {"the", "and", "not"}) & stem(r)) or bool(re.search(rf"\b{re.escape(o)}\b", r)))
        if not ok or (x.get("confidence") == "uncertain" and not re.search(r"not sure|weren't sure|unsure|maybe", r)):
            why.append(f"recall misses current {x['subject']}: {x['object']} (said {r[:90]!r})")
    for k in ("superseded", "absent"):
        for x in exp.get(k, []):
            o = str(x["object"]).lower()
            for c in COORD.findall(o):
                if c in every and c not in cur_text:
                    why.append(f"recall presents {k} {x['subject']}: {c}")
    return why


def base(subject):
    return re.sub(r"\s*\(.*?\)|<.*?>", "", subject).split("/")[0].strip().lower()


async def main_async(a):
    fix = json.loads(Path(a.fixture).read_text("utf-8"))
    only = set(a.only.split(",")) if a.only else None
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for sc in fix["scenarios"]:
        if only and sc["id"] not in only:
            continue
        state, wid = await scenario(out / "work" / sc["id"], fix, sc)
        why = judge(sc, state["facts"], state["history"], wid, state["recall"])
        rows.append({"id": sc["id"], "tag": sc.get("tag"), "pass": not why, "why": why, "recall": state["recall"],
                     "facts": [{k: f[k] for k in ("subject", "predicate", "object", "confidence", "source", "world_id")}
                               for f in state["facts"]]})
        print(("PASS" if not why else "FAIL"), sc["id"], sc.get("tag"), "; ".join(why)[:220], flush=True)
    ok = sum(r["pass"] for r in rows)
    summary = {"fixture": a.fixture, "passed": ok, "total": len(rows), "ts": int(time.time() * 1000)}
    (out / "replies.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
