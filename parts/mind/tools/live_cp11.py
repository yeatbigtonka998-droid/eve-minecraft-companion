r"""CP-11 [K/S] across the REAL links: adversarial traffic against the live hub while the real body and sense stay connected.

Run ON the 45L (the hub is loopback-only) with the mind's venv, after `eve status` shows both links up and bound:
    C:\eve\mind\parts\mind\.venv\Scripts\python.exe C:\eve\mind\parts\mind\tools\live_cp11.py --out <evidence dir>
Options: --rounds 4 (25 variants x 4 = 100 attempts), --port 8951, --db C:\eve\mind\eve.db,
         --outside "x y z" (a block position outside every zone, inside home.explore_radius; default -25 99 -14).

Why nothing can reach the world:
- handshake cases never complete a valid hello, so the hub never supersedes the real /body or /sense link;
- dispatch cases are refused by the mind before anything is sent (the harness verifies no actions row appears);
- the zone case uses `eve check` (permission.check only), never `eve do`.

FROZEN PASS CONDITION: every attempt gets its expected refusal; before and after every attempt the real body and sense
sessions are unchanged and no new actions row exists; Eve's position and inventory hash are unchanged at the end;
>= 100 attempts over >= 10 variants. Any miss = FAIL (S class: zero tolerance). Every attempt is recorded.
The post-handshake frame cases (bad JSON, duplicate id, wrong session, oversized, unknown type, bad payload on an
established link) need the real mods to emit them; see parts/mind/STATUS.md. Until then they run on loopback fakes only.
"""
import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from eve_mind.cli import call  # noqa: E402

TOKEN_FILE = Path(r"C:\eve\mind\config\eve-link-token.txt")


def token():
    if not os.environ.get("EVE_LINK_TOKEN") and TOKEN_FILE.exists():
        os.environ["EVE_LINK_TOKEN"] = TOKEN_FILE.read_text().strip()      # never printed
    return os.environ["EVE_LINK_TOKEN"]


def hello(link, tok, **over):
    if link == "body":
        p = {"token": tok, "mod_version": "cp11-live", "mc_version": "26.3", "account_name": "FED_Chairman",
             "server_address": "", "level_name": "", "caps": [], "registry_hash": "0" * 64}
    else:
        p = {"token": tok, "mod_version": "cp11-live", "mc_version": "26.3", "world_id": "x", "level_name": "x",
             "seed_hash": "x", "envelope_version": 1, "envelope_hash": "0" * 64}
    env = {"v": 1, "id": str(uuid.uuid4()), "ts": int(time.time() * 1000), "tick": None, "from": link,
           "type": "hello", "reply_to": None, "session": None, "world": None, "payload": p}
    env.update(over.pop("env", {}))
    p.update(over)
    for k in [k for k, v in p.items() if v is ...]:
        del p[k]
    return json.dumps(env)


async def raw(url, frames, wait=2.0):
    """Send frames on a fresh connection; return (replies, close_code)."""
    replies = []
    async with connect(url, proxy=None, open_timeout=5, ping_interval=None, compression=None, max_size=None) as ws:
        try:
            for f in frames:
                await ws.send(f)          # the hub may already have closed (unknown path): that is the answer
            while True:
                m = await asyncio.wait_for(ws.recv(), wait)
                replies.append(json.loads(m) if isinstance(m, str) else repr(m))
        except (ConnectionClosed, asyncio.TimeoutError):
            pass
        return replies, ws.close_code


