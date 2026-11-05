# Design project model (CP-47, CP-48, CP-56, CP-65)

Owner: orchestrator. Producer: **design**. Consumers: **mind** (approvals, task creation), **body** (`build_schematic`), **server-helper** (zone snapshots of the built result).

A design is a **folder**, not a database row, so it can be produced offline and inspected by hand:

```
designs/<project_id>/v<N>/
  brief.json         purpose, site, style, budget, constraints, novelty, approval_category, authored_by, ts
  program.eve        the building-language source (text) — or organic.json for the mesh path
  design.json        the compiled, labelled grid (below)
  design.litematic   Litematica export of the FINAL blocks only (no scaffold)
  scaffold.json      temporary blocks, same grid format, built first / removed last
  bom.json           bill of materials (below)
  validation.json    every check with pass/fail, location, reason
  previews/          NE.png NW.png SE.png SW.png top.png interior-*.png + render.json (renderer version, camera)
  critique.json      critic scores (6 categories 1-10), fixes, model, image hashes, round
  approval.json      written only by the mind from an authenticated owner decision
```

## design.json

```json
{
  "format": "eve-design/1",
  "project_id": "cabin-north-meadow", "version": 3,
  "world_id": "…", "dim": "minecraft:overworld", "origin": {"x": 100, "y": 64, "z": -40},
  "size": {"x": 11, "y": 8, "z": 9},
  "style": "cozy_cabin", "style_card_hash": "…",
  "palette": ["minecraft:air", "minecraft:cobblestone", "minecraft:spruce_planks", "minecraft:dark_oak_stairs[facing=north,half=bottom,shape=straight]"],
  "blocks": "<RLE or base64 of uint16 palette indices, x-fastest then z then y>",
  "roles": "<same shape, uint8: 0 none 1 foundation 2 wall 3 window 4 door 5 roof 6 floor 7 stair 8 furniture 9 light 10 ground 11 support 12 trim>",
  "rooms": [{"id": "living", "type": "living", "min": {}, "max": {}, "door_pos": {}}],
  "block_entities": [{"pos": {}, "id": "minecraft:chest", "nbt": "…"}],
  "hash": "sha256 of canonical(palette, blocks, roles, size, origin)",
  "provenance": {"program_hash": "…", "compiler_version": "…", "detailing_rules": ["depth", "frame_fill"], "seed": 42}
}
```

- Block states are written in vanilla `id[prop=value,…]` form; the pinned game's own block-state data is the only authority for legal states.
- The **hash** is the identity approvals bind to. Any change to palette/blocks/roles/size/origin → new version, new hash, old approval invalid.
- `roles` lives here because `.litematic` cannot store it; the builder may use roles to order construction (foundation → support → wall → roof → trim → furniture).

## bom.json

```json
{
  "design_hash": "…",
  "final": [{"item": "minecraft:spruce_planks", "count": 212}],
  "scaffold": [{"item": "minecraft:dirt", "count": 40}],
  "tools": [{"item": "minecraft:iron_axe", "uses": 150}],
  "fuel": [{"item": "minecraft:coal", "count": 12}],
  "deficit": {"computed_against": {"inventory_hash": "…", "storage_index_ts": 0}, "missing": [{"item": "…", "count": 0}], "gather_plan": [{"item": "…", "via": "mine|chop|craft|smelt|farm|hunt", "estimated_minutes": 0}]},
  "totals": {"final_blocks": 0, "scaffold_blocks": 0, "estimated_build_minutes": 0}
}
```

Two-block structures (doors, beds), item/block differences (e.g. `stairs` crafted from 6 blocks → 4 stairs), batch yields and leftovers must match an independent count (CP-48). Deficit is recomputed by the **mind** from live state before gathering starts; the design-side number is an estimate.

## Approval binding (CP-65)

`approval.json` is written by the mind only: `{approval_id, project_id, version, design_hash, site_hash, budget, decided_by, decision, category, channel, ts}`. `decided_by` must be a name in `envelope.owner.trusted_players`; the channel must be the authenticated command path (`/eve approve <project_id> v<N>` in game, or the mind CLI on the 45L). A Discord webhook or a chat message from anyone else is **not** an approval channel.

