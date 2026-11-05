# eve_mind — Eve's mind service

Python 3.12. Hosts the WebSocket hub both mods connect to (`ws://127.0.0.1:8951`, paths `/body`, `/sense`; `/cli` is
mind-private), validates and dispatches registry skills, keeps durable tasks/actions in SQLite and reconciles them
with observed state. Contracts it implements: `contracts/link-protocol.md`, `contracts/skill-registry.md`,
`contracts/memory-schema.sql`, `contracts/envelope-config.md`, approvals from `contracts/design-project.md`.

## Run (Shark, dev)

```bash
cd parts/mind
py -3.12 -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
EVE_LINK_TOKEN=<at least 16 chars> .venv/Scripts/python -m eve_mind        # prints READY port=8951
```

Environment (all configuration is env-only; nothing machine-specific is read from the repo):
`EVE_LINK_TOKEN` (required, ≥ 16 chars, never in the repo), `EVE_DB` (default `parts/mind/var/eve.db`),
`EVE_PORT` (default 8951), `EVE_ENVELOPE` (default `config/envelope.json`, else the example), `EVE_ROOT`
(repo/deploy root holding `contracts/` and `config/`; default: derived from the package location), `EVE_DESIGNS`
(default `<root>/designs`), `EVE_LANE` (`45L` or `shark`: shown in `eve status`, the startup log line and every
evidence folder/RESULT.md, O18; default: the machine name), `EVE_OLLAMA_URL`, `EVE_PLANNER_MODEL`, and the deep-thinker
variables below. The startup log line prints lane, root, envelope path, registry, db.

## Lane B on Shark (S18; eve-watch runs it, the 45L stays the qualification lane)

From a plain checkout of this repo (no deploy layout needed; `EVE_ROOT` is derived from the checkout):

```powershell
cd parts\mind
py -3.12 -m venv .venv; .venv\Scripts\python -m pip install -e ".[dev]"
$env:EVE_LANE = "shark"; $env:EVE_PORT = "8955"
$env:EVE_LINK_TOKEN = "<Shark's own token, ≥ 16 chars; the mods on Shark use the same one; never commit it>"
$env:EVE_ENVELOPE = "<path outside the repo>\envelope-shark.json"   # world_id = the Shark dev world's id,
                                                                    # eve_account = "EveDev" (never FED_Chairman)
$env:EVE_DB = "<path outside the repo>\eve-shark.db"              # never the 45L's DB
$env:EVE_DESIGNS = "<path outside the repo>\designs"
$env:EVE_OLLAMA_URL = "http://127.0.0.1:11434"; $env:EVE_PLANNER_MODEL = "qwen3:8b"
# deep thinker: leave EVE_DEEPSEEK_ENABLED and OPENROUTER_API_KEY unset -> deep_thinker_disabled
.venv\Scripts\python -m eve_mind 2>> <log dir>\mind-shark.log      # READY port=<EVE_PORT>, 127.0.0.1 only
```
- Lane B as deployed by eve-watch: `C:\eve-shark\src` (a clone of main), task "Eve Shark Mind", `EVE_PORT=8955` (so a dev mind on
  8951 is unaffected; the mods on Shark point at 8955), details in `parts/watch/laneb/README.md`. One mind per port.
- The envelope's `world_id` must equal what Shark's eve-sense reports, and `eve_account` the dev client's account, or
  `eve status` shows `REFUSING DISPATCH` (binding). Copy `config/envelope.json` and change only those two fields (+
  `envelope_version` if you change rules); sense must load the same file.
- Evidence from this lane: run tools with `EVE_LANE=shark` and put `shark` in every `--out` folder name
  (`evidence/CP-NN/<stamp>-shark-…`); `run_cp.py` and `live_check.py` record the lane themselves. Shark results are
  development evidence; checkpoints about Eve's real account, the 45L hardware, GPU sharing or endurance qualify only on
  the 45L (O18).

