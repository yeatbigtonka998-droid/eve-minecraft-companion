"""Style-card metrics from reference builds (CP-58).

Exact (counted from blocks): palette counts and shares, bounding size, block-group shares.
Inferred (labelled uncertain): roof type, window-to-wall ratio, storey count. Automatic semantic extraction is not
assumed reliable: every inferred value carries "uncertain": true until a human-labelled fixture confirms it.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import nbtlib

from .gamedata import GameData, StateError, format_state, parse_state
from .grid import AIR, Design
from .schem_io import LIMITS, SchematicError, read_nbt

GROUND = {"minecraft:dirt", "minecraft:grass_block", "minecraft:dirt_path", "minecraft:coarse_dirt",
          "minecraft:podzol", "minecraft:gravel", "minecraft:sand", "minecraft:snow", "minecraft:short_grass",
          "minecraft:tall_grass", "minecraft:fern", "minecraft:large_fern", "minecraft:farmland"}
GROUPS = (("log", ("_log", "_wood")), ("planks", ("_planks",)), ("stairs", ("_stairs",)), ("slab", ("_slab",)),
          ("stone", ("cobblestone", "stone", "brick", "andesite", "deepslate", "tuff")),
          ("glass", ("glass", "_pane")), ("fence_wall", ("_fence", "_wall")), ("light", ("lantern", "torch")))


def load_structure(path: Path, gd: GameData) -> tuple[Design, list[str]]:
    """Vanilla structure NBT (size, palette[{id|Name, properties|Properties}], blocks[{pos, state, nbt?}]).
    Jigsaw blocks become their final_state; structure_void becomes air (it means 'leave the world as is')."""
    root = read_nbt(Path(path), LIMITS)
    size = [int(v) for v in root["size"]]
    if len(size) != 3 or min(size) < 1 or size[0] * size[1] * size[2] > LIMITS.volume:
        raise SchematicError(f"bad structure size {size}")
    states, notes = [], []
    for p in root["palette"]:
        name = str(p.get("id", p.get("Name", "")))
        props = p.get("properties", p.get("Properties", nbtlib.Compound()))
        states.append(format_state(name, {str(k): str(v) for k, v in props.items()}))
    g = Design(tuple(size))
    jig = 0
    for b in root["blocks"]:
        x, y, z = (int(v) for v in b["pos"])
        s = states[int(b["state"])]
        if s.startswith("minecraft:jigsaw"):
            s = str(b.get("nbt", {}).get("final_state", "minecraft:air"))
            jig += 1
        if s.startswith("minecraft:structure_void"):
            s = AIR
        try:
            s = gd.canonical(s)
        except StateError as e:
            notes.append(str(e))
        g.set(x, y, z, s)
    if jig:
        notes.append(f"{jig} jigsaw blocks replaced by their final_state")
    if len(root.get("entities", [])):
        notes.append(f"{len(root['entities'])} entities ignored")
    return g, notes


def metrics(g: Design) -> dict:
    counts = Counter()
    for s, n in g.counts().items():
        counts[parse_state(s)[0]] += n
    build = {b: n for b, n in counts.items() if b not in GROUND}
    total = sum(build.values())
    cells = [(x, y, z) for x, y, z, s, _ in g.cells() if parse_state(s)[0] not in GROUND]
    xs, ys, zs = zip(*cells) if cells else ((0,), (0,), (0,))
    groups = Counter()
    for b, n in build.items():
        name = b.split(":")[1]
        groups[next((gname for gname, pats in GROUPS if any(p in name for p in pats)), "other")] += n
    # inferred, uncertain: roof type from the facing mix of the topmost stairs, windows vs exterior wall cells
    top_stairs = Counter()
    for x, z in {(x, z) for x, _, z in cells}:
        for y in range(g.size[1] - 1, -1, -1):
            s = g.get(x, y, z)
            if s != AIR:
                if "_stairs" in s:
                    top_stairs[parse_state(s)[1].get("facing")] += 1
                break
    dirs = [d for d, n in top_stairs.items() if n >= max(2, 0.15 * sum(top_stairs.values()))]
    roof = "flat_or_other" if not dirs else "gable" if len(dirs) == 2 and set(dirs) in ({"north", "south"}, {"east", "west"}) \
        else "hip" if len(dirs) >= 3 else "shed" if len(dirs) == 1 else "mixed"
    glass = groups["glass"]
    return {
        "exact": {"size": {"x": max(xs) - min(xs) + 1, "y": max(ys) - min(ys) + 1, "z": max(zs) - min(zs) + 1},
                  "blocks": total, "ground_blocks": sum(counts.values()) - total,
                  "palette": dict(sorted(build.items(), key=lambda kv: (-kv[1], kv[0]))),
                  "groups": dict(sorted(groups.items()))},
        "inferred": {"roof": {"value": roof, "top_stair_facings": dict(top_stairs), "uncertain": True},
                     "glass_share": {"value": round(glass / total, 3) if total else 0, "uncertain": True}},
    }


def reference_set(paths: list[Path], gd: GameData, jar_sha1: str) -> dict:
    refs, agg = [], Counter()
    for p in paths:
        g, notes = load_structure(p, gd)
        m = metrics(g)
        refs.append({"file": p.name, "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest(), "notes": notes, **m})
        agg.update(m["exact"]["palette"])
    total = sum(agg.values())
    sizes = [r["exact"]["size"] for r in refs]
    return {
        "references": refs,
        "aggregate": {"blocks": total,
                      "palette_share": {b: round(n / total, 4) for b, n in agg.most_common(12)},
                      "footprint_range": {"x": [min(s["x"] for s in sizes), max(s["x"] for s in sizes)],
                                          "z": [min(s["z"] for s in sizes), max(s["z"] for s in sizes)]},
                      "height_range": [min(s["y"] for s in sizes), max(s["y"] for s in sizes)],
                      "roof_votes": dict(Counter(r["inferred"]["roof"]["value"] for r in refs))},
        "provenance": {"source": "Minecraft 26.3 client jar (data/minecraft/structure/village/taiga/houses/)",
                       "jar_sha1": jar_sha1, "data_version": gd.data_version,
                       "terms": "Mojang game data under the Minecraft EULA: used privately to measure style numbers; "
                                "the files are not committed or redistributed (data/game is gitignored)"},
    }


ROLE_RULE = ("frame: *_log/*_wood (non-stripped first); wall: *_planks and *terracotta; base: cobblestone/stone-brick family "
             "full blocks; roof: the wood family with the most *_stairs. Inferred mapping, uncertain until a human labels it.")


def draft_card(name: str, refs: dict) -> dict:
    """A style card drafted from measured reference shares by the fixed ROLE_RULE (never invents a number)."""
    share = refs["aggregate"]["palette_share"]
    every = Counter()
    for r in refs["references"]:
        every.update(r["exact"]["palette"])
    total = sum(every.values())

    def mix(pred):
        picked = {b: n for b, n in every.items() if pred(b.split(":")[1])}
        s = sum(picked.values())
        return [[b, round(n / s, 3)] for b, n in sorted(picked.items(), key=lambda kv: (-kv[1], kv[0]))][:4] if s else []

    stone = ("cobblestone", "mossy_cobblestone", "stone_bricks", "mossy_stone_bricks", "cracked_stone_bricks", "stone")
    stairs = Counter()
    for b, n in every.items():
        nm = b.split(":")[1]
        if nm.endswith("_stairs"):
            stairs[nm[: -len("_stairs")]] += n
    roof = stairs.most_common(1)[0][0] if stairs else None
    sizes = [r["exact"]["size"] for r in refs["references"]]
    votes = refs["aggregate"]["roof_votes"]
    wood = roof if roof and roof not in ("cobblestone", "stone", "stone_brick") else "oak"
    return {
        "style": name, "status": "draft from references",
        "provenance": {"source": f"{name}.references.json (measured)", "references": [r["file"] for r in refs["references"]],
                       "role_rule": ROLE_RULE, "terms": refs["provenance"]["terms"],
                       "note": "Not Hunter's taste: numbers are measured, the role mapping is a fixed rule; review pending."},
        "palette": {
            "frame": mix(lambda n: n.endswith(("_log", "_wood"))),
            "wall": mix(lambda n: n.endswith("_planks") or n.endswith("terracotta")),
            "base": mix(lambda n: n in stone),
            "floor": [[f"minecraft:{wood}_planks", 1.0]],
            "roof": roof, "door": wood, "fence": wood, "trim": wood,
            "window": "minecraft:glass_pane" if every.get("minecraft:glass_pane") else "minecraft:glass",
            "chimney": "minecraft:cobblestone",
        },
        "proportions": {"footprint": {"min": [min(s["x"] for s in sizes), min(s["z"] for s in sizes)],
                                      "max": [max(s["x"] for s in sizes), max(s["z"] for s in sizes)]},
                        "height": [min(s["y"] for s in sizes), max(s["y"] for s in sizes)],
                        "roof": {"kind": max(votes, key=votes.get), "votes": votes, "uncertain": True}},
        "rules": {"frame_every": 4, "frame_every_source": "unmeasured default"},
        "detailing": {"default": ["pillars", "base_band", "gradient"], "base_band_rows": 1,
                      "source": "references show log frames on a cobblestone base; no trapdoor shutters in them"},
        "reference_share_top": share,
        "blocks_measured": total,
    }
