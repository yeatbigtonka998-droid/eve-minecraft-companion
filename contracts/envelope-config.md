# Operating envelope config (CP-05)

One JSON file, **`config/envelope.json`**, is the machine-readable statement of Hunter's rules. It is read by the **server-helper** (enforcement, below every model) and the **mind** (planning). The body reads a copy for its local `protected` view only. The example at `config/envelope.example.json` ships with **restrictive test-world defaults**; every value Hunter has not decided is marked in `records/DECISIONS.md` as PENDING and stays restrictive until he decides through the trusted path.

Only the owner (trusted player via `/eve …`, or Hunter editing the file on the 45L) changes it. Chat text, books, signs, schematic metadata or model output can never change it (CP-45).

## Fields

| field | meaning | default |
|---|---|---|
| `envelope_version` | integer, bump on any change; both mods log the hash at `hello` | 1 |
| `world_id` | which world this envelope is for; a mismatch at `hello` blocks all actions | required |
| `owner.trusted_players` | exact Minecraft names whose `/eve` commands and chat count as owner commands | `[]` (nobody — Hunter adds himself) |
| `owner.eve_account` | Eve's Minecraft name; the server-helper applies rules to this player only | required |
| `mode_default` / `mode_allowed` | `manual` / `["manual"]` until qualified | manual |
| `difficulty` | fixture difficulty | `normal` |
| `death_rules` | `true_survival` or `keep_inventory_eve_only` (training runs must be labelled) | `true_survival` |
| `home` | `{dim, center{x,y,z}, radius, explore_radius}` — Eve may not leave `radius` in autonomous mode; `explore_radius` is the curiosity cap | radius 64, explore 128 |
| `zones[]` | `{id, dim, min, max, break: none\|task_only\|free, place: none\|task_only\|free, containers: private\|shared, snapshot: bool, note}` | one `home` zone with `task_only`, one `mine` zone `free` |
| `containers.default_tag` | tag for an untagged container: `private` means Eve never opens it | `private` |
| `containers.tags` | map `"dim:x,y,z"` → `shared\|private` | `{}` |
| `protected_entities` | `{players, villagers, iron_golems, pets, named, extra_types[]}` — all true | all true |
| `hazards` | `{fire, lava, tnt}` — allowed only by direct owner command; defaults false | all false |
| `limits` | `{blocks_broken_per_hour, blocks_placed_per_hour, chunk_scan_radius, ws_payload_max_bytes, max_action_queue}` | 200 / 400 / 4 / 262144 / 20 |
| `sensors` | `{storage_index: loaded_chunks_only, ore: exposed_only\|none, crops: zones_only, entities_radius}` — the knowledge policy for CP-14 | ore `exposed_only`, radius 32 |
| `budgets` | `{deepseek_monthly_usd, planner_calls_per_hour_max, fal_monthly_usd}` | 10 / 600 / 0 |
| `approvals` | `{small_build_block_limit, wishlist_max_per_week, terrain_change_blocks}` | 0 (everything needs approval) / 0 / 0 |

## Enforcement expectations (server-helper)

- Zone rules are checked on the **server** for every Eve-caused block break/place, container open/transfer, attack, and item use — including indirect ones the policy covers (sweep attacks, projectiles, fire spread from her actions).
- A denied effect is cancelled before it happens and emitted as `permission.denied`; nothing on the client can grant it.
- Rate limits count server-confirmed effects per rolling hour.
- Eve's account is never an operator and never has `/gamemode`, `/tp`, `/give` or command-block access.

## Amendments (merged 2026-10-19 from sense and foundation)

- **Container tag precedence:** explicit tag in `containers.tags` > the containing zone's `containers` value (private wins where zones overlap) > `containers.default_tag`. Entity containers (minecarts, boats with chests, donkeys) are denied.
- **`eve_account` must never appear in `trusted_players`**; the server-helper refuses such an envelope. `SET_ME` placeholders are refused at startup. An invalid file stops startup; `/eve reload` keeps the old envelope if the new one is invalid.
- **Outside every zone** break, place and interaction are denied. **`task_only`** = deny until a `task.grant` for that action exists (link-protocol v1.1). Overlapping zones use the most restrictive rule.
- **Non-container interactions** (levers, doors, buttons, beds, workstations): allowed inside any zone, denied outside, until a dedicated field exists.
- **Rate limits** count server-confirmed effects per rolling hour in every mode.
- **`online-mode=true` is required** on any server where the rails matter: they apply by player name. The server-helper warns at startup when it is off (gametest servers run offline, which is fine for event-level tests only).
- The `storage.index` scan covers loaded chunks around Eve **and** around `home.center`, so village chests stay known while she is away.
- A `world_id` mismatch between the envelope and the running server denies every Eve effect.

## Amendment 4 (merged 2026-10-20 evening, orchestrator O31)

- `limits.parked_ttl_s` (integer seconds 60..604800, **optional**, default 3600; implemented in eve-sense 0.1.10): how long a parked (paused or blocked) task may wait before the mind cancels it with reason `expired`. The server-helper accepts the key as optional and includes it in the envelope hash like any other field; the mind reads it with the default when absent. The 45L keeps 3600; the Shark lanes may set 21600. Both envelope copies (mind and server-helper) must stay identical because the hash is part of the binding.

## Amendment 5 (merged 2026-10-20 evening, orchestrator; O41b)

- A zone may carry an optional `limits` object overriding `blocks_broken_per_hour` and/or `blocks_placed_per_hour` **while Eve is inside that zone**; the global `limits` apply everywhere else and to any zone without an override. Counts are per zone for overridden zones and global otherwise. Used by the iron-kit `fresh_mine` zone (1200/h); never on the 45L dev world's home zone. Sense enforces it (eve-sense 0.1.18, gametest V29: per-zone count, lowest cap on overlap, global elsewhere); the envelope hash covers it like any field; the validator accepts it.

- `home.doorway` (optional, envelope_version 8, O83f): one or two integer `{x,y,z}` cells, the shelter's 1x2 doorway. `night_survival` seals them from inside at dusk with a block from Eve's inventory and opens exactly those cells at dawn (mind e4c9b87+). Sense validates the key (eve-sense 0.1.20: 1–2 cells, integers, no extra keys; older sense refuses the whole envelope, so the key ships only with a sense that validates it). Absent = the doorway stays open.
