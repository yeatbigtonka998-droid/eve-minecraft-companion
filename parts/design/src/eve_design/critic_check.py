"""Critic self-test (S16: no human calibration). A critic may gate Eve's designs only after it has caught every
deliberately severe visible fault in this suite and scored the same images consistently.

Faults are planted in the exact grid of a known-good design and rendered with the same renderer the pipeline uses:
no roof, a hole in a wall, a clashing palette, a floating junk cube, a griefed (35 % removed) building, a plain stone
box. Ruling O34: the critic's bar is the lowest score it gives the hand-built reference (cabin_detailed); a fault counts
as caught when its lowest category score is at least 1 below that bar, and the same images (median of 3) must re-score
within 1.0. Results, bar included, are written to qualification_file(dir, name, version); eve_design.gate reads them.
"""
from __future__ import annotations

import json
import random
import tempfile
import time
from pathlib import Path

from . import preview
from .compiler import compile_file, load_style
from .gamedata import GameData, REPO
from .grid import AIR, Design
from .models import CATEGORIES, CRITIC_VIEWS as VIEWS, ModelError

SELFTEST_DIR = REPO / "designs" / "_critic"
EXAMPLES = REPO / "parts" / "design" / "examples"


def qualification_file(out_dir: Path, name: str, version: str) -> Path:
    safe = lambda t: "".join(c if c.isalnum() or c in "-._" else "_" for c in str(t))
    return Path(out_dir) / f"{safe(name)}--{safe(version)}.json"


CONCRETE = ["red", "lime", "magenta", "yellow", "light_blue", "orange", "purple", "black"]


def _copy(g: Design) -> Design:
    return Design.from_json(g.to_json())


