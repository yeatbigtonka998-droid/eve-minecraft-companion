"""O142 (Hunter live: "come to me" -> unreachable, no qualified route; his rule: she builds to reach him if she must): walk_to gains
build (Baritone places throwaway blocks from her inventory - dirt, cobblestone, netherrack, planks, never kit - to pillar up and
bridge, and also digs), max_place (default 32) and max_break (default 32). Zones/sense gate every place and break. Additive patch
on any base registry, by default the 0.15.2 draft; prints the canonical hash. Writes nothing: the orchestrator assembles versions.

    py -3.12 parts/body/registry/body-walk_to-build.py [base.json] [--out patched.json]
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
args = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = Path(args[0]) if args else REPO / "contracts" / "skills" / "drafts" / "registry-0.15.2.json"
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None

reg = json.loads(BASE.read_text(encoding="utf-8"))
w = next(s for s in reg["skills"] if s["name"] == "walk_to")
props = w["params"]["properties"]
props["build"] = {"type": "boolean", "default": False}
props["max_place"] = {"type": "integer", "minimum": 0, "maximum": 256, "default": 32}
props["max_break"] = {"type": "integer", "minimum": 0, "maximum": 256, "default": 32}
w["summary"] = ("Path to a position with Baritone; stop within tolerance. build: when walking alone finds no route, she may also "
                "place throwaway blocks from her inventory (dirt, cobblestone, netherrack, planks; never kit) to pillar up or bridge "
                "(at most max_place) and dig through (at most max_break); zones and the server gate every place and break.")
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(f"{BASE.name} {reg['registry_version']} + walk_to build: hash", hashlib.sha256(canon).hexdigest())
if OUT:
    OUT.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("dev copy written:", OUT)
