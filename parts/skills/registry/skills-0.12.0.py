"""Skills' proposed registry 0.12.0: contracts/skills/registry.json (0.11.0) + prospect v1. Prints the canonical hash
(mind config.canonical: sorted keys, no whitespace, UTF-8); --apply writes the registry only after body and mind agree.

    py -3.12 parts/skills/registry/skills-0.12.0.py [--apply]
"""
import hashlib, json, sys
from pathlib import Path

REG = Path(__file__).resolve().parents[3] / "contracts" / "skills" / "registry.json"
NEW = [{
    "name": "prospect", "version": 1,
    "summary": "Bounded branch mining for ore that is not exposed: optionally a staircase down to descend_to first, then a 1x2 tunnel straight ahead along direction (default: her facing; never up) with mine_block, every target ore it exposes within reach mined, a torch every torch_every blocks, then back to the start. Stops on count, max_blocks, full inventory, health below min_health, the timer, or a hazard ahead (lava, water, no floor).",
    "params": {"type": "object", "properties": {
        "block": {"$ref": "#/$defs/block_id"}, "tag": {"type": "string", "pattern": "^[a-z0-9_.-]+:[a-z0-9_./-]+$"},
        "count": {"type": "integer", "minimum": 1, "maximum": 64},
        "max_blocks": {"type": "integer", "minimum": 8, "maximum": 256, "default": 64},
        "torch_every": {"type": "integer", "minimum": 0, "maximum": 32, "default": 8},
        "min_health": {"type": "number", "minimum": 2, "maximum": 20, "default": 12},
        "minutes": {"type": "integer", "minimum": 1, "maximum": 30, "default": 8},
        "direction": {"type": "string", "enum": ["north", "south", "east", "west"]},
        "descend_to": {"type": "integer", "minimum": -58, "maximum": 319}},
        "required": ["count"], "additionalProperties": False},
    "effects": ["move", "break", "place"],
    "preconditions": ["alive", "not_stopped", "link_fresh"],
    "timeout_ms": 1900000, "cancellable": True,
    "evidence": ["mined", "ores", "blocks_broken", "tunnel_length", "torches_placed", "stop_reason", "returned"],
    "failure_codes": ["not_found", "partial", "hazard", "inventory_full", "hurt", "no_tool", "unreachable", "zone_denied", "capped",
                      "precondition_failed", "cancelled", "timeout", "interrupted"],
    "min_mode": "manual"}]

reg = json.loads(REG.read_text(encoding="utf-8"))
assert reg["registry_version"] == "0.11.0", reg["registry_version"]
names = {s["name"] for s in reg["skills"]}
reg["skills"] += [s for s in NEW if s["name"] not in names]
reg["registry_version"] = "0.12.0"
h = lambda d: hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()  # noqa: E731
print("registry 0.12.0 hash", h(reg), f"({len(reg['skills'])} skills)")
if "--apply" in sys.argv:
    text = REG.read_text(encoding="utf-8").rstrip()
    head, tail = text.rsplit("]", 1)
    head = head.rstrip().replace('"registry_version": "0.11.0"', '"registry_version": "0.12.0"')
    lines = [json.dumps(s, ensure_ascii=False) for s in NEW if s["name"] not in names]
    REG.write_text(head + ",\n    " + ",\n    ".join(lines) + "\n  ]" + tail + "\n", encoding="utf-8", newline="\n")
    assert h(json.loads(REG.read_text(encoding="utf-8"))) == h(reg)
    print("applied to", REG)
