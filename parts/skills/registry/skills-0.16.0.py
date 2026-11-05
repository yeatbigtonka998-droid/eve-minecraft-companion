"""Skills' registry 0.16.0 DRAFT (owner: skills; orchestrator O86h/O94): contracts/skills/registry.json 0.15.0 plus
- prospect's and dig_up's summaries trimmed to one sentence each, at most 120 chars (O86g/O86h: held for the next real change),
- skills' CP-39 primitives (O94 wave 2): shear_sheep, milk_cow (the entity grant: entity_ids + zone, as combat's
  harvest_animal takes it; combat owns the kill and merges its own entry), harvest_crop v1.
Nothing else changes: existing params, evidence, failure codes and versions stay, so 0.15.0 callers stay valid. Prints the
canonical hash (sorted keys, no whitespace, UTF-8) and asserts that the jar prefills exactly the declared evidence keys
(EveSkills.keys). Not applied by skills: the orchestrator merges the contract (O94). --local PATH writes a dev copy;
--entries PATH writes just the new entries (the schema sent for the merge); --apply is for the merge, never contracts/
before the go.

    py -3.12 parts/skills/registry/skills-0.16.0.py [--local PATH | --entries PATH | --apply]
"""
import hashlib, json, re, sys
from pathlib import Path

REG = Path(__file__).resolve().parents[3] / "contracts" / "skills" / "registry.json"
SUMMARY = {
    "prospect": "Bounded branch mining for unexposed ore: a 1x2 tunnel (a staircase down first if asked), its ores mined, then back.",
    "dig_up": "The way out when go_home is blocked underground: a staircase up toward `toward`, or a pillar without a pickaxe.",
}
BOX = {"min": {"$ref": "#/$defs/block_pos"}, "max": {"$ref": "#/$defs/block_pos"}}
COMMON = {"preconditions": ["alive", "not_stopped", "link_fresh"], "timeout_ms": 600000, "cancellable": True, "min_mode": "manual"}
TAIL = ["precondition_failed", "cancelled", "timeout", "interrupted"]
ANIMAL_EV = ["gathered", "animals", "refused", "stop_reason"]
# O94: the same entity grant as combat's harvest_animal: the granted ids (sense's entity_grant) and the zone box
GRANT = {"entity_ids": {"type": "array", "minItems": 1, "maxItems": 64, "uniqueItems": True, "items": {"$ref": "#/$defs/entity_id"}},
         "zone": {"type": "object", "required": ["min", "max"], "additionalProperties": False, "properties": BOX},
         "count": {"type": "integer", "minimum": 1, "maximum": 64}}
NEW = [
    {"name": "shear_sheep", "version": 1,
     "summary": "Shear the granted sheep (entity_ids) inside zone and pick up the wool; others refused with why, none chased out.",
     "params": {"type": "object", "required": ["entity_ids", "zone"], "additionalProperties": False, "properties": GRANT},
     "effects": ["move", "use_item"], "evidence": ANIMAL_EV,
     "failure_codes": ["not_found", "no_tool", "partial", "zone_denied"] + TAIL, **COMMON},
    {"name": "milk_cow", "version": 1,
     "summary": "Fill empty buckets from the granted cows (entity_ids) inside zone; others refused with why, none chased out.",
     "params": {"type": "object", "required": ["entity_ids", "zone"], "additionalProperties": False, "properties": GRANT},
     "effects": ["move", "use_item"], "evidence": ANIMAL_EV,
     "failure_codes": ["not_found", "no_tool", "partial", "zone_denied"] + TAIL, **COMMON},
    {"name": "harvest_crop", "version": 1,
     "summary": "Harvest count mature crops (one kind or any) on farmland in the farm box min..max and replant each plot.",
     "params": {"type": "object", "required": ["min", "max"], "additionalProperties": False, "properties": {
         **BOX, "crop": {"type": "string", "enum": ["minecraft:wheat", "minecraft:carrots", "minecraft:potatoes"]},
         "count": {"type": "integer", "minimum": 1, "maximum": 1024},
         "replant": {"type": "boolean", "default": True}}},
     "effects": ["move", "break", "place", "use_item"],
     "evidence": ["harvested", "replanted", "immature_left", "not_replanted", "trampled", "drops", "not_harvested", "walks", "trampled_at",
                  "stop_reason"],
     "failure_codes": ["partial", "not_found", "bad_area", "unreachable", "inventory_full", "zone_denied"] + TAIL, **COMMON},
]

