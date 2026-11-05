"""Real-model design jobs (CP-61 / CP-63 / CP-64 / gate) — meant to run on the 45L where OPENROUTER_API_KEY, the
mind's eve.db (spend table) and the EVE_DEEPSEEK_* settings live (same as run-mind.ps1 sets for the mind).

    python tools/run_jobs.py --out <dir> [--only h01,h02] [--job-cap-usd 2] [--skip-selftest] [--fake] [--resume]
        [--brief-minutes 45]   # O68: a brief past its wall clock scores 0 (status timeout, where it was) and the run goes on
        [--recritique]         # O87: --only briefs keep their saved design: rebuilt, critiqued again, re-gated; no generation

Spend: every call is reserved through the mind's /cli first (EVE_LINK_TOKEN); mind down = no calls.
1. critic self-test (planted visible faults) with the real critic -> <out>/_critic/<name>--<version>.json
2. every held-out brief in jobs/heldout_briefs.json -> concepts -> programs -> compile -> validate -> previews ->
   critique/revise (<= 3 rounds) -> gate.json, all candidates recorded, folder <out>/<project_id>/v1
3. <out>/summary.json and <out>/RESULT.md (lane 45L, or shark for --fake)
O87u: --job-cap-usd is checked before every provider call (that call's worst case included), not only between briefs:
a call that could pass it is not sent, that brief ends as needs_review with reason cap, and the run stops there (the
monthly cap still applies on top).
--fake uses the deterministic fakes and no network (to prove the runner on Shark).
"""
import argparse
import ctypes
import json
import os
import platform
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from eve_design import critic_check, gamedata, gate, models
from eve_design.generate import generate, recritique

DESIGN = Path(__file__).resolve().parents[1]
REPO = DESIGN.parents[1]


class BriefTimeout(BaseException):
    """Raised in the main thread when a brief runs past its wall-clock budget (O68). BaseException: no `except
    Exception` on the way (model clients, the mind ledger) may swallow it."""


def watchdog(seconds: float):
    """Arm a timer that raises BriefTimeout in this (main) thread after `seconds`; returns disarm(). Pure-Python loops
    (the scaffold planner, the placement replay) are interrupted at their next bytecode.
    ponytail: async exception in-process (spend counters stay exact); a hang inside C code would need a child process."""
    main, lock, state = threading.main_thread().ident, threading.Lock(), {"done": False}

    def fire():
        with lock:
            if not state["done"]:
                ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(main), ctypes.py_object(BriefTimeout))
    t = threading.Timer(seconds, fire)
    t.daemon = True
    t.start()

    def disarm():                                # call as the last statement inside the guarded block
        with lock:
            state["done"] = True
        t.cancel()
    return disarm


def where(e: BaseException, n: int = 8) -> str:
    """The innermost frames the timeout interrupted: which loop a brief was stuck in."""
    fr = traceback.extract_tb(e.__traceback__)[-n:]
    return " < ".join(f"{Path(f.filename).stem}.{f.name}:{f.lineno}" for f in reversed(fr))


def _row_from_folder(f: Path, policy: dict) -> dict:
    """A summary row rebuilt from a brief's folder (candidates.json, critique.json, re-gated now): 45L attempts 5 and 6
    stopped before writing summary.json, so their finished briefs exist only as folders."""
    rep = json.loads((f / "candidates.json").read_text())
    crit = json.loads((f / "critique.json").read_text()) if (f / "critique.json").exists() else {}
    d = gate.decide(f, policy) if (f / "design.json").exists() and rep.get("reason") != "cap" \
        else {"decision": "none", "reasons": []}   # O87u: a capped brief's folder may hold an older run's design
    return {"project_id": f.parent.name, "status": rep.get("status"), "error": rep.get("error"),
            **({"reason": "cap"} if rep.get("reason") == "cap" else {}),
            "design_hash": rep.get("design_hash"), "candidates": len(rep.get("candidates", [])),
            "valid_candidates": sum(1 for c in rep.get("candidates", []) if c.get("valid")),
            "critique": crit.get("status"), "rounds_used": crit.get("rounds_used"), "best_scores": crit.get("scores"),
            "gate": d["decision"], "gate_reasons": d["reasons"][:4], "seconds": None, "regated": True, "from_folder": True}


def with_site(brief: dict, tpl: dict) -> dict:
    p = tpl["plot"]
    w, d = p["max"]["x"] - p["min"]["x"] + 1, p["max"]["z"] - p["min"]["z"] + 1
    site = {"plot": p, "height_limit": tpl["height_limit"], "origin": tpl["origin"], "terrain_change": tpl["terrain_change"],
            "terrain": {"origin": {"x": p["min"]["x"], "z": p["min"]["z"]}, "heights": [[tpl["terrain_flat_y"]] * w for _ in range(d)]}}
    return {**brief, "site": site}




