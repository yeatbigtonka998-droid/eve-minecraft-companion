"""Builds the combat part's proposed registry: contracts/skills/registry.json + fight_ranged (v1), registry_version 0.7.0.
Prints the canonical hash (mind config.canonical: sorted keys, no whitespace, UTF-8). Writes nothing into contracts/ unless
--apply is given (only after body and mind say yes in STATUS). Same layout rule as parts/skills/registry/skills-0.3.0.py.

    py -3.12 parts/combat/registry/combat-0.7.0.py [--apply]
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
REG = REPO / "contracts" / "skills" / "registry.json"
PREV, VER = "0.6.0", "0.7.0"

NEW = [{
    "name": "fight_ranged", "version": 1,
    "summary": "Shoot entity_id, or (without it) the nearest hostile in sight within radius, with Eve's own bow from her hotbar: "
               "full draw, pitch solved for the arrow's drag and gravity, lead on a moving target; never releases with a protected "
               "entity near the line of fire; walk_to when out of sight; at most max_arrows arrows. Ends ok when the target dies.",
    "params": {"type": "object", "properties": {
        "entity_id": {"$ref": "#/$defs/entity_id"},
        "radius": {"type": "number", "minimum": 4, "maximum": 48, "default": 24},
        "max_arrows": {"type": "integer", "minimum": 1, "maximum": 64, "default": 16}},
        "additionalProperties": False},
    "effects": ["attack", "move"], "preconditions": ["alive", "not_stopped"], "timeout_ms": 120000, "cancellable": True,
    "evidence": ["shots", "hits", "arrows_used", "kills", "health_start", "health_min", "max_range", "protected_holds", "shot_log"],
    "failure_codes": ["no_target", "lost_target", "protected_entity", "no_weapon", "no_ammo", "out_of_arrows", "too_close",
                      "cancelled", "timeout", "interrupted"],
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
