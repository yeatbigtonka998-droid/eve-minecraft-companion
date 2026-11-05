"""Builds the combat part's proposed registry: contracts/skills/registry.json + fight_melee (v1), registry_version 0.4.0.
Prints the canonical hash (mind config.canonical: sorted keys, no whitespace, UTF-8). Writes nothing into contracts/ unless
--apply is given (only after body and mind say yes in STATUS). Same layout rule as parts/skills/registry/skills-0.3.0.py.

    py -3.12 parts/combat/registry/combat-0.4.0.py [--apply]
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
REG = REPO / "contracts" / "skills" / "registry.json"
PREV, VER = "0.3.0", "0.4.0"

NEW = [{
    "name": "fight_melee", "version": 1,
    "summary": "Melee-fight entity_id, or (without it) the hostiles within radius one at a time (nearest swingable first, at most "
               "max_targets): attack_entity swings at full cooldown and legal reach, spacing and strafing between swings, walk_to "
               "when far or out of sight; never swings with a protected entity in the sweep area; below health_floor it runs and "
               "ends failed health_floor. Creepers are never melee targets.",
    "params": {"type": "object", "properties": {
        "entity_id": {"$ref": "#/$defs/entity_id"},
        "radius": {"type": "number", "minimum": 4, "maximum": 32, "default": 16},
        "health_floor": {"type": "number", "minimum": 1, "maximum": 20, "default": 8},
        "max_targets": {"type": "integer", "minimum": 1, "maximum": 16, "default": 8}},
        "additionalProperties": False},
    "effects": ["attack", "move"], "preconditions": ["alive", "not_stopped"], "timeout_ms": 120000, "cancellable": True,
    "evidence": ["targets", "kills", "swings", "hits", "damage_dealt", "damage_taken", "health_start", "health_min", "health_end",
                 "max_swing_reach", "protected_holds", "disengaged", "swing_log"],
    "failure_codes": ["no_target", "lost_target", "protected_entity", "health_floor", "cancelled", "timeout", "interrupted"],
    "min_mode": "manual"}]

reg = json.loads(REG.read_text(encoding="utf-8"))
assert reg["registry_version"] == PREV, f"registry is {reg['registry_version']}, this script expects {PREV}"
names = {s["name"] for s in reg["skills"]}
reg["skills"] += [s for s in NEW if s["name"] not in names]
reg["registry_version"] = VER
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(f"registry {VER} hash", hashlib.sha256(canon).hexdigest(), f"({len(reg['skills'])} skills)")
if "--apply" in sys.argv:
    text = REG.read_text(encoding="utf-8").rstrip()
    lines = [json.dumps(s, ensure_ascii=False) for s in NEW if s["name"] not in names]
    head, tail = text.rsplit("]", 1)
    head = head.rstrip().replace(f'"registry_version": "{PREV}"', f'"registry_version": "{VER}"')
    REG.write_text(head + ",\n    " + ",\n    ".join(lines) + "\n  ]" + tail + "\n", encoding="utf-8", newline="\n")
    check = json.loads(REG.read_text(encoding="utf-8"))
    assert hashlib.sha256(json.dumps(check, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest() \
        == hashlib.sha256(canon).hexdigest()
    print("applied to", REG)
