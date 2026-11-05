"""Builder's proposed registry: skills' 0.10.0 (gather) + bridge (v1) = registry_version 0.11.0 (CP-53). Prints the canonical
hash (sorted keys, no whitespace, UTF-8). While contracts/ is still at 0.9.0 the hash is computed on skills' proposal
(parts/skills/registry/skills-0.10.0.py, run in memory); --apply writes only once contracts/ is at 0.10.0 and body and mind agree.

    py -3.12 parts/builder/registry/builder-0.11.0.py [--apply]
"""
import contextlib, hashlib, io, json, runpy, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
REG = REPO / "contracts" / "skills" / "registry.json"
OLD, NEW_VERSION = "0.10.0", "0.11.0"

NEW = [{
    "name": "bridge", "version": 1,
    "summary": "Bridge in a straight line along x or z from the block she stands on to `to` (same height as her floor): per cell, "
               "lean over the edge sneaking (vanilla's sneak back-off keeps her on the block), place_block the next floor cell on "
               "the front face, step onto it sneaking. Refuses before stepping out without enough blocks, without a recovery "
               "item (ender pearl, slow falling, water bucket) when the gap is deeper than a safe fall, or with hostiles near; a "
               "hostile closing in mid-bridge sends her back to the start. Any drop of her feet below the floor ends it `fell`.",
    "params": {"type": "object", "required": ["to"], "properties": {
        "to": {"$ref": "#/$defs/block_pos"},
        "item": {"$ref": "#/$defs/item_id"},
        "max_blocks": {"type": "integer", "minimum": 1, "maximum": 64, "default": 40},
        "hostile_radius": {"type": "number", "minimum": 4, "maximum": 32, "default": 16}},
        "additionalProperties": False},
    "effects": ["move", "place", "hazard"],
    "preconditions": ["alive", "not_stopped", "link_fresh"],
    "timeout_ms": 600000, "cancellable": True,
    "evidence": ["start", "end", "length", "placed", "falls", "min_feet_y", "recovery_item", "retreated", "hostiles_seen"],
    "failure_codes": ["bad_target", "no_item", "no_recovery", "hostile_near", "obstructed", "fell", "zone_denied", "partial",
                      "cancelled", "timeout", "interrupted"],
    "min_mode": "manual"}]

reg = json.loads(REG.read_text(encoding="utf-8"))
if reg["registry_version"] == "0.9.0":  # skills' 0.10.0 not applied yet: base on its proposal
    assert "--apply" not in sys.argv, "contracts/ is at 0.9.0: skills applies 0.10.0 first"
    with contextlib.redirect_stdout(io.StringIO()):
        reg = runpy.run_path(str(REPO / "parts" / "skills" / "registry" / "skills-0.10.0.py"), run_name="skills")["reg"]
assert reg["registry_version"] == OLD, f"registry is {reg['registry_version']}, this script expects {OLD}"
names = {s["name"] for s in reg["skills"]}
reg["skills"] += [s for s in NEW if s["name"] not in names]
reg["registry_version"] = NEW_VERSION
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(f"registry {NEW_VERSION} hash", hashlib.sha256(canon).hexdigest(), f"({len(reg['skills'])} skills)")
if "--apply" in sys.argv:
    text = REG.read_text(encoding="utf-8").rstrip()
    assert text.endswith("]\n}") or text.endswith("]}") or text.endswith("]\r\n}"), "unexpected registry tail"
    lines = [json.dumps(s, ensure_ascii=False) for s in NEW if s["name"] not in names]
    head, tail = text.rsplit("]", 1)
    head = head.rstrip().replace(f'"registry_version": "{OLD}"', f'"registry_version": "{NEW_VERSION}"')
    REG.write_text(head + ",\n    " + ",\n    ".join(lines) + "\n  ]" + tail + "\n", encoding="utf-8", newline="\n")
    check = json.loads(REG.read_text(encoding="utf-8"))
    assert hashlib.sha256(json.dumps(check, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest() \
        == hashlib.sha256(canon).hexdigest()
    print("applied to", REG)