h = lambda d: hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()  # noqa: E731
for n, s in list(SUMMARY.items()) + [(s["name"], s["summary"]) for s in NEW]:  # one sentence the mind's brief() keeps whole
    assert len(s) <= 120 and re.split(r"(?<=[.!?])\s", s, maxsplit=1)[0] == s, (n, len(s))
reg = json.loads(REG.read_text(encoding="utf-8"))
assert reg["registry_version"] == "0.15.0", reg["registry_version"]
by_name = {s["name"]: s for s in reg["skills"]}
for n, s in SUMMARY.items():
    by_name[n]["summary"] = s
reg["skills"] += [s for s in NEW if s["name"] not in by_name]
by_name = {s["name"]: s for s in reg["skills"]}
reg["registry_version"] = "0.16.0"
# the jar prefills exactly these keys (EveSkills.keys): an ok result missing one turns uncertain in the mind
src = (REG.parents[2] / "parts/skills/src/main/java/eve/skills/EveSkills.java").read_text(encoding="utf-8")
consts = {k: re.findall(r'"([a-z_]+)"', v) for k, v in re.findall(r"static final String\[\] ([A-Z]+) = \{(.*?)\};", src, re.S)}
regs = re.findall(r'register\("([a-z_]+)", \d+, b -> keys\(b, (.*?)(?:new [A-Z])', src, re.S)
assert {n for n, _ in regs} >= {s["name"] for s in NEW}, [n for n, _ in regs]
for name, body in regs:
    pre = re.findall(r'"([a-z_]+)"', body) + [k for c, ks in consts.items() if re.search(r"\b" + c + r"\b", body) for k in ks]
    assert sorted(pre) == sorted(by_name[name]["evidence"]), (name, sorted(pre), sorted(by_name[name]["evidence"]))
print("registry 0.16.0 DRAFT hash", h(reg), f"({len(reg['skills'])} skills)")
if "--local" in sys.argv:
    out = Path(sys.argv[sys.argv.index("--local") + 1])
    assert out.resolve() != REG.resolve(), "--local never writes contracts/"
    out.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("wrote", out)
if "--entries" in sys.argv:
    out = Path(sys.argv[sys.argv.index("--entries") + 1])
    out.write_text(json.dumps({"summaries": SUMMARY, "new_skills": NEW}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("wrote", out)
if "--apply" in sys.argv:  # the orchestrator's merge (O94): two summary lines rewritten, the new entries appended, the version
    lines = []
    for line in REG.read_text(encoding="utf-8").splitlines():
        st = line.strip()
        name = next((n for n in SUMMARY if st.startswith(f'{{"name": "{n}"')), None)
        if name:
            line = "    " + json.dumps(by_name[name], ensure_ascii=False) + ("," if st.endswith(",") else "")
        lines.append(line)
    text = "\n".join(lines).replace('"registry_version": "0.15.0"', '"registry_version": "0.16.0"')
    head, tail = text.rsplit("]", 1)
    REG.write_text(head.rstrip() + ",\n" + ",\n".join("    " + json.dumps(s, ensure_ascii=False) for s in NEW) + "\n  ]" + tail.rstrip() + "\n",
                   encoding="utf-8", newline="\n")
    assert h(json.loads(REG.read_text(encoding="utf-8"))) == h(reg)
    print("applied to", REG)