def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only")
    ap.add_argument("--job-cap-usd", type=float, default=2.0)
    ap.add_argument("--skip-selftest", action="store_true")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--selftest-probe", metavar="NAMES",
                    help="only the probe: one 1-sample critique per name (base, wall_hole, ... or all), usage to <out>/probe.json")
    ap.add_argument("--resume", action="store_true",
                    help="keep the briefs already in <out>/summary.json that this run does not redo (re-gated now)")
    ap.add_argument("--brief-minutes", type=float, default=45.0,   # healthy 45L briefs took up to 29 min (attempt 3 h08)
                    help="wall-clock budget per brief; past it the brief scores 0 (status timeout) and the run goes on")
    ap.add_argument("--recritique", action="store_true",
                    help="re-check and re-critique the saved design in each --only folder (no generation spend); with --resume")
    a = ap.parse_args(argv)
    gd = gamedata.load()
    jobs = json.loads((DESIGN / "jobs" / "heldout_briefs.json").read_text())
    briefs = [with_site(b, jobs["site_template"]) for b in jobs["briefs"]
              if not a.only or b["project_id"].split("-")[0] in a.only.split(",") or b["project_id"] in a.only.split(",")]
    out = a.out
    out.mkdir(parents=True, exist_ok=True)
    qual_dir = out / "_critic"
    if a.fake:
        gen, critic, clients = models.FakeGenerator(), models.FakeCritic(), []
        policy = {"critics": ["fake"], "selftest_dir": str(qual_dir)}
    else:
        gen, critic = models.DeepSeekGenerator(), models.DeepSeekCritic()
        for c in (gen.client, critic.client):
            if (why := c.why_disabled()):
                print(f"refusing to start: {c.model or '(no model id)'}: {why}", file=sys.stderr)
                return 2
        clients = [gen.client, critic.client]
        policy = {"critics": ["deepseek"], "selftest_dir": str(qual_dir)}
    cap = models.JobCap(a.job_cap_usd, clients)          # O87u: one cap over both clients, checked before every call
    for c in clients:
        c.job_cap = cap
    spent = cap.spent                                    # reserved worst cases, then settled costs
    if a.selftest_probe:
        try:
            r = critic_check.probe(critic, gd, a.selftest_probe.split(","), out)
        except models.SpendCapReached as e:
            print(f"stopped: job spend cap US${a.job_cap_usd:g}: {e}", file=sys.stderr)
            return 3
        for name, res in r["results"].items():
            print(name, res.get("error") or res["scores"], [(c["finish_reason"], c["completion_tokens"],
                                                             c["reasoning_tokens"], c["usd"]) for c in res["calls"]])
        return 0
    t0 = time.time()
    summary = {"lane": "shark" if a.fake else "45L", "started": time.strftime("%Y-%m-%d %H:%M:%S%z"), "fake": a.fake,
               "generator": {"name": gen.name, "version": gen.version}, "critic": {"name": critic.name, "version": critic.version},
               "job_cap_usd": a.job_cap_usd, "selftest": None, "briefs": []}

    def over_cap():
        return spent() > a.job_cap_usd

    bar = None
    if not a.skip_selftest:
        try:
            r = critic_check.run(critic, gd, qual_dir)
        except models.SpendCapReached as e:          # O87u: the self-test alone reached the cap
            r = {"passed": False, "error": f"cap: {e}"}
            summary["stopped"] = f"job spend cap US${a.job_cap_usd:g} reached in the critic self-test"
        summary["selftest"] = {k: r.get(k) for k in ("passed", "bar", "caught", "faults", "false_accepts", "repeat_max_diff", "error")}
        bar = r.get("bar")
    old = json.loads((out / "summary.json").read_text()) if a.resume and (out / "summary.json").exists() else None
    rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip() \
        or os.environ.get("EVE_CODE_REV", "unknown (no .git; set EVE_CODE_REV)")   # 45L runs from a git archive

    def save() -> dict:
        """summary.json + RESULT.md, rewritten after every brief so a stopped run keeps what it finished."""
        s = {**summary, "briefs": list(summary["briefs"])}
        if old:                                    # keep finished briefs; re-gate them under today's gate
            done = {b["project_id"] for b in s["briefs"]}
            for b in old.get("briefs", []):
                if b["project_id"] in done:
                    continue
                folder = out / b["project_id"] / "v1"
                if (folder / "design.json").exists() and b.get("reason") != "cap":   # a capped row wrote no design
                    d = gate.decide(folder, policy)
                    b = {**b, "gate": d["decision"], "gate_reasons": d["reasons"][:4], "regated": True}
                s["briefs"].append(b)
            s["resumed_from"] = {"started": old.get("started"), "code": old.get("code"),
                                 "job_spend_usd": old.get("job_spend_usd"), "selftest": old.get("selftest")}
        if a.resume:                               # finished folders with no row (a run stopped before its summary)
            have = {b["project_id"] for b in s["briefs"]}
            for c in sorted(out.glob("*/v1/candidates.json")):
                f = c.parent
                if f.parent.name not in have:
                    s["briefs"].append(_row_from_folder(f, policy))
        s["briefs"].sort(key=lambda b: b["project_id"])
        s.update(seconds=round(time.time() - t0, 1), job_spend_usd=round(spent(), 6), code=rev)
        (out / "summary.json").write_text(json.dumps(s, indent=1) + "\n")
        plan = lambda r: (lambda p: f"{p['seconds']:g}/{p['budget_s']:g} s, {p['storeys']} st, "   # noqa: E731
                                    f"{'met' if p['o87i_bar_met'] else 'missed'}" if p else "")(r.get("planning"))
        rows = "\n".join(f"| {r['project_id']} | {r['status']} | {r['valid_candidates']}/{r['candidates']} | {r['critique']} | "
                         f"{r['rounds_used']} | {r['gate']} | {plan(r)} |" for r in s["briefs"])
        (out / "RESULT.md").write_text(f"""# Design jobs ({'fake, ' if a.fake else ''}lane {s['lane']}) — {s['started']}

- Code `{rev}` on {platform.node()}; generator {gen.version}; critic {critic.version}
- Critic self-test: {json.dumps(s['selftest'])}
- Job spend: US${s['job_spend_usd']} (job cap US${a.job_cap_usd}; Eve's monthly cap applies on top); {s['seconds']} s
- Brief budget: {a.brief_minutes:g} min wall clock each (past it: status timeout, score 0, the run goes on)
{('- Stopped: ' + s['stopped']) if s.get('stopped') else ''}

| brief | status | valid/candidates | critique | rounds | gate | planning (O87y budget, O87i bar) |
|---|---|---|---|---|---|---|
{rows}

Held-out briefs: parts/design/jobs/heldout_briefs.json. Every candidate is in <folder>/candidates.json.
""", encoding="utf-8")
        return s

    for b in briefs:
        if summary.get("stopped"):
            break
        if over_cap():
            summary["stopped"] = f"job spend passed US${a.job_cap_usd}"
            break
        folder = out / b["project_id"] / "v1"
        t, n0 = time.time(), [len(c.calls) for c in clients]
        disarm = watchdog(a.brief_minutes * 60)
        try:
            thr = {"threshold": bar} if bar is not None else {}   # O34: revise until the critic's own bar
            try:
                r = recritique(folder, gd, critic, **thr) if a.recritique else \
                    generate(b, gd, gen, critic, out=folder, project_id=b["project_id"], **thr)
            except models.SpendCapReached as e:       # O87u (generate reports it itself; recritique does not)
                r = {"status": "needs_review", "reason": "cap", "error": f"cap: {e}", "candidates": []}
            disarm()
        except BriefTimeout as e:                 # O68: a hung brief scores 0 with the reason; the run goes on
            r = {"status": "timeout", "score": 0, "candidates": [],
                 "error": f"no result within the {a.brief_minutes:g}-minute brief budget; stopped in {where(e)}"}
        if clients:                               # O87 (45L h02): per call tokens, reasoning, finish and provider
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "calls.json").write_text(json.dumps([x for c, n in zip(clients, n0) for x in c.calls[n:]], indent=1) + "\n")
        d = gate.decide(folder, policy) if r["status"] in ("accepted", "needs_review", "not_critiqued") \
            and r.get("reason") != "cap" and (folder / "design.json").exists() \
            else {"decision": "none", "reasons": []}   # never an older run's folder (a capped brief writes none)
        summary["briefs"].append({"project_id": b["project_id"], "status": r["status"], "error": r.get("error"),
                                  **({"reason": r["reason"]} if r.get("reason") == "cap" else {}),
                                  **({"score": 0} if r["status"] == "timeout" else {}),
                                  "design_hash": r.get("design_hash"), "candidates": len(r.get("candidates", [])),
                                  "valid_candidates": sum(1 for c in r.get("candidates", []) if c.get("valid")),
                                  "critique": (r.get("critique") or {}).get("status"),
                                  "rounds_used": (r.get("critique") or {}).get("rounds_used"),
                                  "best_scores": (r.get("critique") or {}).get("scores"),
                                  "planning": r.get("planning"),   # O87y: storeys, budget, seconds, O87i bar
                                  "gate": d["decision"], "gate_reasons": d["reasons"][:4], "seconds": round(time.time() - t, 1)})
        print(f"{b['project_id']}: {r['status']} gate={d['decision']}" + (f" ({r['error']})" if r["status"] == "timeout"
                                                                          or r.get("reason") == "cap" else ""), flush=True)
        if r.get("reason") == "cap":                  # O87u: Hunter's money rule, no further brief starts
            summary["stopped"] = f"job spend cap US${a.job_cap_usd:g} reached in {b['project_id']}"
        save()
    s = save()
    print(json.dumps({k: s[k] for k in ("lane", "selftest", "job_spend_usd", "seconds")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