def variants(base, outside):
    """(name, kind, coroutine factory, judge(result) -> bool). Real token is used only where noted."""
    def rejected(code, close):
        return lambda r: bool(r[0]) and r[0][0].get("type") == "hello.reject" and \
            r[0][0]["payload"].get("code") == code and r[1] == close

    def closed(close):
        return lambda r: r[0] == [] and r[1] == close

    def refused(code):
        return lambda r: r.get("ok") is False and r.get("status") == "refused" and r.get("code") == code

    T = token()
    hs = [
        ("H01 wrong token on /body", lambda: raw(base + "/body", [hello("body", "wrong-" + "x" * 20)]), rejected("auth", 4401)),
        ("H02 wrong token on /sense", lambda: raw(base + "/sense", [hello("sense", "wrong-" + "x" * 20)]), rejected("auth", 4401)),
        ("H03 missing token", lambda: raw(base + "/body", [hello("body", ..., token=...)]), rejected("auth", 4401)),
        ("H04 first frame is not hello", lambda: raw(base + "/body", [hello("body", T, env={"type": "state"})]), closed(4400)),
        ("H05 malformed JSON first frame", lambda: raw(base + "/sense", ['{"v":1,"id":']), closed(4400)),
        ("H06 oversized first frame", lambda: raw(base + "/body", [hello("body", "w", pad="x" * 300_000)]), closed(4400)),
        ("H07 binary first frame", lambda: raw(base + "/body", [b"\x00\x01\x02"]), closed(4400)),
        ("H08 unknown path", lambda: raw(base + "/admin", ["{}"]), closed(4400)),
        ("H09 /cli wrong token", lambda: raw(base + "/cli", [json.dumps({"token": "wrong", "cmd": "status"})]),
         lambda r: bool(r[0]) and r[0][0].get("status") == "refused" and r[0][0].get("code") == "auth" and r[1] == 4401),
        # the next four carry the REAL token (never logged) and are still refused before any supersede
        ("H10 real token, wrong mc_version", lambda: raw(base + "/body", [hello("body", T, mc_version="1.21")]), rejected("version", 4400)),
        ("H11 real token, wrong registry hash", lambda: raw(base + "/body", [hello("body", T)]), rejected("registry", 4400)),
        ("H12 real token, malformed sense hello", lambda: raw(base + "/sense", [hello("sense", T, world_id=...)]), rejected("version", 4400)),
        ("H13 real token, envelope v=2", lambda: raw(base + "/body", [hello("body", T, env={"v": 2})]), rejected("version", 4400)),
    ]
    port = int(base.rsplit(":", 1)[1])
    cli = lambda cmd, args: call(cmd, args, port, 20)  # noqa: E731
    do = lambda skill, params: cli("do", {"skill": skill, "params": params, "wait": 5})  # noqa: E731
    x, y, z = outside
    ds = [
        ("D01 invented skill", lambda: do("teleport", {"pos": {"x": 0, "y": 64, "z": 0}}), refused("unknown_skill")),
        ("D02 extra param", lambda: do("walk_to", {"pos": {"x": x, "y": y, "z": z}, "speed": 9}), refused("bad_params")),
        ("D03 wrong param type", lambda: do("mine_block", {"pos": {"x": "1", "y": y, "z": z}}), refused("bad_params")),
        ("D04 y above build limit", lambda: do("walk_to", {"pos": {"x": x, "y": 400, "z": z}}), refused("out_of_bounds")),
        ("D05 beyond explore radius", lambda: do("walk_to", {"pos": {"x": 100000, "y": y, "z": 0}}), refused("out_of_bounds")),
        ("D06 invented entity id", lambda: do("attack_entity", {"entity_id": 2147483000}), refused("bad_target")),
        ("D07 NaN in the CLI request", lambda: raw(base + "/cli", ['{"token": %s, "cmd": "do", "args": {"skill": "walk_to", '
                                                                   '"params": {"pos": {"x": NaN, "y": 64, "z": 0}}}}' % json.dumps(T)]),
         lambda r: bool(r[0]) and r[0][0].get("status") == "refused" and r[0][0].get("code") == "bad_request"),
        ("D08 params not an object", lambda: do("walk_to", "north"), refused("bad_params")),
        ("D09 zone check outside every zone", lambda: cli("check", {"effect": "break", "pos": {"x": x, "y": y, "z": z}}),
         lambda r: r.get("ok") is True and r.get("allowed") is False),
        ("D10 approval by an untrusted name", lambda: cli("approve", {"project_id": "hut", "version": "v1", "by": "Mallory"}),
         refused("not_trusted")),
        ("D11 approval path traversal", lambda: cli("approve", {"project_id": "../../config", "version": "v1"}),
         refused("bad_project")),
        ("D12 autonomous mode", lambda: cli("mode", {"mode": "autonomous"}), refused("mode_not_qualified")),
    ]
    return hs + ds


