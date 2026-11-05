# Link protocol v1 — mind ⇄ body, mind ⇄ sense

Owner: orchestrator. Implementers: **mind** (Python, hosts the server), **body** (Fabric client mod), **server-helper** (Fabric server mod, "sense").
Changes: propose in your part's STATUS.md under "Contract change requests"; the orchestrator merges and bumps `v`. Do not fork this file.

## Topology

- The **mind** listens on `ws://127.0.0.1:8951` (loopback only, never exposed via Tailscale serve/funnel). Two paths:
  - `/body` — Eve's client mod connects here (one connection; a second `hello` for the same account replaces the first and the old one is closed with reason `superseded`).
  - `/sense` — the server-helper mod connects here (one connection).
- Both mods are WebSocket **clients** and reconnect with exponential backoff (1 s → 30 s cap, jitter). The mind may restart at any time; the mods must survive it.
- Transport: text frames, one JSON object per frame, UTF-8. Max frame **262 144 bytes**; a larger frame is rejected with `error{code:"payload_too_large"}` and the connection is closed.
- Port 8951 is reserved for Eve on the 45L (ports already in use there: 443, 8384, 8443, 8444, 8446, 8448, 8771, 8820, 8821, 8830, 8900, 10000, 11434, 28880 — never bind those).

## Envelope (every message)

```json
{
  "v": 1,
  "id": "9b5f…",            // UUIDv4, unique per message
  "ts": 1760000000000,      // sender wall clock, ms since epoch
  "tick": 12345,            // sender's current game tick, or null (mind)
  "from": "body" | "sense" | "mind",
  "type": "action.request", // namespace.name, see below
  "reply_to": "<id>" | null,
  "session": "<session_id>" | null,   // null only on hello
  "world": "<world_id>" | null,       // see World identity
  "payload": { }
}
```

Rules:
- Unknown `type` → reply `error{code:"unknown_type"}`; never crash, never ignore silently.
- Every message after `hello.ok` must carry the assigned `session`; a mismatch is dropped and counted (`metrics.dropped_session_mismatch`).
- `reply_to` links responses to requests. One request may get many `action.progress` replies and exactly one terminal `action.result`.
- Numbers: positions are block/entity coordinates as doubles `{x,y,z}`; dimensions are namespaced ids (`minecraft:overworld`, `minecraft:the_nether`, `minecraft:the_end`).

## Authentication and binding

- Shared secret in env var `EVE_LINK_TOKEN` on the 45L (mind reads env; mods read env, falling back to `<gamedir>/config/eve-link-token.txt`). Never in source, logs or the repo (`.gitignore` already covers `config/link-token.txt`).
- First message on a connection must be `hello` carrying `payload.token`. The mind compares in constant time. Wrong/missing token → `hello.reject{code:"auth"}` and close. Any non-hello first message → close.
- `hello.ok` assigns `session` (UUID) and tells the peer the expected `registry_hash` and `state_hz`.
- Replay protection: `id` values seen in the last 10 minutes are rejected (`error{code:"duplicate_id"}`).

## World identity

`world_id` = `"<level_name>:<sha256(seed)[:12]>"` as reported by the server-helper in its `hello`. The body reports what it sees (`server_address`, `level_name`); the mind binds the two and refuses to dispatch actions while they disagree or while either link is down (`metrics.world_mismatch`). Memory rows are keyed by `world_id` so one world's facts never leak into another.

## Heartbeat, timeouts, staleness

- `ping`/`pong` every 5 s each direction; 15 s silence = dead link, close and reconnect.
- Body loses the mind: finish the current action only if it is already past its last safe pause point (bounded by its own `timeout_ms`), then enter **IDLE-SAFE** (reflexes on, no new actions, stand still or hold position near home). It must never start a new skill without the mind.
- Every `state` carries `freshness{tick, age_ms}`; the mind treats data older than 2 000 ms as stale and will not plan on it.
- Body must answer `action.request` with `action.accepted` or `action.rejected` within **500 ms**, send `action.progress` at least every **5 s** while running, and a terminal `action.result` by `deadline_ms` (else it sends `status:"timeout"` itself and cancels).

## Body link (`/body`)

