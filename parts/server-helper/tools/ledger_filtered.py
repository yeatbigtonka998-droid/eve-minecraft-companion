r"""Filtered Ledger rollback of Eve's block work that never overwrites another player's later edit (CP-18, rule O17).

Ledger itself cannot skip positions, so the filter is done here from Ledger's own log (<world>/ledger.sqlite, read-only):
Eve's not-yet-rolled-back block-place/block-break/block-change/entity-kill actions in the area since --minutes; a
position some OTHER player edited after Eve is "kept" (skipped); every other position gets its own positioned
`ledger rollback range:1 source:<Eve> action:<a> after:<N>m` (measured on Ledger 1.3.25: range:N reaches less than N
blocks from the centre, so range:1 is exactly that one block). Containers are never touched (rule O17). ponytail: non-player sources (tnt, fluids, commands) are
not owners of a position; an explosion is rolled back by its own source (`source:@tnt`).
    python ledger_filtered.py --root C:\eve\server-dev --center 66 56 -40 --radius 16 --minutes 10 [--dry-run]
"""
import argparse
import datetime
import sqlite3
import time
from pathlib import Path

SAFE = ("block-place", "block-break", "block-change", "entity-kill")


def plan(db, eve, center, radius, minutes, world="minecraft:overworld"):
    """-> (todo {action: {pos}}, kept [(pos, other player)])"""
    since = (datetime.datetime.now() - datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")  # Ledger stores local time
    lo = [c - radius for c in center]
    hi = [c + radius for c in center]
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = c.execute(
        """select a.id, ai.action_identifier, a.x, a.y, a.z, p.player_name, a.rolled_back from actions a
           join ActionIdentifiers ai on ai.id = a.action_id join worlds w on w.id = a.world_id
           left join players p on p.id = a.player_id
           where w.identifier = ? and a.time >= ? and a.x between ? and ? and a.y between ? and ? and a.z between ? and ?
           order by a.id""", (world, since, lo[0], hi[0], lo[1], hi[1], lo[2], hi[2])).fetchall()
    c.close()
    eve_rows = [(i, act, (x, y, z)) for i, act, x, y, z, who, rb in rows if who == eve and act in SAFE and not rb]
    kept = {}
    for i, _, pos in eve_rows:
        later = next((who for j, _, x, y, z, who, rb in rows if j > i and (x, y, z) == pos and who and who != eve and not rb), None)
        if later:
            kept[pos] = later
    todo = {}
    for _, act, pos in eve_rows:
        if pos not in kept:
            todo.setdefault(act, set()).add(pos)
    return todo, sorted(kept.items())


def rollback(command, eve, todo, minutes, world="minecraft:overworld", pause=2.0):
    """One single-block rollback per position through `command` (an RCON call); returns the replies. ponytail: fixed pause
    so Ledger's async rollbacks do not pile up; one command per position is slow for thousands of blocks."""
    out = []
    for act, positions in todo.items():
        for q in sorted(positions):
            out.append(command(f"execute in {world} positioned {q[0]} {q[1]} {q[2]} run ledger rollback range:1 source:{eve} "
                               f"action:{act} after:{minutes}m"))
            time.sleep(pause)
    return out


def db_path(root):
    props = dict(l.split("=", 1) for l in (Path(root) / "server.properties").read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))
    return Path(root) / props.get("level-name", "world") / "ledger.sqlite"


def _selfcheck():
    """Fake Ledger log: Eve places a row of 3; another player replaces the middle one later -> the middle is kept and no cube reaches it."""
    import tempfile
    d = Path(tempfile.mkdtemp()) / "l.sqlite"
    c = sqlite3.connect(d)
    c.executescript("""create table ActionIdentifiers(id integer primary key, action_identifier text);
        create table worlds(id integer primary key, identifier text); create table players(id integer primary key, player_name text);
        create table actions(id integer primary key, action_id int, time text, x int, y int, z int, world_id int, player_id int, rolled_back int);
        insert into ActionIdentifiers values (1,'block-place'),(2,'block-break'),(3,'item-remove');
        insert into worlds values (1,'minecraft:overworld'); insert into players values (1,'Eve'),(2,'Hunter');""")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.000")
    for i, (a, x, p) in enumerate([(1, 0, 1), (1, 1, 1), (1, 2, 1), (3, 5, 1), (2, 1, 2), (1, 1, 2), (1, 3, 2)], 1):
        c.execute("insert into actions values (?,?,?,?,?,?,1,?,0)", (i, a, now, x, 0, 0, p))
    c.commit()
    c.close()
    todo, kept = plan(d, "Eve", (0, 0, 0), 8, 5)
    assert kept == [((1, 0, 0), "Hunter")], kept
    assert todo == {"block-place": {(0, 0, 0), (2, 0, 0)}}, todo                  # item-remove (container) never planned
    print("selfcheck ok", todo)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"C:\eve\server-dev")
    ap.add_argument("--ops", default=r"C:\eve\ops")
    ap.add_argument("--eve", help="Eve's account (default: the envelope's eve_account)")
    ap.add_argument("--center", nargs=3, type=int)
    ap.add_argument("--radius", type=int, default=16)
    ap.add_argument("--minutes", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()
    if a.selfcheck:
        _selfcheck()
        raise SystemExit
    import json
    import sys
    eve = a.eve or json.loads((Path(a.root) / "config" / "envelope.json").read_text(encoding="utf-8"))["owner"]["eve_account"]
    todo, kept = plan(db_path(a.root), eve, a.center, a.radius, a.minutes)
    print(json.dumps({"rolled_back": {k: sorted(v) for k, v in todo.items()}, "kept_for_other_players": kept}))
    if not a.dry_run:
        sys.path.insert(0, a.ops)
        import rcon
        for r in rollback(lambda cmd: rcon.command(Path(a.root), cmd), eve, todo, a.minutes):
            print(r)
