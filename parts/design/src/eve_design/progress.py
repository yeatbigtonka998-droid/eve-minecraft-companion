"""Reference implementation of the construction-progress arithmetic the mind owns (CP-48 identity clause).

The mind records progress per (project_id, version, design_hash) as one row per non-air cell of design.json
("final") and scaffold.json ("scaffold"), addressed by the grid index idx = x + sx * (z + sz * y). The items still
needed are the sum of bom.json `items_per_state[state]` over cells not yet placed; the mind feeds that `want` and
the live inventory + storage.index into its recipe solver. This module exists so both sides can test against the
same vector (tests/fixtures/progress/hut5_half.json); the mind keeps its own implementation.
"""
from __future__ import annotations

from collections import Counter

from .grid import AIR, Design

DONE_FINAL = {"placed", "verified"}            # a 'mismatch' cell still needs its block
DONE_SCAFFOLD = {"placed", "verified", "removed"}


def cell_index(g: Design, x: int, y: int, z: int) -> int:
    sx, _, sz = g.size
    return x + sx * (z + sz * y)


def cell_pos(g: Design, idx: int) -> tuple[int, int, int]:
    sx, _, sz = g.size
    return idx % sx, idx // (sx * sz), (idx // sx) % sz


def cells(g: Design) -> list[int]:
    """Indices of every non-air cell (the rows a build run starts with, all 'pending')."""
    return sorted(cell_index(g, x, y, z) for x, y, z, _, _ in g.cells())


def remaining_want(g: Design, scaffold: Design | None, status: dict, items_per_state: dict) -> Counter:
    """status: {(layer, idx): 'pending'|'placed'|'verified'|'mismatch'|'removed'}; missing keys count as pending."""
    want = Counter()
    for layer, grid, done in (("final", g, DONE_FINAL), ("scaffold", scaffold, DONE_SCAFFOLD)):
        if grid is None:
            continue
        for idx in cells(grid):
            if status.get((layer, idx), "pending") in done:
                continue
            state = grid.get(*cell_pos(grid, idx))
            if state != AIR:
                for it in items_per_state[state]:
                    want[it["item"]] += it["count"]
    return want
