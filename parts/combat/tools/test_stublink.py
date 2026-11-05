"""O102c offline check of laneb_combat.StubLink with a fake hub (no lane, no port): the start gate both group-(b) fixtures use
(cp98 needs fight_melee.doorway, cp39 needs harvest_animal), and the frame logic (state gap, entity id, dispatch result).
    py -3.12 parts/combat/tools/test_stublink.py"""
import json, sys, time, types
from pathlib import Path

sys.argv = ["x", "--lane", "e"]
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import laneb_combat as L  # noqa: E402

REPO = HERE.parents[2]
DRAFT = json.loads((REPO / "contracts" / "skills" / "drafts" / "registry-0.16.0.json").read_text(encoding="utf-8"))
OLD = json.loads((REPO / "contracts" / "skills" / "registry.json").read_text(encoding="utf-8"))


def fake_stub_module(reg, body_hash_matches=True):
    class Hub:
        def __init__(self, verbose=False):
            self.reg, self.hash = reg, "h" * 64
            hello = {"type": "hello", "payload": {"registry_hash": self.hash if body_hash_matches else "x" * 64}}
            self.frames = [{"dir": "in", "t": time.time(), "msg": hello}]
            self.conn = object()

        def start(self):
            return self
    return types.SimpleNamespace(Hub=Hub)


def gate(reg, skill, param=None, match=True):
    sys.modules["stub_mind"] = fake_stub_module(reg, match)
    try:
        L.StubLink(skill, param)
        return True
    except SystemExit:
        return False


assert gate(DRAFT, "fight_melee", "doorway"), "cp98 on the 0.16.0 draft"
assert gate(DRAFT, "harvest_animal"), "cp39 on the 0.16.0 draft"
assert not gate(OLD, "fight_melee", "doorway"), "0.15.0 has no doorway: refused"
assert not gate(OLD, "harvest_animal"), "0.15.0 has no harvest_animal: refused"
assert not gate(DRAFT, "harvest_animal", match=False), "client and stub registries differ: refused"

now = time.time()
fr = [{"dir": "in", "t": now - 3 + i * 0.3, "msg": {"type": "state", "payload": {"nearby_entities": [{"uuid": "u-1", "id": 42}]}}}
      for i in range(8)]
fr.insert(4, {"dir": "in", "t": now - 1.5, "msg": {"type": "pong", "payload": {}}})  # non-state frames never count as states
fr.append({"dir": "in", "t": now - 0.1, "msg": {"type": "action.result", "payload": {"action_id": "a1", "status": "ok",
                                                                                     "evidence": {"refused": [], "kills": [1]}}}})
link = object.__new__(L.StubLink)
link.hub = types.SimpleNamespace(frames=fr, request=lambda s, p: "a1", wait_result=lambda aid, t, since: fr[-1]["msg"]["payload"])
assert link.gap(now - 4) == 0.3
assert link.net_id("u-1", 1) == 42 and link.net_id("nobody", 0.3) is None
r = link.do("harvest_animal", {}, 1)
assert r["status"] == "ok" and r["evidence"]["kills"] == [1]
link.hub.wait_result = lambda aid, t, since: None
assert link.do("harvest_animal", {}, 1)["status"] == "no_result", "a lost result is a result, not a crash"
print("StubLink: gate (draft ok x2, 0.15.0 refused x2, hash mismatch refused), gap, net_id, do - ok")
