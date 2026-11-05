"""Check for safe_teardown (45L CP-28 attempt 1: the arena floor was removed with Eve on it; she fell to 0.15 HP).
RCON and park-eve are stubbed; needs a lane's token file to import (run on Shark):

    py -3.12 parts/combat/tools/test_teardown.py
"""
import sys
from pathlib import Path

sys.argv = [sys.argv[0], "cp26", "--lane", "c"]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import laneb_combat as L  # noqa: E402

L.subprocess.run = lambda *a, **k: None
L.time.sleep = lambda s: None
calls = []


def stub(inside):
    def r(cmd):
        calls.append(cmd)
        if cmd.startswith("execute if entity @a["):
            return f"Test passed. Count: {inside}" if inside else "Test failed"
        if "Health" in cmd:
            return "X has the following entity data: 9.99f"
        return "ok"
    return r


for inside, want_removed in ((1, False), (2, False), (0, True)):
    calls.clear()
    L.r = stub(inside)
    removed = []
    msg = L.safe_teardown(lambda: removed.append(1))
    assert bool(removed) == want_removed, (inside, msg)
    heal = next(i for i, c in enumerate(calls) if "instant_health" in c)
    check = next(i for i, c in enumerate(calls) if c.startswith("execute if entity @a["))
    assert heal < check, "she is healed and parked before the volume check"
    assert ("SKIPPED" in msg) == (not want_removed), msg
print("safe_teardown: blocks never removed with a player inside/above the fixture; park before check - ok")
