"""O94q (CP-65): the owner's approval of one design version, the only way a hold-gated design (an organic piece, a
design no qualified critic judged) builds on the 45L. approval.json in the design folder is written only by the mind,
through the owner approval path, on Hunter's word (contracts/design-project.md, "Approval binding"); the builder and
the mind verify it with check() before building. Read-only: nothing here writes a file.

    python -m eve_design approval <folder> [--trusted Name,Name]   exit 0: may build; 3: may not (reasons printed)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .grid import Design

FORMAT = "eve-approval/1"
CHANNELS = ("eve_command", "mind_cli")   # `/eve approve <project_id> v<N>` in game; the mind's CLI on the 45L
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except ValueError:
        return {"_unreadable": True}


def check(folder: Path, trusted: list[str] | None = None) -> dict:
    """{may_build, design_hash, gate, approval: {present, valid, reasons[]}, reasons[]}. A folder may be built iff its
    gate.json (bound to the re-verified design hash) says build, or says hold and approval.json verifies: written for
    exactly this project, version and design hash, decision approved, by a trusted owner (`trusted`: the envelope's
    owner.trusted_players, when the caller has it) through an owner channel, with a time and the owner's own words,
    and validation passed. A reject, a schematic checkpoint (n/a) or an unbound file never builds this way."""
    folder = Path(folder)
    out = {"may_build": False, "design_hash": None, "gate": None, "approval": {"present": False, "valid": False,
                                                                              "reasons": []}, "reasons": []}
    try:
        g = Design.load(folder / "design.json")             # re-verifies the stored hash
    except (OSError, ValueError) as e:
        out["reasons"].append(f"design.json: {e}")
        return out
    h = out["design_hash"] = g.hash()
    gate = _load(folder / "gate.json") or {}
    out["gate"] = decision = gate.get("decision")
    if gate.get("design_hash") != h:
        out["reasons"].append("gate.json missing or for another design")
        return out
    if decision == "build":
        out["may_build"] = True
        return out
    if decision != "hold":
        out["reasons"].append(f"gate decision {decision!r}: only a hold can be approved (a reject never builds; a "
                              "schematic checkpoint is outside the gate, O61)")
        return out
    a = _load(folder / "approval.json")
    ap = out["approval"]
    if a is None:
        out["reasons"].append("gate says hold and there is no approval.json")
        return out
    ap["present"] = True
    why = ap["reasons"]
    if a.get("_unreadable") or not isinstance(a, dict):
        why.append("approval.json is not a JSON object")
    else:
        v = _load(folder / "validation.json") or {}
        meta = g.meta or {}
        for bad, reason in (
                (a.get("format") != FORMAT, f"format must be {FORMAT!r}"),
                (a.get("design_hash") != h, "design_hash is not this design's (any change is a new version)"),
                (a.get("project_id") != meta.get("project_id"), "project_id is not this design's"),
                (a.get("version") != meta.get("version"), "version is not this design's"),
                (a.get("decision") != "approved", "decision is not 'approved'"),
                (not isinstance(a.get("decided_by"), str) or not a["decided_by"].strip(), "decided_by (who) is missing"),
                (trusted is not None and a.get("decided_by") not in trusted,
                 f"decided_by {a.get('decided_by')!r} is not a trusted owner"),
                (a.get("channel") not in CHANNELS, f"channel must be one of {list(CHANNELS)}"),
                (not isinstance(a.get("ts"), int) or isinstance(a.get("ts"), bool) or a["ts"] <= 0,
                 "ts (when, epoch ms) is missing"),
                (not isinstance(a.get("text"), str) or not a["text"].strip(), "text (the owner's words) is missing"),
                (not isinstance(a.get("approval_id"), str) or not a["approval_id"].strip(), "approval_id is missing"),
                (a.get("site_hash") is not None and not HEX64.match(str(a.get("site_hash"))),
                 "site_hash must be null or a sha256"),
                (v.get("design_hash") != h or v.get("passed") is not True, "validation.json is not a pass for this design")):
            if bad:
                why.append(reason)
    ap["valid"] = not why
    out["may_build"] = ap["valid"]
    out["reasons"] += [f"approval.json: {r}" for r in why]
    return out
