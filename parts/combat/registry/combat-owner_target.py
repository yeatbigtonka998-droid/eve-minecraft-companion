"""O136 (Hunter live: "fight the wandering trader" -> "entity 5861 (wandering_trader) is protected"; his rule: "when I say to fight
a mob she needs to override her protected rule, just until that mob I told her to fight or kill is dead"). fight_melee and
fight_ranged gain an optional `owner_target` flag. Additive patch on any base registry, by default the 0.15.1 draft; prints the
canonical hash (mind config.canonical). Writes nothing: the orchestrator assembles registry versions. With --out <file> it writes
the patched registry for a dev build only.

    py -3.12 parts/combat/registry/combat-owner_target.py [base.json] [--out patched.json]

fight_melee swings through body's attack_entity, which re-checks protection itself: that needs body's matching attack_entity
`owner_target` (body's patch), or a flagged fight_melee ends protected_entity at its first swing. fight_ranged needs nothing more.
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
args = [a for a in sys.argv[1:] if not a.startswith("--") and not (sys.argv.index(a) > 0 and sys.argv[sys.argv.index(a) - 1] == "--out")]
BASE = Path(args[0]) if args else REPO / "contracts" / "skills" / "drafts" / "registry-0.15.1.json"
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None

OWNER_TARGET = {"type": "boolean", "default": False}
SUMMARY_ADD = (" With owner_target (only with entity_id: the owner named this mob), the protected-type rule is lifted for that one "
               "entity until it dies or the action ends; never for a player; other protected mobs stay protected (sweep and line "
               "of fire).")

reg = json.loads(BASE.read_text(encoding="utf-8"))
for name in ("fight_melee", "fight_ranged"):
    s = next(s for s in reg["skills"] if s["name"] == name)
    s["params"]["properties"]["owner_target"] = OWNER_TARGET
    if SUMMARY_ADD not in s["summary"]:
        s["summary"] += SUMMARY_ADD
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(f"{BASE.name} {reg['registry_version']} + fight_melee/fight_ranged.owner_target: hash", hashlib.sha256(canon).hexdigest())
if OUT:
    OUT.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", OUT, "(dev build only)")
