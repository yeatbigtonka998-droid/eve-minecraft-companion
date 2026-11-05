# CI notes for the test suites (for the eve-ci workflow)

Written 2026-10-30 by kickoff from the part owners' reports. Hardware-free: no Minecraft client, no GPU.

## mind (Python 3.12), from `parts/mind`
- `PYTHONPATH=src:.` (Windows `src;.`), `EVE_OLLAMA_URL=http://127.0.0.1:9` (dead port: tests marked `ollama` are deselected, everything else must not reach a model).
- `python -m pytest -q -m "not ollama" -p no:cacheprovider tests agenda/tests`
- `EVE_REGISTRY` unset (defaults to `contracts/skills/registry.json`).
- The git-ignored game data `data/game/26.3/` (recipes, tags, blockstates extracted from the vanilla 26.3 jar) must exist or the craft tests fall through to the planner and fail. In CI either (a) download the official 26.3 server jar in a step and run the repo's extraction script (see `parts/foundation/ops/` and `parts/mind/tools/`), or (b) mark those tests `needs_game_data` and deselect them, stating the count in the job summary. Never commit the jar or the extracted data.
- Full suite is ~800 tests, about 15 min single-process; split by file across a matrix if the runner is slow.

## design (Python), from `parts/design`: `python -m pytest -q` (local 2026-10-30: 504 passed, 11 failed pending the O94ag-c re-plans; report counts, do not fix).
## watch (Python), from `parts/watch`: `python -m pytest -q`.
## learning (Python), from `parts/learning`: `python -m pytest -q`.
## Java parts (Temurin 25, Gradle wrapper in each folder): `./gradlew test` in `parts/body`, `parts/skills`, `parts/combat`, `parts/builder`; `parts/server-helper` additionally has headless gametests (`./gradlew runGametest` or the task named in its build.gradle). Loom resolves Minecraft from Mojang's servers at build time; if an artifact needs a private mirror, mark the job continue-on-error and say so.

## Rules
No secrets, no jars, no game data committed. Report exact failing test names; do not change product code from CI work.
