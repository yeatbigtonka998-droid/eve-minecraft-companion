-- Eve memory: SQLite schema v1.2 (contract). v1.1: actions.note added; v1.2: build_runs/build_cells added (migration: ALTER TABLE actions ADD COLUMN note TEXT). Owner: mind. Readers: design (designs/approvals/ratings), foundation (backup/restore).
-- Rules: every world-specific row carries world_id; facts carry provenance; nothing is deleted, rows are superseded.
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);           -- schema_version, created_at, last_backup_id
CREATE TABLE IF NOT EXISTS worlds (world_id TEXT PRIMARY KEY, level_name TEXT, seed_hash TEXT, first_seen_ts INTEGER, last_seen_ts INTEGER);
CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, link TEXT CHECK(link IN ('body','sense')), world_id TEXT, peer_version TEXT, registry_hash TEXT, started_ts INTEGER, ended_ts INTEGER, end_reason TEXT);

-- Observations: ring of state snapshots (keep last N minutes; failures pin a window)
CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY, world_id TEXT NOT NULL, session_id TEXT, tick INTEGER, ts INTEGER, kind TEXT, payload TEXT NOT NULL, pinned INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS obs_world_ts ON observations(world_id, ts);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, world_id TEXT NOT NULL, session_id TEXT, tick INTEGER, ts INTEGER, source TEXT, kind TEXT, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ev_world_ts ON events(world_id, ts);

-- Durable tasks and actions (CP-19..CP-23)
CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, world_id TEXT NOT NULL, parent_task_id TEXT, kind TEXT NOT NULL, goal TEXT NOT NULL, params TEXT, source TEXT NOT NULL, -- owner_command | needs | project | recovery
  status TEXT NOT NULL CHECK(status IN ('queued','running','paused','blocked','done','failed','cancelled')), reason TEXT, budget TEXT, progress TEXT, created_ts INTEGER, updated_ts INTEGER, version INTEGER DEFAULT 1, assumptions TEXT);
CREATE TABLE IF NOT EXISTS actions (action_id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(task_id), world_id TEXT NOT NULL, skill TEXT NOT NULL, skill_version INTEGER NOT NULL, params TEXT NOT NULL, sent_ts INTEGER, accepted_ts INTEGER, result_ts INTEGER,
  status TEXT NOT NULL CHECK(status IN ('pending','sent','accepted','rejected','running','ok','failed','cancelled','uncertain','timeout')), code TEXT, evidence TEXT, note TEXT, -- note: the body's action.result.note (why it ended that way); schema v1.1 2026-10-19
  reconciled INTEGER DEFAULT 0, reconcile_note TEXT);
CREATE INDEX IF NOT EXISTS act_task ON actions(task_id);

