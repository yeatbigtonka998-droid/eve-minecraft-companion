"""The board: Eve's real records, read-only. `eve status` comes from the mind's /cli (token from EVE_LINK_TOKEN in the
environment, never logged or served); everything else from the mind's SQLite opened with mode=ro."""
import json
import os
import sqlite3
import time
from collections import Counter

OPEN = ("pending", "sent", "accepted", "running")
HEARTBEAT_KINDS = ("eve.status", "metrics")       # periodic, shown as the heartbeat line instead of in the event list


def now_ms():
    return int(time.time() * 1000)


def short(v, n=240):
    """Compact one-line text of a JSON value or string, cut at n chars."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            pass
    s = v if isinstance(v, str) else json.dumps(v, separators=(",", ":"))
    return s if len(s) <= n else s[: n - 1] + "…"


def cli_status(port=8951, timeout=2.0):
    """`eve status` via the mind's /cli. -> (dict | None, error | None). Read-only: only ever sends cmd=status."""
    tok = os.environ.get("EVE_LINK_TOKEN")
    if not tok:
        return None, "EVE_LINK_TOKEN not set for eve-watch"
    from websockets.sync.client import connect
    try:
        with connect(f"ws://127.0.0.1:{port}/cli", proxy=None, open_timeout=timeout, max_size=4 << 20) as ws:
            ws.send(json.dumps({"token": tok, "cmd": "status", "args": {}}))
            r = json.loads(ws.recv(timeout))
    except Exception as e:                       # unreachable, closed, timeout: the board says so instead of failing
        return None, f"mind /cli unreachable: {type(e).__name__}"
    return (r, None) if r.get("ok") else (None, f"mind refused status: {r.get('error')}")


def rows(con, sql, *args):
    return [dict(r) for r in con.execute(sql, args)]


def board(db_path, status=None, status_err=None):
    t = now_ms()
    out = {"now": t, "status": status, "status_error": status_err}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error as e:
        return {**out, "db_error": str(e)}
    con.row_factory = sqlite3.Row
    try:
        meta = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM meta")}
        out["meta"] = {k: meta.get(k) for k in ("control.stopped", "control.stopped_by", "control.stopped_ts",
                                                  "control.mode", "control.resumed_ts")}

        obs = con.execute("SELECT ts, world_id, payload FROM observations WHERE kind='state' "
                          "ORDER BY id DESC LIMIT 1").fetchone()
        if obs:
            p = json.loads(obs["payload"])
            inv = Counter()
            for s in p.get("inventory") or []:
                inv[s["item"].removeprefix("minecraft:")] += s.get("count", 1)
            out["eve"] = {"ts": obs["ts"], "world_id": obs["world_id"], "pos": p.get("pos"), "dim": p.get("dim"),
                          "health": p.get("health"), "food": p.get("food"), "gamemode": p.get("gamemode"),
                          "mainhand": (p.get("equipment") or {}).get("mainhand"), "biome": p.get("biome"),
                          "time_of_day": p.get("time_of_day"), "light": p.get("light_level"),
                          "threats": len(p.get("threats") or []), "control": p.get("control"),
                          "inventory": [f"{i} {n}" for i, n in inv.most_common(12)],   # ASCII: read in cp1252 consoles too
                          "inventory_slots": len(p.get("inventory") or [])}

        cur = con.execute(f"SELECT * FROM actions WHERE status IN ({','.join('?' * len(OPEN))}) "
                          "ORDER BY sent_ts DESC LIMIT 1", OPEN).fetchone()
        if cur:
            cur = dict(cur)
            cur["elapsed_ms"] = t - (cur["accepted_ts"] or cur["sent_ts"] or t)
            cur["params"] = short(cur["params"])
            cur["evidence"] = short(cur["evidence"]) if cur["evidence"] else None
        out["active_action"] = cur
        # mind dcdba09: active = running/queued only; paused/blocked are parked (never continue on their own)
        task = con.execute("SELECT task_id, kind, goal, status, reason, source, progress, created_ts, updated_ts "
                           "FROM tasks WHERE status IN ('running','queued') ORDER BY updated_ts DESC LIMIT 1").fetchone()
        out["active_task"] = dict(task) if task else None
        out["parked_tasks"] = rows(con, "SELECT task_id, goal, status, reason, updated_ts FROM tasks "
                                        "WHERE status IN ('paused','blocked') ORDER BY updated_ts DESC LIMIT 5")
        out["recent_tasks"] = rows(con, "SELECT goal, status, reason, source, updated_ts FROM tasks "
                                        "ORDER BY created_ts DESC LIMIT 5")

        # SELECT * so a DB from before schema v1.1 (no actions.note) still reads
        acts = rows(con, "SELECT * FROM actions ORDER BY rowid DESC LIMIT 10")
        acts = [{k: a.get(k) for k in ("action_id", "skill", "params", "status", "code", "evidence", "note",
                                       "reconcile_note", "sent_ts", "result_ts")} for a in acts]
        for a in acts:
            a["params"], a["evidence"] = short(a["params"], 120), a["evidence"] and short(a["evidence"])
        out["actions"] = acts

        decs = rows(con, "SELECT ts, kind, model, prompt_version, latency_ms, validated, input, output "
                         "FROM decisions ORDER BY ts DESC LIMIT 10")
        for d in decs:
            d["input"], d["output"] = short(d["input"], 200), short(d["output"], 400)
        out["decisions"] = decs
        lat = [d["latency_ms"] for d in decs if d["latency_ms"] and d["model"] != "rules"]
        out["planner_latency_ms"] = {"last": lat[0], "avg": sum(lat) // len(lat)} if lat else None

        hb = ",".join("?" * len(HEARTBEAT_KINDS))
        evs = rows(con, f"SELECT ts, source, kind, payload FROM events WHERE kind NOT IN ({hb}) "
                        "ORDER BY id DESC LIMIT 10", *HEARTBEAT_KINDS)
        for e in evs:
            e["payload"] = short(e["payload"], 200)
        out["events"] = evs
        out["heartbeat"] = {}
        for k in HEARTBEAT_KINDS:
            r = con.execute("SELECT ts, payload FROM events WHERE kind=? ORDER BY id DESC LIMIT 1", (k,)).fetchone()
            if r:
                out["heartbeat"][k] = {"ts": r["ts"], **json.loads(r["payload"])}

        j = con.execute("SELECT ts, kind, text FROM journal ORDER BY id DESC LIMIT 1").fetchone()
        out["journal"] = dict(j) if j else None
    except sqlite3.Error as e:
        out["db_error"] = str(e)
    finally:
        con.close()
    return out
