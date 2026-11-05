# eve-watch — watch Eve from Shark

A small read-only web app on the 45L: **left** a live view of Eve's game window (~2 frames/s), **right** a board of
what her mind is doing: links, world, mode, STOPPED, position/health/food, inventory, the active task and action,
the last 10 actions with evidence, the last 10 planner decisions, the last 10 events, the server/client heartbeat and
the last journal line. Nothing on it can send anything to Eve.

## Open it on Shark

```bash
"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --app=http://<45L-LAN-address>:8953 --window-size=1100,650
```

Or just browse to http://<45L-LAN-address>:8953. LAN only: it is not reachable over Tailscale and is never exposed via
`tailscale serve`/`funnel`.

## What the page shows (and where it comes from)

| Board item | Source |
|---|---|
| RUNNING/STOPPED, mode, links (mind/body/sense), "dispatch refused: …" | `eve status` via the mind's `/cli` (`cmd: status` only), every 1.5 s while someone watches |
| Eve now (pos, dim, health, food, holding, threats, biome), inventory | latest `observations` row (`kind='state'`) |
| Doing (task, action, elapsed) | `tasks` (running/queued/paused/blocked), `actions` (pending/sent/accepted/running) |
| Last actions + evidence, decisions, events, journal | `actions`, `decisions`, `events` (minus `eve.status`/`metrics`), `journal` |
| Heartbeat: server TPS, chunks, broke/placed; client fps/tick | latest `events` of kind `eve.status` (sense) and `metrics` (body) |

The DB is opened with `mode=ro`. Action progress percent is not stored by the mind, so "Doing" shows elapsed time.

If the game window is minimised, missing or the screen is locked/black, the video area says so instead of failing.

## Run, deploy, stop

Deploy from Shark (repo root). It copies the **committed** `parts/watch` to `C:\eve\watch` and runs `install-watch.ps1`
there: venv (Python 3.12, Pillow, websockets), firewall rule **"Eve Watch 8953 (LAN)"** (TCP 8953, Ethernet,
LocalSubnet), scheduled task **"Eve Watch"** (at eveuser's logon, *Interactive*, so it runs in the console session that
owns Eve's window). It then prints the listening address and the PID's session (must be 1).

```powershell
.\parts\watch\ops\deploy-watch.ps1
```

Stop and start on the 45L:

```bash
ssh eveuser@<45L-LAN-address> "powershell -NoProfile -ExecutionPolicy Bypass -File C:\eve\watch\ops\stop-watch.ps1"
ssh eveuser@<45L-LAN-address> "powershell -NoProfile -Command Start-ScheduledTask 'Eve Watch'"
```

Log: `C:\eve\logs\watch.log`. `run-watch.ps1` loads the link token into its own environment (never logged or served;
used only for the read-only `status` call), binds `<45L-LAN-address>:8953`, restarts the server 10 s after any exit.

Local (Shark), board only against a copy of the DB:

```bash
cd parts/watch && py -3.12 -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
EVE_DB=path/to/eve.db .venv/Scripts/python -m eve_watch --host 127.0.0.1 --no-capture
.venv/Scripts/python -m pytest -q
```

## How the capture works

`PrintWindow(PW_CLIENTONLY | PW_RENDERFULLCONTENT)` on the visible `SDL_app` (26.3) or `GLFW30` window titled
`Minecraft…`, via ctypes, scaled to ≤960 px wide and JPEG-encoded. It reads the window's own surface: it works when the
window is behind others and never focuses it or moves the mouse. Captures happen only when a viewer asks for a frame
(at most every 0.4 s, shared by all viewers), so it costs nothing while the window on Shark is closed.