Try it without a game: start `tools/fake_sense.py` and `tools/fake_body.py` (same env), then use the CLI.
The fakes use the example envelope's world (`eve-dev:000000000000`); point `EVE_ENVELOPE` at
`config/envelope.example.json` for that demo.

## CLI (`eve`, installed by `pip install -e`)

```
eve status                         RUNNING/STOPPED, mode, links, world binding, reconcile, open action
eve tasks                          last 20 tasks and actions with status, code, reconcile notes
eve do <skill> key=value ...       e.g. eve do walk_to x=10 y=64 z=-3   (x/y/z fold into pos; --wait S)
eve stop [reason]                  durable STOPPED; sends control.stop; survives restarts
eve resume                         the only way out of STOPPED (paused tasks stay paused)
eve home                           control.home (refused while STOPPED)
eve mode manual|supervised|autonomous   (autonomous refused until CP-73+; others need envelope.mode_allowed)
eve approve|reject <project_id> v<N> [--by <trusted player>]
eve revoke <project_id> v<N> [--by NAME] [--reason TEXT]   invalidate an approval (tasks relying on it go stale)
eve cancel [reason]                cancel the running action with the `stop` skill (not a hard STOP)
eve continue <task_id|prefix>      resume a paused/blocked task: re-checks assumptions + observed state first
eve drop <task_id|prefix> | --parked   cancel parked (paused/blocked) tasks; parked tasks also expire after 1 h
eve ask <words...>                 owner words: rule mapper first, then the local planner (Ollama)
eve check <effect> x y z [--dim D] [--face F] | --entity ID   ask sense via permission.check (nothing runs)
```
Planner: `EVE_OLLAMA_URL` (default `http://127.0.0.1:11434`), `EVE_PLANNER_MODEL` (default `qwen3:8b`).
Deep thinker (DeepSeek via OpenRouter) stays disabled unless `EVE_DEEPSEEK_ENABLED=1`, `OPENROUTER_API_KEY`,
`EVE_DEEPSEEK_MODELS`, `EVE_DEEPSEEK_PRICES` and the envelope cap are all set; `eve spend` shows month-to-date.
Recipe solver: `python -c "from eve_mind.recipes import *; from eve_mind.config import ROOT; print(solve(RecipeBook(ROOT/'data/game/26.3'), {'minecraft:chest': 2}))"`
Exit codes: 0 ok, 1 refused (reason printed), 2 service unreachable.

## Tests and evidence

```bash
.venv/Scripts/python -m pytest -q                    # everything (Ollama-dependent tests skip without a server)
.venv/Scripts/python tools/run_cp.py CP-11           # a counted qualification attempt -> evidence/CP-11/...
```
`run_cp.py` writes `evidence/CP-NN/<stamp>-attempt-N/` (gitignored: junit.xml, pytest.log, manifest.json,
RESULT.md) and copies RESULT.md to `evidence-summaries/` (committed).

## Deploy notes for the 45L (foundation owns the scheduled task)

- Layout: `C:\eve\mind\` holding `parts\mind\` (this folder), `contracts\` and `config\` with the same relative layout
  as the repo, or set `EVE_ROOT`. Python 3.12 venv inside `C:\eve\mind\parts\mind\.venv`.
- Scheduled task: run at boot, restart on failure, working dir `C:\eve\mind\parts\mind`, command
  `.venv\Scripts\python.exe -m eve_mind`, env `EVE_LINK_TOKEN` (machine secret), `EVE_DB=C:\eve\mind\eve.db`.
  Logs go to stderr; redirect to `C:\eve\logs\mind.log`.
- Binds 127.0.0.1:8951 only. Never expose via Tailscale serve/funnel.
- Backups: the DB is SQLite in WAL mode; copy it online with Python's `sqlite3.Connection.backup()` (a plain
  file copy of a live WAL database can be inconsistent).
