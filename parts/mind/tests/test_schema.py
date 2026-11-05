"""memory-schema v1 -> v1.1 migration at startup (idempotent): the 45L's existing eve.db gains actions.note."""
import json
import sqlite3

from conftest import TOKEN, envelope
from eve_mind.config import Config
from eve_mind.mind import open_db


def test_v1_database_is_migrated_once_and_keeps_its_rows(tmp_path):
    cfg = Config(token=TOKEN, db_path=tmp_path / "eve.db", envelope=envelope(), envelope_path=tmp_path / "e.json")
    v1 = cfg.schema_path.read_text("utf-8").replace("evidence TEXT, note TEXT,", "evidence TEXT,")
    assert v1 != cfg.schema_path.read_text("utf-8")
    con = sqlite3.connect(cfg.db_path)
    con.executescript(v1)
    assert "note" not in {r[1] for r in con.execute("PRAGMA table_info(actions)")}
    con.execute("INSERT INTO actions (action_id, world_id, skill, skill_version, params, status, evidence) VALUES "
                "('a1', 'w', 'mine_block', 1, '{}', 'failed', ?)", (json.dumps({"block_before": "x", "note": "why"}),))
    con.execute("INSERT INTO actions (action_id, world_id, skill, skill_version, params, status, evidence) VALUES "
                "('a2', 'w', 'jump', 1, '{}', 'ok', ?)", (json.dumps({"left_ground": True}),))
    con.commit()
    con.close()
    for _ in range(2):                       # second open is a no-op
        db = open_db(cfg)
        rows = {r["action_id"]: r for r in db.execute("SELECT action_id, note, evidence FROM actions")}
        assert rows["a1"]["note"] == "why" and json.loads(rows["a1"]["evidence"]) == {"block_before": "x"}
        assert rows["a2"]["note"] is None and json.loads(rows["a2"]["evidence"]) == {"left_ground": True}
        assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "1.1"
        db.close()


def test_new_database_has_the_column(tmp_path):
    cfg = Config(token=TOKEN, db_path=tmp_path / "new.db", envelope=envelope(), envelope_path=tmp_path / "e.json")
    db = open_db(cfg)
    assert "note" in {r[1] for r in db.execute("PRAGMA table_info(actions)")}
    db.close()
