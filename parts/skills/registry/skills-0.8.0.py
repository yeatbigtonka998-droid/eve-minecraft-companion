"""Skills' proposed registry 0.8.0: contracts/skills/registry.json (0.7.0) + harvest_replant v1. Prints the canonical hash
(mind config.canonical: sorted keys, no whitespace, UTF-8); --apply writes the registry only after body and mind agree.

    py -3.12 parts/skills/registry/skills-0.8.0.py [--apply]
"""
import hashlib, json, sys
from pathlib import Path

REG = Path(__file__).resolve().parents[3] / "contracts" / "skills" / "registry.json"
NEW = [{
    "name": "harvest_replant", "version": 1,
    "summary": "Harvest every mature wheat/carrot/potato crop in the farm box min..max (at most 32x4x32) and replant each harvested plot with the matching seed; immature crops are left, farmland is never jumped on; missing seeds end as a partial report.",
    "params": {"type": "object", "required": ["min", "max"], "properties": {
        "min": {"$ref": "#/$defs/block_pos"}, "max": {"$ref": "#/$defs/block_pos"},
        "replant": {"type": "boolean", "default": True}}, "additionalProperties": False},
    "effects": ["move", "break", "place", "use_item"],
    "preconditions": ["alive", "not_stopped", "link_fresh"],
    "timeout_ms": 600000, "cancellable": True,
    "evidence": ["harvested", "replanted", "immature_left", "not_replanted", "trampled", "drops"],
    "failure_codes": ["partial", "bad_area", "unreachable", "inventory_full", "zone_denied", "precondition_failed",
                      "cancelled", "timeout", "interrupted"],
    "min_mode": "manual"}]

reg = json.loads(REG.read_text(encoding="utf-8"))
assert reg["registry_version"] == "0.7.0", reg["registry_version"]
names = {s["name"] for s in reg["skills"]}
reg["skills"] += [s for s in NEW if s["name"] not in names]
reg["registry_version"] = "0.8.0"
h = lambda d: hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()  # noqa: E731
print("registry 0.8.0 hash", h(reg), f"({len(reg['skills'])} skills)")
if "--apply" in sys.argv:
    text = REG.read_text(encoding="utf-8").rstrip()
    head, tail = text.rsplit("]", 1)
    head = head.rstrip().replace('"registry_version": "0.7.0"', '"registry_version": "0.8.0"')
    lines = [json.dumps(s, ensure_ascii=False) for s in NEW if s["name"] not in names]
    REG.write_text(head + ",\n    " + ",\n    ".join(lines) + "\n  ]" + tail + "\n", encoding="utf-8", newline="\n")
    assert h(json.loads(REG.read_text(encoding="utf-8"))) == h(reg)
    print("applied to", REG)
