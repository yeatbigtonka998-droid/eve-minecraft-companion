"""Builds the skills part's proposed registry: contracts/skills/registry.json + craft, smelt, withdraw, deposit (v1 each),
registry_version 0.3.0. Prints the canonical hash (mind config.canonical: sorted keys, no whitespace, UTF-8). Writes nothing into
contracts/ unless --apply is given (only after body and mind agree in STATUS).

    py -3.12 parts/skills/registry/skills-0.3.0.py [--apply]
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
REG = REPO / "contracts" / "skills" / "registry.json"
POS = {"$ref": "#/$defs/block_pos"}
ITEM = {"$ref": "#/$defs/item_id"}
OPEN = ["out_of_range", "obstructed", "zone_denied", "precondition_failed"]  # from opening the block (body's useBlock)
END = ["cancelled", "timeout", "interrupted"]


def skill(name, summary, props, req, effects, timeout, evidence, codes):
    return {"name": name, "version": 1, "summary": summary,
            "params": {"type": "object", "required": req, "properties": props, "additionalProperties": False},
            "effects": effects, "preconditions": ["alive", "not_stopped", "in_reach"], "timeout_ms": timeout,
            "cancellable": True, "evidence": evidence, "failure_codes": codes, "min_mode": "manual"}


NEW = [
    skill("craft", "Craft at least count of an item with the game's recipes: the inventory 2x2 grid, or the crafting table at pos (in reach).",
          {"item": ITEM, "count": {"$ref": "#/$defs/count"}, "pos": POS}, ["item", "count"], ["craft"], 180000,
          ["crafted", "consumed", "recipe", "batches", "workstation"],
          ["no_recipe", "missing_ingredients", "no_workstation", "partial", "inventory_full", "not_confirmed", *OPEN, *END]),
    skill("smelt", "Smelt count of an item in the furnace at pos (in reach, all slots empty) with Eve's own input and fuel; takes the output and unburnt fuel back.",
          {"pos": POS, "item": ITEM, "count": {"type": "integer", "minimum": 1, "maximum": 64}, "fuel": ITEM}, ["pos", "item", "count"],
          ["craft", "container"], 900000, ["smelted", "input_used", "fuel_used", "furnace_left", "inventory_delta"],
          ["no_recipe", "missing_ingredients", "no_fuel", "no_workstation", "workstation_busy", "stalled", "partial", *OPEN, *END]),
    skill("withdraw", "Take exactly count of an item from the container at pos (in reach) into the main inventory.",
          {"pos": POS, "item": ITEM, "count": {"$ref": "#/$defs/count"}}, ["pos", "item", "count"], ["container"], 120000,
          ["requested", "moved", "short", "container_before", "container_after", "inventory_delta", "foreign_change"],
          ["insufficient", "inventory_full", "not_a_container", *OPEN, *END]),
    skill("deposit", "Put exactly count of an item from the main inventory into the container at pos (in reach); tools, weapons, armour and the last 8 food stay unless allow_essentials.",
          {"pos": POS, "item": ITEM, "count": {"$ref": "#/$defs/count"}, "allow_essentials": {"type": "boolean", "default": False}},
          ["pos", "item", "count"], ["container"], 120000,
          ["requested", "moved", "short", "container_before", "container_after", "inventory_delta", "foreign_change"],
          ["insufficient", "container_full", "protected_item", "not_a_container", *OPEN, *END]),
    {**skill("mine_resource", "Mine count exposed blocks of one block id or block tag within radius: walk_to (no breaking on the way) then mine_block with the best hotbar tool; ends at the first server refusal.",
             {"block": {"$ref": "#/$defs/block_id"}, "tag": {"type": "string", "pattern": "^[a-z0-9_.-]+:[a-z0-9_./-]+$"},
              "count": {"type": "integer", "minimum": 1, "maximum": 256}, "max_blocks": {"type": "integer", "minimum": 1, "maximum": 512},
              "allow_vein": {"type": "boolean", "default": True}, "radius": {"type": "integer", "minimum": 4, "maximum": 32, "default": 16}},
             ["count"], ["move", "break"], 600000, ["mined", "attempts", "blocks", "drops"],
             ["not_found", "no_tool", "inventory_full", "unreachable", "partial", "zone_denied", "precondition_failed", *END]),
       "preconditions": ["alive", "not_stopped", "link_fresh"]},
]
NEW[-1]["params"]["oneOf"] = [{"required": ["block"]}, {"required": ["tag"]}]

reg = json.loads(REG.read_text(encoding="utf-8"))
names = {s["name"] for s in reg["skills"]}
reg["skills"] += [s for s in NEW if s["name"] not in names]
reg["registry_version"] = "0.3.0"
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print("registry 0.3.0 hash", hashlib.sha256(canon).hexdigest(), f"({len(reg['skills'])} skills)")
if "--apply" in sys.argv:
    text = REG.read_text(encoding="utf-8").rstrip()
    assert text.endswith("]\n}") or text.endswith("]}") or text.endswith("]\r\n}"), "unexpected registry tail"
    # keep the file's one-skill-per-line layout: insert the new lines before the closing "]"
    lines = [json.dumps(s, ensure_ascii=False) for s in NEW if s["name"] not in names]
    head, tail = text.rsplit("]", 1)
    head = head.rstrip().replace('"registry_version": "0.2.0"', '"registry_version": "0.3.0"')
    REG.write_text(head + ",\n    " + ",\n    ".join(lines) + "\n  ]" + tail + "\n", encoding="utf-8", newline="\n")
    check = json.loads(REG.read_text(encoding="utf-8"))
    assert hashlib.sha256(json.dumps(check, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest() \
        == hashlib.sha256(canon).hexdigest()
    print("applied to", REG)