## Site hash

`site_hash` = sha256 of the observed terrain volume (`query.blocks`) under the footprint plus a 2-block margin at design time. The mind re-queries before building; a mismatch beyond the brief's terrain-change allowance invalidates the approval and reports it. Until the site query exists the mind writes `site_hash: null`.

## Amendments (merged 2026-10-19 from design and mind; both implement these)

- **Exact design hash rule:** `sha256(json.dumps({"blocks": <stored base64 string>, "origin": [x, y, z], "palette": <canonical palette>, "roles": <stored base64 string>, "size": [x, y, z]}, sort_keys=True, separators=(",", ":"), ensure_ascii=True))`. Canonical palette = `minecraft:air` first, the rest sorted, every entry a **full canonical block state** (all properties written). Test vector: `parts/design/examples/out/hut5/design.json` → `90a0175d…000edd` (the mind verifies with the same rule and refuses `design_hash_mismatch`).
- `rooms[]` entries carry `part`, `storey` and `openings`.
- `scaffold.json` uses the same grid format; its `origin` is the world position of its own min corner and `provenance.scaffold_for` = the design hash. Scaffold pillars are built first and removed last; walkability is judged on the finished building without scaffold.
- `validation.json` = `{design_hash, validator_version, passed, skipped[], needs_approval, checks: {<name>: {status, failures: [{pos, reason}], notes[], approximations[]}}}`. The mind allows `approve` only when `passed == true`; a non-empty `skipped` list (site fit and affordability need a brief) is returned as a warning and treated as "needs review"; with `approvals.small_build_block_limit = 0`, `needs_approval` is always true.
- `previews/render.json` carries `design_hash`, renderer version, cameras and an `unsupported` list (blocks drawn as placeholders).
- Construction progress is the mind's record, keyed by `(project_id, version, design_hash)`; the live material deficit is recomputed by the mind from `storage.index` + the recipe solver (CP-35) before gathering starts.
- `program.eve` hash is taken over the LF-normalised source. Litematica export is format v7 / subversion 1, data version 5023.

## Amendments 2 (merged 2026-10-20 from design; S16 in force)

- **`gate.json`** (written by design after validation + critique): `{version, design_hash, category: "eve_own"|"owner_supplied", decision: "build"|"hold"|"reject", reasons[], checks: {<name>: {ok, reason?}}, policy}`. The mind builds an Eve-authored design only when `decision == "build"` and the hash re-verifies; `hold` and `reject` are reported, never auto-built. Owner-supplied folders (`import --owner-supplied`) carry `provenance.owner_supplied: true` and `origin_stated` (the position Hunter gave) and skip design but keep BOM/buildability checks.
- **`build_order.json`**: ordered placement steps derived from roles (foundation → support → wall → floor → stair → window/door → roof → trim → furniture/light) with scaffold per layer (pillars first, removed last) and `items_per_state` (item needed per full block state, two-block halves noted). The body's `build_schematic` consumes it.
- **`candidates.json`** (all concept/program candidates with scores and rejection reasons) and **`critique.json`** per round (6 category scores 1–10, fixes, model, image hashes) live in the design folder; the best valid version is kept (CP-64).
- **Construction progress** (mind, memory schema v1.2): tables `build_runs(run_id, project_id, version, design_hash, lane, started_ts, ended_ts, status)` and `build_cells(run_id, pos, state_expected, state_observed, placed_ts, verified)`; the live deficit = BOM − inventory − shared storage, recomputed before gathering.
- `place_block` v2 with a target **state** (facing/half/shape) is a registry change owned by body; until then design lists stair/slab/door placements that need it under `manual` in `build_order.json`.

## Amendments 3 (merged 2026-10-20 evening from builder's request)

- **`build_order.json` steps carry an optional `stand`**: `{"pos": {x, y, z}, "yaw": <deg, optional>}`, the standing position design's walkability simulator used for that placement (feet block, reachable from the previous step's `stand` or from outside the footprint without passing through unplaced cells). The builder walks to `stand` before placing; when absent it falls back to its own spot search. Design marks steps whose only reachable spot is inside the building with `interior: true` so the builder orders doors/openings before enclosing walls.
