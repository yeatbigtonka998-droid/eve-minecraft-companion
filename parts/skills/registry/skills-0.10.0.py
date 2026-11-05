"""Skills' proposed registry 0.10.0: contracts/skills/registry.json (0.9.0) + gather v1. Prints the canonical hash
(mind config.canonical: sorted keys, no whitespace, UTF-8); --apply writes the registry only after body and mind agree.

    py -3.12 parts/skills/registry/skills-0.10.0.py [--apply]
"""
import hashlib, json, sys
from pathlib import Path

REG = Path(__file__).resolve().parents[3] / "contracts" / "skills" / "registry.json"
NEW = [{
    "name": "gather", "version": 1,
    "summary": "Gather a renewable resource by qualified means only: wood = mine_resource over #minecraft:logs; wool = shears on an adult unsheared sheep, then the wool picked up; milk = a bucket on an adult cow. Never kills; protected animals (pets, named, envelope extra types) and babies are skipped and counted; no eligible animal or no tool ends as a bounded shortage report.",
    "params": {"type": "object", "required": ["product", "count"], "properties": {
        "product": {"type": "string", "enum": ["wood", "wool", "milk"]},
        "count": {"type": "integer", "minimum": 1, "maximum": 256},
        "radius": {"type": "integer", "minimum": 4, "maximum": 32, "default": 16}}, "additionalProperties": False},
    "effects": ["move", "break", "use_item"],
    "preconditions": ["alive", "not_stopped", "link_fresh"],
    "timeout_ms": 600000, "cancellable": True,
    "evidence": ["gathered", "animals", "protected_skipped", "babies_skipped"],
    "failure_codes": ["not_found", "no_tool", "partial", "inventory_full", "unreachable", "zone_denied", "precondition_failed",
                      "cancelled", "timeout", "interrupted"],
    "min_mode": "manual"}]

reg = json.loads(REG.read_text(encoding="utf-8"))
assert reg["registry_version"] == "0.9.0", reg["registry_version"]
names = {s["name"] for s in reg["skills"]}
reg["skills"] += [s for s in NEW if s["name"] not in names]
reg["registry_version"] = "0.10.0"
h = lambda d: hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()  # noqa: E731
print("registry 0.10.0 hash", h(reg), f"({len(reg['skills'])} skills)")
if "--apply" in sys.argv:
    text = REG.read_text(encoding="utf-8").rstrip()
    head, tail = text.rsplit("]", 1)
    head = head.rstrip().replace('"registry_version": "0.9.0"', '"registry_version": "0.10.0"')
    lines = [json.dumps(s, ensure_ascii=False) for s in NEW if s["name"] not in names]
    REG.write_text(head + ",\n    " + ",\n    ".join(lines) + "\n  ]" + tail + "\n", encoding="utf-8", newline="\n")
    assert h(json.loads(REG.read_text(encoding="utf-8"))) == h(reg)
    print("applied to", REG)
