"""H-boat follow_boat v1 (kickoff S93: follow gains a water mode; Hunter: riding with him is "extremely important"). Additive patch on
any base registry, by default the 0.15.3 draft; prints the canonical hash. Writes nothing: the orchestrator assembles registry
versions (0.15.4). With --out <file> it writes a dev copy.

    py -3.12 parts/body/registry/body-follow_boat.py [base.json] [--out patched.json]
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
args = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT_RAW = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
OUT = Path(OUT_RAW) if OUT_RAW else None
if OUT_RAW in args:
    args.remove(OUT_RAW)  # the --out path is not the base
BASE = Path(args[0]) if args else REPO / "contracts" / "skills" / "drafts" / "registry-0.15.3.json"

FOLLOW_BOAT = {
    "name": "follow_boat",
    "version": 1,
    "summary": ("Follow the owner across water by boat: place her boat on the nearest water within 4 and get in, steer to stay "
                "keep_min..keep_max behind him while he is on water, then land at the nearest shore when he leaves it, get out, "
                "break the boat and pick it back up. Progress notes carry the phase (launch | sail | land | recover)."),
    "params": {"type": "object", "required": ["entity_id"],
               "properties": {"entity_id": {"$ref": "#/$defs/entity_id"},
                              "keep_min": {"type": "number", "minimum": 1, "maximum": 16, "default": 4},
                              "keep_max": {"type": "number", "minimum": 2, "maximum": 24, "default": 8}},
               "additionalProperties": False},
    "effects": ["move", "use_item", "attack"],
    "preconditions": ["alive", "not_stopped"],
    "timeout_ms": 600000,
    "cancellable": True,
    "evidence": ["boat_placed", "mounted", "landed_at", "boat_recovered", "distance_to_owner", "phase"],
    "failure_codes": ["no_boat", "no_water", "owner_gone", "unreachable", "cancelled", "timeout"],
    "min_mode": "manual",
}

reg = json.loads(BASE.read_text(encoding="utf-8"))
reg["skills"] = [s for s in reg["skills"] if s["name"] != "follow_boat"] + [FOLLOW_BOAT]
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(f"{BASE.name} {reg['registry_version']} + follow_boat v1: hash", hashlib.sha256(canon).hexdigest())
if OUT:
    OUT.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("dev copy written:", OUT)
