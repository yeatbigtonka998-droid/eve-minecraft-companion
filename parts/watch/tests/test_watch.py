"""Board against a DB built from the real contract schema; the HTTP server end to end; read-only guarantee."""
import json
import sqlite3
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from eve_watch.__main__ import Status, make_handler
from eve_watch.board import board

SCHEMA = Path(__file__).resolve().parents[3] / "contracts" / "memory-schema.sql"
STATE = {"pos": {"x": -16.5, "y": 99.0, "z": -20.5}, "dim": "minecraft:overworld", "health": 18, "food": 20,
         "gamemode": "survival", "equipment": {"mainhand": "minecraft:stone_sword"},
         "inventory": [{"slot": 0, "item": "minecraft:oak_log", "count": 5}, {"slot": 1, "item": "minecraft:oak_log", "count": 3},
                       {"slot": 2, "item": "minecraft:stone_sword", "count": 1}],
         "threats": [], "control": {"stopped": False, "active_action_id": "a2"}, "freshness": {"tick": 9, "age_ms": 5}}


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "eve.db"
    c = sqlite3.connect(p, isolation_level=None)
    c.executescript(SCHEMA.read_text("utf-8"))
    c.executemany("INSERT INTO meta VALUES (?,?)", [("control.stopped", "1"), ("control.stopped_by", "cli"), ("control.mode", "manual")])
    c.execute("INSERT INTO observations (world_id, ts, kind, payload) VALUES ('dev:x', 1000, 'state', ?)", (json.dumps(STATE),))
    c.execute("INSERT INTO tasks (task_id, world_id, kind, goal, source, status, created_ts, updated_ts) "
              "VALUES ('t1','dev:x','skill','walk_to','owner_command','running',100,200)")
    c.execute("INSERT INTO tasks (task_id, world_id, kind, goal, source, status, reason, created_ts, updated_ts) "
              "VALUES ('t0','dev:x','skill','look_at','owner_command','paused','stopped by cli',50,300)")
    c.execute("INSERT INTO actions (action_id, task_id, world_id, skill, skill_version, params, sent_ts, result_ts, status, code, evidence) "
              "VALUES ('a1','t1','dev:x','look_at',1,'{}',10,20,'ok',NULL,'{\"yaw\":90}')")
    c.execute("INSERT INTO actions (action_id, task_id, world_id, skill, skill_version, params, sent_ts, status) "
              "VALUES ('a2','t1','dev:x','walk_to',1,'{\"pos\":{\"x\":1}}',30,'running')")
    c.execute("INSERT INTO decisions (decision_id, world_id, ts, kind, model, input, output, latency_ms, validated) "
              "VALUES ('d1','dev:x',50,'plan','qwen3:8b','get wood','{\"steps\":[\"walk_to\"]}',1550,1)")
    for kind, payload in [("damage", {"amount": 2, "source": "<script>x</script>"}), ("eve.status", {"tps": 20.0}),
                          ("metrics", {"fps": 30})]:
        c.execute("INSERT INTO events (world_id, ts, source, kind, payload) VALUES ('dev:x', 60, 'body', ?, ?)", (kind, json.dumps(payload)))
    c.execute("INSERT INTO journal (world_id, ts, kind, text) VALUES ('dev:x', 70, 'day_summary', 'Walked to the pad.')")
    c.close()
    return p


def test_board_reads_real_records(db):
    b = board(db)
    assert b["meta"]["control.stopped"] == "1" and b["eve"]["pos"]["x"] == -16.5
    assert b["eve"]["inventory"] == ["oak_log 8", "stone_sword 1"]
    assert [t["goal"] for t in b["recent_tasks"]] == ["walk_to", "look_at"]
    assert [p["task_id"] for p in b["parked_tasks"]] == ["t0"]            # newer, but parked, never active
    assert b["actions"][0]["note"] is None                                  # v1 schema: no actions.note column
    assert b["active_task"]["goal"] == "walk_to" and b["active_action"]["skill"] == "walk_to"
    assert [a["action_id"] for a in b["actions"]] == ["a2", "a1"]
    assert b["decisions"][0]["validated"] == 1 and b["planner_latency_ms"] == {"last": 1550, "avg": 1550}
    assert [e["kind"] for e in b["events"]] == ["damage"]                     # heartbeat kinds kept out of the list
    assert b["heartbeat"]["eve.status"]["tps"] == 20.0 and b["heartbeat"]["metrics"]["fps"] == 30
    assert b["journal"]["text"] == "Walked to the pad."


def test_board_never_writes(db):
    before = db.read_bytes()
    board(db)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO meta VALUES ('x','y')")
    con.close()
    assert db.read_bytes() == before


def test_missing_db_is_reported_not_raised(tmp_path):
    assert "db_error" in board(tmp_path / "nope.db")


def test_server_end_to_end(db, monkeypatch):
    monkeypatch.delenv("EVE_LINK_TOKEN", raising=False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db, Status(1), None))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert b"<title>Eve watch</title>" in urllib.request.urlopen(base + "/").read()
        b = json.loads(urllib.request.urlopen(base + "/api/board").read())
        assert b["eve"]["health"] == 18 and b["status"] is None and "EVE_LINK_TOKEN" in b["status_error"]
        assert urllib.request.urlopen(base + "/frame.jpg").status == 204
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/api/do")
    finally:
        srv.shutdown()


def test_locked_console_hides_frames(monkeypatch):
    from PIL import Image
    from eve_watch import capture
    frames = iter([Image.new("RGB", (4, 4), c) for c in ("red", "red", "blue", "blue")])
    locked = iter([True, True, False])
    monkeypatch.setattr(capture, "find_window", lambda: (1, "Minecraft* 26.3"))
    monkeypatch.setattr(capture, "process_started_ms", lambda h: 123)
    monkeypatch.setattr(capture, "grab", lambda h: (next(frames), "ok"))
    monkeypatch.setattr(capture, "console_locked", lambda: next(locked))
    cam = capture.Camera(min_s=0)
    assert cam.frame() is None and cam.info()["locked_grab"] == "changing" and "console locked" in cam.info()["note"]
    assert cam.frame() is None and cam.info()["locked_grab"] == "unchanged"     # same picture twice = stale
    assert cam.frame()[:2] == b"\xff\xd8" and cam.info()["note"] == "ok"        # unlocked: JPEG again
    assert cam.info()["client_started_ms"] == 123
