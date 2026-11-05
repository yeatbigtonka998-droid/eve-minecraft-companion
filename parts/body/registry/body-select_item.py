"""select_item v1 (kickoff, mind-c's boat flow: no way to bring a main-inventory item into the hotbar): hold an item, pulling its
stack into the hotbar first as place_block does (O130). Additive patch on any base registry, by default the 0.15.2 draft; prints
the canonical hash. Writes nothing: the orchestrator assembles registry versions. With --out <file> it writes a dev copy.

    py -3.12 parts/body/registry/body-select_item.py [base.json] [--out patched.json]
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
args = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = Path(args[0]) if args else REPO / "contracts" / "skills" / "drafts" / "registry-0.15.2.json"
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None

SELECT_ITEM = {
    "name": "select_item",
    "version": 1,
    "summary": ("Hold an item: select the hotbar slot that has it, or first move its stack from the main inventory into the hotbar "
                "(an empty slot, else 8, or 7 when 8 is held), as place_block does (O130)."),
    "params": {"type": "object", "required": ["item"],
               "properties": {"item": {"type": "string", "pattern": "^[a-z0-9_.-]+:[a-z0-9_./-]+$"}},
               "additionalProperties": False},
    "effects": [],
    "preconditions": ["alive", "not_stopped", "has_item"],
    "timeout_ms": 2000,
    "cancellable": False,
    "evidence": ["hotbar_slot", "mainhand_item", "item_from_inventory"],
    "failure_codes": ["no_item", "timeout"],
    "min_mode": "manual",
}

reg = json.loads(BASE.read_text(encoding="utf-8"))
reg["skills"] = [s for s in reg["skills"] if s["name"] != "select_item"] + [SELECT_ITEM]
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(f"{BASE.name} {reg['registry_version']} + select_item v1: hash", hashlib.sha256(canon).hexdigest())
if OUT:
    OUT.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("dev copy written:", OUT)
