r"""Planner speed/quality bench (eve-brain-speed, 2026-10-22). Replays owner lines through the mind's OWN code paths -
intent.classify (O30) then Planner.plan + validate_plan, in the mind's order - against one Ollama, per model@ctx, and
reports latency p50/p95, model reloads (Ollama's load_duration), VRAM, validity and whether the plan does what was
asked. Talks only to Ollama: no hub, no world, no DB. Fixture state = live_cp41.fixture (home from the envelope).
    .venv\Scripts\python.exe tools\planner_bench.py --url http://127.0.0.1:11434 \
        --configs qwen3:8b@4096,qwen3:8b@8192,qwen3:14b@8192 --out <file.json>
classify sends no num_ctx (the server default applies, as in the mind), so a planner ctx that differs from the server
default shows up as reloads - the point of running the 8b at both contexts.
"""
import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eve_mind import intent, planner  # noqa: E402
from eve_mind.config import strict_loads  # noqa: E402
from eve_mind.planner import Planner, validate_plan  # noqa: E402
from eve_mind.registry import Registry  # noqa: E402
from live_cp41 import fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
Q = "Q"   # an honest question / empty plan is the right answer
LOGS = lambda p: p.get("product") == "wood" or "log" in str(p.get("tag", "")) or str(p.get("block", "")).endswith("_log")  # noqa: E731
ANY = lambda p: True  # noqa: E731
AT = lambda x, y, z: lambda p: [p.get("pos", {}).get(a) for a in "xyz"] == [x, y, z]  # noqa: E731
# (line, {acceptable main skill: params check}); the 14 owner lines of 2026-10-22-live-owner-session.md, then the 12
OWNER = [("come to me", {Q: ANY}), ("come to me", {Q: ANY}), ("come to 5 100 -40", {"walk_to": AT(5, 100, -40)}),
         ("come to 5 100 -40", {"walk_to": AT(5, 100, -40)}), ("5 100 -40", {Q: ANY, "walk_to": AT(5, 100, -40)}),
         ("come to me", {Q: ANY}), ("-19 112 -74", {Q: ANY, "walk_to": AT(-19, 112, -74)}), ("come to me", {Q: ANY}),
         ("-19 112 -73", {Q: ANY, "walk_to": AT(-19, 112, -73)}), ("what do you have on you?", {Q: ANY}),
         ("you don't have bread on you?", {Q: ANY}), ("walk to -40 113 -58", {"walk_to": AT(-40, 113, -58)}),
         ("chop 8 logs", {"gather": LOGS, "mine_resource": LOGS}), ("chop 8 logs", {"gather": LOGS, "mine_resource": LOGS})]
COMMON = [("come to me", {Q: ANY}), ("follow me", {Q: ANY}), ("walk to -40 113 -58", {"walk_to": AT(-40, 113, -58)}),
          ("chop 8 logs", {"gather": LOGS, "mine_resource": LOGS}),
          ("mine 10 cobblestone", {"mine_resource": lambda p: "stone" in str(p.get("block", "")) + str(p.get("tag", ""))}),
          ("place a torch here", {Q: ANY}), ("open the chest", {Q: ANY}),
          ("craft a wooden pickaxe", {"craft": lambda p: p.get("item") == "minecraft:wooden_pickaxe"}),
          ("smelt the iron", {Q: ANY}), ("eat something", {"use_item": lambda p: "target_pos" not in p}),
          ("go home", {"go_home": ANY}), ("stop", {Q: ANY})]
# O30 intent each line should get (bare coordinates are left out: an answer, not an intent)
INTENT = {t: "command" for t, _ in OWNER + COMMON if t[0].isalpha() and not t.endswith("?")}
INTENT.update({"what do you have on you?": "records_question", "you don't have bread on you?": "records_question"})
PREP = {"walk_to", "look_at", "select_hotbar", "sneak", "jump", "equip_best"}

CALLS = []    # every Ollama reply's timing, appended by the wrapped _post below


def _wrap(mod):
    real = mod._post

    def post(url, body, timeout):
        raw = real(url, body, timeout)
        CALLS.append({"ctx": (body.get("options") or {}).get("num_ctx"), "load_ms": raw.get("load_duration", 0) / 1e6,
                      "total_ms": raw.get("total_duration", 0) / 1e6, "prompt_tokens": raw.get("prompt_eval_count"),
                      "out_tokens": raw.get("eval_count")})
        return raw
    mod._post = post


_wrap(planner)
_wrap(intent)


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())


