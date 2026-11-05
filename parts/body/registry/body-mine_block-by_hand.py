"""O101e: mine_block gains an optional `by_hand` param (the mind's dawn unseal of a stone seal without a pickaxe). Additive patch
on any base registry, by default the assembled 0.16.0 draft; prints the canonical hash. Writes nothing: the orchestrator
assembles registry versions.

    py -3.12 parts/body/registry/body-mine_block-by_hand.py [base.json]
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BASE = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "contracts" / "skills" / "drafts" / "registry-0.16.0.json"

BY_HAND = {"type": "boolean", "default": False}
SUMMARY_ADD = (" With by_hand, a block no tool she has can take is broken by hand (slow; one that needs the tool drops nothing; "
               "evidence by_hand_used) instead of failing no_tool.")

reg = json.loads(BASE.read_text(encoding="utf-8"))
mb = next(s for s in reg["skills"] if s["name"] == "mine_block")
mb["params"]["properties"]["by_hand"] = BY_HAND
if SUMMARY_ADD not in mb["summary"]:
    mb["summary"] += SUMMARY_ADD
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(f"{BASE.name} {reg['registry_version']} + mine_block.by_hand: hash", hashlib.sha256(canon).hexdigest())
