"""The decision gate (S16): with no owner review, validation + the critic decide whether Eve builds her own design.
Owner-supplied schematics with a stated position skip design and critique; only buildability gates them.

decide(folder) -> gate.json {design_hash, category, decision: build | hold | reject | n/a, reasons[], checks{}, policy}
  n/a    — category "schematic" (O61): an owner-provided schematic checkpoint; its checks are reported, not gating;
  build  — every check passed;
  hold   — something is missing that could still arrive (no brief/site, no qualified critic, critic unavailable);
  reject — the design failed (validation failed, or the critic left it below the threshold after the revision budget,
           or its build_order.json has a stand that today's replay finds no walk to or back from: plan_fresh, O94ag-b).
Every artefact must carry the same design hash, and the critique must be of exactly the previews in the folder.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .critic_check import SELFTEST_DIR, qualification_file
from .grid import Design

GATE_VERSION = "eve-gate/1"
DEFAULT_POLICY = {"critics": ["deepseek"], "threshold": 7.0, "require_brief_checks": True,
                  "allow_validation_only": False, "selftest_dir": str(SELFTEST_DIR)}
OWNER_GATING = ("stable", "buildable", "within_limits", "affordable")


def _load(p: Path):
    return json.loads(p.read_text()) if p.exists() else None


def decide(folder: Path, policy: dict | None = None) -> dict:
    folder = Path(folder)
    pol = {**DEFAULT_POLICY, **(policy or {})}
    checks, holds, rejects = {}, [], []

    def check(name, ok, why, kind="reject"):
        checks[name] = {"ok": bool(ok), **({} if ok else {"reason": why})}
        if not ok:
            (holds if kind == "hold" else rejects).append(f"{name}: {why}")

    try:
        g = Design.load(folder / "design.json")              # re-verifies the stored hash
    except (OSError, ValueError) as e:
        return {"version": GATE_VERSION, "decision": "reject", "reasons": [f"design.json: {e}"], "checks": {}}
    h = g.hash()
    prov = g.meta.get("provenance") or {}
    # O61: an owner-provided schematic checkpoint (CP-50's hut, test3) is outside the gate by category: decision n/a
    category = "schematic" if prov.get("schematic") else "owner_supplied" if prov.get("owner_supplied") else "eve_own"
    v, b = _load(folder / "validation.json"), _load(folder / "bom.json")
    render = _load(folder / "previews" / "render.json")
    s = Design.load(folder / "scaffold.json") if (folder / "scaffold.json").exists() else None
    check("validation_bound", v and v.get("design_hash") == h, "validation.json missing or for another design")
    check("bom_bound", b and b.get("design_hash") == h, "bom.json missing or for another design")
    check("scaffold_bound", s is None or (s.meta.get("provenance") or {}).get("scaffold_for") == h, "scaffold for another design")
    bo = _load(folder / "build_order.json")
    if bo is not None:      # O94ag-b: the plan is replayed afresh whenever the gate runs: a stale one never builds
        from . import gamedata
        from .placecheck import stale_stands
        stale = None if bo.get("design_hash") != h or (s is None and "scaffold" in {r[0] for r in bo["rows"]}) else stale_stands(
            g, gamedata.load(), s, [dict(zip(bo["columns"], r)) for r in bo["rows"]],
            (_load(folder / "brief.json") or {}).get("site"))
        check("plan_fresh", stale == [], "build_order.json is for another design, or its scaffold.json is missing"
              if stale is None else
              f"{len(stale)} planned stands fail today's replay (first: step {stale[0]['step']}, stand "
              f"{stale[0]['stand']}: {stale[0]['why']}); re-plan the folder" if stale else "")

    if category == "schematic":   # reported, not gating: no brief, no critic (O61)
        check("validation_passed", v and v.get("passed"), "not validated or failed")
        check("previews_bound", render and render.get("design_hash") == h, "previews missing or for another design")
    elif category == "owner_supplied":
        check("position_stated", prov.get("origin_stated") is True, "an owner schematic needs the stated position", "hold")
        for name in OWNER_GATING:
            st = (v or {}).get("checks", {}).get(name, {}).get("status")
            check(f"validation.{name}", st in ("pass", "skipped"),
                  "; ".join(f["reason"] for f in (v or {}).get("checks", {}).get(name, {}).get("failures", [])[:3]) or st, "hold")
    else:
        check("validation_passed", v and v.get("passed"),
              "; ".join(f"{k}: {c['failures'][0]['reason']}" for k, c in (v or {}).get("checks", {}).items()
                        if c.get("status") == "fail" and c.get("failures"))[:400] or "not validated")
        if pol["require_brief_checks"]:
            check("brief_checks_ran", v and not v.get("skipped"), f"skipped without a brief: {(v or {}).get('skipped')}", "hold")
        check("previews_bound", render and render.get("design_hash") == h, "previews missing or for another design")
        crit = _load(folder / "critique.json")
        if not crit or crit.get("status") == "not_critiqued":
            check("critiqued", pol["allow_validation_only"], "no critique (critic not configured or unavailable)", "hold")
        else:
            check("critique_bound", crit.get("design_hash") == h, "critique is of another design")
            best = next((r for r in crit.get("history", []) if r.get("design_hash") == h and r.get("scores")), None)
            critic = (best or {}).get("critic") or next((r["critic"] for r in crit.get("history", []) if r.get("critic")), {})
            check("critic_allowed", critic.get("name") in pol["critics"],
                  f"critic {critic.get('name')!r} may not gate real builds (allowed: {pol['critics']})", "hold")
            qual = _load(qualification_file(pol["selftest_dir"], critic.get("name"), critic.get("version")))
            check("critic_qualified", qual and qual.get("passed"),
                  f"no passing self-test for {critic.get('name')} {critic.get('version')} (critic_check)", "hold")
            shown = (best or {}).get("image_hashes", {})
            now = {p.stem: hashlib.sha256(p.read_bytes()).hexdigest() for p in (folder / "previews").glob("*.png")}
            check("critic_saw_these_previews", shown and all(now.get(k) == hv for k, hv in shown.items()),
                  "the critic's images are not the previews in this folder")
            bar = (qual or {}).get("bar", pol["threshold"])   # O34: the bar the critic's own self-test set
            pol = {**pol, "bar": bar, "selftest": {k: (qual or {}).get(k) for k in
                                                   ("passed", "caught", "faults", "repeat_max_diff", "base_scores", "ts")}}
            low = min(best["scores"].values()) if best else None
            check("critique_accepted", low is not None and low >= bar, f"best min score {low} < bar {bar}")
    decision = "n/a" if category == "schematic" else "reject" if rejects else "hold" if holds else "build"
    out = {"version": GATE_VERSION, "design_hash": h, "category": category, "decision": decision,
           "reasons": rejects + holds, "checks": checks, "policy": pol}
    (folder / "gate.json").write_text(json.dumps(out, indent=1) + "\n")
    return out
