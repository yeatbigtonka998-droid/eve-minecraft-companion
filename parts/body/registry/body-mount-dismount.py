"""O145 (Hunter live: "get in the wood boat"; riding with him is "extremely important for survival and companionship"): mount v1 and
dismount v1. Additive patch on any base registry, by default the 0.15.2 draft; prints the canonical hash. Writes nothing: the
orchestrator assembles registry versions. With --out <file> it writes a dev copy.

    py -3.12 parts/body/registry/body-mount-dismount.py [base.json] [--out patched.json]
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
args = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = Path(args[0]) if args else REPO / "contracts" / "skills" / "drafts" / "registry-0.15.2.json"
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None

MOUNT = {
    "name": "mount",
    "version": 1,
    "summary": ("Get into or onto a rideable entity: walk within reach, then right-click it (vanilla). Boats (2 seats; a chest boat "
                "1), minecarts, horses, donkeys, mules, llamas, camels (2 seats), and a saddled pig or strider. Leaves a vehicle she "
                "is already in first."),
    "params": {"type": "object", "required": ["entity_id"], "properties": {"entity_id": {"$ref": "#/$defs/entity_id"}},
               "additionalProperties": False},
    "effects": ["move", "use_item"],
    "preconditions": ["alive", "not_stopped"],
    "timeout_ms": 20000,
    "cancellable": True,
    "evidence": ["vehicle_id", "seat"],
    "failure_codes": ["no_seat", "unreachable", "not_rideable", "cancelled", "timeout"],
    "min_mode": "manual",
}
DISMOUNT = {
    "name": "dismount",
    "version": 1,
    "summary": "Leave the vehicle she rides (vanilla sneak). Not in one: ok, nothing to do.",
    "params": {"type": "object", "properties": {}, "additionalProperties": False},
    "effects": ["move"],
    "preconditions": ["alive", "not_stopped"],
    "timeout_ms": 3000,
    "cancellable": False,
    "evidence": ["pos"],
    "failure_codes": ["timeout"],
    "min_mode": "manual",
}

reg = json.loads(BASE.read_text(encoding="utf-8"))
reg["skills"] = [s for s in reg["skills"] if s["name"] not in ("mount", "dismount")] + [MOUNT, DISMOUNT]
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(f"{BASE.name} {reg['registry_version']} + mount v1 + dismount v1: hash", hashlib.sha256(canon).hexdigest())
if OUT:
    OUT.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("dev copy written:", OUT)
