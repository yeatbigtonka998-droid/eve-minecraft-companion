# Continuous integration

`.github/workflows/ci.yml` runs every part's unit tests on GitHub-hosted Ubuntu runners on each push and on pull
requests against `main`. Nothing in it needs a Minecraft client, a GPU, the 45L or Shark (S93: code and unit tests
only). A newer push to the same branch cancels the run in progress. Each job writes its test counts to the run's
summary page; the final `summary (all parts)` job prints one table for the whole run with the exact names of
anything red.

Toolchain in CI: Temurin 25 (`actions/setup-java`), Gradle through each part's own wrapper (9.7.1, cached by
`gradle/actions/setup-gradle`), Python 3.12 (`actions/setup-python`, pip cache). Versions of Minecraft, Loom,
Fabric Loader and Fabric API come from each part's `gradle.properties`; the workflow pins none of its own.

## What runs, part by part

| part | job | command (run in the part's directory) | needs |
|---|---|---|---|
| parts/body | `java body` | `./gradlew test` | Baritone API jar (below) |
| parts/skills | `java skills` | `./gradlew test` | game data 26.3 (below) |
| parts/combat | `java combat` | `./gradlew test` | - |
| parts/builder | `java builder` | `./gradlew test` | `parts/design/examples/out` (committed) |
| parts/server-helper | `java server-helper` | `./gradlew runGameTest -Peve.eula=true` | headless dedicated server started by Loom |
| parts/mind | `python mind` | `python -m pytest -m "not ollama" --timeout=600` | game data 26.3 (CP-35 tests), `EVE_OLLAMA_URL` dead |
| parts/design | `python design` | `python -m pytest` | game data 26.3 |
| parts/watch | `python watch` | `python -m pytest` | - |
| parts/learning | `python learning` | `python -m pytest` | duckdb, pandas, numpy, scikit-learn |

The Java `test` tasks are plain JUnit 5: Loom resolves Minecraft and Fabric from Mojang's and Fabric's public
servers but launches no game. The server helper has no `src/test`; its suite is the Fabric gametest entrypoint
`RailsTests` (CP-14/15/16/17/45), which Loom runs on a headless dedicated server. `-Peve.eula=true` is the EULA
accepted for dev/test servers (records/DECISIONS.md S11). The gametest writes its evidence folders under
`evidence/`, which stays gitignored; the job uploads the server log as an artifact.

### Inputs that are not in the repository

- **Game data 26.3** (`data/game/26.3/`): only `MANIFEST.json` is committed. The `gamedata` job runs
  `parts/design/tools/extract_game_data.py`, which downloads Mojang's public client and server jars, verifies their
  SHA-1 against the version manifest, extracts recipes, tags, blockstates, models and textures, and runs the server's
  data generator for `reports/blocks.json` and `items.json`. The extracted tree plus the client jar (the mind's recipe
  solver reads loot tables from `jars/client-26.3.jar`) is cached under a key made from `MANIFEST.json` and the
  extractor itself, so later runs restore it in seconds; the server jar is dropped after the reports step. Jobs that need it restore the same
  key and fail if it is missing.
- **Baritone API jar** (`parts/body/libs/baritone-api-fabric-<version>.jar`): `compileOnly`, gitignored. The body job
  downloads it from the public cabaletta/baritone release named in `gradle.properties` and checks its SHA-1 against
  both `libs/checksums.txt` and `baritone_sha1`; the build's own `verifyBaritone` task checks it again.

### Tests that are deliberately not run in CI

- mind tests marked `ollama` (`tests/test_cp41.py::test_live_planner`) need a live Ollama with `qwen3:8b`. They are
  deselected with `-m "not ollama"` and would also self-skip because `EVE_OLLAMA_URL` points at a dead port.
- design tests marked `slow` run the planner to its full budget (10+ minutes each); design's `conftest.py` skips
  them unless `EVE_SLOW=1`.
- learning's `test_dataset.py::test_no_row_from_an_invalid_run` skips when `eve-evidence.duckdb` (gitignored, built
  from `evidence/`) is absent.
- Nothing that needs a Minecraft client, a lane, the 45L or a GPU: those stay evidence runs, not CI.

## Running the same suites locally

Java parts need JDK 25 on `JAVA_HOME` (Shark: the portable Temurin 25 named in each part's README/STATUS). Python
parts need Python 3.12 in a venv (`.venv/`, gitignored).

```bash
# game data (once; needs Java 25 for the reports and network to Mojang)
python parts/design/tools/extract_game_data.py --version 26.3

# Baritone API jar for the body (once; SHA-1 is checked by the build)
gh release download v1.20.0 -R cabaletta/baritone -p baritone-api-fabric-1.20.0.jar -D parts/body/libs

# Java parts
(cd parts/body && ./gradlew test)
(cd parts/skills && ./gradlew test)
(cd parts/combat && ./gradlew test)
(cd parts/builder && ./gradlew test)
(cd parts/server-helper && ./gradlew runGameTest -Peve.eula=true)

# Python parts (one venv per part, as the READMEs do, or one shared venv)
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e parts/mind[dev] -e parts/design[test] -e parts/watch[dev] duckdb pandas numpy scikit-learn pytest-timeout
(cd parts/mind && python -m pytest -q -m "not ollama")
(cd parts/design && python -m pytest -q)           # EVE_SLOW=1 adds the slow planner tests
(cd parts/watch && python -m pytest -q)
(cd parts/learning && python -m pytest -q)
```

JUnit XML lands in `parts/<part>/build/test-results/test/` for Gradle and wherever `--junitxml` points for pytest;
`.github/scripts/junit_summary.py <path>` prints the same counts table CI shows.

## Reading a red run

Open the run's summary page: each job's table names the failed tests. A failure in `gamedata` or in a dependency
download is a CI-environment problem (Mojang, Fabric maven or GitHub releases unreachable), not a product failure;
re-run the job. A failing unit test with a stack trace is a real failure to report to the owning part; CI never
edits product code.
