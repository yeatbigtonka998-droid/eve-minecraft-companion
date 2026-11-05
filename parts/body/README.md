# eve-body — Eve's Fabric client mod (Minecraft 26.3)

Eve's hands: streams state to the mind, runs registry skills with ordinary player inputs, owns the one control arbiter
(`STOPPED` > reflex > action > idle), honours stop from the mind (`control.stop`) and from the server helper (`eve:control`).
Contracts: `contracts/link-protocol.md`, `contracts/skill-registry.md`, `contracts/skills/registry.json` (shipped inside the jar).

## Build

Needs Temurin JDK 25 (`winget install EclipseAdoptium.Temurin.25.JDK`). Versions live only in `gradle.properties` (+ Gradle in the wrapper).

```bash
JAVA_HOME="/c/Program Files/Eclipse Adoptium/jdk-25.0.4.101-hotspot" ./gradlew build
```

Baritone (once): `gh release download v1.20.0 -R cabaletta/baritone -p baritone-api-fabric-1.20.0.jar -D libs` (the build checks its SHA-1 against `gradle.properties`; it is never bundled, the dev client gets it in `run/mods`).

`build/libs/eve-body-<version>.jar` is the mod; `./gradlew build` also runs `src/test` (registry validation and hash).

## Run locally (Shark dev fixture)

1. Dev link token (once): put a random string in `run/config/eve-link-token.txt` (gitignored). The stub/mind reads the same value from `EVE_LINK_TOKEN`.
2. Server: `./gradlew runServer` — Fabric dev server in `run-server/` (gitignored) with `src/testmod` (dev-only `/evetest control <op> <players> [mode]`, the stand-in for eve-sense's `/eve stop`; logs acks and server-observed stop timing to `run-server/evetest-log.jsonl`). `run-server/server.properties` for the fixture: `online-mode=false`, `server-ip=127.0.0.1`, flat, peaceful, `white-list=false`, RCON on 127.0.0.1:25575 with a random password.
3. Mind: the dev client talks to 8952 (the real mind owns 8951; `-Peve.link.url=ws://127.0.0.1:8951/body` to use it). Either `EVE_LINK_TOKEN=... py -3.12 tools/stub_mind.py` (stdin: `stop`, `resume`, `home`, `mode m`, `do <skill> <json>`, `raw <json>`) or a harness below — never both (both bind 8952).
4. Client: `./gradlew runClient` — offline dev account `EveDev`, quick-plays into `localhost:25565`. First run: set `onboardAccessibility:false` and `pauseOnLostFocus:false` in `run/options.txt`, or the onboarding screen blocks quick play.

## Qualification harness

`py -3.12 tools/arena.py cp09 [--baritone]` / `cp12` / `cp12home` (home routes; needs `run/config/envelope.json` with home (-12.5, -60, 12.5)), `py -3.12 tools/cp10.py` (CP-10) / `cp12` (stop while mining/attacking/using), `py -3.12 tools/cp08.py` (state vs server truth), `py -3.12 tools/reflex.py [eat,retreat,shield_arrow,shield_melee,dodge,edge,fall,arbiter]` (Phase 3 reflexes). They need server + client up, `EVE_LINK_TOKEN` set, stub not running). It builds the marked arena by RCON,
runs every attempt, reads server truth by RCON, and writes `evidence/CP-09/<stamp>-attempt-N/` (gitignored) plus
`evidence-summaries/CP-09-<stamp>-attempt-N.md` (committed).

## Skill providers (API v1, package `eve.body.api`)

Another Fabric client mod can add skills to the body without sharing its Gradle project (`parts/skills` = eve-skills).
- **Compile against** `build/api/eve-body-api-0.2.0.jar` (`./gradlew apiJar`) as `compileOnly`. At runtime eve-body's own jar carries the same classes. Depend on `"eve-body": "*"` and declare the entrypoint `"eve-body:skills": ["your.Provider"]`.
- **Registry stays single:** a provider only names a skill and version that already exist in `contracts/skills/registry.json`, the shared file shipped in the body jar. It holds params schema, preconditions, effects, timeout, evidence and failure codes. The body refuses unknown names, other versions and names already taken (logged), and puts the rest into `hello.caps`. Adding a skill = a registry entry (a minor registry bump, orchestrator-merged) plus the provider.
- **What a skill gets** (`BodyServices`): `params`, `evidence`, `progress`, `event`, `state()`, `cancelled()`; `run(skill, params)` runs any body skill as a child (Baritone `walk_to`, `look_at`, `mine_block`, `place_block`, `use_item`, `open_container`, `attack_entity`, `select_hotbar`, `sneak`, `jump`, `go_home`) with the same validation, preconditions and evidence; `precheck`, `steerTo`, `hotbarSlot`, `selectHotbar`, `inventoryCounts`, `isProtected`. Zones and grants are sense's (server-side), and the mind pre-flights them.
- **API v2/v3 additions:** `useBlock(pos)` leaves a block's menu open, with `clickSlot(slot, button, ContainerInput)` (inventory menu too) and `closeMenu()`. `guard(up)`: hold the off-hand shield as part of your action; call it every tick and the melee shield reflex yields to you. Face the attacker yourself: a shield blocks from the front only. `aim(point, maxDegPerTick)` turns toward a point. `holdUse(down)` holds or releases the main-hand use (draw and release a bow). Held inputs and open menus are released on every end of a provider action.
- **What the arbiter does:** a provider skill is an ordinary action. Each tick it returns a `Step` (input, or ok/failed/uncertain); it never writes input or clicks itself. Stop, reflexes, deadline, link loss, idempotency and reporting are the body's.

```java
public final class Provider implements EveSkillProvider {           // fabric.mod.json: "eve-body:skills": ["…Provider"]
    public void registerSkills(SkillRegistrar r) {
        r.register("walk_and_look", 1, body -> new Skill() {           // name + version must be in registry.json
            SubAction step; boolean looking;
            public Step tick(BodyServices b) {
                if (step == null) step = b.run("walk_to", obj("pos", b.params().get("pos")));
                Step s = step.tick();
                if (!s.done()) return s;                                // the child's input (Baritone while it paths)
                b.evidence().add(looking ? "look" : "walk", step.result());
                if (looking || !"ok".equals(s.status())) return s;
                looking = true; step = b.run("look_at", obj("pos", b.params().get("look"))); return Step.running();
            }});
    }
}
```
Full file: `src/example/java/eve/example/ExampleProvider.java` (dev-only `exampleJar`, never shipped). Checked on Shark with a temporary registry entry: in caps, 3/3 ok via Baritone + look_at, and a stop cancels it with no drift (`evidence/PROVIDER-API/2026-10-20-1505-attempt-1`).

## Notes

- The keyboard cannot move Eve: the arbiter replaces `LocalPlayer.input` with its own input every tick.
- STOPPED and mode persist in `<gamedir>/config/eve-body-control.json`; a restart never resumes. An unreadable file starts STOPPED.
- `walk_to` uses Baritone (API jar) when present, with chat control and breaking/placing/parkour off; without it, a straight-line direct walker. Evidence says which (`walker`).
- Hand actions (attack, mine, use, place, open) go through vanilla's own click code after aiming, only with the crosshair on the target; mouse clicks cannot drive Eve.
- Deploy to the 45L is foundation's scp + scheduled-task flow; the client needs only the jar in its `mods/` and the token in env or `config/`.
