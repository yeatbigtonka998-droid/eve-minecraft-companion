# Skill registry contract

The file `contracts/skills/registry.json` is the **only** list of skills Eve can run. The mind validates every plan step against it (CP-41); the body implements it and reports what it has actually implemented in `hello.caps`. A skill is dispatchable only when it is in the registry **and** in `caps` with the same `version`.

## Entry format

```json
{
  "name": "walk_to",
  "version": 1,
  "summary": "Path to a block position with Baritone and stop within tolerance.",
  "params": { "type": "object", "required": ["pos"], "properties": {
      "pos": {"$ref": "#/$defs/pos"},
      "tolerance": {"type": "number", "minimum": 0.5, "maximum": 8, "default": 1.5},
      "allow_break": {"type": "boolean", "default": false},
      "allow_place": {"type": "boolean", "default": false}
  }, "additionalProperties": false },
  "effects": ["move"],
  "preconditions": ["alive", "not_stopped", "link_fresh"],
  "timeout_ms": 120000,
  "cancellable": true,
  "evidence": ["final_pos", "distance_to_goal", "blocks_broken", "blocks_placed"],
  "failure_codes": ["unreachable", "timeout", "cancelled", "zone_denied", "interrupted", "stuck"],
  "min_mode": "manual"
}
```

- `effects` ⊆ `move, break, place, attack, use_item, container, craft, chat, hazard`. The mind pre-checks `break/place/container/attack` against `permission.check` for the target when a position/entity is known; the server-helper is still the final authority.
- `params` is JSON Schema draft 2020-12 with `additionalProperties:false`. Shared `$defs` live at the top of the registry (`pos`, `item_id`, `block_id`, `count`).
- `evidence` names the keys the body **must** fill in `action.result.evidence` from server-observed facts (inventory diff, confirmed block change, entity death seen), never from "I sent the input".
- `failure_codes` is closed: a body that needs a new code adds it in the registry (bump `registry_version`) and notes it in STATUS.md.
- `min_mode` gates which control mode may run the skill; `autonomous`-only skills do not exist yet.

## Versioning

- `registry_version` is semver. Adding a skill or a backward-compatible optional param → minor bump. Changing a param, effect or evidence → major bump of **that skill's** `version` and the registry minor.
- `registry_hash` = sha256 of the canonical (sorted keys, no whitespace) JSON; both peers verify it at `hello`.
- The **body** chat may edit the registry for skills it is implementing; the **mind** chat may edit `$defs` and validation notes. Both must bump versions and announce the change in STATUS.md the same day. The orchestrator resolves conflicts.

## Initial set (v0.1.0)

Phase 1 primitives (CP-09..CP-12): `stop`, `look_at`, `walk_to`, `go_home`, `select_hotbar`, `sneak`, `jump`, `use_item`, `attack_entity`, `mine_block`, `place_block`, `open_container`, `chat_say`.
Phase 4/5 skills (CP-25..CP-40), added as they are implemented: `eat`, `equip_best`, `retreat`, `fight_melee`, `fight_ranged`, `withdraw`, `deposit`, `craft`, `smelt`, `harvest_replant`, `mine_resource`, `collect_drops`, `return_to_death_point`.
Phase 7 (CP-49..CP-53): `build_schematic`, `repair_zone`, `bridge`.

Each skill is a **bounded contract** (CP-19): it checks its preconditions itself, reports progress, honours cancellation within one tick, and ends with server-observed evidence or an explicit `uncertain`.

Note (2026-10-19): `walk_to` v1 as shipped is a **direct-input walker** (straight routes, no obstacles, slopes, water or edges) with exactly the registry's params, effects and evidence; Baritone v1.20.0 replaces the implementation under the same contract. The registry text was left unchanged so the hash `08646a3f…c059` stays valid for the running body and mind.
