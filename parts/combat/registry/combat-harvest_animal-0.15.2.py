"""O146 (Hunter: "kill 3 sheep" for a bed): harvest_animal v1 into the 0.15.2 re-cut. The entry is copied verbatim from the 0.16.0
draft (the same entry combat-0.16.0.py writes, and the one jars 5a4e93c8 / ab953a6b carry), appended at the end as body's
patches do. Additive patch on any base registry, by default the 0.15.2 draft; prints the canonical hash. Writes nothing: the
orchestrator assembles registry versions. With --out <file> it writes the patched registry for a dev build only.

    py -3.12 parts/combat/registry/combat-harvest_animal-0.15.2.py [base.json] [--out patched.json]
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DRAFTS = REPO / "contracts" / "skills" / "drafts"
out_i = sys.argv.index("--out") if "--out" in sys.argv else None
args = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and i != (out_i or -1) + 1]
BASE = Path(args[0]) if args else DRAFTS / "registry-0.15.2.json"
OUT = Path(sys.argv[out_i + 1]) if out_i else None

entry = next(s for s in json.loads((DRAFTS / "registry-0.16.0.json").read_text(encoding="utf-8"))["skills"] if s["name"] == "harvest_animal")
reg = json.loads(BASE.read_text(encoding="utf-8"))
for ref in ("entity_id", "block_pos"):  # the entry's $refs must resolve in the base
    assert ref in reg["$defs"], f"{BASE.name} has no $defs.{ref}"
reg["skills"] = [s for s in reg["skills"] if s["name"] != "harvest_animal"] + [entry]
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(f"{BASE.name} {reg['registry_version']} + harvest_animal v1 (from the 0.16.0 draft): hash", hashlib.sha256(canon).hexdigest())
if OUT:
    OUT.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", OUT, "(dev build only)")