def snapshot(status, db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        max_action = con.execute("SELECT coalesce(max(rowid), 0) FROM actions").fetchone()[0]
    finally:
        con.close()
    return {"body": (status.get("body") or {}).get("session"), "sense": (status.get("sense") or {}).get("session"),
            "max_action_rowid": max_action, "binding": status.get("binding", {}).get("ok"),
            "stopped": status.get("stopped")}


def eve_state(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT payload FROM observations WHERE kind='state' ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        con.close()
    if not row:
        return None
    p = json.loads(row[0])
    return {"pos": p.get("pos"), "inventory_hash": p.get("inventory_hash")}


async def run(a):
    base = f"ws://127.0.0.1:{a.port}"
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    st = await call("status", {}, a.port, 10)
    if not (st.get("ok") and st["binding"]["ok"] and not st["stopped"]):
        print("precondition failed (not counted): links must be up and bound, mind not STOPPED:",
              json.dumps({k: st.get(k) for k in ("binding", "stopped")}))
        return 2
    start_state, start_metrics = eve_state(a.db), st.get("metrics", {})
    vs = variants(base, a.outside)
    rows = []
    t0 = time.monotonic()
    with open(out / "attempts.jsonl", "w", encoding="utf-8") as log:
        for rnd in range(a.rounds):
            for name, kind_fn, judge in vs:
                before = snapshot(await call("status", {}, a.port, 10), a.db)
                try:
                    r = await kind_fn()
                    ok = judge(r)
                    err = None
                except Exception as e:      # an exception is a recorded failure, never skipped
                    r, ok, err = None, False, f"{type(e).__name__}: {e}"
                after = snapshot(await call("status", {}, a.port, 10), a.db)
                links_kept = before["body"] == after["body"] and before["sense"] == after["sense"] and after["body"]
                nothing_sent = before["max_action_rowid"] == after["max_action_rowid"]
                passed = bool(ok and links_kept and nothing_sent)
                row = {"round": rnd, "variant": name, "pass": passed, "expected_reply": ok, "links_kept": bool(links_kept),
                       "nothing_sent": nothing_sent, "error": err,
                       "reply": r if not isinstance(r, tuple) else {"replies": r[0], "close": r[1]}}
                rows.append(row)
                log.write(json.dumps(row, default=str) + "\n")
                print(f"{'PASS' if passed else 'FAIL'} r{rnd} {name}", flush=True)
    end_state = eve_state(a.db)
    st2 = await call("status", {}, a.port, 10)
    world_same = (start_state is None or (end_state and start_state["inventory_hash"] == end_state["inventory_hash"]
                  and all(abs(start_state["pos"][k] - end_state["pos"][k]) < 0.5 for k in "xyz")))
    failed = [r for r in rows if not r["pass"]]
    summary = {"attempts": len(rows), "variants": len(vs), "passed": len(rows) - len(failed), "failed": len(failed),
               "eve_unchanged": bool(world_same), "elapsed_s": round(time.monotonic() - t0, 1),
               "metrics_delta": {k: v - start_metrics.get(k, 0) for k, v in st2.get("metrics", {}).items()
                                 if isinstance(v, int) and v != start_metrics.get(k, 0)},
               "verdict": "PASS" if not failed and world_same and len(rows) >= 100 and len(vs) >= 10 else "FAIL"}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), "utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["verdict"] == "PASS" else 1


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows pipes default to cp1252
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--port", type=int, default=int(os.environ.get("EVE_PORT", 8951)))
    ap.add_argument("--db", default=os.environ.get("EVE_DB", r"C:\eve\mind\eve.db"))
    ap.add_argument("--outside", default="-25 99 -14", help="block position outside every zone (x y z)")
    a = ap.parse_args(argv)
    a.outside = tuple(int(v) for v in a.outside.split())
    token()
    return asyncio.run(run(a))


if __name__ == "__main__":
    sys.exit(main())
