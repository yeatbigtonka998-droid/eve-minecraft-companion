"""Pinned game data (data/game/26.3, produced by tools/extract_game_data.py). The only authority for
legal block states, item stack sizes, recipes and tags."""
from __future__ import annotations

import json
import os
import re
from functools import cached_property, lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
DEFAULT_ROOT = REPO / "data" / "game" / "26.3"
_STATE = re.compile(r"^([a-z0-9_.-]+:)?([a-z0-9_./-]+)(?:\[([a-z0-9_=,]*)\])?$")


class StateError(ValueError):
    pass


def parse_state(s: str) -> tuple[str, dict[str, str]]:
    """'oak_stairs[facing=north]' -> ('minecraft:oak_stairs', {'facing': 'north'}). Syntax only."""
    m = _STATE.match(s.strip())
    if not m:
        raise StateError(f"bad block state syntax: {s!r}")
    ns, path, props = m.groups()
    out: dict[str, str] = {}
    for kv in filter(None, (props or "").split(",")):
        k, eq, v = kv.partition("=")
        if not eq or not k or not v or k in out:
            raise StateError(f"bad property {kv!r} in {s!r}")
        out[k] = v
    return (ns or "minecraft:") + path, out


def format_state(block_id: str, props: dict[str, str]) -> str:
    return block_id + ("[" + ",".join(f"{k}={props[k]}" for k in sorted(props)) + "]" if props else "")


class GameData:
    def __init__(self, root: Path):
        self.root = Path(root)
        if not (self.root / "MANIFEST.json").exists():
            raise FileNotFoundError(f"no game data at {self.root}; run tools/extract_game_data.py")
        self.manifest = json.loads((self.root / "MANIFEST.json").read_text(encoding="utf-8"))
        self.version = self.manifest["version"]
        self.data_version = int(self.manifest["data_version"])

    @cached_property
    def blocks(self) -> dict[str, dict]:
        """id -> {'props': {name: [values]}, 'default': {name: value}}"""
        raw = json.loads((self.root / "reports" / "blocks.json").read_text(encoding="utf-8"))
        out = {}
        for bid, b in raw.items():
            default = next(s for s in b["states"] if s.get("default"))
            out[bid] = {"props": b.get("properties", {}), "default": default.get("properties", {})}
        return out

    @cached_property
    def items(self) -> dict[str, dict]:
        return json.loads((self.root / "reports" / "items.json").read_text(encoding="utf-8"))

    def max_stack(self, item: str) -> int:
        return int(self.items[item]["minecraft:max_stack_size"])

    def canonical(self, state: str) -> str:
        """Full state string with every property (defaults filled), sorted. Raises StateError if illegal."""
        bid, props = parse_state(state)
        b = self.blocks.get(bid)
        if b is None:
            raise StateError(f"unknown block {bid!r} in game {self.version}")
        for k, v in props.items():
            if k not in b["props"]:
                raise StateError(f"{bid} has no property {k!r}")
            if v not in b["props"][k]:
                raise StateError(f"{bid}[{k}={v}] is not a legal value (allowed: {b['props'][k]})")
        return format_state(bid, {**b["default"], **props})

    @cached_property
    def recipes(self) -> dict[str, list[tuple[str, dict]]]:
        """result item -> [(recipe_id, recipe json)], sorted by recipe id."""
        out: dict[str, list] = {}
        for p in sorted((self.root / "recipe").glob("*.json")):
            r = json.loads(p.read_text(encoding="utf-8"))
            res = r.get("result")
            if isinstance(res, dict) and "id" in res:
                out.setdefault(res["id"], []).append((f"minecraft:{p.stem}", r))
        return out

    @lru_cache(maxsize=None)
    def tag(self, kind: str, name: str) -> frozenset[str]:
        """Resolve an item/block tag (with nested tags) to ids. name: 'minecraft:planks' or '#minecraft:planks'."""
        name = name.lstrip("#")
        ns, _, path = name.partition(":")
        f = self.root / "tags" / kind / f"{path}.json"
        if ns != "minecraft" or not f.exists():
            raise KeyError(f"unknown {kind} tag {name}")
        out: set[str] = set()
        for v in json.loads(f.read_text(encoding="utf-8"))["values"]:
            v = v["id"] if isinstance(v, dict) else v
            out |= self.tag(kind, v) if v.startswith("#") else {v}
        return frozenset(out)


@lru_cache(maxsize=4)
def load(root: str | None = None) -> GameData:
    return GameData(Path(root or os.environ.get("EVE_GAME_DATA") or DEFAULT_ROOT))