def unload(url, model):
    req = urllib.request.Request(url + "/api/generate", json.dumps({"model": model, "keep_alive": 0}).encode(),
                                 {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()


def gpu_used_mib():
    try:
        return int(subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                  capture_output=True, text=True, timeout=10).stdout.split()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def judge(r, expect, reg, caps, env, st, text):
    """-> (names_ok, params_ok, errs, right): known skills, params pass the registry, validate_plan's reasons, and the plan
    does what was asked (its non-prep steps hit an expected skill with the right params, or an honest question)."""
    if not r["ok"]:
        return False, False, [r.get("error")], False
    steps = r["plan"].get("steps", [])
    names_ok = all(s.get("skill") in reg.skills for s in steps)
    params_ok = names_ok and not any(reg.validate(s["skill"], s.get("params")) for s in steps)
    errs = validate_plan(r["plan"], reg, caps, "supervised", env, st, request=text)
    main = [s for s in steps if s["skill"] not in PREP] or steps
    if not steps:
        right = Q in expect
    else:
        right = not errs and all(s["skill"] in expect and expect[s["skill"]](s.get("params") or {}) for s in main)
    return names_ok, params_ok, errs, right


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else None


async def run_config(url, model, ctx, reg, caps, env, st, timeout):
    os.environ["EVE_OLLAMA_URL"], os.environ["EVE_PLANNER_MODEL"] = url, model     # classify reads these
    m = SimpleNamespace(registry=reg)
    p = Planner(url=url, model=model, timeout_s=timeout, num_ctx=ctx)
    base = gpu_used_mib()
    CALLS.clear()
    t0 = time.monotonic()
    warm = await p.plan("say hello in chat", reg, caps, st, env)                   # cold load, not counted
    cold_ms = int((time.monotonic() - t0) * 1000)
    ps = [x for x in get(url + "/api/ps")["models"] if x["name"] == model]
    vram = {"size_vram_gb": round(ps[0]["size_vram"] / 1e9, 2), "size_gb": round(ps[0]["size"] / 1e9, 2),
            "loaded_ctx": ps[0].get("context_length"), "gpu_delta_mib": (gpu_used_mib() or 0) - (base or 0)} if ps else {}
    rows = []
    for group, lines in (("owner", OWNER), ("common", COMMON)):
        for text, expect in lines:
            m.__dict__.pop("_intents", None)                                       # no cache between lines
            CALLS.clear()
            t = time.monotonic()
            it = await intent.classify(m, text, timeout=timeout)
            c_ms = int((time.monotonic() - t) * 1000)
            c_load = sum(c["load_ms"] for c in CALLS)
            CALLS.clear()
            r = await p.plan(text, reg, caps, st, env)
            p_load = sum(c["load_ms"] for c in CALLS)
            names_ok, params_ok, errs, right = judge(r, expect, reg, caps, env, st, text)
            rows.append({"group": group, "text": text, "intent": it and it.get("intent"), "classify_ms": c_ms,
                         "classify_load_ms": round(c_load), "plan_ms": r.get("latency_ms"), "plan_load_ms": round(p_load),
                         "ok": r["ok"], "error": r.get("error"), "plan": r.get("plan"), "names_ok": names_ok,
                         "params_ok": params_ok, "valid": not errs, "errs": errs, "right": right,
                         "intent_right": None if text not in INTENT else (it or {}).get("intent") == INTENT[text]})
            print(f"  {model}@{ctx} {text!r}: plan {r.get('latency_ms')} ms (load {p_load:.0f}), classify {c_ms} ms "
                  f"(load {c_load:.0f}) right={right} {json.dumps(r.get('plan'))[:160]}", flush=True)
    unload(url, model)
    lat = [x["plan_ms"] for x in rows if x["plan_ms"] is not None]
    cl = [x["classify_ms"] for x in rows]
    return {"model": model, "ctx": ctx, "cold_ms": cold_ms, "warm_ok": warm["ok"], **vram,
            "plan_p50_ms": pct(lat, 50), "plan_p95_ms": pct(lat, 95), "classify_p50_ms": pct(cl, 50),
            "classify_p95_ms": pct(cl, 95), "reloads": sum(1 for x in rows if x["plan_load_ms"] > 500 or
                                                              x["classify_load_ms"] > 500),
            "n": len(rows), "replied": sum(x["ok"] for x in rows), "names_ok": sum(x["names_ok"] for x in rows),
            "params_ok": sum(x["params_ok"] for x in rows), "valid": sum(x["valid"] for x in rows),
            "right": sum(x["right"] for x in rows), "right_owner": sum(x["right"] for x in rows if x["group"] == "owner"),
            "right_common": sum(x["right"] for x in rows if x["group"] == "common"),
            "intent_right": sum(bool(x["intent_right"]) for x in rows),
            "intent_n": sum(x["intent_right"] is not None for x in rows), "rows": rows}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:11434")
    ap.add_argument("--configs", default="qwen3:8b@4096,qwen3:8b@8192")
    ap.add_argument("--envelope", default=str(ROOT / "config" / "envelope.json"))
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    reg = Registry(ROOT / "contracts" / "skills" / "registry.json")
    env = strict_loads(Path(a.envelope).read_text("utf-8"))
    st, _ = fixture(env)
    caps = [{"skill": n, "version": s["version"]} for n, s in reg.skills.items()]   # every registry skill advertised
    out = {"registry": reg.version, "prompt_version": planner.PROMPT_VERSION, "url": a.url,
           "ollama": get(a.url + "/api/version").get("version"), "results": []}
    for c in a.configs.split(","):
        model, ctx = c.rsplit("@", 1)
        print(f"== {model} @ {ctx}", flush=True)
        out["results"].append(asyncio.run(run_config(a.url.rstrip("/"), model, int(ctx), reg, caps, env, st, a.timeout)))
        Path(a.out).write_text(json.dumps(out, indent=1), "utf-8")
    print("| model@ctx | cold s | VRAM GB (on GPU/total) | plan p50/p95 ms | classify p50/p95 ms | reloads | valid | right "
          "(owner/common) | intent |")
    for r in out["results"]:
        print(f"| {r['model']}@{r['ctx']} | {r['cold_ms'] / 1000:.1f} | {r.get('size_vram_gb')}/{r.get('size_gb')} | "
              f"{r['plan_p50_ms']}/{r['plan_p95_ms']} | {r['classify_p50_ms']}/{r['classify_p95_ms']} | {r['reloads']} | "
              f"{r['valid']}/{r['n']} | {r['right']}/{r['n']} ({r['right_owner']}/14, {r['right_common']}/12) | {r['intent_right']}/{r['intent_n']} |")


if __name__ == "__main__":
    main()
