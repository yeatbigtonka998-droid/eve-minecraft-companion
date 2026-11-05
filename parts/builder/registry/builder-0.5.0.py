"""Builder's proposed registry: combat's 0.4.0 (fight_melee) + build_schematic (v1) = registry_version 0.5.0. Prints the
canonical hash (sorted keys, no whitespace, UTF-8). While contracts/ is still at 0.3.0 the hash is computed on combat's
proposal (parts/combat/registry/combat-0.4.0.py, run in memory); --apply writes only once contracts/ is at 0.4.0 and body
and mind agree in STATUS. repair_zone and bridge come in their own bump once their params are settled.

    py -3.12 parts/builder/registry/builder-0.5.0.py [--apply]
"""
import contextlib, hashlib, io, json, runpy, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
REG = REPO / "contracts" / "skills" / "registry.json"
OLD, NEW_VERSION = "0.4.0", "0.5.0"

NEW = [{
    "name": "build_schematic", "version": 1,
    "summary": "Build the design folder <project_id>/v<version> (design.json + build_order.json [+ scaffold.json]) whose hash equals "
               "design_hash: scaffold first, role order, normal-reach placement with place_block/mine_block/use_item/walk_to, "
               "cells already right are skipped (resume), then compare every cell and fix mismatches; scaffold removed last. "
               "origin (the approved site) overrides design.json's origin. States place_block v1 cannot produce are reported "
               "under manual. Fails no_item with the deficit before placing anything when supplies are short.",
    "params": {"type": "object", "required": ["project_id", "version", "design_hash"], "properties": {
        "project_id": {"type": "string", "pattern": "^[a-z0-9_.-]{1,64}$"},
        "version": {"type": "integer", "minimum": 1},
        "design_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "origin": {"$ref": "#/$defs/block_pos"},
        "max_fix_rounds": {"type": "integer", "minimum": 0, "maximum": 5, "default": 2}},
        "additionalProperties": False},
    "effects": ["move", "place", "break", "use_item"],
    "preconditions": ["alive", "not_stopped", "link_fresh"],
    "timeout_ms": 3600000, "cancellable": True,
    "evidence": ["design_hash", "origin", "cells_total", "already_present", "placed", "fixed", "scaffold_removed",
                 "matched", "mismatches", "manual", "deficit"],
    "failure_codes": ["no_design", "design_hash_mismatch", "gate_refused", "no_item", "partial", "unreachable",
                      "zone_denied", "cancelled", "timeout", "interrupted"],
    "min_mode": "manual"}]

reg = json.loads(REG.read_text(encoding="utf-8"))
if reg["registry_version"] == "0.3.0":  # combat's 0.4.0 not applied yet: base on its proposal
    assert "--apply" not in sys.argv, "contracts/ is at 0.3.0: combat applies 0.4.0 first"
    with contextlib.redirect_stdout(io.StringIO()):
        reg = runpy.run_path(str(REPO / "parts" / "combat" / "registry" / "combat-0.4.0.py"), run_name="combat")["reg"]
assert reg["registry_version"] == OLD, f"base registry is {reg['registry_version']}, this script expects {OLD}"
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
