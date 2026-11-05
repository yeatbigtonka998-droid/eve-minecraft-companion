"""The CP-42 sandbox's sense: tools/fake_sense.py (the test double, not mind code) with a zone policy from the envelope,
so an out-of-zone wording meets a refusal as it would on a lane instead of the fake's allow-all.
ponytail: approximation of real sense - inside any zone box = allowed (owner commands carry task.grant), outside = deny,
use_hazard always denied. The qualifying judge of the zone rules is real eve-sense on a lane, not this file.
    EVE_LINK_TOKEN=... python agenda/sandbox_sense.py --port P --envelope config/envelope.example.json
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from fake_sense import FakeSense  # noqa: E402


def zone_policy(env):
    def check(p):
        if p.get("effect") == "use_hazard":
            return False, "sandbox-hazard", "hazards are off in the envelope"
        pos = p.get("pos")
        if pos is None:
            return True, "sandbox-entity", "entity checks: the mind's protected-entity rule applies"
        dim = p.get("dim") or env["home"]["dim"]
        for z in env["zones"]:
            if z["dim"] == dim and all(z["min"][a] <= pos[a] <= z["max"][a] for a in "xyz"):
                return True, f"sandbox-zone:{z['id']}", f"inside zone {z['id']}"
        return False, "sandbox-out-of-zone", "outside every zone in the envelope"
    return check


async def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--envelope", required=True)
    a = ap.parse_args()
    env = json.loads(Path(a.envelope).read_text("utf-8"))
    sense = FakeSense(f"ws://127.0.0.1:{a.port}/sense", os.environ["EVE_LINK_TOKEN"], envelope=env)
    sense.permissions = zone_policy(env)
    sense.start()
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(_main())