-- World knowledge (CP-13, CP-14)
CREATE TABLE IF NOT EXISTS containers (container_id TEXT PRIMARY KEY, world_id TEXT NOT NULL, dim TEXT, x INTEGER, y INTEGER, z INTEGER, kind TEXT, tag TEXT, loaded INTEGER, observed_tick INTEGER, observed_ts INTEGER, gone INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS container_items (container_id TEXT REFERENCES containers(container_id), item TEXT NOT NULL, count INTEGER NOT NULL, observed_ts INTEGER, PRIMARY KEY(container_id, item));
CREATE TABLE IF NOT EXISTS reservations (reservation_id TEXT PRIMARY KEY, task_id TEXT, container_id TEXT, item TEXT, count INTEGER, created_ts INTEGER, expires_ts INTEGER, released_ts INTEGER, release_reason TEXT);
CREATE TABLE IF NOT EXISTS resources (id INTEGER PRIMARY KEY, world_id TEXT NOT NULL, dim TEXT, x INTEGER, y INTEGER, z INTEGER, block TEXT, state TEXT, kind TEXT, policy TEXT, observed_ts INTEGER, stale INTEGER DEFAULT 0, UNIQUE(world_id, dim, x, y, z));
CREATE TABLE IF NOT EXISTS zones (zone_id TEXT, world_id TEXT NOT NULL, envelope_version INTEGER, spec TEXT NOT NULL, PRIMARY KEY(zone_id, world_id));
CREATE TABLE IF NOT EXISTS zone_snapshots (snapshot_id TEXT PRIMARY KEY, zone_id TEXT, world_id TEXT NOT NULL, version INTEGER, requested_by TEXT, block_count INTEGER, path TEXT, created_ts INTEGER, approved INTEGER DEFAULT 0, superseded_by TEXT);
CREATE TABLE IF NOT EXISTS block_events (id INTEGER PRIMARY KEY, world_id TEXT NOT NULL, tick INTEGER, ts INTEGER, dim TEXT, x INTEGER, y INTEGER, z INTEGER, kind TEXT, before TEXT, after TEXT, actor TEXT, zone_id TEXT);
CREATE INDEX IF NOT EXISTS be_world_ts ON block_events(world_id, ts);

-- Memory facts with provenance (CP-44)
CREATE TABLE IF NOT EXISTS facts (fact_id TEXT PRIMARY KEY, world_id TEXT, -- NULL = world-independent (owner preference)
  subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL, confidence REAL NOT NULL, source TEXT NOT NULL, -- owner_chat | observation | planner | journal | correction
  source_ref TEXT, created_ts INTEGER, superseded_by TEXT, confirmed INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS facts_subj ON facts(world_id, subject, predicate);
CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY, world_id TEXT NOT NULL, ts INTEGER, kind TEXT, -- day_summary | briefing | design_note
  text TEXT NOT NULL, generated_by TEXT, grounded_in TEXT); -- grounded_in: JSON list of task/action/event ids the text cites; ungrounded text is never 'confirmed history'

-- Planner decisions (CP-41..CP-43; later training data CP-79)
CREATE TABLE IF NOT EXISTS decisions (decision_id TEXT PRIMARY KEY, world_id TEXT NOT NULL, ts INTEGER, kind TEXT, -- need_pick | command_map | plan | threat_score | stuck
  state_ref INTEGER, prompt_version TEXT, model TEXT, input TEXT, output TEXT, latency_ms INTEGER, validated INTEGER, correction TEXT, correction_source TEXT, correction_ts INTEGER, split TEXT); -- split: train | holdout | NULL

-- Design, approvals, ratings (contracts/design-project.md)
CREATE TABLE IF NOT EXISTS designs (project_id TEXT, version INTEGER, world_id TEXT, dim TEXT, origin_x INTEGER, origin_y INTEGER, origin_z INTEGER, hash TEXT NOT NULL, path TEXT NOT NULL, brief TEXT, style TEXT, block_count INTEGER, status TEXT, created_ts INTEGER, PRIMARY KEY(project_id, version));
CREATE TABLE IF NOT EXISTS approvals (approval_id TEXT PRIMARY KEY, project_id TEXT, version INTEGER, design_hash TEXT NOT NULL, site_hash TEXT, budget TEXT, decided_by TEXT NOT NULL, decision TEXT CHECK(decision IN ('approved','rejected','revise','cancelled')), category TEXT, -- owner_small | village_plot | wishlist
  channel TEXT, ts INTEGER, invalidated_ts INTEGER, invalidated_reason TEXT);
CREATE TABLE IF NOT EXISTS ratings (rating_id TEXT PRIMARY KEY, project_id TEXT, version INTEGER, rater TEXT NOT NULL, -- hunter | critic:<model> | eve
  score REAL, comment TEXT, ts INTEGER);

-- Spend and budgets (CP-06, CP-82)
CREATE TABLE IF NOT EXISTS spend (id INTEGER PRIMARY KEY, ts INTEGER, provider TEXT, model TEXT, job TEXT, tokens_in INTEGER, tokens_out INTEGER, usd REAL, cached INTEGER);
CREATE TABLE IF NOT EXISTS backups (backup_id TEXT PRIMARY KEY, ts INTEGER, world_id TEXT, world_path TEXT, db_path TEXT, manifest TEXT, verified INTEGER DEFAULT 0, restore_tested_ts INTEGER);

-- v1.2 (2026-10-20): construction progress (design-project.md Amendments 2)
CREATE TABLE IF NOT EXISTS build_runs (run_id TEXT PRIMARY KEY, project_id TEXT, version INTEGER, design_hash TEXT NOT NULL, lane TEXT, started_ts INTEGER, ended_ts INTEGER, status TEXT CHECK(status IN ('running','paused','done','failed','cancelled')));
CREATE TABLE IF NOT EXISTS build_cells (run_id TEXT REFERENCES build_runs(run_id), x INTEGER, y INTEGER, z INTEGER, state_expected TEXT, state_observed TEXT, placed_ts INTEGER, verified INTEGER DEFAULT 0, PRIMARY KEY(run_id, x, y, z));
