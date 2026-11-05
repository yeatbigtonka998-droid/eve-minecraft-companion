"""`eve` — owner CLI for the mind service on this machine (talks to ws://127.0.0.1:<EVE_PORT>/cli)."""
import argparse
import asyncio
import json
import os
import sys
import time

from websockets.asyncio.client import connect

from .config import env_token, strict_loads


def parse_kv(pairs):
    """key=value ... -> params. Values parse as JSON when they can (numbers, true/false, objects), else strings.
    x/y/z fold into pos, so `eve do walk_to x=10 y=64 z=-3` works."""
    params = {}
    for kv in pairs:
        if "=" not in kv:
            raise SystemExit(f"expected key=value, got {kv!r}")
        k, v = kv.split("=", 1)
        try:
            params[k] = strict_loads(v)
        except ValueError:
            params[k] = v
    xyz = {a: params.pop(a) for a in "xyz" if a in params}
    if xyz:
        params.setdefault("pos", xyz)
    return params


async def call(cmd, args, port, timeout):
    async with connect(f"ws://127.0.0.1:{port}/cli", proxy=None, open_timeout=3, max_size=4 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"token": env_token(), "cmd": cmd, "args": args}))
        return strict_loads(await asyncio.wait_for(ws.recv(), timeout))


def render(cmd, r):
    if not r.get("ok"):
        return f"REFUSED {r.get('error')}: {r.get('detail', '')}".rstrip(": ")
    if cmd == "status":
        b, s = r["body"], r["sense"]
        lines = ["STOPPED" + (f" (by {r['stopped_by']})" if r["stopped_by"] else "") if r["stopped"] else "RUNNING",
                 f"mode: {r['mode']}  lane: {r.get('lane')}  mind: {r.get('mind_commit')} src: {r.get('mind_src')} since "
                 f"{r.get('mind_started')}",
                 f"body: {'up ' + b['account'] + ' level=' + b['level'] + ' state_age=' + str(b['state_age_ms']) + 'ms pos=' + json.dumps(b['pos']) if b else 'DOWN'}",
                 f"sense: {'up world=' + s['world_id'] if s else 'DOWN'}",
                 f"world binding: {'OK ' + r['binding']['detail'] if r['binding']['ok'] else 'REFUSING DISPATCH - ' + r['binding']['detail']}",
                 f"reconcile: {'PENDING' if r['reconcile_needed'] else 'done'}",
                 f"open action: {json.dumps(r['open_action']) if r['open_action'] else 'none'}",
                 f"tasks: active {r['active_task']['goal'] + ' ' + r['active_task']['task_id'][:8] if r['active_task'] else 'none'}"
                 f", parked {r['parked_tasks']}" + (" (eve tasks)" if r["parked_tasks"] else ""),
                 f"registry: {r['registry']['version']} {r['registry']['hash'][:16]}"]
        return "\n".join(lines)
    if cmd == "tasks":
        age = lambda t: f"{(r['now_ts'] - t['updated_ts']) // 60000:>3} min"  # noqa: E731
        line = lambda t: f"  {t['task_id'][:8]} {t['status']:<9} {t['goal']:<14} {age(t)}  {t['reason'] or ''}".rstrip()  # noqa
        out = ["ACTIVE"] + ([line(t) for t in r["active"]] or ["  none"])
        out += [f"PARKED (never continue on their own; `eve continue <id>` re-checks the world, `eve drop <id>` or "
                f"`eve drop --parked` clears; auto-cancelled after {r['parked_ttl_s'] / 60:g} min)"]
        out += [line(t) for t in r["parked"]] or ["  none"]
        out += ["RECENT"] + ([line(t) for t in r["recent"]] or ["  none"])
        out += ["ACTIONS"] + [f"  {a['action_id'][:8]} {a['status']:<9} {a['skill']:<14} {a['code'] or ''} "
                              f"{a['reconcile_note'] or ''}{' note: ' + a['note'] if a.get('note') else ''}".rstrip()
                              for a in r["actions"]]
        return "\n".join(out)
    if cmd == "do":
        return (f"{r['status'].upper()} {r['action_id']} (task {r['task_id'][:8]})"
                + (f" code={r['code']}" if r["code"] else "") + (f"\nevidence: {json.dumps(r['evidence'])}" if r["evidence"] else "")
                + (f"\nreconcile: {r['reconcile_note']}" if r["reconcile_note"] else "")
                + (f"\nnote: {r['note']}" if r.get("note") else ""))
    if cmd == "check":
        return (f"{'ALLOWED' if r['allowed'] else 'DENIED'} {r['query']['effect']} "
                f"{r['query'].get('pos') or 'entity ' + str(r['query'].get('entity_id'))} in {r['query']['dim']}: "
                f"rule={r['rule_id']} reason={r['reason']} ({r['latency_ms']} ms)")
    if cmd == "recall":
        if r["unknown"]:
            return "unknown (nothing stored; no guess)"
        return "\n".join(f"{f['subject']} {f['predicate']} {f['object']}  [{f['source']}, conf {f['confidence']}"
                         f"{', STALE' if f.get('stale') else ''}, {f['world_id'] or 'any world'}, "
                         f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(f['created_ts'] / 1000))}]"
                         for f in r["facts"])
    if cmd == "spend":
        return (f"month-to-date US${r['month_to_date_usd']:.6f} of US${r['cap_usd']} cap, remaining "
                f"US${r['remaining_usd']:.6f}; today US${r['today_usd']:.6f}; {r['calls']} calls "
                f"({r['unsettled']} unsettled, counted at worst case)\n" +
                "\n".join(f"  {m}: US${u:.6f}" for m, u in r["by_model"].items()))
    if cmd == "stop":
        return "STOPPED" + ("" if r["delivered"] else " (body link down: will be re-asserted when it reconnects)")
    if cmd == "resume":
        return "RESUMED" + ("" if r["delivered"] else " (body link down)") + f" - {r['note']}"
    return json.dumps({k: v for k, v in r.items() if k != "ok"})


