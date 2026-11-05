"""O136 (Hunter: "fight the wandering trader" -> protected): attack_entity takes owner_target: true, which suspends the
protected-type rule for that one entity_id (never for a player; the sweep check still guards every other protected entity).
combat's fight_melee passes it only when it was given the flag with that entity_id. Additive patch on any base registry, by
default the 0.15.1 draft; prints the canonical hash. Writes nothing: the orchestrator assembles registry versions.

    py -3.12 parts/body/registry/body-attack_entity-owner_target.py [base.json] [--out patched.json]
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
args = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = Path(args[0]) if args else REPO / "contracts" / "skills" / "drafts" / "registry-0.15.1.json"
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None

reg = json.loads(BASE.read_text(encoding="utf-8"))
a = next(s for s in reg["skills"] if s["name"] == "attack_entity")
a["params"]["properties"]["owner_target"] = {"type": "boolean", "default": False}
a["summary"] = ("One melee swing at an entity in reach, respecting cooldown and protection. owner_target: the owner named this "
                "entity: its protected type does not stop the swing (never a player); protected entities in the sweep still do.")
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(f"{BASE.name} {reg['registry_version']} + attack_entity owner_target: hash", hashlib.sha256(canon).hexdigest())
if OUT:
    OUT.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("dev copy written:", OUT)
