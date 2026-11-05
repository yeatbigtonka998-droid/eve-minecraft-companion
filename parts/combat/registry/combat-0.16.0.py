"""O94 CP-39: combat's proposed registry delta, harvest_animal (v1) on 0.15.0. Prints the canonical hash (mind config.canonical:
sorted keys, no whitespace, UTF-8). Writes nothing into contracts/ unless --apply is given, and the orchestrator said no
registry apply for O94. If another part's delta lands first, rebase PREV/VER and re-run. Same layout as combat-0.7.0.py.

    py -3.12 parts/combat/registry/combat-0.16.0.py [--apply]
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
REG = REPO / "contracts" / "skills" / "registry.json"
PREV, VER = "0.15.0", "0.16.0"

NEW = [{
    "name": "harvest_animal", "version": 1,
    "summary": "Kill exactly the animals in entity_ids (sense's kill grant) with Eve's best melee weapon and walk onto their drops. "
               "Never hits an id outside entity_ids (the mind's permission.check-filtered candidates), an excluded one, one not in "
               "species, a baby, a named, tamed, leashed or protected animal; never takes a species below keep_min adults in the "
               "zone (counted from what the client sees, so it stops early, never late); never walks or chases outside the zone box "
               "(every walk target is clamped into it; a target that leaves the zone is dropped). Ends ok after at least one kill. "
               "Every skipped id is in evidence.refused as {id, rule_id}, as shear_sheep / milk_cow report it: sense's rule ids "
               "where sense has the rule (animal:baby, animal:leashed, protected:named, protected:pets), else client:gone, "
               "client:wrong_type, client:excluded, client:out_of_zone, client:left_zone, client:keep_floor, client:denied (three "
               "swings did no damage: the server refused the hit; its rule id is in the action note from sense's permission.denied)",
    "params": {"type": "object", "required": ["entity_ids", "zone"], "properties": {
        "entity_ids": {"type": "array", "items": {"$ref": "#/$defs/entity_id"}, "minItems": 1, "maxItems": 64},
        "exclude_ids": {"type": "array", "items": {"$ref": "#/$defs/entity_id"}, "maxItems": 256},
        "zone": {"type": "object", "required": ["min", "max"], "properties": {"min": {"$ref": "#/$defs/block_pos"}, "max": {"$ref": "#/$defs/block_pos"}},
                 "additionalProperties": False},
        "species": {"type": "array", "items": {"type": "string", "pattern": "^[a-z0-9_.-]+:[a-z0-9_./-]+$"}, "maxItems": 16},
        "keep_min": {"type": "integer", "minimum": 0, "maximum": 256},
        "max_kills": {"type": "integer", "minimum": 1, "maximum": 64},
        "collect_drops": {"type": "boolean", "default": True}},
        "additionalProperties": False},
    "effects": ["attack", "move"], "preconditions": ["alive", "not_stopped"], "timeout_ms": 300000, "cancellable": True,
    "evidence": ["kills", "drops", "refused", "swings", "targets"],
    "failure_codes": ["out_of_zone", "not_granted", "no_target", "cancelled", "timeout", "interrupted"],
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