| type | dir | payload |
|---|---|---|
| `hello` | body→mind | `token, mod_version, mc_version, account_name, server_address, level_name, caps:[{skill,version}], registry_hash` |
| `hello.ok` | mind→body | `session, registry_hash, state_hz (default 4), mode` |
| `hello.reject` | mind→body | `code: auth \| version \| registry` |
| `state` | body→mind | see **State payload** below; sent at `state_hz` and immediately on health/damage/dimension/inventory change |
| `event` | body→mind | `kind: damage \| death \| respawn \| disconnect \| reconnect \| reflex \| stuck \| chat_received \| item_pickup \| container_opened \| control, detail:{…}` |
| `action.request` | mind→body | `action_id (UUID), skill, version, params, deadline_ms, idempotency_key (= action_id)` |
| `action.accepted` | body→mind | `action_id` |
| `action.rejected` | body→mind | `action_id, code, reason` (codes: `unknown_skill`, `bad_params`, `precondition_failed`, `busy`, `stopped`, `mode_denied`, `zone_denied`) |
| `action.progress` | body→mind | `action_id, pct (0-100 or null), note, partial_evidence:{}` |
| `action.result` | body→mind | `action_id, status: ok \| failed \| cancelled \| uncertain \| timeout, code, evidence:{…}, elapsed_ms` |
| `control.stop` | mind→body | `reason, source` — hard stop (see Stop semantics) |
| `control.resume` | mind→body | `source` (only honoured when source is a trusted owner command relayed by the mind) |
| `control.home` | mind→body | `reason` |
| `control.mode` | mind→body | `mode: manual \| supervised \| autonomous` |
| `control.ack` | body→mind | `op, latency_ms (from receipt to inputs cleared), state_after` |
| `chat.send` | mind→body | `text` (≤256 chars; body sends as the player), optional `reply: true` on an owner reply. Rate 1 msg / 2 s (O114): a line over it waits in a body-side queue of at most 3 and is sent when the gap allows, an owner reply ahead of the other waiting lines; a full queue is refused with error `rate_limited`; a line still unsent after 8 s is dropped with error `rate_limited` naming it (`about_id` = the chat.send) |
| `query` | mind→body | `kind: inventory \| blocks_around \| can_reach \| path_estimate, params` |
| `query.result` | body→mind | `kind, result` |
| `metrics` | body→mind | every 30 s: `tick_ms_avg, fps, actions_running, reflex_triggers, dropped_messages` |
| `ping`/`pong` | both | `{}` |
| `error` | both | `code, message, about_id` |

Idempotency: the body remembers the last 1 000 `action_id`s for 10 min; a repeated `action.request` with a known id re-sends the current/terminal status and **never re-executes**.

### State payload

```json
{
  "pos": {"x":0,"y":64,"z":0}, "dim": "minecraft:overworld", "yaw": 0, "pitch": 0,
  "health": 20, "food": 20, "saturation": 5, "air": 300, "xp_level": 0,
  "on_ground": true, "in_water": false, "in_lava": false, "fall_distance": 0, "sneaking": false,
  "gamemode": "survival",
  "inventory": [{"slot":0,"item":"minecraft:stone_sword","count":1,"damage":10,"max_damage":131}],
  "inventory_hash": "sha1…",
  "equipment": {"head":null,"chest":null,"legs":null,"feet":null,"mainhand":"minecraft:stone_sword","offhand":"minecraft:shield"},
  "hotbar_slot": 0,
  "nearby_entities": [{"id":123,"type":"minecraft:zombie","name":null,"pos":{},"distance":6.2,"hostile":true,"protected":false,"line_of_sight":true}],
  "threats": [{"entity_id":123,"kind":"melee","danger":0.7}],
  "light_level": 7, "biome": "minecraft:plains", "time_of_day": 13000, "weather": "clear",
  "control": {"mode":"manual","stopped":false,"active_action_id":null,"reflex_active":null},
  "freshness": {"tick": 12345, "age_ms": 12}
}
```
`protected` is the body's local view from the envelope config (players, villagers, golems, pets, named); the server-helper is the enforcing authority.

## Sense link (`/sense`)

