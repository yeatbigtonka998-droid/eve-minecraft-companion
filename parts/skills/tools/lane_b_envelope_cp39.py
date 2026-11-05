"""S96: the envelope cp39_tend.py needs on lane B: the lane's own envelope (its world_id, zones, owner) + v9's animal rules + the
CP-38 farmland box, as version 9. Reads, never writes the lane: eve-watch installs the output while it prepares the lane.
Asserts cp39_tend's own pre-flight finds no gap, and prints the sha256.

    py -3.12 parts/skills/tools/lane_b_envelope_cp39.py --out <file>
"""
import hashlib, json, sys
from pathlib import Path

out = Path(sys.argv[sys.argv.index("--out") + 1])
sys.argv = [sys.argv[0], "--lane", "B"]  # laneb_skills reads --lane on import
sys.path.insert(0, str(Path(__file__).parent))
import cp39_tend as T  # noqa: E402

L = T.L
base = json.loads((L.SERVER / "config" / "envelope.json").read_text(encoding="utf-8-sig"))
v9 = json.loads((L.REPO / "parts/server-helper/envelope-v9-cp39-draft.json").read_text(encoding="utf-8-sig"))
was = base.get("envelope_version")
base["envelope_version"] = 9
base["animals"] = v9["animals"]
base["farmland"] = [{"id": "cp38", "dim": "minecraft:overworld", "min": {"x": L.FX0, "y": L.FY, "z": min(L.FZ)},
                     "max": {"x": L.FX1, "y": L.FY, "z": max(L.FZ)}, "note": "the CP-38 rows; z UZ stays untagged"}]
gaps, q = T.envelope_gaps(base)
assert not gaps, gaps
text = json.dumps(base, indent=2, ensure_ascii=False) + "\n"
out.write_text(text, encoding="utf-8", newline="\n")
print(f"{out}: v{was} -> 9, world_id {base.get('world_id')}, cow milk quota {q}, pre-flight clean, sha256 {hashlib.sha256(text.encode('utf-8')).hexdigest()}")
