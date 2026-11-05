# Eve (Project Sentience)

Eve is a persistent companion for **Minecraft: Java Edition 26.3**. She plays on her own account next to her owner,
takes orders in chat, follows, fights, gathers, crafts, stores and builds, and keeps a memory of the world.
Not an official Minecraft product; not approved by or associated with Mojang or Microsoft.

## How it fits together
| part | language | what it is |
|---|---|---|
| `parts/mind` | Python 3.12 | the mind: a WebSocket hub, owner-language understanding, planner, task control, memory, dialogue. It talks to a planner model through one replaceable provider (local Ollama by default). |
| `parts/body` | Java 25, Fabric client mod | Eve's hands and feet: control arbiter, reflexes, skill primitives. Walking uses Baritone. |
| `parts/server-helper` | Java 25, Fabric server mod ("sense") | senses and safety rails on the server: zones, permission checks, grants, audit, owner position and activity. |
| `parts/combat`, `parts/skills`, `parts/builder` | Java 25, Fabric client mods | fighting, resource/crafting/storage skills, building from designs. |
| `parts/design` | Python 3.12 | offline design system: schematic I/O, building language, validation, bill of materials, previews. |
| `parts/watch` | Python 3.12 | a read-only status board and evidence tooling. |
| `contracts/` | docs | the interfaces between parts: link protocol, skill registry, envelope config, memory schema, design project model. |
| `config/envelope.example.json` | JSON | an example envelope (owner, home, zones, limits). Copy it to `config/envelope.json` and edit. |

## What you need
- Your own copy of Minecraft: Java Edition 26.3. Nothing from Mojang is included here.
- **Game data**, extracted from the official 26.3 jars on your machine (needed by the mind's recipes and the design
  tests): `python parts/design/tools/extract_game_data.py --version 26.3`, which fills `data/game/26.3/` and checks
  the jars against `data/game/26.3/MANIFEST.json`. None of it is redistributed here.
- JDK 25 and the Gradle wrappers in each Java part. Fabric Loader 0.19.5 and Fabric API 0.163.0+26.3 are fetched by Gradle.
- **Baritone** (LGPL-3.0, a separate mod, not bundled): put `baritone-api-fabric-1.20.0.jar` in `parts/body/libs/`
  from Baritone's own release. The build checks the SHA-1 pinned in `parts/body/gradle.properties`.
- Python 3.12 for `parts/mind`, `parts/design` and `parts/watch` (`pip install -e .[dev]` for mind/watch, `.[test]` for design).
- A planner model: by default [Ollama](https://ollama.com) with `qwen3:8b`. Set `EVE_OLLAMA_URL` / `EVE_PLANNER_MODEL`
  if it is not on `127.0.0.1:11434`.
- A dedicated Fabric 26.3 server with the sense mod, one client for Eve with her mods, and the mind running beside them.
  The link token comes from the `EVE_LINK_TOKEN` environment variable (at least 16 characters, never committed).

## Build and test
```
# Python parts (each folder)
cd parts/mind && pip install -e .[dev] && pytest -m "not ollama"
cd parts/design && pip install -e .[test] && pytest          # add [litematic] for .litematic export (see below)
# Java parts (each folder)
cd parts/body && ./gradlew build
```
Some tests are named for the machines the project was developed on ("45L", "Shark", lane B/C/D) and use the
original owner/account names as fixtures. Those names are just data.

## Optional GPL extra
`.litematic` export uses **litemapy** (GPL-3.0). It is an optional extra (`pip install 'eve-design[litematic]'`), not a
dependency of this MIT code. Without it, writing a `.litematic` fails with a clear message and its tests are skipped.

## Hosted planner (optional, off by default)
The planner sits behind one provider boundary (`parts/mind/src/eve_mind/provider.py`). The default is the local Ollama
model. The hosted option uses OpenAI's official **Sign in with ChatGPT** flow for open-source apps, so requests use
your own ChatGPT plan. There is no API key and no automatic fallback: if the hosted route fails, the planner says so.
```
cd parts/mind && pip install -e .
eve-brain-login      # opens OpenAI's sign-in page in your browser ("Continue with ChatGPT"); answer comes back on
                     # http://127.0.0.1:<port>/auth/callback; checks plan usage was granted; one small test request
eve-brain-status     # connected / signed out / limit reached, model, billing mode (tokens are never printed)
set EVE_PLANNER_PROVIDER=openai_plan   # then start the mind; unset it to go back to the local model
eve-brain-logout     # revokes the session and deletes the local tokens
```
Tokens are kept outside the repository (`EVE_BRAIN_HOME`, else `%LOCALAPPDATA%\eve\hosted-brain` or
`~/.local/share/eve/hosted-brain`), owner-only, never logged. Set per-app limits in ChatGPT -> Settings -> Usage; on
Plus the five-hour limit is shared with your other ChatGPT apps. Movement, combat, eating, stop and every safety reflex
stay local and keep working without the cloud.

## Not included
Development records and decision logs, recorded play sessions and evidence, lane and install scripts for the original
machines, private configuration, and any game files. `parts/design/examples/out/*/previews/*.png` are rendered
previews: each face is a flat colour averaged from Minecraft's textures. They are kept because the design tests hash
them.

## Licence
MIT, see `LICENSE`. Third-party pieces keep their own licences: Fabric (Apache-2.0), Gradle wrapper (Apache-2.0),
Baritone (LGPL-3.0, not bundled), litemapy (GPL-3.0, optional extra), Python dependencies (MIT/BSD).
