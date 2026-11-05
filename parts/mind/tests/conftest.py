"""Shared harness: a real hub on an ephemeral loopback port, with fake body and fake sense connected."""
import asyncio
import contextlib
import json
import os
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from fake_body import ROOT, FakeBody  # noqa: E402
from fake_sense import FakeSense  # noqa: E402

from eve_mind.__main__ import start  # noqa: E402
from eve_mind.config import Config  # noqa: E402
from eve_mind import memory  # noqa: E402

import pytest  # noqa: E402

REAL_PLACE_CHECK = memory.place_check


@pytest.fixture(autouse=True)
def place_check_there(monkeypatch):
    """O86x: unit tests stub the model - the focused place call answers "there" and the scope call "every_world"
    unless a test scripts them."""
    async def there(text, thing, place, url, model, timeout=60):
        return "there"

    async def every(text, thing, what, url, model, timeout=60):
        return "every_world"
    monkeypatch.setattr(memory, "place_check", there)
    monkeypatch.setattr(memory, "scope_check", every)

TOKEN = "test-token-0123456789abcdef"
os.environ["EVE_LINK_TOKEN"] = TOKEN       # for in-process CLI calls; never a real token
EXAMPLE_ENVELOPE = json.loads((ROOT / "config" / "envelope.example.json").read_text("utf-8"))


def envelope(**over):
    env = json.loads(json.dumps(EXAMPLE_ENVELOPE))
    env["owner"]["trusted_players"] = ["Hunter"]
    env.update(over)
    return env


async def until(pred, timeout=3.0, what="condition"):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


@contextlib.asynccontextmanager
async def mind_env(tmp_path, env=None, body=True, sense=True, port=0, db_path=None, **cfg):
    env = env or envelope()
    cfg.setdefault("ping_s", 0.5)
    cfg.setdefault("dead_s", 1.5)
    cfg.setdefault("chat_gap_s", 0.0)   # O114's 2.1 s spacing is the live body's; tests send at once
    c = Config(token=TOKEN, port=port, db_path=db_path or tmp_path / "eve.db", envelope=env,
               envelope_path=tmp_path / "envelope.json", **cfg)
    mind, hub = await start(c)
    mind.extractor = None          # unit tests never call a model for facts; live runs opt in
    mind.talker = None             # nor for dialogue (CP-46 live runs opt in)
    mind.intent_fn = None          # nor for intent routing (O30): the rule router
    url = f"ws://127.0.0.1:{hub.port}"
    e = SimpleNamespace(mind=mind, hub=hub, url=url, cfg=c, db=mind.db, body=None, sense=None)
    if sense:
        e.sense = FakeSense(url + "/sense", TOKEN, envelope=env).start()
        await asyncio.wait_for(e.sense.ready.wait(), 3)
    if body:
        e.body = FakeBody(url + "/body", TOKEN).start()
        await asyncio.wait_for(e.body.ready.wait(), 3)
    if body and sense:
        await until(lambda: mind.fresh_state() and not mind.reconcile_needed, what="mind ready")
    try:
        yield e
    finally:
        for p in (e.body, e.sense):
            if p:
                await p.close()
        await hub.close()
        mind.db.close()


HUT5 = ROOT / "parts" / "design" / "examples" / "out" / "hut5" / "design.json"


def design_folder(designs_dir, project="hut", version=1, validation=True, mutate=None, category="owner_small",
                  authored_by="Hunter", critique=None):
    """A design version folder built from the design chat's test vector (hut5). critique: None (no file) or a dict
    merged over {status: accepted, threshold: 7.0, design_hash: <this hash>, scores: all 8}."""
    d = designs_dir / project / f"v{version}"
    d.mkdir(parents=True, exist_ok=True)
    design = json.loads(HUT5.read_text("utf-8"))
    if mutate:
        mutate(design)
    (d / "design.json").write_text(json.dumps(design), "utf-8")
    (d / "brief.json").write_text(json.dumps({"budget": {"blocks": 300}, "approval_category": category,
                                              "authored_by": authored_by}))
    if validation is not None:
        (d / "validation.json").write_text(json.dumps({"passed": validation, "skipped": [],
                                                       "design_hash": design["hash"]}))
    if critique is not None:
        cats = ["function", "proportion", "palette", "detail", "site", "style"]
        (d / "critique.json").write_text(json.dumps({"status": "accepted", "threshold": 7.0, "design_hash": design["hash"],
                                                     "categories": cats, "scores": {c: 8 for c in cats}, **critique}))
    return d, design["hash"]


def count(db, sql, *args):
    row = db.execute(sql, args).fetchone()
    return row[0] if row else None


def run(coro):
    return asyncio.run(coro)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def service(tmp_path, port, env=None, db_name="eve.db"):
    """The real service as a separate process (python -m eve_mind) so it can be killed like a crash."""
    ep = tmp_path / "envelope.json"
    ep.write_text(json.dumps(env or envelope()), "utf-8")
    penv = {**os.environ, "EVE_LINK_TOKEN": TOKEN, "EVE_DB": str(tmp_path / db_name), "EVE_PORT": str(port),
            "EVE_ENVELOPE": str(ep)}
    with open(tmp_path / "mind.log", "a") as log:
        proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "eve_mind", env=penv,
                                                    stdout=asyncio.subprocess.PIPE, stderr=log)
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), 15)
            assert b"READY" in line, line
            yield SimpleNamespace(proc=proc, port=port, url=f"ws://127.0.0.1:{port}")
        finally:
            if proc.returncode is None:
                proc.kill()          # TerminateProcess on Windows: an abrupt crash, no cleanup runs
                await proc.wait()


async def cli(port, cmd, **args):
    from eve_mind.cli import call
    return await call(cmd, args, port, 10)
