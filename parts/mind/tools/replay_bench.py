r"""Stage B replay harness (S105/O377, eve-brain-speed): a recorded owner line (+ what Eve just said or is doing) in,
the mind's understanding / plan out, scored against the expected intents, N repeats per line for run-to-run variance.
Provider-neutral: today it runs the local qwen planner; a hosted provider is scored on the SAME lines and state later.

    .venv\Scripts\python.exe tools\replay_bench.py --mind-src <worktree>\parts\mind\src \
        --fixture <worktree>\parts\mind\tests\fixtures\stageb_heldout_owner.jsonl --repeats 5 --out <file.json>

Paths scored per line:
  rules      grammar.steps(text) - today's deterministic understanding (no model; one run is enough)
  classify   intent.classify - the O30 model router: does it call an order an order and a question a question
  plan       Planner.plan + validate_plan - the model planner on the line as the mind would send it
  understand a CANDIDATE prompt asking the model for the corpus vocabulary itself ([{verb, object, place}]) with the
             context line - the contract a hosted brain would replace; vocabulary from grammar.py's O185 comment only
             (nothing from the held-out fixture)
Expected: the fixture's intents (mind-b, 6925805f). An action verb (anything but query/answer/report/chat) means
something should start; query/answer lines must not dispatch a world action.
"""
import argparse
import asyncio
import collections
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ap = argparse.ArgumentParser()
ap.add_argument("--mind-src", required=True, help="parts/mind/src of the mind rev under test")
ap.add_argument("--fixture", required=True)
ap.add_argument("--url", default="http://127.0.0.1:11434")
ap.add_argument("--model", default="qwen3:8b")
ap.add_argument("--ctx", type=int, default=4096, help="match the server's default ctx (planner_bench finding: else reloads)")
ap.add_argument("--repeats", type=int, default=5)
ap.add_argument("--paths", default="rules,classify,plan,understand")
ap.add_argument("--timeout", type=float, default=90.0)
ap.add_argument("--out", required=True)
A = ap.parse_args()
sys.path.insert(0, A.mind_src)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eve_mind import grammar, intent  # noqa: E402
from eve_mind.config import strict_loads  # noqa: E402
from eve_mind.planner import Planner, validate_plan, _post  # noqa: E402
from eve_mind.registry import Registry  # noqa: E402
from live_cp41 import fixture  # noqa: E402

ROOT = Path(A.mind_src).resolve().parents[2]
QUIET = {"query", "answer", "report", "chat"}              # verbs that must never start a world action
VERBS = ("mine craft place give deposit drop pickup equip fight go follow unfollow stop resume find ride build dig_down "
         "query report answer chat progress").split()
PLACES = ["owner", "near_eve", "front_of_eve", "nearby", "home", "surface", "water", "crafting_table", "chest",
          "near_owner", None]
UNDERSTAND = """You read ONE chat line from Hunter (the owner) to Eve, a Minecraft companion, and write what he wants as
an ordered list of steps in Eve's vocabulary. Do not plan how; only say what.
verbs: mine craft place give deposit drop pickup equip fight go follow unfollow stop resume find ride build dig_down
query report answer chat progress.
- stop = stop what she is doing now; go = move somewhere (place "owner" for "come to me/here"); follow = keep following.
- query = he asks a question (object = the topic, e.g. "progress:status"); answer = he answers HER question (object
  "yes" or "no"); chat = small talk.
- build objects look like "structure:shelter"; fight objects are a mob id ("minecraft:zombie") or "hostiles".
- Several orders in one line become several steps, in order. A question or an answer starts nothing.
What Eve just said or is doing (may be empty): {context}"""
SCHEMA = {"type": "object", "required": ["steps"], "properties": {"steps": {"type": "array", "maxItems": 4, "items": {
    "type": "object", "required": ["verb", "object", "place"], "properties": {
        "verb": {"enum": VERBS}, "object": {"type": ["string", "null"]}, "place": {"enum": PLACES}}}}}}


def norm(steps):
    return [(s.get("verb"), s.get("object")) for s in steps]


def starts(steps):
    return any(v not in QUIET for v, _ in steps)


def key(x):
    return hashlib.sha1(json.dumps(x, sort_keys=True).encode()).hexdigest()[:10]


def pct(xs, p):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else None


async def understand(text, context):
    body = {"model": A.model, "stream": False, "format": SCHEMA, "think": False, "keep_alive": "10m",
            "options": {"temperature": 0, "num_ctx": A.ctx},
            "messages": [{"role": "system", "content": UNDERSTAND.format(context=context or "")},
                         {"role": "user", "content": text}]}
    t = time.monotonic()
    raw = await asyncio.wait_for(asyncio.to_thread(_post, A.url + "/api/chat", body, A.timeout), A.timeout)
    return json.loads(raw["message"]["content"]).get("steps", []), int((time.monotonic() - t) * 1000)


