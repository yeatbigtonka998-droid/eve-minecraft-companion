"""H-range100 / O163 (Hunter: resource search out to 100 blocks): mine_resource.radius and gather.radius maximum 32 -> 128.
Nothing else changes (minimum 4, default 16 stay), so every caller of the base stays valid. Additive patch on any base
registry, by default the 0.15.2 draft; prints the canonical hash. Writes nothing: the orchestrator assembles registry versions.
With --out <file> it writes the patched registry for a dev build only. The jar takes any radius (MineResource.scan, O163).

    py -3.12 parts/skills/registry/skills-radius-128.py [base.json] [--out patched.json]
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
out_i = sys.argv.index("--out") if "--out" in sys.argv else None
args = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and i != (out_i or -1) + 1]
BASE = Path(args[0]) if args else REPO / "contracts" / "skills" / "drafts" / "registry-0.15.2.json"
OUT = Path(sys.argv[out_i + 1]) if out_i else None

reg = json.loads(BASE.read_text(encoding="utf-8"))
by_name = {s["name"]: s for s in reg["skills"]}
for name in ("mine_resource", "gather"):
    r = by_name[name]["params"]["properties"]["radius"]
    assert r["maximum"] in (32, 128), (name, r)
    r["maximum"] = 128
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(f"{BASE.name} {reg['registry_version']} + mine_resource/gather radius max 128: hash", hashlib.sha256(canon).hexdigest())
if OUT:
    OUT.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", OUT, "(dev build only)")