def main(argv=None):
    ap = argparse.ArgumentParser(prog="eve", description="Eve's mind: owner commands")
    ap.add_argument("--port", type=int, default=int(os.environ.get("EVE_PORT", 8951)))
    ap.add_argument("--json", action="store_true", help="print the raw reply")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("tasks")
    sub.add_parser("resume")
    sub.add_parser("home")
    sub.add_parser("spend", help="DeepSeek/OpenRouter month-to-date spend against Eve's cap")
    p = sub.add_parser("retain", help="keep state rows this many minutes (10..1440; 10 = default) - set at run start")
    p.add_argument("minutes", type=int)
    p = sub.add_parser("check", help="ask sense whether an effect is allowed (permission.check; nothing runs)")
    p.add_argument("effect", help="break | place | open_container | attack | use_item | use_hazard")
    p.add_argument("xyz", nargs="*", type=int, help="block position x y z")
    p.add_argument("--entity", type=int, help="entity id instead of a position")
    p.add_argument("--dim", help="dimension (default: the envelope's home dimension)")
    p.add_argument("--face", choices=["up", "down", "north", "south", "east", "west"],
                   help="clicked face (use_item: sense checks the block and its neighbour on this face)")
    p = sub.add_parser("ask", help="owner words: rules first, then the local planner")
    p.add_argument("text", nargs="+")
    p = sub.add_parser("objective", help="CP-43: run a multi-step objective (plan in chunks, re-plan, budget)")
    p.add_argument("text", nargs="*")
    p.add_argument("--status", nargs="?", const="", help="status of an objective (default: the current one)")
    p.add_argument("--continue", dest="cont", help="continue a stopped/partial objective from its ledger")
    p = sub.add_parser("recall", help="CP-44: current facts about a subject (owner.home, owner, job, container <id>)")
    p.add_argument("subject", nargs="+")
    p.add_argument("--predicate")
    p = sub.add_parser("forget", help="O111-b: retire one false fact (kept, never deleted); id prefix + its subject and "
                                      "predicate must all match")
    p.add_argument("fact_id")
    p.add_argument("--subject", required=True)
    p.add_argument("--predicate", required=True)
    sub.add_parser("history", help="CP-44: journal entries grounded in ok actions (confirmed history only)")
    p = sub.add_parser("cancel")
    p.add_argument("reason", nargs="*")
    p = sub.add_parser("drop", help="cancel a parked (paused/blocked) task, or all of them")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--parked", action="store_true", help="drop every paused/blocked/queued task")
    p = sub.add_parser("continue")
    p.add_argument("task_id", help="task id or unique prefix (see eve tasks)")
    p.add_argument("--wait", type=float, default=15.0)
    p = sub.add_parser("revoke")
    p.add_argument("project_id")
    p.add_argument("version", help="v<N>")
    p.add_argument("--by")
    p.add_argument("--reason", default="")
    p = sub.add_parser("stop")
    p.add_argument("reason", nargs="*")
    p = sub.add_parser("mode")
    p.add_argument("mode")
    p = sub.add_parser("do")
    p.add_argument("skill")
    p.add_argument("params", nargs="*", help="key=value ...")
    p.add_argument("--wait", type=float, default=15.0, help="seconds to wait for a terminal result")
    p.add_argument("--assume", action="append", default=[],
                   help="record what this task relies on (CP-23): container_has:<id>:<item>:<count> | approval:<project>:v<N>")
    p = sub.add_parser("self-approve", help="S16: approve one of Eve's own designs if validation + critic allow it")
    p.add_argument("project_id")
    p.add_argument("version", help="v<N>")
    for verb in ("approve", "reject"):
        p = sub.add_parser(verb)
        p.add_argument("project_id")
        p.add_argument("version", help="v<N>")
        p.add_argument("--by", help="trusted player name deciding (default: the only trusted player)")
    a = ap.parse_args(argv)

    args, timeout = {}, 10
    if a.cmd == "stop":
        args = {"reason": " ".join(a.reason) or None}
    elif a.cmd == "mode":
        args = {"mode": a.mode}
    elif a.cmd == "retain":
        args = {"minutes": a.minutes}
    elif a.cmd == "do":
        args, timeout = {"skill": a.skill, "params": parse_kv(a.params), "wait": a.wait, "assume": a.assume}, a.wait + 10
    elif a.cmd in ("approve", "reject"):
        args = {"project_id": a.project_id, "version": a.version, "by": a.by}
    elif a.cmd == "self-approve":
        a.cmd, args = "self_approve", {"project_id": a.project_id, "version": a.version}
    elif a.cmd == "cancel":
        args = {"reason": " ".join(a.reason) or None}
    elif a.cmd == "ask":
        args, timeout = {"text": " ".join(a.text)}, 600
    elif a.cmd == "objective":
        args = {"text": " ".join(a.text), "status": a.status, "continue": a.cont}
    elif a.cmd == "recall":
        args = {"subject": " ".join(a.subject), "predicate": a.predicate}
    elif a.cmd == "forget":
        args = {"fact_id": a.fact_id, "subject": a.subject, "predicate": a.predicate}
    elif a.cmd == "check":
        if a.entity is None and len(a.xyz) != 3:
            ap.error("check needs x y z or --entity ID")
        args = {"effect": a.effect, "dim": a.dim, "entity_id": a.entity, "face": a.face,
                "pos": dict(zip("xyz", a.xyz)) if len(a.xyz) == 3 else None}
    elif a.cmd == "drop":
        if not a.parked and not a.task_id:
            ap.error("drop needs a task id or --parked")
        args = {"task_id": a.task_id, "parked": a.parked}
    elif a.cmd == "continue":
        args, timeout = {"task_id": a.task_id, "wait": a.wait}, a.wait + 10
    elif a.cmd == "revoke":
        args = {"project_id": a.project_id, "version": a.version, "by": a.by, "reason": a.reason}
    try:
        r = asyncio.run(call(a.cmd, args, a.port, timeout))
    except (OSError, asyncio.TimeoutError) as e:
        print(f"mind service not reachable on 127.0.0.1:{a.port}: {e}", file=sys.stderr)
        return 2
    print(json.dumps(r, indent=2) if a.json else render(a.cmd, r))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
