"""Builder's proposed registry: skills' 0.8.0 (harvest_replant) + repair_zone (v1) = registry_version 0.9.0. Prints the
canonical hash (sorted keys, no whitespace, UTF-8). While contracts/ is still at 0.7.0 the hash is computed on skills'
proposal (parts/skills/registry/skills-0.8.0.py, run in memory); --apply writes only once contracts/ is at 0.8.0 and body
and mind agree in STATUS.

    py -3.12 parts/builder/registry/builder-0.9.0.py [--apply]
"""
import contextlib, hashlib, io, json, runpy, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
REG = REPO / "contracts" / "skills" / "registry.json"
OLD, NEW_VERSION = "0.8.0", "0.9.0"

NEW = [{
    "name": "repair_zone", "version": 1,
    "summary": "Restore a zone to an approved snapshot from sense's zone.repair steps (link v1.3), passed in by the mind: removals "
               "in order (a block is broken only if it is still the block the step names, so later edits stay), then placements "
               "(place_block v2 states; farmland = dirt tilled with a hoe), then replanting `replant` (sense's crop_cycle skips "
               "with their snapshot block, now air) with the crop's seed, then a check of every step's cell. Only step "
               "positions are touched; sense's manual (containers, O17) and skipped (owner edits, crops) are not. Fails "
               "no_item with the deficit before acting when supplies or a hoe are missing.",
    "params": {"type": "object", "required": ["zone_id", "snapshot_id", "steps"], "properties": {
        "zone_id": {"type": "string", "pattern": "^[a-z0-9_.-]{1,64}$"},
        "snapshot_id": {"type": "string", "pattern": "^[A-Za-z0-9_.@:-]{1,128}$"},
        "steps": {"type": "array", "maxItems": 4096, "items": {"type": "object", "required": ["op", "pos", "block"], "properties": {
            "op": {"type": "string", "enum": ["remove", "place"]},
            "pos": {"$ref": "#/$defs/block_pos"},
            "block": {"type": "string", "pattern": "^[a-z0-9_.-]+:[a-z0-9_./-]+(\\[[a-z0-9_=,]*\\])?$"}},
            "additionalProperties": False}},
        "replant": {"type": "array", "maxItems": 1024, "items": {"type": "object", "required": ["pos", "block"], "properties": {
            "pos": {"$ref": "#/$defs/block_pos"},
            "block": {"type": "string", "pattern": r"^[a-z0-9_.-]+:[a-z0-9_./-]+(\[[a-z0-9_=,]*\])?$"}},
            "additionalProperties": False}},
        "max_fix_rounds": {"type": "integer", "minimum": 0, "maximum": 5, "default": 2}},
        "additionalProperties": False},
    "effects": ["move", "place", "break", "use_item"],
    "preconditions": ["alive", "not_stopped", "link_fresh"],
    "timeout_ms": 3600000, "cancellable": True,
    "evidence": ["zone_id", "snapshot_id", "cells_total", "already_present", "placed", "fixed", "matched", "mismatches",
                 "manual", "deficit"],
    "failure_codes": ["no_item", "partial", "unreachable", "zone_denied", "cancelled", "timeout", "interrupted"],
    "min_mode": "manual"}]

reg = json.loads(REG.read_text(encoding="utf-8"))
if reg["registry_version"] == "0.7.0":  # skills' 0.8.0 not applied yet: base on its proposal
    assert "--apply" not in sys.argv, "contracts/ is at 0.7.0: skills applies 0.8.0 first"
    with contextlib.redirect_stdout(io.StringIO()):
        reg = runpy.run_path(str(REPO / "parts" / "skills" / "registry" / "skills-0.8.0.py"), run_name="skills")["reg"]
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
