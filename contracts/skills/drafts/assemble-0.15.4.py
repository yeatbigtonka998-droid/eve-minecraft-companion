"""Kickoff: registry 0.15.4 DRAFT = 0.15.3 draft + body's follow_boat v1 + skills' shear_sheep / milk_cow / harvest_crop v1
(the O94 primitives the jar already ships, registry-independent). Reads the two owners' scripts' outputs, writes
registry-0.15.4.json next to this file, prints the canonical hash.

    py -3.12 contracts/skills/drafts/assemble-0.15.4.py <boat-patched.json> <skills-entries.json>
"""
import hashlib, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
reg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))          # 0.15.3 + follow_boat (body-follow_boat.py --out)
new = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))["new_skills"]  # skills-0.16.0.py --entries
have = {s["name"] for s in reg["skills"]}
assert "follow_boat" in have and reg["registry_version"] == "0.15.3", (have, reg["registry_version"])
reg["skills"] += [s for s in new if s["name"] not in have]
reg["registry_version"] = "0.15.4"
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
out = HERE / "registry-0.15.4.json"
out.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
print(f"registry 0.15.4 DRAFT hash {hashlib.sha256(canon).hexdigest()} ({len(reg['skills'])} skills) -> {out.name}")