def _exposed_wall_strip(d: Design, width: int) -> list:
    """Wall/window cells seen first from the side where most of them are, in a strip `width` wide around its middle
    (doors kept)."""
    sx, sy, sz = d.size
    sides = {"S": (sx, lambda u, y: [(u, y, z) for z in range(sz - 1, -1, -1)]),
             "N": (sx, lambda u, y: [(u, y, z) for z in range(sz)]),
             "E": (sz, lambda u, y: [(x, y, u) for x in range(sx - 1, -1, -1)]),
             "W": (sz, lambda u, y: [(x, y, u) for x in range(sx)])}
    best = []
    for n, (span, ray) in sides.items():
        seen = []
        for u in range(span):
            for y in range(sy):
                first = next((c for c in ray(u, y) if d.get(*c) != AIR), None)
                if first and d.role_at(*first) in ("wall", "window"):
                    seen.append((u, first))
        if len(seen) > len(best):
            best = seen
    us = sorted({u for u, _ in best})
    mid = us[len(us) // 2] if us else 0
    return [c for u, c in best if mid - width // 2 <= u < mid - width // 2 + width and d.role_at(*c) != "door"]


def faults(g: Design, gd: GameData) -> dict[str, Design]:
    rng = random.Random(1)
    out = {}
    d = _copy(g)
    for x, y, z, s, r in list(d.cells()):
        if r == "roof":
            d.set(x, y, z, AIR)
    out["no_roof"] = d
    d = _copy(g)
    # a 4-wide hole through the house's most exposed wall (O34d: a real defect seen from outside). Earlier versions cut
    # the southernmost "wall-like" cells, which on cabin_detailed were two porch posts: attempt 5's "hole" showed nothing
    for x, y, z in _exposed_wall_strip(d, width=4):
        d.set(x, y, z, AIR)
    out["wall_hole"] = d
    d = _copy(g)
    for x, y, z, s, r in list(d.cells()):
        if r in ("wall", "support", "roof"):
            d.set(x, y, z, gd.canonical(f"minecraft:{rng.choice(CONCRETE)}_concrete"), r)
    out["clashing_palette"] = d
    d = Design((g.size[0], g.size[1] + 6, g.size[2]), g.origin)
    for x, y, z, s, r in g.cells():
        d.set(x, y, z, s, r)
    cx, cz = g.size[0] // 2, g.size[2] // 2
    for y in range(g.size[1] + 3, g.size[1] + 6):
        for z in range(cz - 1, cz + 2):
            for x in range(cx - 1, cx + 2):
                d.set(x, y, z, "minecraft:cobblestone", "trim")
    out["floating_junk"] = d
    d = _copy(g)
    for x, y, z, s, r in list(d.cells()):
        if rng.random() < 0.35:
            d.set(x, y, z, AIR)
    out["griefed"] = d
    d = _copy(g)
    for x, y, z, s, r in list(d.cells()):
        d.set(x, y, z, "minecraft:stone", r)
    out["plain_box"] = d
    for v in out.values():
        v.canonicalize(gd)
    return out


def _views(g: Design, gd: GameData, work: Path) -> dict[str, Path]:
    preview.render(g, gd, work)
    return {k: work / f"{k}.png" for k in VIEWS}


MAX_DRIFT = 1.0      # O34: median-of-3 repeat drift allowed on the same images
MARGIN = 1.0         # O34: every planted fault at least this far below the bar


def run(critic, gd: GameData, out_dir: Path = SELFTEST_DIR, base: str = "cabin_detailed") -> dict:
    g = compile_file(EXAMPLES / f"{base}.eve", gd)
    card, _ = load_style(g.meta.get("style") or "cozy_cabin", 0)
    brief = {"purpose": "a small home", "rooms": [r["type"] for r in g.rooms], "style": g.meta.get("style")}
    cases, errors = [], []
    with tempfile.TemporaryDirectory(prefix="eve-critic-check-") as tmp:
        tmp = Path(tmp)
        base_views = _views(g, gd, tmp / "base")
        stage = "base"
        try:                                     # the error names the request that failed (45L adbba7b: it did not)
            first = critic.critique(brief, card, base_views, design=g)
            stage = "repeat"
            again = critic.critique(brief, card, base_views, design=g)
        except ModelError as e:
            return {"critic": critic.name, "version": critic.version, "passed": False, "error": f"{stage}: {e}"}
        repeat = max(abs(first["scores"][c] - again["scores"][c]) for c in CATEGORIES)
        bar = min(first["scores"].values())          # what the critic gives our own hand-built reference
        for name, d in faults(g, gd).items():
            try:
                c = critic.critique(brief, card, _views(d, gd, tmp / name), design=d)
            except ModelError as e:
                errors.append(f"{name}: {e}")
                continue
            low = min(c["scores"].values())
            cases.append({"fault": name, "min_score": low, "caught": low <= bar - MARGIN, "scores": c["scores"],
                          "defects": c.get("defect_samples") or c.get("defects"), "fixes": c["fixes"][:5]})
    caught = sum(c["caught"] for c in cases)
    report = {"critic": critic.name, "version": critic.version, "ts": int(time.time()), "bar": bar,
              "base": base, "base_scores": first["scores"], "repeat_scores": again["scores"],
              "repeat_max_diff": repeat, "base_samples": first.get("samples"),
              "base_defects": first.get("defect_samples") or first.get("defects"),
              "faults": len(cases) + len(errors), "caught": caught,
              "false_accepts": [c["fault"] for c in cases if not c["caught"]], "errors": errors, "cases": cases,
              "passed": not errors and caught == len(cases) and repeat <= MAX_DRIFT,
              "rule": f"bar = the reference's lowest score; every planted fault's lowest score at least {MARGIN} below "
                      f"it; the same images re-scored within {MAX_DRIFT}; no API errors (ruling O34)"}
    report["calls"] = list(getattr(getattr(critic, "client", None), "calls", []))   # usage per model call (O34n)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    qualification_file(out_dir, critic.name, critic.version).write_text(json.dumps(report, indent=1) + "\n")
    return report


def probe(critic, gd: GameData, names: list[str], out_dir: Path, base: str = "cabin_detailed") -> dict:
    """O34n: one critique per named image set ("base" = the reference, else a fault), one sample each, with each call's
    usage (finish_reason, completion and reasoning tokens, cost), before spending on the full self-test."""
    g = compile_file(EXAMPLES / f"{base}.eve", gd)
    card, _ = load_style(g.meta.get("style") or "cozy_cabin", 0)
    brief = {"purpose": "a small home", "rooms": [r["type"] for r in g.rooms], "style": g.meta.get("style")}
    sets = {"base": g, **faults(g, gd)}
    names = list(sets) if names in (["all"], ("all",)) else names   # O34r: base + every fault
    critic.SAMPLES = 1
    out = {"critic": critic.name, "version": critic.version, "ts": int(time.time()), "results": {}}
    with tempfile.TemporaryDirectory(prefix="eve-critic-probe-") as tmp:
        for name in names:
            n0 = len(getattr(getattr(critic, "client", None), "calls", []))
            try:
                c = critic.critique(brief, card, _views(sets[name], gd, Path(tmp) / name), design=sets[name])
                res = {"scores": c["scores"], "defects": c.get("defects")}
            except ModelError as e:
                res = {"error": str(e)}
            res["calls"] = list(getattr(getattr(critic, "client", None), "calls", []))[n0:]
            out["results"][name] = res
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "probe.json").write_text(json.dumps(out, indent=1) + "\n")
    return out
