"""Body's proposed registry 0.13.0: skills' 0.12.0 (prospect) + equip_best v1 (O41, CP-40) + `interrupted` in the failure codes
of attack_entity, use_item, place_block and open_container (a reflex pre-empting them already ends them so). Prints the
canonical hash (sorted keys, no whitespace, UTF-8). While contracts/ is at 0.11.0 it computes on skills' 0.12.0 proposal in
memory, and --apply writes both entries in one bump (0.11.0 -> 0.13.0, orchestrator) once skills and mind agree. --local PATH writes the 0.13.0 file to
PATH (a dev build's copy; never contracts/).

    py -3.12 parts/body/registry/body-0.13.0.py [--apply | --local PATH]
"""
import contextlib, hashlib, io, json, runpy, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
REG = REPO / "contracts" / "skills" / "registry.json"
NEW = [{
    "name": "equip_best", "version": 1,
    "summary": "Equip the best available gear from Eve's own inventory: per armour slot the best piece by armour + toughness (a nearly broken piece counts as nothing; binding is never touched), a shield into the off hand, and in the main hand the best correct tool for for_block (destroy speed, more than 5 uses left) or with weapon the best sword/axe by attack damage, moved into the selected hotbar slot. Legal inventory clicks only; evidence read 10 ticks after the last click (what the server kept).",
    "params": {"type": "object", "properties": {
        "slots": {"type": "array", "maxItems": 5, "items": {"type": "string", "enum": ["head", "chest", "legs", "feet", "offhand"]}},
        "for_block": {"$ref": "#/$defs/block_id"},
        "weapon": {"type": "boolean", "default": False}},
        "additionalProperties": False},
    "effects": [], "preconditions": ["alive", "not_stopped"],
    "timeout_ms": 5000, "cancellable": True,
    "evidence": ["before", "after", "changed"],
    "failure_codes": ["obstructed", "cancelled", "timeout", "interrupted"],
    "min_mode": "manual"}]
# A reflex pre-empting a hand skill ends it failed/interrupted (Arbiter.unpause), but these entries never listed the code, so the
# mind stored "unlisted:interrupted" (lane B bow_draw attempt 2): list it.
INTERRUPTIBLE = ("attack_entity", "use_item", "place_block", "open_container")

h = lambda d: hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()  # noqa: E731
reg = json.loads(REG.read_text(encoding="utf-8"))
if reg["registry_version"] == "0.11.0":
    argv = sys.argv
    sys.argv = [str(REPO / "parts/skills/registry/skills-0.12.0.py")]
    with contextlib.redirect_stdout(io.StringIO()):
        reg = runpy.run_path(sys.argv[0])["reg"]
    sys.argv = argv
assert reg["registry_version"] == "0.12.0", reg["registry_version"]
for s in reg["skills"]:
    if s["name"] in INTERRUPTIBLE and "interrupted" not in s["failure_codes"]:
        s["failure_codes"].append("interrupted")
names = {s["name"] for s in reg["skills"]}
reg["skills"] += [s for s in NEW if s["name"] not in names]
reg["registry_version"] = "0.13.0"
print("registry 0.13.0 hash", h(reg), f"({len(reg['skills'])} skills)")
if "--local" in sys.argv:
    out = Path(sys.argv[sys.argv.index("--local") + 1])
    assert out.resolve() != REG.resolve(), "--local never writes contracts/"
    out.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("wrote", out)
if "--apply" in sys.argv:  # one skill per line, as contracts/ keeps it: changed entries rewritten in place, new ones appended
    by_name = {s["name"]: s for s in reg["skills"]}
    lines, seen = [], set()
    for line in REG.read_text(encoding="utf-8").splitlines():
        st = line.strip()
        if st.startswith('{"name": "'):
            name = json.loads(st.rstrip(","))["name"]
            seen.add(name)
            line = "    " + json.dumps(by_name[name], ensure_ascii=False) + ("," if st.endswith(",") else "")
        lines.append(line)
    text = "\n".join(lines).replace('"registry_version": "0.12.0"', '"registry_version": "0.13.0"').replace(
        '"registry_version": "0.11.0"', '"registry_version": "0.13.0"')  # straight 0.11.0 -> 0.13.0 (orchestrator: one bump)
    head, tail = text.rsplit("]", 1)
    extra = [json.dumps(s, ensure_ascii=False) for s in reg["skills"] if s["name"] not in seen]  # prospect (skills) + equip_best
    REG.write_text(head.rstrip() + "".join(",\n    " + e for e in extra) + "\n  ]" + tail.rstrip() + "\n", encoding="utf-8", newline="\n")
    assert h(json.loads(REG.read_text(encoding="utf-8"))) == h(reg)
    print("applied to", REG)
