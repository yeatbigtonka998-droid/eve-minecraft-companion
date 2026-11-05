"""O124: new body primitive drop_item (Hunter live: "give me the stone pickaxe" -> come to the owner and hand it over). Additive patch
on any base registry, by default the assembled 0.16.0 draft; prints the canonical hash. Writes nothing: the orchestrator
assembles registry versions (draft v4). With --out <file> it writes the patched registry for a dev build only.

    py -3.12 parts/body/registry/body-drop_item.py [base.json] [--out patched.json]
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
args = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = Path(args[0]) if args else REPO / "contracts" / "skills" / "drafts" / "registry-0.16.0.json"
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None

DROP_ITEM = {
    "name": "drop_item",
    "version": 1,
    "summary": ("Toss count items of one kind on the ground (vanilla drop, as Q), optionally facing a block first (e.g. the owner's "
                "feet). The stack is taken from the hotbar or moved there from the main inventory; one toss per 10 ticks; the "
                "selected slot is restored. Protected kit (swords, pickaxes, shields, bread, worn armour) is refused unless kit_ok "
                "(the owner named that exact item). Fewer than count in her inventory ends partial."),
    "params": {
        "type": "object",
        "required": ["item"],
        "properties": {
            "item": {"type": "string", "pattern": "^[a-z0-9_.-]+:[a-z0-9_./-]+$"},
            "count": {"type": "integer", "minimum": 1, "maximum": 64, "default": 1},
            "toward": {"$ref": "#/$defs/block_pos"},
            "kit_ok": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    "effects": [],  # skill-registry.md fixes effects to move..hazard; no "inventory" (as select_hotbar, equip_best)
    "preconditions": ["alive", "not_stopped", "has_item"],
    "timeout_ms": 15000,
    "cancellable": True,
    "evidence": ["dropped", "entity_ids", "item_count_delta", "from_slot", "faced"],
    "failure_codes": ["no_item", "protected_kit", "partial", "cancelled", "timeout"],
    "min_mode": "manual",
}

reg = json.loads(BASE.read_text(encoding="utf-8"))
reg["skills"] = [s for s in reg["skills"] if s["name"] != "drop_item"] + [DROP_ITEM]
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(f"{BASE.name} {reg['registry_version']} + drop_item v1: hash", hashlib.sha256(canon).hexdigest())
if OUT:
    OUT.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("dev copy written:", OUT)
