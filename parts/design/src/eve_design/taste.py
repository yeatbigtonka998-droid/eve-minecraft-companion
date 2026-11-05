"""Eve's taste profile and design journal (CP-67 machinery; the owner waived human ratings, S16).

Fed ONLY by Eve's own critiques (critique.json) and build outcomes reported by the mind — every entry says which.
It is a generated preference, not Hunter's taste: `preferences()` labels itself that way. Entries are append-only;
`revert()` supersedes one (reversible). If Hunter ever does correct something, an entry with source "owner" outranks
Eve's own entries for the same feature.

    profile = designs/_taste/profile.json   {"entries": [...]}
"""
from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict
from pathlib import Path

from .gamedata import REPO
from .grid import Design

PROFILE = REPO / "designs" / "_taste" / "profile.json"
SOURCES = ("eve_critique", "build_outcome", "owner")
LABEL = "Eve's own generated preferences (from her critiques and build outcomes), not Hunter's ratings"


def _load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {"entries": []}


def _save(path: Path, prof: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prof, indent=1) + "\n")


def _append(path: Path, entry: dict) -> dict:
    if entry["source"] not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    prof = _load(path)
    entry = {"id": uuid.uuid4().hex[:12], "ts": int(time.time()), "superseded_by": None, **entry}
    prof["entries"].append(entry)
    _save(path, prof)
    return entry


def features_of(g: Design) -> list[str]:
    prov = g.meta.get("provenance") or {}
    return sorted(set(prov.get("commands", [])) | {f"detail:{r}" for r in prov.get("detailing_rules", [])} |
                  ({f"style:{g.meta['style']}"} if g.meta.get("style") else set()))


def record_critique(folder: Path, path: Path = PROFILE) -> dict | None:
    """One entry per critiqued design: its features and the critic's best scores."""
    folder = Path(folder)
    crit = json.loads((folder / "critique.json").read_text())
    if not crit.get("scores"):
        return None
    g = Design.load(folder / "design.json")
    critic = next((h.get("critic") for h in crit.get("history", []) if h.get("design_hash") == crit["design_hash"]
                   and h.get("critic")), None)        # not an entry whose critique failed (O87w review)
    return _append(path, {"source": "eve_critique", "design_hash": crit["design_hash"], "project_id": g.meta.get("project_id"),
                          "features": features_of(g), "scores": crit["scores"], "critic": critic,
                          "status": crit.get("status")})


def record_outcome(design_hash: str, outcome: dict, features: list[str], path: Path = PROFILE) -> dict:
    """outcome from the mind after a build: {built: bool, mismatches: int, rebuilt_cells: int, minutes: float, ...}."""
    return _append(path, {"source": "build_outcome", "design_hash": design_hash, "features": features, "outcome": outcome})


def record_owner(feature: str, verdict: str, note: str, path: Path = PROFILE) -> dict:
    """Only for something Hunter actually said; verdict 'prefer' or 'avoid'. Never generated."""
    return _append(path, {"source": "owner", "features": [feature], "verdict": verdict, "note": note})


def revert(entry_id: str, reason: str, path: Path = PROFILE) -> None:
    prof = _load(path)
    for e in prof["entries"]:
        if e["id"] == entry_id:
            e["superseded_by"] = {"reason": reason, "ts": int(time.time())}
    _save(path, prof)


def preferences(path: Path = PROFILE, recent: int = 3) -> dict:
    prof = [e for e in _load(path)["entries"] if not e.get("superseded_by")]
    score, built = defaultdict(list), defaultdict(list)
    owner = {}
    for e in prof:
        for f in e["features"]:
            if e["source"] == "eve_critique":
                score[f].append(sum(e["scores"].values()) / len(e["scores"]))
            elif e["source"] == "build_outcome":
                built[f].append(1.0 if e["outcome"].get("built") and not e["outcome"].get("mismatches") else 0.0)
            elif e["source"] == "owner":
                owner[f] = e["verdict"]
    feats = {}
    for f in sorted(set(score) | set(built) | set(owner)):
        feats[f] = {"mean_critic_score": round(sum(score[f]) / len(score[f]), 2) if score[f] else None,
                    "critiques": len(score[f]),
                    "clean_build_rate": round(sum(built[f]) / len(built[f]), 2) if built[f] else None,
                    "builds": len(built[f]), **({"owner": owner[f]} if f in owner else {})}
    designs = [e for e in prof if e["source"] == "eve_critique"]
    last = designs[-recent:]
    signature = sorted({f for e in last for f in e["features"] if not f.startswith(("style:", "detail:"))
                        and f in ("dormer", "courtyard", "wing", "chimney", "porch")})
    return {"label": LABEL, "features": feats, "avoid_repeating": signature,
            "note": "explicit requests in a brief always override avoid_repeating; owner entries outrank Eve's"}


def journal_note(folder: Path) -> dict:
    """A design_note for the mind's journal table, built only from recorded facts in the folder."""
    folder = Path(folder)
    g = Design.load(folder / "design.json")
    v = json.loads((folder / "validation.json").read_text())
    crit = json.loads((folder / "critique.json").read_text()) if (folder / "critique.json").exists() else {}
    gate = json.loads((folder / "gate.json").read_text()) if (folder / "gate.json").exists() else {}
    feats = ", ".join(f for f in features_of(g) if not f.startswith("style:")) or "plain shell"
    parts = [f"{g.meta.get('project_id')} v{g.meta.get('version')}: {sum(g.counts().values())} blocks, {len(g.rooms)} rooms, "
             f"style {g.meta.get('style')}, used {feats}.",
             f"Validation {'passed' if v['passed'] else 'failed'}.",
             f"Critique {crit.get('status', 'not run')}" + (f", best min score {min(crit['scores'].values())} after "
                                                             f"{crit.get('rounds_used', 0)} revision rounds." if crit.get("scores") else "."),
             f"Gate: {gate.get('decision', 'not run')}."]
    return {"kind": "design_note", "text": " ".join(parts), "generated_by": "eve_design.taste.journal_note",
            "grounded_in": [g.hash()] + ([crit["design_hash"]] if crit.get("design_hash") else [])}