async def main():
    os.environ["EVE_OLLAMA_URL"], os.environ["EVE_PLANNER_MODEL"] = A.url, A.model
    rows = [json.loads(l) for l in Path(A.fixture).read_text("utf-8").splitlines() if l.strip()]
    reg = Registry(ROOT / "contracts" / "skills" / "registry.json")
    env = strict_loads((ROOT / "config" / ("envelope.json" if (ROOT / "config" / "envelope.json").exists()
                                           else "envelope.example.json")).read_text("utf-8"))
    st, _ = fixture(env)
    caps = [{"skill": n, "version": s["version"]} for n, s in reg.skills.items()]
    planner = Planner(url=A.url, model=A.model, timeout_s=A.timeout, num_ctx=A.ctx)
    paths = A.paths.split(",")
    out = {"model": A.model, "ctx": A.ctx, "repeats": A.repeats, "fixture": A.fixture, "lines": []}
    for r in rows:
        exp = norm(r["intents"])
        line = {"id": r["id"], "text": r["text"], "expect": exp, "starts": starts(exp)}
        if "rules" in paths:
            got = norm(grammar.steps(r["text"]))
            line["rules"] = {"got": got, "exact": got == exp, "verbs_ok": [v for v, _ in got] == [v for v, _ in exp],
                             "safe": starts(got) == starts(exp) or not starts(got)}
        for p in ("classify", "plan", "understand"):
            if p not in paths:
                continue
            runs = []
            for _ in range(A.repeats):
                try:
                    if p == "classify":
                        m = SimpleNamespace(registry=reg)
                        t = time.monotonic()
                        it = await intent.classify(m, r["text"], timeout=A.timeout)
                        runs.append({"out": it and it.get("intent"), "ms": int((time.monotonic() - t) * 1000)})
                    elif p == "plan":
                        res = await planner.plan(r["text"], reg, caps, st, env)
                        plan = res.get("plan") or {}
                        errs = validate_plan(plan, reg, caps, "supervised", env, st, request=r["text"]) if res["ok"] else [res.get("error")]
                        runs.append({"out": [s["skill"] for s in plan.get("steps", [])], "question": plan.get("question"),
                                     "valid": res["ok"] and not errs, "ms": res.get("latency_ms")})
                    else:
                        steps, ms = await understand(r["text"], r.get("context"))
                        runs.append({"out": norm(steps), "ms": ms})
                except Exception as e:                      # a failed call is a scored outcome, not a crash
                    runs.append({"out": None, "error": f"{type(e).__name__}: {e}"[:160], "ms": None})
            outs = collections.Counter(key(x["out"]) for x in runs)
            line[p] = {"runs": runs, "distinct": len(outs), "agree": max(outs.values()) / len(runs)}
            if p == "classify":
                want = "command" if line["starts"] else None
                line[p]["right"] = sum((x["out"] == "command") == bool(want) for x in runs) / len(runs)
            elif p == "plan":
                line[p]["valid"] = sum(x["valid"] for x in runs) / len(runs)
                line[p]["acts"] = sum(bool(x["out"]) for x in runs) / len(runs)
                line[p]["needless_q"] = sum(line["starts"] and not x["out"] for x in runs) / len(runs)
                line[p]["acts_on_quiet"] = sum((not line["starts"]) and bool(x["out"]) for x in runs) / len(runs)
            else:
                line[p]["exact"] = sum(x["out"] == exp for x in runs) / len(runs)
                line[p]["verbs_ok"] = sum(x["out"] is not None and [v for v, _ in x["out"]] == [v for v, _ in exp]
                                          for x in runs) / len(runs)
                line[p]["safe"] = sum(x["out"] is not None and (not starts(x["out"]) or line["starts"]) for x in runs) / len(runs)
        out["lines"].append(line)
        print(r["id"], repr(r["text"])[:40], {p: {k: v for k, v in line[p].items() if k not in ("runs", "got")}
                                              for p in paths if p in line}, flush=True)
        Path(A.out).write_text(json.dumps(out, indent=1), "utf-8")
    L = out["lines"]
    summ = {"n": len(L)}
    if "rules" in paths:
        summ["rules"] = {k: sum(l["rules"][k] for l in L) for k in ("exact", "verbs_ok", "safe")}
    for p in ("classify", "plan", "understand"):
        if p not in paths:
            continue
        ms = [x["ms"] for l in L for x in l[p]["runs"]]
        s = {"p50_ms": pct(ms, 50), "p95_ms": pct(ms, 95), "lines_unstable": sum(l[p]["distinct"] > 1 for l in L),
             "mean_agree": round(sum(l[p]["agree"] for l in L) / len(L), 3),
             "errors": sum(1 for l in L for x in l[p]["runs"] if x.get("error"))}
        for k in {"classify": ["right"], "plan": ["valid", "acts", "needless_q", "acts_on_quiet"],
                  "understand": ["exact", "verbs_ok", "safe"]}[p]:
            s[k] = round(sum(l[p][k] for l in L), 2)
        summ[p] = s
    out["summary"] = summ
    Path(A.out).write_text(json.dumps(out, indent=1), "utf-8")
    print(json.dumps(summ, indent=1))


asyncio.run(main())