| type | dir | payload |
|---|---|---|
| `hello` | sense→mind | `token, mod_version, mc_version, world_id, level_name, seed_hash, envelope_version, envelope_hash` |
| `hello.ok` | mind→sense | `session` |
| `storage.index` | sense→mind | on change and on `storage.request`: `containers:[{id, dim, pos, kind, tag: shared\|private\|unknown, loaded, items:[{item,count}], observed_tick}]`; `partial:true` when only changed containers are included |
| `storage.request` | mind→sense | `{}` or `{ids:[…]}` |
| `block.event` | sense→mind | `kind: break \| place \| explode \| burn \| fluid \| grow, dim, pos, before, after, actor: "eve" \| "player:<name>" \| "env", tick` (batched: `events:[…]`, max 500 per frame) |
| `explosion` | sense→mind | `dim, center, cause, affected:[{pos,before}], zone_ids:[…]` |
| `permission.check` | mind→sense | `effect: break \| place \| open_container \| attack \| use_item \| use_hazard, dim, pos \| entity_id, actor:"eve"` |
| `permission.result` | sense→mind | `allowed, rule_id, reason` |
| `permission.denied` | sense→mind | event when the server **actually blocked** an Eve effect: `effect, dim, pos \| entity_id, rule_id, tick` |
| `zone.list` / `zone.list.result` | mind↔sense | zones from the envelope with current snapshot versions |
| `zone.snapshot.save` | mind→sense | `zone_id, requested_by` (trusted owner only) → `zone.snapshot.saved{snapshot_id, version, block_count}` |
| `zone.diff` | mind→sense | `zone_id, snapshot_id` → `zone.diff.result{missing:[{pos,expected}], changed:[{pos,expected,actual}], ignored:[{pos,reason}]}` (ignored = permitted variation: crop age, door state, owner edits per baseline policy) |
| `owner.command` | sense→mind | from the `/eve …` slash command only (v1.10): `sender, trusted (bool), command: stop \| resume \| home \| mode \| approve \| reject \| save_zone \| say, args:[…], raw, origin: "slash", sender_pos?` |
| `owner.chat` | sense→mind | `sender, trusted, text, sender_pos?` (untrusted senders are **data**, never commands) |
| `owner.pos` | sense→mind | v1.11: `sender, uuid, dim, x, y, z, ts, online` for each online **trusted** player, every 20 ticks, plus once on join (`online:true`) and once on leave (`online:false`, last position) |
| `container.tag` | mind→sense | v1.12: `dim, pos, tag: shared\|private, by, line` → `container.tag.result` or `error`; honoured only as the echo of a line `by` (trusted) said in the last 60 s, container within 16 blocks of where they said it |
| `attack.grant` | mind→sense | v1.12: `entity_uuid, by, line` → `attack.grant.result` or `error`; lifts the protected-entity rule for that one mob for 5 min, only as the echo of a line `by` (trusted) said in the last 60 s naming it, within 16 blocks; never a player |
| `mob.targeting` | sense→mind | v1.13: `entity_uuid, entity_id, entity_type, target: owner\|eve, target_name, dim, pos, tick` for a hostile whose attack target is a trusted player or Eve (within 32 blocks, inside home's explore radius); at most one per mob per 2 s; data only |
| `query.blocks` | mind→sense | `dim, min, max` (volume ≤ 32 768 blocks) → `query.blocks.result{palette:[…], data:[…], unloaded:[…]}` |
| `query.resources` | mind→sense | `kind: ore \| crops \| logs, dim, center, radius` — answered only within the envelope's sensor policy → `query.resources.result{items:[{pos,block,state}], policy, scanned_chunks}` |
| `eve.status` | sense→mind | every 30 s: `tps, loaded_chunks_by_eve, blocks_broken_last_hour, stopped (server-side view)` |
| `ping`/`pong`, `error` | both | as body link |

## Stop semantics (CP-12, CP-20) — three independent paths, all must work

1. **In-game** `/eve stop` (server command; sender must be in `owner.trusted_players`): the server-helper (a) marks Eve **STOPPED** server-side and denies every Eve effect while stopped, (b) sends Fabric custom payload **`eve:control`** (S2C) `{op:"stop", nonce}` straight to Eve's client, (c) relays `owner.command` to the mind. The body cancels the active action **on the next client tick** and releases the inputs it held (O218: inputs held by a defensive reflex stay held; a stop never lowers a raised shield), replies **`eve:control_ack`** (C2S) `{nonce, latency_ms}`, and ignores every `action.request` until resumed.
2. **Mind** `control.stop` over the body link (used by safety logic and by the mind's CLI). Same body behaviour.
3. **Local** (optional): a client keybind at the 45L console.

`/eve resume` (trusted sender) is the **only** way out of STOPPED; it is never automatic (not on reconnect, not on timer, not from a late model reply). `/eve home` = stop current action, then run skill `go_home` with the qualified safe route, or report `home_blocked`. A stop cannot freeze physics: if Eve is mid-air or in water the reflex layer still runs (it is not an "action").

## Control arbiter (body)

One arbiter owns the controls. Priority, highest first: defensive reflex (dodge, shield incl. floor shield, eat-emergency, retreat, creeper flee, edge-sneak, fall rescue, hazard) > `STOPPED` > active action > idle (O218, 2026-10-22: STOPPED suppresses actions, skills, movement orders, sleep, torch placing and equip moves; it never suppresses a defensive reflex and never cancels one in progress; the stop's verify event carries `reflex_active`). A reflex pre-empts the active action and emits `event{kind:"reflex"}`; when the reflex ends the action resumes **only if** its preconditions still hold, otherwise it ends `failed{code:"interrupted"}`. Baritone paths run as an action and obey the same arbiter: a stop or reflex cancels Baritone's process immediately.

## Modes

- `manual` (default): only actions that the mind derives from a direct trusted owner command, one at a time.
- `supervised`: the mind may queue tasks while a trusted owner is online.
- `autonomous`: needs the autonomy checkpoints (CP-73+) PASS and `envelope.mode_allowed` to include it; the body refuses `control.mode{autonomous}` otherwise with `error{code:"mode_not_qualified"}`.

## Libraries (recommendation, not contract)

- Mods: Gson (bundled with Minecraft) for JSON; `org.java-websocket:Java-WebSocket:1.6.0` included via Loom `include` for the WS client.
- Mind: Python 3.12, `websockets` ≥ 13 (asyncio), `jsonschema` for registry/param validation.

## v1.1 amendments (merged by the orchestrator 2026-10-19 from body, sense, mind and foundation requests)

**Hashes (registry, envelope, design).** Canonical JSON = keys sorted at every level, separators `,` and `:`, no whitespace, UTF-8 with non-ASCII not escaped, integers printed without a decimal point (`8`, never `8.0`), other numbers shortest round-trip. Gson parses numbers as doubles: print integral values as integers. Registry 0.1.0 = `08646a3ffd12055556c58a46b91ae5c4b1929f3ee48c1c5c1b24f2e29144c059`.

**World identity.** `world_id = "<level_name>:" + sha256(<decimal seed string, UTF-8>)[:12]` (dev world: `dev:a1a5b81d7d18`). The body cannot see the level name on a multiplayer server, so it sends `level_name: ""` and `server_address` as known, and **`world: null` on every frame**; the mind tags body frames with the world bound from the sense hello and silently drops a non-null unbound world. Sense sends the client **`eve:control {op:"world", level_name, world_id}` on Eve's every join and rejoin**; the body reports that `level_name` and the mind refuses dispatch unless body `level_name` == sense `level_name`.

**Binding (mind refuses dispatch unless all hold).** Both links up; `sense.hello.world_id == envelope.world_id`; **`body.hello.account_name == envelope.owner.eve_account`**; `body.level_name == sense.level_name`; `sense.hello.envelope_version`/`envelope_hash` equal the mind's copy.

**Handshake and close codes (as implemented by the mind).** First frame not `hello` → close 4400; bad token → `hello.reject{code:"auth"}` + 4401; `mc_version` ≠ `"26.3"` → `hello.reject{code:"version", expected}`; registry mismatch → `hello.reject{code:"registry", expected, registry_version}`; second valid hello on a path → old connection closed 4409 `superseded`; 15 s silence → 4408 `dead link`; oversized frame → `error{payload_too_large}` then close 1009. `hello.ok` to body = `{session, registry_hash, state_hz, mode}` exactly; to sense = `{session}`; `reply_to` = the hello's `id`. If the mind is STOPPED when a body connects it sends `control.stop{reason:"mind is STOPPED", source:"mind"}` after `hello.ok` and repeats it (≤ every 2 s) while `state.control.stopped` is false; it never sends `control.resume` on its own.

**Frame validation order** (every refusal before storage or dispatch): size → JSON → envelope schema → `from` matches path → `session` → `world` → duplicate `id` → known `type` → payload schema. Error codes: `payload_too_large`, `bad_json`, `bad_envelope`, `duplicate_id`, `unknown_type`, `bad_payload`. Top-level keys are exactly the ten in the envelope (`tick`, `reply_to`, `session`, `world` may be null); extra top-level keys are rejected; extra keys **inside payloads** are allowed. `error{code:"not_implemented"}` is a valid answer to `query` and `chat.send` until the body implements them (CP-10).

**Dispatch details.** `action.request.deadline_ms` is a **relative budget in ms from receipt** (= the skill's `timeout_ms`; the body treats values > 1e12 as epoch ms; effective deadline = min(deadline, registry timeout)). No `action.accepted`/`action.progress` within 1 s → the mind re-sends the identical request (same `action_id`, new `id`) at most twice, only within the same body session; after a mind restart or new body session it never re-sends, it **reconciles on `state.control.active_action_id`** (the body keeps it accurate, `null` when idle): equal to an open action → adopted; otherwise the skill's postcondition is checked on the next state and the action is settled `uncertain`. An `ok` result missing a registry `evidence` key is stored `uncertain` (`missing_evidence:<keys>`); a failure code outside `failure_codes` is stored as `unlisted:<code>`. Duplicate or late results never change a terminal action. One action in flight. **Cancelling one action = the `stop` skill** (`action.request{skill:"stop"}` while another action runs); **`control.stop` = hard stop** (STOPPED until a trusted resume). `action.result` may carry an optional `note`. Home outcome when not run as a skill: `event{kind:"control", detail:{op:"home", result:"home_blocked"|"refused_stopped", reason}}`.

**Body link lifetime.** The link lives per game connection: `hello` on every server join, `event{kind:"disconnect"}` before leaving, `event{kind:"reconnect"}` after a re-join. While the mind link is down any active action is cancelled (`interrupted`) and kept for idempotent re-reporting (IDLE-SAFE). `state.freshness.age_ms` = ms since the client last received any packet from the server; no `state` is sent before the first packet.

**`eve:control` wire format (pinned).** A Fabric play payload whose body is one `ByteBufCodecs.STRING_UTF8` JSON object. S2C `eve:control` = `{op: "stop"|"resume"|"home"|"mode"|"world", nonce, source?, mode? (op=mode), level_name?, world_id? (op=world)}`. C2S `eve:control_ack` = `{nonce, op, latency_ms, tick, stopped, duplicate?, error?}`. Duplicates (same nonce) are re-acked, never re-applied. Sense **must** send `stop` (and re-send it on rejoin while STOPPED) and `world`; it **may** send `resume`; the body honours both `eve:control{op:"resume"}` and the mind's `control.resume` when their source is a trusted owner command. Sense sends payloads only when Eve's client declares the `eve:control` receiver, otherwise logs a loud error.

**Sense link additions.** `storage.index`: a partial may carry `{id, removed:true}` for a broken/gone container; a full index split over several frames is sent as `partial:true` frames with **`complete:true` on the last**; a non-partial frame is the whole index. `permission.denied.effect` may also be `use_block`, `interact`, `use_item_on`, `container_transfer`, `use_entity` (entity effects include `entity_type`). New sense→mind `envelope.changed{version, hash}` after a successful `/eve reload`; `reload` joins the `owner.command` list. New mind→sense **`task.grant{action_id, effect, dim, positions[], expires_tick}`**: a bounded grant for `task_only` zones, issued only from an owner-approved task (never from model text); sense allows exactly those positions/effects until `expires_tick` or the action ends; without a grant `task_only` = deny. The mind's pre-flight `permission.check` waits 1 s; no answer → `permission_unavailable`.

**Mind-private `/cli` path.** The hub also serves `/cli` on the same loopback port and token, one JSON request per connection; it is the "authenticated mind CLI on the 45L" used by the `eve` command (stop, resume, approve…). Not for the mods.

**Ports on Shark (dev).** Body's dev server 25565, sense's dev server 25566; a shared two-mod server for CP-12 end-to-end uses sense's. Mind hub 8951; a second stub may use 8952 via `-Peve.link.url`.

## v1.2 amendments (merged 2026-10-19 evening from mind, foundation and sense)

- **Refusals have a status.** Any reply to a dispatch request that the mind refuses before sending (binding, permission pre-flight, protected target, validation) is `{ok:false, status:"refused", code, reason, action_id:null}`; it is never silent. A harness treats a reply without `status` as FAIL. Codes include `zone_denied`, `permission_unavailable`, `protected_entity`, `not_a_container`, `private_container`, `link_down`, `stale_epoch`, `bad_params`.
- **`task.grant` revocation:** a `task.grant` with `positions: []` for an `action_id` revokes that grant; the mind revokes every grant when its action ends (any terminal status). Sense enforces the revoke immediately; a dig in progress ends `zone_denied` with the note "server restored".
- **`use_item` on a block** is pre-checked and granted as effect `place` on the target block **and** its face neighbour; sense's `permission.check` for `use_item` must answer with the same rule enforcement uses (place rule on block + neighbour), not the interaction gate alone.
- An `action.rejected` arriving after `action.accepted` ends the action as `failed` with that code (contract violation logged, counted in `metrics.ignored_transitions`).
- Server `permission.denied` events that match an open action are appended to that action's `note`.

## v1.3 amendments (merged 2026-10-20 from sense 0.1.7)

- **`task.grant` scaling:** several `task.grant` frames for one `action_id` accumulate (about 9 000 positions per frame, 16 384 live positions per action). New mind→sense **`grant_region{action_id, effect, dim, min, max, expires_tick}`**: grants a box of at most 32 768 blocks, only if every block in it is grantable under the envelope; at most 64 regions live; a stop or revoke clears them. Issued by the mind only from an owner-approved build/repair task.
- **CP-52 repair diff:** mind→sense **`zone.repair{zone_id, snapshot_id, max_steps?}`** → `zone.repair.result{steps:[{op: remove|place, pos, block}], manual:[{pos, kind, note}], skipped:[…]}`: exactly the blocks to restore from the approved snapshot as ordered steps (removals top-down, then placements bottom-up); permitted variation skipped; containers listed under `manual` for backup recovery (O17). The mind turns steps into `build_schematic`-style actions with the matching grant.
- **Evidence lanes (O18):** every harness takes `--lane 45L|shark`; the lane names the evidence folder and the RESULT header.

## v1.4 amendments (merged 2026-10-20 from sense 0.1.9)

- **Workstation state in `storage.index`:** entries for `minecraft:furnace`, `minecraft:smoker` and `minecraft:blast_furnace` carry an extra `furnace{input, fuel, output, lit, burn_ticks, cook_progress}` and are re-sent once per second while lit. Crafting tables have no server state (none sent). Container close/transfer changes reach the index within 1 s. Fetching across several `shared` chests needs no grant (the container rule allows it; `private` stays refused).

## Amendment v1.5 (merged 2026-10-20 evening, orchestrator; from mind/sense)

- `storage.index` entries may describe **workstations** as well as containers: `kind` in {`minecraft:crafting_table`, `minecraft:furnace`, `minecraft:blast_furnace`, `minecraft:smoker`, `minecraft:anvil`, …} with an empty `items` list and the same `pos`, `zone`, `tag` fields. No schema change; consumers that only want containers filter on `kind`. The mind uses these to resolve "no workstation known" truthfully (CP-35/36/51). Implemented in eve-sense 0.1.12 (crafting/smithing/cartography/fletching/enchanting tables, stonecutter, loom, anvils, grindstone; place/break arrives as a partial within 1 s).

## Amendment v1.6 (merged 2026-10-20 evening, orchestrator; sense proposal 10, eve-sense 0.1.14)

- `zone.repair` and `zone.diff` take an optional `box: {min: {x,y,z}, max: {x,y,z}}` that limits the answer to that part of the zone, clamped to the snapshot volume; a box with no overlap is refused `bad_payload`. The mind **always** sends the repair site's box (the damage footprint plus margin), never a whole-zone repair, so later legitimate additions elsewhere in the zone (other fixtures, the owner's builds) are not listed for removal. A whole-zone repair requires an explicit owner command naming the zone.

## Amendment v1.7 (merged 2026-10-20 evening, orchestrator; from builder's CP-52 attempt 2)

- **Grant clocks:** `expires_tick` in `task.grant` and `grant_region` is in **server ticks** as last reported by sense (the `tick` field of the newest `state`/`index` frame the mind holds), never wall-clock or the mind's own counter; sense accepts `(now, now+72000]`. The mind derives it as `last_server_tick + ttl_ticks` and refreshes the grant if the link was stale for more than 600 ticks.
- **Grant effects:** `effect` may be `place`, `break` or **`use`** (`use_item_on` a block: tilling farmland with a hoe, planting seeds, placing water from a bucket, lighting, opening a door). `repair_zone`'s farmland replant and `harvest_replant` need `use` grants in task_only zones; sense enforces `use` grants exactly like place/break (box, expiry, zone grantability).

## Amendment v1.8 (merged 2026-10-20 evening, orchestrator; sense proposal 11, eve-sense 0.1.16)

- `query.resources{kind: "void_edge", center, radius <= 32}` returns standable blocks (solid, two clear cells above) with a horizontal neighbour Eve could walk off into and nothing solid within 8 below: `{pos, block, state, toward: [north|south|east|west], depth}`. For the builder's bridge planning and the mind's hazard reasoning; body's edge-sneak reflex stays client-side (a server round trip is too slow for a reflex). Sense has no stop-below rule and never denies a fall rescue (O20); treating a fall below the islands as unrecoverable is the guard's and body's choice.

## Amendment v1.9 (2026-10-21, CP-54): mind CLI read-only survey passthroughs

The mind's `/cli` surface gains three read-only commands that forward existing sense queries unchanged:
- `survey {dim, min, max}` -> `query.blocks` over the box, chunked to <= 32 768 blocks per query, results concatenated in order;
- `resources {kind?}` -> `query.resources` (sensor policy applies);
- `storage` -> the current storage.index snapshot.
They never move Eve, never place or break, never write memory, and are refused while a `stop` is pending. Results carry the server tick they were observed at. Consumers: design's CP-54 site reader (lanes only until a 45L pin carries them).

## Amendment v1.10 (2026-10-22, O111 + O94q; eve-sense 0.1.22)

Additive; a receiver that ignores unknown payload keys needs no change.
- `owner.chat` and `owner.command` may carry **`sender_pos: {dim, x, y, z}`**: the sender's block position (integers) and dimension when the line was sent. It is absent when there is no player sender (console, RCON). The mind resolves "come to me" / "follow me" from it, so the owner need not type coordinates. It is a hint about the sender, never a permission: every effect at that position still goes through the rails.
- `owner.command` carries **`origin: "slash"`** and comes **only** from the `/eve` server command (O94q decision (b)). Chat, trusted or not, is only ever `owner.chat`; sense never turns a chat line into an `owner.command`.

## Amendment v1.11 (2026-10-22, O121; eve-sense 0.1.23)

Additive. **`owner.pos`** (sense→mind) `{sender, uuid, dim, x, y, z, ts, online}`:
- Sent for each online player in `owner.trusted_players`, never for anyone else (Eve's account is never trusted).
  - Every 20 server ticks (tick count divisible by 20).
  - Once on join with `online: true`.
  - Once on leave with `online: false` and the last position.
- `x, y, z` are block integers (the player's block position), `dim` is the dimension id and `ts` is epoch milliseconds.
- Purpose: the mind's follow keeps a current target position while Eve cannot see the owner.
- It is data, never a permission: every effect still goes through the rails.

## Amendment v1.12 (2026-10-22, O135; eve-sense 0.1.24)

Additive.
- **`container.tag`** (mind→sense) `{dim, pos:{x,y,z}, tag: "shared"|"private", by, line}` → **`container.tag.result`** `{dim, positions:[{x,y,z}], tag, by, line}`, or `error{bad_payload}` with the reason.
  - Sense honours it only as the echo of the owner's own words: `line` must equal a line that `by` (a trusted player) said in the last 60 s and that sense itself relayed (`owner.chat` text, or `/eve` raw).
  - The container must be loaded and within 16 blocks of where `by` stood when they said it, in the same dimension.
  - A double chest is tagged as one container (both halves).
  - The tag is kept by sense per world, outside the envelope, so the envelope hash is unchanged. It outranks the envelope's `containers.tags` and zone values.
  - It shows as `tag` in `storage.index`, and container rails and `permission.check(open_container)` follow it.
  - The owner gets an in-game line.
  - Breaking the container, or placing a new one at that cell, clears the tag.
- **`attack.grant`** (mind→sense, O136) `{entity_uuid, by, line}` → **`attack.grant.result`** `{entity_uuid, entity_type, name?, by, line, expires_ms}`, or `error{bad_payload}`. It uses the same echo rule as `container.tag`: `line` is a line `by` (trusted) said in the last 60 s, relayed by sense.
  - The line must name the mob: its custom name, its type (e.g. "wandering trader") or the type's last word ("trader"), as a whole word.
  - The mob must be loaded and within 16 blocks of where `by` said it.
  - The grant lifts only the protected-entity rule (villagers, golems, pets, named, extra types, decorations) for that one entity, for 5 minutes. Players are never grantable. The CP-39 animal rules still apply, and so do STOPPED and world mismatch.
  - The owner gets an in-game line.
- **`storage.index`** entries may carry **`placed_by`** (player name) and **`placed_ms`** (epoch ms) when sense saw the placement in this server run. For older containers, `placed_by` comes alone, from Ledger's history (no `placed_ms`). They resolve "the chest you just placed".

## Amendment v1.13 (2026-10-22, O166 H-autofight; eve-sense 0.1.25)

Additive. **`mob.targeting`** (sense→mind, relayed by the mind to the body's reflex): `{entity_uuid, entity_id, entity_type, target: "owner"|"eve", target_name, dim, pos:{x,y,z}, tick}`.
- Sense scans every 10 server ticks. It covers each online trusted player (`target: "owner"`) and Eve (`target: "eve"`) who stands in home's dimension within the envelope's `home.explore_radius` (horizontal) of `home.center`.
- It reports each hostile (`Enemy`) within 32 blocks whose current attack target (`Mob.getTarget()`, brain-driven mobs included) is that player.
- It sends at once on first sight, then at most one frame per mob every 2 s while the targeting lasts. There is no stop message: a mob not reported for more than 3 s is stale.
- `entity_id` is the server entity id used by the body's attack actions; `entity_uuid` matches `nearby_entities`. `pos` is the mob's block position.
- It is data, never a permission: Eve's attacks still go through the protected-entity and animal rails (`attack.grant` unchanged).

## Amendment v1.14 (2026-10-22, O193; eve-sense 0.1.27)

Additive. Hazards stay as configured; one scoped exception is added.
- With `hazards.fire = false`, Eve's flint and steel is still allowed on obsidian that is part of a complete, unlit nether portal frame (vanilla PortalShape: inner 2×3 up to 21×21, corners optional) around the empty cell the fire goes into.
- That cell must be one where Eve may place (the play zone). A side-face click must lie in the frame's plane.
- The rule is named **`portal_light`**: the `portal_light` audit, and `permission.result` `rule_id: "portal_light"`.
- `permission.check {effect: "use_hazard", hazard: "fire"}` may carry `pos` and `face` (the clicked block and face) to ask for this exception.
- Every other fire stays `hazard:fire`: fire charges, any other block, an incomplete or lit frame, and anything outside the zone.

## Amendment v1.15 (2026-10-30, O246 H-boat; eve-body 2f4dc569)

Additive. Each `nearby_entities` entry may carry **`vehicle`** (string entity type the entity is riding, e.g. `"minecraft:oak_boat"`, or `null`) and **`in_water`** (boolean). Absent means unknown; a receiver treats absent as `null` / `false`. The mind's follow uses them to switch between `follow` and `follow_boat` when the owner takes to a boat on water and back when he lands.

## Amendment v1.16 (2026-11-05, S97 initiative mode; eve-sense 0.1.29)

Additive. **`owner.activity`** (sense→mind) `{sender, uuid, dim, pos:{x,y,z}, ts, activity, detail?, held_item, vehicle?}` says what a trusted player is doing, so the mind can join in. It is data, never a permission: every effect still goes through the rails.
- Sent only for online players in `owner.trusted_players`, never for anyone else (Eve's account is never trusted).
- `activity` comes from that player's server-side events in the last 10 s:
  - `fighting`: any attack on an entity; it wins over everything else.
  - Otherwise the activity with the most events (ties: the latest): `chopping` (breaks of #logs or #leaves), `farming` (breaks of crops, melon, pumpkin, sugar cane, nether wart, cocoa, sweet berries; placing crops or saplings; a hoe used on a block), `mining` (any other break), `building` (any other placement).
  - Otherwise `sailing` when the player's vehicle is a boat.
  - Otherwise `idle`.
- `detail`, absent for `sailing` and `idle`: `{block: id, count}`, or `{entity: id, count}` for `fighting`. `count` is that activity's events in the last 10 s; the id is the latest of them. A hit on a cow is `fighting` with `entity: "minecraft:cow"`.
- `held_item` is the main-hand item id (`minecraft:air` when empty). `vehicle` is the entity type id, absent on foot. `pos` is the block position, `ts` epoch ms.
- At most one frame per player per 5 s, and only when `activity`, `detail`, `held_item` or `vehicle` changed; `pos` alone does not trigger one (`owner.pos` covers that). Going idle sends one `idle` frame, then silence. There is no frame on join or leave (`owner.pos` carries `online`), and none at first sight of an idle player.

## Amendment v1.17 (2026-11-05, O295; eve-sense 0.1.30)

Additive. **`world.explosion`** (sense→mind) `{dim, pos:{x,y,z}, ts, tick, source_type, source_uuid?, radius, blocks_broken, near:{home_m, eve_m?, owner_m?}}` is the server's record of a blast. It is data, never a permission.
- Sent for every explosion in `home.dim` within `home.explore_radius` (horizontal) of `home.center`, the same area as `mob.targeting`. Outside it there is no frame, even right next to Eve.
- Every vanilla explosion counts, wind charges included. There is no rate limit: a TNT chain sends one frame per blast.
- Each blast also writes an `[eve-audit] world.explosion` server log line with the same JSON, including when the link is down and the frame is dropped.
- `source_type` is the direct source's entity type id (`minecraft:creeper`, `minecraft:tnt`), or `unknown` when no entity caused it. `source_uuid` is that entity's uuid, absent when there is none.
- `pos` is the block holding the blast's centre, `ts` epoch ms, `tick` the server tick, `radius` the blast radius (creeper 3, TNT 4).
- `blocks_broken` is the number of blocks the blast really removed, 0 when it breaks nothing.
- `near` holds distances in metres to one decimal. `home_m` is horizontal from `home.center`, always present. `eve_m` is from Eve, absent unless she is online in that dimension. `owner_m` is from the nearest online trusted player in that dimension, absent if there is none.
