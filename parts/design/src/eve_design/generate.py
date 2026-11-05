"""Brief -> concepts -> programs -> compiled, validated design, with a bounded repair budget (CP-61) and a bounded
critique/revision loop that keeps the best valid candidate (CP-64).

Rules kept here, whatever the generator or critic says:
  * a program is only ever read by the restricted compiler; every candidate is recompiled and revalidated;
  * a candidate counts only if validation passes AND every requested room and feature is present (no silent
    simplification); exact compiler/validator problems are fed back for repair;
  * at most `repair_budget` repairs per concept and `rounds` revisions after the first critique (default 3);
  * the best valid candidate is kept with its full scores; anything below the threshold after the budget is
    "needs_review", never accepted by running out of rounds;
  * every candidate, rejected or not, is recorded with generator/critic name and version (candidates.json).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path

from . import bom as bom_mod
from . import buildorder, preview, scaffold
from .compiler import CompileError, LangError, compile_program, load_style
from .gamedata import GameData
from .models import CATEGORIES, CRITIC_VIEWS, PROGRAM_TRUNCATED, ModelError, SpendCapReached
from .schem_io import save_litematic
from .validate import validate

GENERATE_VERSION = "eve-generate/1"


def _sha(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode()).hexdigest()


def build_candidate(src: str, gd: GameData, brief: dict, project_id: str, version: int) -> dict:
    """Compile + BOM + validate + brief coverage. Returns a record; `valid` only if all of it holds. The buildable check
    (scaffold planning + placement simulation, slow) runs in _finish for the design that is about to be written."""
    rec = {"program_hash": _sha(src), "program": src, "valid": False, "errors": []}
    origin = tuple(brief.get("site", {}).get("origin", {}).get(k, d) for k, d in (("x", 0), ("y", 64), ("z", 0)))
    try:
        g = compile_program(src, gd, project_id=project_id, version=version, origin=origin,
                            plot=brief.get("site", {}).get("plot"))
    except (LangError, CompileError) as e:
        rec["errors"] = [f"compile: {e}"]
        return rec
    try:
        b = bom_mod.compute(g, gd, None)
    except bom_mod.BomError as e:
        rec["errors"] = [f"materials: {e}"]
        return rec
    v = validate(g, gd, brief=brief, bom=b, skip_buildable=True)
    errs = _fails(v)
    used = set(g.meta["provenance"].get("commands", []))
    errs += [f"brief: asks for a {f} and the program has none" for f in brief.get("features", []) if f not in used]
    if brief.get("storeys") and max((r.get("storey", 0) for r in g.rooms), default=0) + 1 < brief["storeys"]:
        errs.append(f"brief: asks for {brief['storeys']} storeys with rooms")
    rec.update(design_hash=g.hash(), errors=errs, valid=not errs, size=list(g.size), blocks=sum(g.counts().values()))
    rec["_built"] = (g, None, b, v)
    return rec


def _fails(v: dict) -> list[str]:
    return [f"{k}: {f['reason']}" + (f" at {f['pos']}" if f["pos"] else "") for k, c in v["checks"].items()
            if c["status"] == "fail" for f in c["failures"][:5]]


def _finish(rec: dict, gd: GameData, brief: dict) -> bool:
    """Plan the scaffold and run the full validation (buildable included) for a design about to be written. O87y: the
    plan gets its storey budget (scaffold.budget), at most what is left of rec["_allowance"] ([seconds]: the finishing
    plans of one brief share one scaffold.TIME_CAP, so a brief's worst case stays FINISH_TRIES x 600 s as before);
    rec["planning"] records storeys, budget, seconds and the O87i bar."""
    g, allowance = rec["_built"][0], rec.get("_allowance")
    seconds = min(scaffold.budget(g), allowance[0]) if allowance is not None else None
    rec["planning"] = report = {}
    t0 = time.monotonic()
    try:
        s, left = scaffold.plan(g, gd, (brief or {}).get("site"), seconds=seconds, report=report)
    except ValueError as e:                      # never let one candidate end the brief (45L attempt 5)
        rec.update(valid=False, errors=[f"buildable: no build order: {e}"])
        return False
    finally:
        if allowance is not None:
            allowance[0] -= time.monotonic() - t0
    b = bom_mod.compute(g, gd, s)
    v = validate(g, gd, brief=brief, bom=b, scaffold=s)
    rec["_built"] = (g, s, b, v)
    rec["scaffold_blocks"] = sum(s.counts().values()) if s else 0
    if not v["passed"]:   # a planner that ran out of work says so first (45L attempt 6, h06: hours, no answer)
        rec.update(valid=False, errors=[f"buildable: {x['reason']}" for x in left if x.get("budget")] + _fails(v))
    return v["passed"]


def _public(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def _render(g, gd, out: Path) -> dict[str, Path]:
    preview.render(g, gd, out)
    return {p.stem: p for p in sorted(out.glob("*.png"))}


def _critique(critic, brief, card, g, gd, work: Path, round_no: int | str) -> dict:
    imgs = _render(g, gd, work / f"round-{round_no}")
    out = critic.critique(brief, card, {k: v for k, v in imgs.items() if k in CRITIC_VIEWS}, design=g)
    out["image_hashes"] = {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in imgs.items()}
    return out


FINISH_TRIES = 3   # designs given the (slow) scaffold planning before giving up
# O87w: when the first valid design is under the bar, up to this many concepts' valid designs are critiqued before
# revising (45L attempt 8: ~US$0.0074 a critique; h03 and h10 would have had one more each)
CRITIQUED_CONCEPTS = 3
# O87 (45L attempt 7, h02: concept 2 answered the same program three times, 2.5 s each, no reasoning)
PROGRAM_REPEATED = "your last answer repeated the previous program unchanged and fails the same way: make the change the error names"


def _rank(scores: dict) -> tuple:
    return (min(scores.values()), sum(scores.values()) / len(scores))


def generate(brief: dict, gd: GameData, gen, critic=None, out: Path | None = None, project_id: str | None = None,
             version: int = 1, n_concepts: int = 3, repair_budget: int = 2, rounds: int = 3, threshold: float = 7.0) -> dict:
    """O87u: a provider call refused by the job's spend cap ends the brief where it is: status needs_review, reason
    cap, every candidate so far in candidates.json, no design folder (Hunter's money rule)."""
    candidates, more = [], []
    report = {"generator": {"name": gen.name, "version": gen.version}, "version": GENERATE_VERSION,
              "limits": {"repair_budget": repair_budget, "rounds": rounds, "threshold": threshold}}
    try:
        return _generate(brief, gd, gen, critic, out, project_id, version, n_concepts, repair_budget, rounds,
                         threshold, candidates, more, report)
    except SpendCapReached as e:
        done = candidates + [c for c in more if not any(c is d for d in candidates)]
        report.update(status="needs_review", reason="cap", error=f"cap: {e}", candidates=[_public(c) for c in done])
        _write_report(out, report)
        return report


def _generate(brief, gd, gen, critic, out, project_id, version, n_concepts, repair_budget, rounds, threshold,
              candidates, more, report) -> dict:
    pid = project_id or brief.get("project_id", "generated")
    card, card_hash = load_style(brief.get("style", "cozy_cabin"), 0)
    try:
        concepts = gen.concepts(brief, card, n_concepts)
    except ModelError as e:
        return {**report, "status": "model_unavailable", "error": str(e), "candidates": []}
    report["concepts"] = concepts
    valid = []
    for ci, concept in enumerate(concepts):
        prev = feedback = None
        for attempt in range(repair_budget + 1):
            try:
                src = gen.program(brief, concept, card, previous=prev, feedback=feedback)
            except ModelError as e:
                candidates.append({"concept": ci, "attempt": attempt, "valid": False, "errors": [f"model: {e}"]})
                if e.code != "truncated":
                    break
                feedback = [PROGRAM_TRUNCATED]   # 45L attempt 2: a repair attempt, not the end; the next call is tighter
                continue
            rec = build_candidate(src, gd, brief, pid, version)
            rec.update(concept=ci, attempt=attempt, stage="initial" if attempt == 0 else "repair")
            candidates.append(rec)
            if rec["valid"]:
                valid.append(rec)
                break
            prev, feedback = src, rec["errors"] + ([PROGRAM_REPEATED] if src == prev else [])
    if not valid:
        errs = Counter(e.split(" at ")[0] for c in candidates for e in c["errors"])
        report.update(status="no_valid_design", candidates=[_public(c) for c in candidates],
                      error="; ".join(f"{n}x {e}" for e, n in errs.most_common(3)))
        _write_report(out, report)
        return report

    best = valid[0]
    critique = {"status": "not_critiqued", "reason": "no critic configured (DECISIONS P10: critique stage blocked)"}
    if critic is not None:
        alts = [(v, concepts[v["concept"]]) for v in valid[1:CRITIQUED_CONCEPTS]]
        best, critique, _ = _revise(best, concepts[best["concept"]], brief, card, gd, gen, critic, pid, version,
                                    rounds, threshold, more, alts)
        candidates += more
    # the chosen design must also be buildable (scaffold + placement simulation); else the next best scored one
    ranked = [best] + sorted([c for c in valid + more if c.get("valid") and c is not best],
                             key=lambda c: _rank(c["scores"]) if c.get("scores") else (-1, -1), reverse=True)
    seen: set = set()                            # the same design twice is the same answer (O68: 3 x 16 min)
    ranked = [c for c in ranked if not (c["design_hash"] in seen or seen.add(c["design_hash"]))]
    if out is not None:                          # a brief stopped while planning stays reproducible (h06 twice)
        (out / "finishing").mkdir(parents=True, exist_ok=True)
        for c in ranked[:FINISH_TRIES]:
            (out / "finishing" / f"{c['design_hash'][:12]}.eve").write_bytes(c["program"].encode())
    allowance = [scaffold.TIME_CAP]             # O87y: one capped clock for this brief's finishing plans
    for c in ranked[:FINISH_TRIES]:
        c["_allowance"] = allowance
    chosen = next((c for c in ranked[:FINISH_TRIES] if allowance[0] > 0 and _finish(c, gd, brief)), None)
    if chosen is None:
        report.update(status="no_buildable_design", candidates=[_public(c) for c in candidates],
                      error="; ".join(ranked[0]["errors"][:3]))
        _write_report(out, report)
        return report
    if chosen is not best:
        if not chosen.get("scores") and critic is not None:   # O87 (45L h02): the critic sees a fallback before it is chosen
            work = Path(tempfile.mkdtemp(prefix="eve-critique-"))
            try:
                c = _critique(critic, brief, card, chosen["_built"][0], gd, work, 0)
                chosen["scores"], entry = c["scores"], {**c, "critic": {"name": critic.name, "version": critic.version}}
            except ModelError as e:
                entry = {"errors": [f"critic: {e}"]}
            finally:
                shutil.rmtree(work, ignore_errors=True)
            critique = {**critique, "history": critique.get("history", []) + [
                {"round": "fallback", "design_hash": chosen["design_hash"], "valid": True, **entry}]}
        sc = chosen.get("scores")
        critique = {**critique, "design_hash": chosen["design_hash"], "scores": sc,
                    "status": "accepted" if sc and min(sc.values()) >= threshold else "needs_review",
                    "note": "the best-scored design could not be built; this is the next best"}
        best = chosen
    report.update(status=critique.get("status", "not_critiqued"), chosen=best["program_hash"],
                  design_hash=best["design_hash"], planning=best.get("planning"),
                  candidates=[_public(c) for c in candidates])
    if out is not None:
        _write_folder(out, brief, best, critique, card_hash, gd)
        _write_report(out, report)
    report["critique"] = critique
    return report


def _revise(best, concept, brief, card, gd, gen, critic, pid, version, rounds, threshold, more=None, alts=()):
    """Critique `best`, then revise until the critic's bar. O87w: if the first design is under the bar, the other
    concepts' valid designs (`alts`: (candidate, concept)) are critiqued too, and the best-ranked one is revised."""
    work = Path(tempfile.mkdtemp(prefix="eve-critique-"))
    history, more = [], [] if more is None else more   # the caller's list: a spend cap mid-revision keeps them
    critic_id = {"name": critic.name, "version": critic.version}
    try:
        try:
            c0 = _critique(critic, brief, card, best["_built"][0], gd, work, 0)
        except ModelError as e:
            return best, {"status": "needs_review", "reason": f"critic unavailable: {e}", "history": []}, more
        best["scores"], fixes = c0["scores"], c0["fixes"]
        history.append({"round": 0, "design_hash": best["design_hash"], "valid": True, **c0, "critic": critic_id})
        seen, critic_down = {best["design_hash"]}, False
        for alt, alt_concept in alts:
            if min(best["scores"].values()) >= threshold:
                break                                     # one at the bar is enough: no more spent
            if alt["design_hash"] in seen:
                continue
            try:
                c = _critique(critic, brief, card, alt["_built"][0], gd, work, f"0-concept-{alt['concept']}")
            except ModelError as e:                       # the critic is down: no revision is paid for either
                history.append({"round": 0, "design_hash": alt["design_hash"], "valid": True, "concept": alt["concept"],
                                "errors": [f"critic: {e}"]})
                critic_down = True
                break
            seen.add(alt["design_hash"])
            alt["scores"] = c["scores"]
            history.append({"round": 0, "design_hash": alt["design_hash"], "valid": True, "concept": alt["concept"],
                            **c, "critic": critic_id})
            if _rank(alt["scores"]) > _rank(best["scores"]):
                best, concept, fixes = alt, alt_concept, c["fixes"]
        current = best
        for r in range(1, 0 if critic_down else rounds + 1):
            if min(current["scores"].values()) >= threshold and current is best:
                break
            src = None
            for note in ([], [PROGRAM_TRUNCATED]):   # O87: a cut-off revision gets one tighter try (45L h08 round 1)
                try:
                    src = gen.program(brief, concept, card, previous=current["program"], feedback=fixes + note)
                    break
                except ModelError as e:
                    history.append({"round": r, "valid": False, "errors": [f"generator: {e}"]})
                    if e.code != "truncated":
                        break
            if src is None:
                break
            rec = build_candidate(src, gd, brief, pid, version)
            rec.update(stage="revision", round=r)
            more.append(rec)
            if not rec["valid"]:
                history.append({"round": r, "design_hash": rec.get("design_hash"), "valid": False, "errors": rec["errors"]})
                fixes = rec["errors"]
                continue
            if rec["design_hash"] in seen:
                history.append({"round": r, "design_hash": rec["design_hash"], "valid": True, "oscillation": True})
                break
            seen.add(rec["design_hash"])
            try:
                c = _critique(critic, brief, card, rec["_built"][0], gd, work, r)
            except ModelError as e:
                history.append({"round": r, "design_hash": rec["design_hash"], "valid": True, "errors": [f"critic: {e}"]})
                break
            rec["scores"] = c["scores"]
            history.append({"round": r, "design_hash": rec["design_hash"], "valid": True, **c, "critic": critic_id})
            current, fixes = rec, c["fixes"]
            if _rank(rec["scores"]) > _rank(best["scores"]):
                best = rec
        ok = min(best["scores"].values()) >= threshold
        return best, {"design_hash": best["design_hash"], "categories": list(CATEGORIES), "threshold": threshold,
                      "max_rounds": rounds, "rounds_used": max(h["round"] for h in history),
                      "status": "accepted" if ok else "needs_review",
                      "best_round": next(h["round"] for h in history if h.get("design_hash") == best["design_hash"]),
                      "scores": best["scores"], "history": history}, more
    finally:
        shutil.rmtree(work, ignore_errors=True)


def recritique(out: Path, gd: GameData, critic, threshold: float = 7.0) -> dict:
    """O87: a finished folder's saved design (program.eve) rebuilt under today's checks (buildable included) and critiqued
    again by a self-tested critic; no generation spend. The folder is rewritten only when that all succeeds."""
    brief = json.loads((out / "brief.json").read_text())
    old = json.loads((out / "critique.json").read_text()) if (out / "critique.json").exists() else {}
    pid = brief.get("project_id") or out.parent.name
    card, card_hash = load_style(brief.get("style", "cozy_cabin"), 0)
    rec = build_candidate((out / "program.eve").read_text(encoding="utf-8"), gd, brief, pid, 1)
    status = "no_valid_design" if not rec["valid"] else "no_buildable_design" if not _finish(rec, gd, brief) else None
    if status is None:
        best, critique, _ = _revise(rec, None, brief, card, gd, None, critic, pid, 1, 0, threshold)   # 0 rounds: critique only
        if not critique.get("history"):
            status, rec["errors"] = "critic_unavailable", [critique.get("reason", "")]
    if status:
        return {"status": status, "error": "; ".join(rec["errors"][:3]), "candidates": [_public(rec)]}
    critique["recritique_of"] = {"design_hash": old.get("design_hash"), "status": old.get("status"), "scores": old.get("scores"),
                                 "critic": next((h["critic"] for h in old.get("history", []) if h.get("critic")), None)}
    _write_folder(out, brief, best, critique, card_hash, gd)
    return {"status": critique["status"], "design_hash": best["design_hash"], "critique": critique,
            "candidates": [_public(best)]}


def _write_folder(out: Path, brief, rec, critique, card_hash, gd):
    out.mkdir(parents=True, exist_ok=True)
    g, s, b, v = rec["_built"]
    (out / "brief.json").write_text(json.dumps(brief, indent=1) + "\n")
    (out / "program.eve").write_bytes(rec["program"].encode())
    h = g.save(out / "design.json")
    save_litematic(g, out / "design.litematic", gd, name=g.meta["project_id"], description=f"Eve {h[:12]}")
    if s:
        s.save(out / "scaffold.json")
    (out / "bom.json").write_text(json.dumps(b, indent=1) + "\n")
    (out / "validation.json").write_text(json.dumps(v, indent=1) + "\n")
    preview.render(g, gd, out / "previews")
    (out / "critique.json").write_text(json.dumps(critique, indent=1) + "\n")
    (out / "build_order.json").write_text(json.dumps(buildorder.compact(h, buildorder.plan_order(g, gd, s, site=brief.get("site"))[0]), separators=(",", ":")) + "\n")


def _write_report(out: Path | None, report: dict):
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "candidates.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
