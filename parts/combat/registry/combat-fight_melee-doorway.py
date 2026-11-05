"""O98: fight_melee gains an optional `doorway` param (the shelter door the mind passes at night). Additive patch on any base
registry, by default the assembled 0.16.0 draft; prints the canonical hash (mind config.canonical). Writes nothing: the
orchestrator assembles registry versions.

    py -3.12 parts/combat/registry/combat-fight_melee-doorway.py [base.json]
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BASE = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "contracts" / "skills" / "drafts" / "registry-0.16.0.json"

# {pos: the door cell as block_pos, out: the direction from inside to outside}. The $ref keeps the planner cost at +50 tokens
# by the O86g CI estimate (inline x/y/z: +69).
DOORWAY = {"type": "object", "required": ["pos", "out"], "properties": {
    "pos": {"$ref": "#/$defs/block_pos"}, "out": {"enum": ["north", "south", "east", "west"]}}, "additionalProperties": False}
SUMMARY_ADD = (" With doorway (the door cell and the direction out), a target outside the door is never followed out: she holds "
               "inside beside the door wall on the target's side, out of the line through the opening, shield up and facing the "
               "door, and engages when it enters the doorway cell (evidence door_hold_ticks).")

reg = json.loads(BASE.read_text(encoding="utf-8"))
fm = next(s for s in reg["skills"] if s["name"] == "fight_melee")
fm["params"]["properties"]["doorway"] = DOORWAY
if SUMMARY_ADD not in fm["summary"]:
    fm["summary"] += SUMMARY_ADD
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(f"{BASE.name} {reg['registry_version']} + fight_melee.doorway: hash", hashlib.sha256(canon).hexdigest())
