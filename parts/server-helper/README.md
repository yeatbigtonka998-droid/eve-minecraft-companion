# eve-sense — Eve's server helper (Fabric server mod, Minecraft 26.3)

Gives Eve what a client cannot see (storage index, later block events and snapshots). It also enforces Hunter's rails on the **server, before the effect**, below every model: zones, container permissions, protected entities, hazards, rate limits, and the owner's `/eve` commands and stop path. The rails apply to Eve's player only (`owner.eve_account`). Command authority comes only from `owner.trusted_players`.

## Build and test (Shark)
```bash
export JAVA_HOME=/c/Users/eveuser/tools/jdk-25.0.4.1+1   # portable Temurin 25; Shark's PATH java is 21
./gradlew assemble                                      # build/libs/eve-sense-<version>.jar
./gradlew runGameTest -Peve.eula=true                   # CP-14/15/16/17/45 event-level suites (RailsTests) -> ../../evidence/CP-1x/<stamp>-attempt-N/
```
`./gradlew build` also runs the gametests, so it needs `-Peve.eula=true` too. The EULA was accepted by Hunter for dev/test servers (records/DECISIONS.md S11). All versions live in `gradle.properties`.

## Run a local dev server
`run/` is gitignored. It needs:
- `run/eula.txt`
- `run/config/envelope.json`: a copy of `../../config/envelope.example.json` with `world_id` set to this world. The first boot logs the value: `world_id … does not match this world <name:hash>`.
- `run/server.properties`: `server-port=25566` on Shark, because body's dev server uses 25565.
```bash
./gradlew runServer                                           # console = stdin
./gradlew runServer -Peve.link.url=ws://127.0.0.1:8952/sense  # when 8951 is taken (body's arena.py uses it on Shark)
```
- Startup refuses a missing or invalid envelope. An envelope for another world fails closed: every Eve effect is denied.
- The `STOPPED` flag persists in `<serverdir>/eve-sense/STOPPED`. Only a trusted `/eve resume` clears it.
- Link token: env `EVE_LINK_TOKEN` or `<serverdir>/config/eve-link-token.txt`, never in the repo. Without a token the link stays off and the rails still enforce.

## End-to-end stop with eve-body's client (CP-12 server side)
- `run-client/` (gitignored) holds a copy of `../body/build/libs/eve-body-*.jar` in `mods/` and an `options.txt` with `onboardAccessibility:false` and `pauseOnLostFocus:false`.
- `./gradlew runClient` joins `127.0.0.1:25566` as the offline dev account `EveDev`. That needs `online-mode=false` in `run/server.properties`: loopback dev only. The dev envelope must name `EveDev` as Eve.
- `./gradlew runServer -Peve.devtools=true` adds the **dev-only** console command `/evetest as <name> <command>`, so the console can act as a trusted owner such as `OwnerTest`. It lives in the gametest source set, is console/RCON only, and is never in the shipped jar.

## G1 integration on Shark (real mind + both mods + owner)
- Mind: `parts/mind/.venv/Scripts/python -m eve_mind` with `EVE_LINK_TOKEN`, `EVE_ENVELOPE=<this>/run/config/envelope.json`, `EVE_DB=<evidence>/mind.db`.
- Server: `./gradlew runServer -Peve.devtools=true` (same token).
- Eve: `./gradlew runClient -Peve.devtools=true` (same token; eve-body jar in `run-client/mods`).
- Owner: `./gradlew runOwnerClient` (no token, no eve-body).
- The dev driver acts on `/evetest drive <player> {"do":"break|place|open|click|close|use|draw|release|select|attack|command",...}` from the console (`attack` by `"entity"` id or the attackable entity nearest `"near":[x,y,z]`).
- `./gradlew devtoolsJar` packs the same dev tools as their own mod, `build/libs/eve-sense-devtools-<version>.jar` (no eve-sense dependency, so it also loads on Eve's client). It is for disposable test servers only: install it on the server and Eve's client for a run, then remove it. `/evetest query <blocks|resources|diff> <json>` runs eve-sense's own query/diff code and prints the reply. `tools/cp13_45l.py` … `tools/cp18_45l.py` (one per checkpoint, CP-13 to CP-18) are the RCON harnesses that use it (live runs; spec in STATUS.md "45L runs").
- The harness used for the recorded run is in the evidence folder (`harness-g1_live.py`).

## Link check against the stub hub
```bash
export EVE_LINK_TOKEN=$(py -3.12 -c "import secrets;print(secrets.token_hex(16))")
py -3.12 tools/stub_mind.py --port 8952 --log out.jsonl     # type: storage | zones | check break minecraft:overworld 0 64 0 | big
```
Run the server with the same `EVE_LINK_TOKEN` in its environment, using `-Peve.link.url=…:8952/sense`.

## Commands (trusted players only; everyone else is refused politely and logged as `owner.command{trusted:false}`)
`/eve stop | resume | home | mode <m> | approve <project> v<N> | reject <project> v<N> | save_zone <zone> | say <text> | reload`

## Where things are enforced
| effect | hook |
|---|---|
| break | `PlayerBlockBreakEvents.BEFORE` (+ `AFTER` counts the rolling-hour limit) |
| place | `BlockItemMixin`: wraps `placeBlock` inside `BlockItem.place`; checks the final position and the second half of doors and beds |
| item use on a block (hoe, axe, bone meal…) | `ItemStackMixin` on `ItemStack.useOn` |
| container open, interaction, hazard items | `UseBlockCallback` (both halves of double chests) |
| container transfer | `ContainerMenuMixin` on `clicked`/`clickMenuButton`, re-checked every click |
| item use, entity use, attack | `UseItemCallback`, `UseEntityCallback`, `AttackEntityCallback` |
| storage changes | `BlockEntityMixin` (static `setChanged`) + block-entity load/unload events |

Every denial is cancelled and emitted as `permission.denied`, plus an `[eve-audit]` line in the server log.

## Task grants (link v1.1)
`task.grant{action_id, effect: break|place, dim, positions[], expires_tick}` from the mind lifts `task_only` for exactly those positions until `expires_tick` (at most 1 h ahead).
- A whole grant is refused, with `error{bad_payload}`, if any position is outside a `task_only`/`free` zone, while STOPPED, if it has expired, or if it has fractional coordinates.
- Grants never lift `none` zones, hazards, containers or rate limits.
- They are cleared on `/eve stop`, on any envelope change and on mind link loss. `positions:[]` revokes that action's grants.

## Payload wire format (link v1.1, pinned)
`eve:control` (S2C) and `eve:control_ack` (C2S): each is one `ByteBufCodecs.STRING_UTF8` holding a JSON object.
- Control: `{"op":"stop"|"resume"|"world","nonce","source"}`; `world` adds `level_name` and `world_id` and is sent on every join of Eve. `stop` is re-sent on a join while STOPPED.
- Ack: `{"nonce","op","latency_ms","tick","stopped","duplicate"?,"error"?}`, logged as `[eve-audit] eve:control_ack` with the server round trip.
