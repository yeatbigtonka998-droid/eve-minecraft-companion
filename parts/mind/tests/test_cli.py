"""The `eve` CLI as an owner would run it, against the real service process and the fakes.

Covers the first-session done criteria: `eve do walk_to x y z` reaches the body and the result is persisted with
evidence; `eve stop` shows STOPPED until `eve resume` (also across a service restart); approvals need a trusted name.
"""
import asyncio
import json
import os
import sqlite3
import sys

from conftest import TOKEN, envelope, free_port, run, service, until
from fake_body import FakeBody
from fake_sense import FakeSense


async def eve(port, *argv):
    p = await asyncio.create_subprocess_exec(sys.executable, "-m", "eve_mind.cli", "--port", str(port), *argv,
                                             env={**os.environ, "EVE_LINK_TOKEN": TOKEN},
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate()
    return p.returncode, out.decode() + err.decode()


async def fakes(url, env):
    sense = FakeSense(url + "/sense", TOKEN, envelope=env).start()
    body = FakeBody(url + "/body", TOKEN, state_hz=10).start()
    await asyncio.wait_for(asyncio.gather(sense.ready.wait(), body.ready.wait()), 5)
    return body, sense


def test_cli_do_stop_resume(tmp_path):
    async def go():
        env, port = envelope(), free_port()
        async with service(tmp_path, port, env) as svc:
            body, sense = await fakes(svc.url, env)
            await asyncio.sleep(0.5)
            rc, out = await eve(port, "do", "walk_to", "x=10", "y=64", "z=-3", "--wait", "5")
            assert rc == 0 and out.startswith("OK "), out
            assert body.pos == {"x": 10, "y": 64, "z": -3}
            con = sqlite3.connect(tmp_path / "eve.db")
            st, ev = con.execute("SELECT status, evidence FROM actions").fetchone()
            assert st == "ok" and json.loads(ev)["final_pos"] == {"x": 10, "y": 64, "z": -3}
            assert con.execute("SELECT status FROM tasks").fetchone()[0] == "done"

            rc, out = await eve(port, "check", "break", "3", "63", "3")
            assert rc == 0 and out.startswith("ALLOWED break {'x': 3, 'y': 63, 'z': 3}"), out
            rc, out = await eve(port, "stop", "testing")
            assert rc == 0 and out.strip() == "STOPPED"
            await until(lambda: body.stopped)
            rc, out = await eve(port, "status")
            assert out.startswith("STOPPED (by cli)"), out
            rc, out = await eve(port, "do", "walk_to", "x=1", "y=64", "z=1")
            assert rc == 1 and "REFUSED stopped" in out
        # crash + restart: STOPPED must survive, and be re-asserted on a body that forgot it
        body.stopped = False
        async with service(tmp_path, port, env) as svc:
            await until(lambda: body.ready.is_set() and body.stopped, timeout=10, what="stop re-asserted")
            rc, out = await eve(port, "status")
            assert out.startswith("STOPPED"), out
            rc, out = await eve(port, "resume")
            assert rc == 0 and out.startswith("RESUMED"), out
            await until(lambda: not body.stopped)
            rc, out = await eve(port, "status")
            assert out.startswith("RUNNING"), out
            rc, out = await eve(port, "tasks")
            assert "walk_to" in out and "done" in out
        await body.close()
        await sense.close()
    run(go())


def test_cli_mode_and_approvals(tmp_path):
    async def go():
        env, port = envelope(), free_port()
        async with service(tmp_path, port, env) as svc:
            rc, out = await eve(port, "mode", "autonomous")
            assert rc == 1 and "mode_not_qualified" in out
            rc, out = await eve(port, "mode", "supervised")
            assert rc == 1 and "mode_not_allowed" in out
            rc, out = await eve(port, "approve", "../../etc", "v1")
            assert rc == 1 and "bad_project" in out
            rc, out = await eve(port, "approve", "cabin", "v1", "--by", "Griefer")
            assert rc == 1 and "not_trusted" in out
            rc, out = await eve(port, "status")
            assert "body: DOWN" in out and "REFUSING DISPATCH" in out
    run(go())


def test_cli_unreachable(tmp_path):
    async def go():
        rc, out = await eve(free_port(), "status")
        assert rc == 2 and "not reachable" in out
    run(go())
