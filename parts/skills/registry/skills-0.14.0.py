"""Skills' registry 0.14.0 (owner: skills, orchestrator O58): contracts/skills/registry.json 0.13.0 + every evidence key the
skills' results carry declared on their entries (prospect's threat and stop_at for the mind's O58 runner, and the rest the
audit found undeclared). Prospect's summary now says how a threat stops it. No params, failure codes or versions change, so
0.13.0 callers stay valid. Prints the canonical hash (sorted keys, no whitespace, UTF-8). --apply rewrites the changed
entries in place, one skill per line, as contracts/ keeps it. It is run only when the mind's O58 runner is ready, so both
land in one lane refresh. --local PATH writes a dev copy (never contracts/).

    py -3.12 parts/skills/registry/skills-0.14.0.py [--apply | --local PATH]
"""
import hashlib, json, sys
from pathlib import Path

REG = Path(__file__).resolve().parents[3] / "contracts" / "skills" / "registry.json"
ADD = {  # skill -> evidence keys appended (each is filled on every end; EveSkills prefills them with null)
    "craft": ["missing"],
    "smelt": ["input", "fuel", "accepted_inputs", "cook_ms"],
    "withdraw": ["clicks"],
    "deposit": ["clicks"],
    "harvest_replant": ["not_harvested", "walks", "trampled_at"],
    "prospect": ["threat", "stop_at", "torch_failure"],
}
PROSPECT_SUMMARY = (
    "Bounded branch mining for ore that is not exposed: optionally a staircase down to descend_to first, then a 1x2 tunnel "
    "straight ahead along direction (default: her facing; never up) with mine_block, every target ore it exposes within reach "
    "mined, outline-only plants in its way cleared, a torch every torch_every blocks, then back to the start. Stops on count, "
    "max_blocks, full inventory, the timer, or a hazard ahead (lava, water, no floor). A threat checked every tick (health "
    "below min_health, a hit just taken, a hostile within 6 in sight) ends it at once where she stands: hurt, with evidence "
    "threat {kind, health, entity_id, type, distance} and stop_at {x, y, z}, no walk back.")

h = lambda d: hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()  # noqa: E731
reg = json.loads(REG.read_text(encoding="utf-8"))
assert reg["registry_version"] == "0.13.0", reg["registry_version"]
by_name = {s["name"]: s for s in reg["skills"]}
for name, keys in ADD.items():
    ev = by_name[name]["evidence"]
    ev += [k for k in keys if k not in ev]
by_name["prospect"]["summary"] = PROSPECT_SUMMARY
reg["registry_version"] = "0.14.0"
# the jar prefills exactly these keys (EveSkills.keys), so an ok result never misses one (mind: missing_evidence -> uncertain)
import re  # noqa: E402
src = (REG.parents[2] / "parts/skills/src/main/java/eve/skills/EveSkills.java").read_text(encoding="utf-8")
transfer = re.findall(r'"([a-z_]+)"', re.search(r"TRANSFER = \{(.*?)\};", src, re.S).group(1))
for name, body in re.findall(r'register\("([a-z_]+)", \d+, b -> keys\(b, (.*?)new [A-Z]', src, re.S):
    pre = transfer if "TRANSFER" in body else re.findall(r'"([a-z_]+)"', body)
    assert sorted(pre) == sorted(by_name[name]["evidence"]), (name, sorted(pre), sorted(by_name[name]["evidence"]))
print("registry 0.14.0 hash", h(reg), f"({len(reg['skills'])} skills)")
if "--local" in sys.argv:
    out = Path(sys.argv[sys.argv.index("--local") + 1])
    assert out.resolve() != REG.resolve(), "--local never writes contracts/"
    out.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("wrote", out)
if "--apply" in sys.argv:
    lines = []
    for line in REG.read_text(encoding="utf-8").splitlines():
        st = line.strip()
        if st.startswith('{"name": "') and json.loads(st.rstrip(","))["name"] in ADD:
            line = "    " + json.dumps(by_name[json.loads(st.rstrip(","))["name"]], ensure_ascii=False) + ("," if st.endswith(",") else "")
        lines.append(line)
    text = "\n".join(lines).replace('"registry_version": "0.13.0"', '"registry_version": "0.14.0"')
    REG.write_text(text + "\n", encoding="utf-8", newline="\n")
    assert h(json.loads(REG.read_text(encoding="utf-8"))) == h(reg)
    print("applied to", REG)
