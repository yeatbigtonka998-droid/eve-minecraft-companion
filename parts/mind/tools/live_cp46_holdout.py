r"""CP-46 factual run on an eve-watch HELD-OUT scenario set (read in place, e.g. C:\eve-shark\holdout\cp46_set3.json).
Owner tone rating waived (S16). Frozen before the run (this file's commit):

RECORDS -> mind state (the setup vocabulary eve-watch writes per scenario in `records`):
  {"walking_to": [x,y,z]}                                  a walk in progress (fake body walking for 30 s)
  {"action": skill, "params": {...}, "status": "ok"|"failed"|"uncertain", "code"?, "note"?}   a finished action
       (dispatched through Mind.do on fake links with that outcome; uncertain = result lost, reconciled, not redone)
  {"fact": {subject, predicate, object, world_specific, certain}}   told by the owner (owner_chat)
  {"stopped_by": name, "minutes_ago": n}                    STOPPED by that owner n minutes ago (walks become parked)
  {"untrusted": {"sender", "text"}}                         an untrusted player's request, not obeyed
  {"objective_report": str}                                 a finished objective's report
MATCHING (eve-watch's rules): every must_say group satisfied (any alternative, case-insensitive substring), no
must_not_say string; expect_stopped true -> after the reply `status` shows STOPPED and nothing new is dispatched,
then the runner resumes after the scenario's last phase (recorded); false -> not STOPPED. restart:true -> asked again after a mind restart on the same
DB with the same checks. The reply is Eve's chat line (dialogue reply, question, or for a stop the owner sees no reply:
the check is the STOPPED state; must_say is then matched against the stop/resume record text).
    python tools\live_cp46_holdout.py --fixture <json> --out <evidence dir>
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

MIND = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(MIND / "tests"), str(MIND / "tools"), str(MIND / "src")]
from conftest import envelope, mind_env, until  # noqa: E402
from eve_mind import dialogue  # noqa: E402
from eve_mind.memory import extract_facts  # noqa: E402

A = "dev:a1a5b81d7d18"


async def apply(e, rec):
    m = e.mind
    if "walking_to" in rec:
        x, y, z = rec["walking_to"]
        e.body.walk_s = 30
        await m.do("walk_to", {"pos": {"x": x, "y": y, "z": z}})
        await asyncio.sleep(0.4)
    elif "action" in rec:
        status = rec.get("status", "ok")
        e.body.result_override = None if status in ("ok", "uncertain") else (status, rec.get("code"))
        e.body.result_note = rec.get("note")
        e.body.walk_s = 0.05
        r = await m.do(rec["action"], rec.get("params", {}), wait=5)
        e.body.result_override = e.body.result_note = None
        if status == "uncertain" and r.get("action_id"):
            m.db.execute("DELETE FROM facts WHERE subject='job' AND source_ref=?", (r["action_id"],))
            m.db.execute("UPDATE actions SET status='uncertain', code=?, reconciled=1, reconcile_note=? WHERE "
                         "action_id=?", (rec.get("code") or "no_result",
                                         rec.get("note") or "result lost; postcondition not observed; not redone",
                                         r["action_id"]))
        return r
    elif "fact" in rec:
        f = rec["fact"]
        certain = f.get("certain", True)
        m.memory.remember(A if f.get("world_specific") else None, f["subject"], f["predicate"], str(f["object"]),
                          "owner_chat", source_ref="owner:Hunter", confirmed=1 if certain else 0,
                          confidence=1.0 if certain else 0.5)
    elif "stopped_by" in rec:
        await m.stop(f"owner:{rec['stopped_by']}", "in-game /eve stop")
        m.set_meta({"control.stopped_ts": int((time.time() - 60 * rec.get("minutes_ago", 0)) * 1000)})
    elif "untrusted" in rec:
        u = rec["untrusted"]
        m.db.execute("INSERT INTO events (world_id, ts, source, kind, payload) VALUES (?,?,?,?,?)",
                     (A, int(time.time() * 1000) - 60000, "sense", "owner.command",
                      json.dumps({"sender": u["sender"], "trusted": False, "command": "say", "args": [u["text"]],
                                  "accepted_as_owner": False})))
    elif "objective_report" in rec:
        m.db.execute("INSERT INTO events (world_id, ts, source, kind, payload) VALUES (?,?,?,?,?)",
                     (A, int(time.time() * 1000) - 60000, "mind", "objective",
                      json.dumps({"id": "x", "status": "", "report": rec["objective_report"]})))
    else:
        raise ValueError(f"unknown record {rec}")


def matches(text, must_say, must_not_say):
    t = (text or "").lower()
    missing = [g for g in must_say if not any(alt.lower() in t for alt in (g if isinstance(g, list) else [g]))]
    bad = [s for s in must_not_say if s.lower() in t]
    return not missing and not bad, {"missing": missing, "forbidden": bad}


async def one(sc, tmp, phase, log, last=True):
    async with mind_env(tmp, env=envelope(world_id=A, owner={**envelope()["owner"], "trusted_players": ["Hunter"]})) as e:
        e.mind.talker, e.mind.extractor = dialogue.answer, extract_facts
        e.mind.intent_fn = "model"          # O30: the constrained intent choice (live runs opt in)
        await until(lambda: e.mind.fresh_state() is not None, timeout=5)
        if phase == "first":
            for rec in sc.get("records", []):
                await apply(e, rec)
        sent0 = sum(e.body.requests.values())
        r = await e.mind.owner_text(sc["line"], "Hunter", source="owner")
        await asyncio.sleep(1.0)
        reply = r.get("reply") or r.get("question") or ""
        stopped = e.mind.stopped
        text = reply or json.dumps(r)
        ok, why = matches(text, sc.get("must_say", []), sc.get("must_not_say", []))
        if "expect_stopped" in sc:
            want = bool(sc["expect_stopped"])
            ok = ok and stopped == want and (not want or sum(e.body.requests.values()) == sent0)
            why["stopped"] = stopped
        resumed = None
        if stopped and last:                 # resume after the scenario's LAST phase (a restart phase must still
            resumed = (await e.mind.resume("cli")).get("ok")      # find her STOPPED); recorded
        log.append({"id": sc["id"], "phase": phase, "line": sc["line"], "pass": ok, "why": why, "reply": reply,
                    "result": {k: v for k, v in r.items() if k not in ("context",)}, "stopped": stopped,
                    "resumed_after": resumed, "source": r.get("source")})
        print(f"{'PASS' if ok else 'FAIL'} {sc['id']} {phase}: {str(reply or r)[:170]}", flush=True)
        return ok


async def run(fix_path, out):
    fix = json.loads(Path(fix_path).read_text("utf-8"))
    scen = (fix.get("scenarios") or fix["cases"]) if isinstance(fix, dict) else fix   # stop suite uses "cases"
    log = []
    for sc in scen:
        tmp = out / "work" / str(sc["id"])
        tmp.mkdir(parents=True, exist_ok=True)
        await one(sc, tmp, "first", log, last=not sc.get("restart"))
        if sc.get("restart"):
            await one(sc, tmp, "after_restart", log)
    (out / "replies.json").write_text(json.dumps(log, indent=1, default=str), "utf-8")
    first = [r for r in log if r["phase"] == "first"]
    again = [r for r in log if r["phase"] == "after_restart"]
    summary = {"fixture": str(fix_path), "first_pass": f"{sum(r['pass'] for r in first)}/{len(first)}",
               "after_restart": f"{sum(r['pass'] for r in again)}/{len(again)}", "owner_rating": "waived (S16)",
               "verdict": "PASS" if log and all(r["pass"] for r in log) else "FAIL", "lane": "shark",
               "conditions": "fake links; real qwen3:8b on Shark for facts/dialogue; records interpreted as in this "
                             "file's docstring (frozen before the run); eve-watch's matching rules"}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), "utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["verdict"] == "PASS" else 1


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    return asyncio.run(run(a.fixture, Path(a.out)))


if __name__ == "__main__":
    sys.exit(main())
