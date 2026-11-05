"""Skills' registry 0.15.0 (owner: skills, orchestrator O86): contracts/skills/registry.json 0.14.0 + dig_up v1 (the way out
when go_home is home_blocked underground) + prospect's evidence seals and bridged (O71: declared at the next set). No other
entry changes. Prints the canonical hash (sorted keys, no whitespace, UTF-8) and asserts that the jar prefills exactly the
declared evidence keys (EveSkills.keys). --apply appends dig_up and rewrites prospect's line, as contracts/ keeps them,
once body, mind, combat and builder agree (the 0.14.0 procedure). --local PATH writes a dev copy (never contracts/).

    py -3.12 parts/skills/registry/skills-0.15.0.py [--apply | --local PATH]
"""
import hashlib, json, re, sys
from pathlib import Path

REG = Path(__file__).resolve().parents[3] / "contracts" / "skills" / "registry.json"
NEW = [{
    "name": "dig_up", "version": 1,
    "summary": "The way out when go_home is blocked underground (O86): a 1x2 staircase up, one block a step, toward `toward` "
               "(home or the mine entry; the larger horizontal axis first) with mine_block and place_block, using prospect's "
               "rules: a threat (health below min_health, a hit, a hostile in sight, drowning) ends it at once; lava touching the "
               "next step or over her head stops it; visible water is sealed with mined blocks; a missing step floor within 3 is "
               "bridged; a zone_denied face turns it 90 degrees. Ores are ignored and it never walks back. Without a pickaxe in the "
               "hotbar it pillars up an open column instead (look down, jump, place filler in the cell left behind at the top of "
               "the jump; stone or dirt first, planks while more than 6 are left) and ends no_tool when a solid block over her "
               "head would have to be broken. Ends ok when she stands at toward's level (or 2 below it under open sky); else "
               "failed, where she got to.",
    "params": {"type": "object", "properties": {
        "toward": {"$ref": "#/$defs/block_pos"},
        "max_blocks": {"type": "integer", "minimum": 8, "maximum": 256, "default": 128},
        "torch_every": {"type": "integer", "minimum": 0, "maximum": 32, "default": 8},
        "min_health": {"type": "number", "minimum": 2, "maximum": 20, "default": 12},
        "minutes": {"type": "integer", "minimum": 1, "maximum": 30, "default": 8}},
        "required": ["toward"], "additionalProperties": False},
    "effects": ["move", "break", "place"],
    "preconditions": ["alive", "not_stopped", "link_fresh"],
    "timeout_ms": 1900000, "cancellable": True,
    "evidence": ["climbed", "reached", "blocks_broken", "tunnel_length", "torches_placed", "stop_reason", "stop_at", "threat", "seals",
                 "bridged", "mined", "torch_failure"],
    "failure_codes": ["not_reached", "hazard", "inventory_full", "hurt", "no_tool", "unreachable", "zone_denied", "capped",
                      "precondition_failed", "cancelled", "timeout", "interrupted"],
    "min_mode": "manual"}]
PROSPECT_ADD = ["seals", "bridged"]

h = lambda d: hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()  # noqa: E731
reg = json.loads(REG.read_text(encoding="utf-8"))
assert reg["registry_version"] == "0.14.0", reg["registry_version"]
by_name = {s["name"]: s for s in reg["skills"]}
ev = by_name["prospect"]["evidence"]
ev += [k for k in PROSPECT_ADD if k not in ev]
reg["skills"] += [s for s in NEW if s["name"] not in by_name]
by_name = {s["name"]: s for s in reg["skills"]}
reg["registry_version"] = "0.15.0"
# the jar prefills exactly these keys (EveSkills.keys): an ok result missing one turns uncertain in the mind
src = (REG.parents[2] / "parts/skills/src/main/java/eve/skills/EveSkills.java").read_text(encoding="utf-8")
transfer = re.findall(r'"([a-z_]+)"', re.search(r"TRANSFER = \{(.*?)\};", src, re.S).group(1))
regs = re.findall(r'register\("([a-z_]+)", \d+, b -> keys\(b, (.*?)(?:new [A-Z]|Prospect\.digUp)', src, re.S)
assert {n for n, _ in regs} >= {"prospect", "dig_up"}, [n for n, _ in regs]
for name, body in regs:
    pre = transfer if "TRANSFER" in body else re.findall(r'"([a-z_]+)"', body)
    assert sorted(pre) == sorted(by_name[name]["evidence"]), (name, sorted(pre), sorted(by_name[name]["evidence"]))
print("registry 0.15.0 hash", h(reg), f"({len(reg['skills'])} skills)")
if "--local" in sys.argv:
    out = Path(sys.argv[sys.argv.index("--local") + 1])
    assert out.resolve() != REG.resolve(), "--local never writes contracts/"
    out.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("wrote", out)
if "--apply" in sys.argv:
    lines = []
    for line in REG.read_text(encoding="utf-8").splitlines():
        st = line.strip()
        if st.startswith('{"name": "prospect"'):
            line = "    " + json.dumps(by_name["prospect"], ensure_ascii=False) + ("," if st.endswith(",") else "")
        lines.append(line)
    text = "\n".join(lines).replace('"registry_version": "0.14.0"', '"registry_version": "0.15.0"')
    head, tail = text.rsplit("]", 1)
    REG.write_text(head.rstrip() + ",\n    " + json.dumps(NEW[0], ensure_ascii=False) + "\n  ]" + tail.rstrip() + "\n", encoding="utf-8", newline="\n")
    assert h(json.loads(REG.read_text(encoding="utf-8"))) == h(reg)
    print("applied to", REG)
