r"""CP-74 [R/K]: brief Hunter accurately on what Eve did while he was offline. No model writes a word of it.

Reads the mind's SQLite store (contracts/memory-schema.sql) READ-ONLY for a window [since, until] and builds claims:
  {"section", "kind", "text", "facts", "cites": [[table, id], ...]}
The rule: every sentence is a claim, and its text must be re-derivable from ONLY the rows it cites (verify()). A
sentence with no cites, a cite that does not exist or lies outside the window, or text that the cited rows do not
say, is an unbacked claim and verify() reports it. verify() also checks completeness for losses: every death and
every failed/cancelled/blocked task in the window must be mentioned.

Sections: did (done tasks, ok actions per skill) / failed (tasks not finished, failed actions with codes) /
resources (inventory delta between the first and last state observation) / deaths / memory (corrections) /
questions (blocked or paused tasks, uncertain actions that need the owner, the agenda scheduler's own questions).
    python -m agenda.briefing --db <eve.db> --since <ms> [--until <ms>] [--world W] [--agenda-log decisions.jsonl]
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from collections import Counter

PK = {"tasks": "task_id", "actions": "action_id", "events": "id", "observations": "id", "facts": "fact_id"}
TS = {"tasks": "updated_ts", "actions": "COALESCE(result_ts, sent_ts)", "events": "ts", "observations": "ts",
      "facts": "created_ts"}
SECTIONS = [("did", "What I did"), ("failed", "What failed and why"), ("resources", "Resources in / out"),
            ("deaths", "Deaths"), ("memory", "Memory"), ("questions", "Open questions for you")]
BAD_ACTION = ("failed", "timeout", "uncertain", "rejected")
CTRL = re.compile(r"[\x00-\x1f\x7f]+")


def clean(v, n=160):
    """Logged text often holds owner words (a goal like "x\n- Done: castle [tasks:t1]"): one line, no cite brackets."""
    return CTRL.sub(" ", str(v)).replace("[", "(").replace("]", ")").strip()[:n] if v is not None else None


def connect(db_path):
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)       # read-only: the briefing never writes
    c.row_factory = sqlite3.Row
    return c


# derive: the ONLY place text is produced, from the cited rows alone -------------------------------------------------
def derive(kind, rows, log=None):
    """rows: list of (table, dict) in cite order. Returns (facts, text, section) or raises ValueError."""
    t = [r for _, r in rows]
    need = lambda ok, why: None if ok else (_ for _ in ()).throw(ValueError(why))  # noqa: E731
    if kind == "task_done":
        need(rows[0][0] == "tasks" and t[0]["status"] == "done", "task not done")
        acts = t[1:]
        need(all(tb == "actions" for tb, _ in rows[1:]), "only actions may back a done task")
        need(all(a["task_id"] == t[0]["task_id"] and a["status"] == "ok" for a in acts), "action not ok for this task")
        f = {"goal": clean(t[0]["goal"]), "actions": len(acts)}
        return f, f"Done: {f['goal']} ({f['actions']} confirmed action{'s' * (f['actions'] != 1)}).", "did"
    if kind == "actions_ok":
        need(rows and all(tb == "actions" and r["status"] == "ok" for tb, r in rows), "not all ok actions")
        need(len({r["skill"] for r in t}) == 1, "mixed skills")
        f = {"skill": t[0]["skill"], "n": len(t)}
        return f, f"{f['n']} {f['skill']} action{'s' * (f['n'] != 1)} succeeded.", "did"
    if kind == "task_not_finished":
        need(rows[0][0] == "tasks" and t[0]["status"] in ("failed", "cancelled"), "task not failed/cancelled")
        f = {"goal": clean(t[0]["goal"]), "status": t[0]["status"], "reason": clean(t[0]["reason"]) or "no reason recorded"}
        return f, f"Not finished: {f['goal']} - {f['status']}: {f['reason']}.", "failed"
    if kind == "actions_bad":
        need(rows and all(tb == "actions" and r["status"] in BAD_ACTION for tb, r in rows), "not all bad actions")
        need(len({(r["skill"], r["status"], r["code"]) for r in t}) == 1, "mixed skill/status/code")
        a = t[0]
        f = {"skill": a["skill"], "status": a["status"], "code": a["code"] or "no code", "n": len(t),
             "note": clean(a["note"] or a["reconcile_note"] or "", 120)}
        return f, (f"{f['n']}x {f['skill']} {f['status']} ({f['code']})" +
                   (f": {f['note']}" if f["note"] and f["n"] == 1 else "") + "."), "failed"
    if kind == "task_open":
        need(rows[0][0] == "tasks" and t[0]["status"] in ("paused", "blocked", "queued", "running"), "task not open")
        f = {"goal": clean(t[0]["goal"]), "status": t[0]["status"], "reason": clean(t[0]["reason"]) or "no reason recorded"}
        return f, f"Still {f['status']}: {f['goal']} ({f['reason']}). Shall I continue it or drop it?", "questions"
    if kind == "action_needs_owner":
        need(rows[0][0] == "actions" and t[0]["status"] == "uncertain", "action not uncertain")
        f = {"skill": t[0]["skill"], "note": clean(t[0]["reconcile_note"] or t[0]["note"]) or "outcome unknown"}
        return f, f"I could not confirm whether {f['skill']} worked ({f['note']}). Can you check?", "questions"
    if kind == "death":
        need(rows[0][0] == "events" and t[0]["kind"] == "event", "not a body event")
        p = json.loads(t[0]["payload"])
        need(p.get("kind") == "death", "not a death")
        d = p.get("detail") or {}
        pos = d.get("pos") or {}
        f = {"pos": " ".join(str(round(pos[a])) for a in "xyz") if all(a in pos for a in "xyz") else "an unknown spot",
             "dim": clean(d.get("dim")) or "unknown dimension", "cause": clean(d.get("cause")) or "unknown cause",
             "killer": clean(d.get("killer_type"))}
        return f, (f"Died at {f['pos']} in {f['dim']} to {f['cause']}" +
                   (f" ({f['killer']})" if f["killer"] else "") + "."), "deaths"
    if kind == "inventory":
        need(len(rows) == 2 and all(tb == "observations" and r["kind"] == "state" for tb, r in rows), "need 2 states")
        a, b = (Counter(), Counter())
        for c, r in zip((a, b), t):
            for s in json.loads(r["payload"]).get("inventory") or []:
                c[clean(s["item"], 80)] += int(s.get("count", 1))
        delta = {k: b[k] - a[k] for k in sorted(set(a) | set(b)) if b[k] != a[k]}
        f = {"delta": delta}
        return f, ("Inventory change: " + ", ".join(f"{v:+d} {k}" for k, v in delta.items()) + "." if delta
                   else "No inventory change between the first and last state I recorded."), "resources"
    if kind == "correction":
        need(rows[0][0] == "facts" and t[0]["source"] == "correction", "not a correction")
        f = {k: clean(t[0][k]) for k in ("subject", "predicate", "object")}
        return f, f"Memory corrected: {f['subject']} {f['predicate'].replace('_', ' ')} is now {f['object']}.", "memory"
    if kind == "agenda_question":
        need(rows[0][0] == "agenda_log" and t[0]["decision"].get("question"), "no scheduler question")
        f = {"question": clean(t[0]["decision"]["question"]), "reason": clean(t[0]["decision"]["reason"])}
        return f, f"{f['question']} ({f['reason']})", "questions"
    raise ValueError(f"unknown claim kind {kind!r}")


def fetch(c, cite, log, since, until):
    table, key = cite
    if table == "agenda_log":
        r = next((x for x in log or [] if x["seq"] == key), None)
        if r is None:
            raise ValueError(f"no agenda_log seq {key}")
        if r.get("ts") is not None and not since <= r["ts"] <= until:
            raise ValueError(f"agenda_log {key} outside the window")
        return table, r
    if table not in PK:
        raise ValueError(f"unknown table {table!r}")
    r = c.execute(f"SELECT *, {TS[table]} AS _ts FROM {table} WHERE {PK[table]}=?", (key,)).fetchone()
    if r is None:
        raise ValueError(f"no {table} row {key}")
    if r["_ts"] is None or not since <= r["_ts"] <= until:
        raise ValueError(f"{table} {key} outside the window")
    return table, dict(r)


def claim(c, kind, cites, log, since, until):
    rows = [fetch(c, x, log, since, until) for x in cites]
    f, text, section = derive(kind, rows, log)
    return {"section": section, "kind": kind, "text": text, "facts": f, "cites": [list(x) for x in cites]}


# build -----------------------------------------------------------------------------------------------------------------
def build(db_path, since, until=None, world=None, agenda_log=None):
    until = until if until is not None else int(time.time() * 1000)
    c = connect(db_path)
    w, wa = ("AND world_id=?", (world,)) if world else ("", ())
    q = lambda sql, *a: [dict(r) for r in c.execute(sql, (*a, *wa))]  # noqa: E731
    out = []
    add = lambda kind, cites: out.append(claim(c, kind, cites, agenda_log, since, until))  # noqa: E731
    tasks = q(f"SELECT * FROM tasks WHERE updated_ts BETWEEN ? AND ? {w} ORDER BY updated_ts, task_id", since, until)
    acts = q(f"SELECT * FROM actions WHERE COALESCE(result_ts, sent_ts) BETWEEN ? AND ? {w} ORDER BY rowid",
             since, until)
    in_failed = {t["task_id"] for t in tasks if t["status"] in ("failed", "cancelled")}
    for t in tasks:
        if t["status"] == "done":
            add("task_done", [["tasks", t["task_id"]]] +
                [["actions", a["action_id"]] for a in acts if a["task_id"] == t["task_id"] and a["status"] == "ok"])
    for skill in dict.fromkeys(a["skill"] for a in acts if a["status"] == "ok" and a["skill"] != "stop"):
        add("actions_ok", [["actions", a["action_id"]] for a in acts if a["status"] == "ok" and a["skill"] == skill])
    for t in tasks:
        if t["task_id"] in in_failed:
            add("task_not_finished", [["tasks", t["task_id"]]])
    groups = {}
    for a in acts:
        if a["status"] in BAD_ACTION and a["status"] != "uncertain":
            groups.setdefault((a["skill"], a["status"], a["code"]), []).append(["actions", a["action_id"]])
    for g in groups.values():
        add("actions_bad", g)
    obs = q(f"SELECT id FROM observations WHERE kind='state' AND ts BETWEEN ? AND ? {w} ORDER BY ts, id", since, until)
    if len(obs) >= 2:
        add("inventory", [["observations", obs[0]["id"]], ["observations", obs[-1]["id"]]])
    for e in deaths(c, since, until, world):
        add("death", [["events", e]])
    fw = "AND (world_id=? OR world_id IS NULL)" if world else ""      # owner preferences are world-independent
    for f in q(f"SELECT fact_id FROM facts WHERE source='correction' AND created_ts BETWEEN ? AND ? {fw} "
               "ORDER BY created_ts", since, until):
        add("correction", [["facts", f["fact_id"]]])
    for t in tasks:
        if t["status"] in ("paused", "blocked"):
            add("task_open", [["tasks", t["task_id"]]])
    for a in acts:
        if a["status"] == "uncertain":
            add("action_needs_owner", [["actions", a["action_id"]]])
    for r in agenda_log or []:
        if r["decision"].get("question") and (r.get("ts") is None or since <= r["ts"] <= until):
            if not any(o["kind"] == "agenda_question" and o["facts"]["question"] == clean(r["decision"]["question"]) for o in out):
                add("agenda_question", [["agenda_log", r["seq"]]])
    return out


def deaths(c, since, until, world=None):
    sql = "SELECT id, payload FROM events WHERE kind='event' AND ts BETWEEN ? AND ?" + (" AND world_id=?" if world else "")
    return [r["id"] for r in c.execute(sql, (since, until, *((world,) if world else ())))
            if json.loads(r["payload"]).get("kind") == "death"]


# verify ----------------------------------------------------------------------------------------------------------------
def verify(claims, db_path, since, until, world=None, agenda_log=None):
    """[] when every sentence is backed and nothing lost is left out; else one line per problem."""
    c = connect(db_path)
    bad = []
    for i, x in enumerate(claims):
        if not x.get("cites"):
            bad.append(f"#{i} unbacked (no cites): {x.get('text')!r}")
            continue
        try:
            f, text, section = derive(x.get("kind"), [fetch(c, tuple(k), agenda_log, since, until) for k in x["cites"]],
                                      agenda_log)
        except (ValueError, KeyError, IndexError, TypeError) as e:
            bad.append(f"#{i} unbacked ({e}): {x.get('text')!r}")
            continue
        if text != x.get("text") or f != x.get("facts") or section != x.get("section"):
            bad.append(f"#{i} text not what the cited rows say: {x.get('text')!r} vs {text!r}")
    cited = {(t, k) for x in claims for t, k in x.get("cites") or []}
    for e in deaths(c, since, until, world):
        if ("events", e) not in cited:
            bad.append(f"death event {e} left out")
    w = " AND world_id=?" if world else ""
    for (tid,) in c.execute("SELECT task_id FROM tasks WHERE status IN ('failed','cancelled','blocked','paused') "
                            f"AND updated_ts BETWEEN ? AND ?{w}", (since, until, *((world,) if world else ()))):
        if ("tasks", tid) not in cited:
            bad.append(f"task {tid} (not finished) left out")
    return bad


def render(claims, since, until):
    stamp = lambda ms: time.strftime("%Y-%m-%d %H:%M", time.localtime(ms / 1000))  # noqa: E731
    lines = [f"# Briefing {stamp(since)} -> {stamp(until)}", ""]
    for key, title in SECTIONS:
        xs = [x for x in claims if x["section"] == key]
        lines += [f"## {title}"] + ([f"- {x['text']}  [{', '.join(f'{t}:{k}' for t, k in x['cites'])}]" for x in xs]
                                    or ["- nothing logged"]) + [""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m agenda.briefing")
    ap.add_argument("--db", required=True)
    ap.add_argument("--since", type=int, required=True, help="window start, epoch ms")
    ap.add_argument("--until", type=int, help="window end, epoch ms (default now)")
    ap.add_argument("--world")
    ap.add_argument("--agenda-log", help="the scheduler's decisions.jsonl")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    until = a.until or int(time.time() * 1000)
    log = [json.loads(x) for x in open(a.agenda_log, encoding="utf-8")] if a.agenda_log else None
    claims = build(a.db, a.since, until, a.world, log)
    bad = verify(claims, a.db, a.since, until, a.world, log)
    print(json.dumps({"claims": claims, "problems": bad}, indent=1) if a.json else render(claims, a.since, until))
    if bad:
        print("\n".join(["UNBACKED - do not send:"] + bad), file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
