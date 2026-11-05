"""The failure codes combat's last revs emit but no registry declares, so the mind reports them as `unlisted:<code>` (lane F x_wall
2026-10-22 16:46: `failed/unlisted:unreachable`):
- fight_melee: unreachable (O126, b6a34c83), bad_params (O136, eb70c278);
- fight_ranged: no_clear_shot (O116, cd2a8606), bad_params (O136).
Additive patch on any base registry, by default the 0.15.2 draft; prints the canonical hash. Writes nothing: the orchestrator
assembles registry versions. With --out <file> it writes the patched registry for a dev build only.

    py -3.12 parts/combat/registry/combat-failure_codes.py [base.json] [--out patched.json]
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
out_i = sys.argv.index("--out") if "--out" in sys.argv else None
args = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and i != (out_i or -1) + 1]
BASE = Path(args[0]) if args else REPO / "contracts" / "skills" / "drafts" / "registry-0.15.2.json"
OUT = Path(sys.argv[out_i + 1]) if out_i else None

ADD = {"fight_melee": ["unreachable", "bad_params"], "fight_ranged": ["no_clear_shot", "bad_params"]}

reg = json.loads(BASE.read_text(encoding="utf-8"))
for name, codes in ADD.items():
    s = next(s for s in reg["skills"] if s["name"] == name)
    for c in codes:
        if c not in s["failure_codes"]:
            s["failure_codes"].append(c)
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(f"{BASE.name} {reg['registry_version']} + fight_* failure codes {ADD}: hash", hashlib.sha256(canon).hexdigest())
if OUT:
    OUT.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", OUT, "(dev build only)")
