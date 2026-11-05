"""Fake sense: a test double for the server-helper mod that speaks link protocol v1 on /sense.

Knobs: permissions(payload) -> (allowed, rule_id, reason); silent_permissions (never answer permission.check);
blocks {(x,y,z): block_id} for query.blocks; containers for storage.index; world_id / envelope override.
Usage: EVE_LINK_TOKEN=... python tools/fake_sense.py [--port 8951]
"""
import argparse
import asyncio
import json
import os
import time

from fake_body import ROOT, Peer, chash


class FakeSense(Peer):
    link = "sense"

    def __init__(self, url, token, envelope=None, world_id=None, level_name="eve-dev", **kw):
        super().__init__(url, token, **kw)
        self.envelope_cfg = envelope or json.loads((ROOT / "config" / "envelope.example.json").read_text("utf-8"))
        self.world_id = world_id or self.envelope_cfg["world_id"]
        self.level_name = level_name
        self.permissions = lambda q: (True, "fake-allow", "allowed by fake sense")
        self.silent_permissions = False
        self.permission_checks = []
        self.blocks, self.containers = {}, []
        self.grants, self.order, self.regions = [], [], []        # task.grant payloads; order of grant/check arrivals
        self.tick_offset, self.refused = 0, []                    # server clock offset; grants refused by the window
        self.repair_asks, self.echo_box = [], True
        self.void_edges, self.resource_asks, self.crops = [], [], []
        self.attack_grants, self.attack_grant_refuse = [], None   # O136: attack.grant asks; a refusal reason
        self.repair_answers = lambda q: {"steps": [], "manual": [], "skipped": [], "total": 0, "truncated": False}

    @property
    def tick(self):
        return int(time.monotonic() * 20) + self.tick_offset   # a server tick at 20 Hz on its own clock

    def window_ok(self, p):
        """Real sense (link v1.7): expires_tick must be in (now, now+72000]; otherwise the grant is refused."""
        ok = self.tick < p["expires_tick"] <= self.tick + 72000
        if not ok:
            self.refused.append(p)
        return ok

    def granted(self, q):
        return any(g["effect"] == q["effect"] and q.get("pos") in g["positions"] and g["expires_tick"] > self.tick
                   for g in self.grants)

    def hello_payload(self):
        return {"token": self.token, "mod_version": "fake-sense-0.1", "mc_version": "26.3", "world_id": self.world_id,
                "level_name": self.level_name, "seed_hash": "000000000000",
                "envelope_version": self.envelope_cfg["envelope_version"], "envelope_hash": chash(self.envelope_cfg)}

    def on_hello_ok(self, msg):
        self.world = self.world_id

    async def handle(self, msg):
        t, p = msg["type"], msg["payload"]
        if t == "task.grant":
            if not p["positions"]:                      # positions [] revokes that action's grants (and regions)
                self.grants = [g for g in self.grants if g["action_id"] != p["action_id"]]
                self.regions = [g for g in self.regions if g["action_id"] != p["action_id"]]
                self.order.append(("revoke", p["action_id"]))
                return
            if not self.window_ok(p):
                return
            self.grants.append(p)
            self.order.append(("grant", p["action_id"]))
            return
        if t == "grant_region":                         # link v1.3: a box, cleared by revoke/stop
            if not self.window_ok(p):
                return
            self.regions.append(p)
            self.order.append(("region", p["action_id"]))
            return
        if t == "permission.check":
            self.permission_checks.append(p)
            self.order.append(("check", None))
            if self.silent_permissions:
                return
            allowed, rule, reason = self.permissions(p)
            return await self.send("permission.result", {"allowed": allowed, "rule_id": rule, "reason": reason},
                                   reply_to=msg["id"])
        if t == "attack.grant":                         # link v1.12 (O136): granted unless the knob refuses
            self.attack_grants.append(p)
            if self.attack_grant_refuse:
                return await self.send("error", {"code": "bad_payload", "message": f"attack.grant: {self.attack_grant_refuse}"},
                                       reply_to=msg["id"])
            return await self.send("attack.grant.result", {**p, "entity_type": "?", "expires_ms": int(time.time() * 1000) + 300000},
                                   reply_to=msg["id"])
        if t == "query.blocks":
            lo, hi = p["min"], p["max"]
            palette, data = ["minecraft:air"], []
            for (x, y, z), b in self.blocks.items():
                if all(lo[a] <= v <= hi[a] for a, v in zip("xyz", (x, y, z))):
                    if b not in palette:
                        palette.append(b)
                    data.append({"pos": {"x": x, "y": y, "z": z}, "i": palette.index(b)})
            return await self.send("query.blocks.result", {"palette": palette, "data": data, "unloaded": []},
                                   reply_to=msg["id"])
        if t == "storage.request":
            return await self.send_storage(reply_to=msg["id"])
        if t == "query.resources":                      # v1.8 void_edge (CP-53): the knob `void_edges`
            self.resource_asks.append(p)
            items = {"void_edge": self.void_edges, "crops": self.crops}.get(p.get("kind"), [])
            return await self.send("query.resources.result", {"kind": p.get("kind"), "items": items, "truncated": False},
                                   reply_to=msg["id"])
        if t == "zone.repair":                          # CP-52: repair_answers(payload) -> zone.repair.result payload
            self.repair_asks.append(p)
            ans = self.repair_answers(p)
            if self.echo_box and "box" in p and "box" not in ans:      # eve-sense >= 0.1.15 echoes the box used
                ans = {**ans, "box": p["box"]}
            return await self.send("zone.repair.result", ans, reply_to=msg["id"])
        await super().handle(msg)

    async def send_storage(self, containers=None, partial=False, reply_to=None):
        return await self.send("storage.index", {"containers": containers if containers is not None else self.containers,
                                                 "partial": partial}, reply_to=reply_to)

    async def owner_command(self, sender, trusted, command, args=()):
        return await self.send("owner.command", {"sender": sender, "trusted": trusted, "command": command,
                                                 "args": list(args), "raw": f"/eve {command} {' '.join(args)}".strip()})


async def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("EVE_PORT", 8951)))
    a = ap.parse_args()
    sense = FakeSense(f"ws://127.0.0.1:{a.port}/sense", os.environ["EVE_LINK_TOKEN"]).start()
    while True:
        await asyncio.sleep(5)
        print(f"session={sense.session} world={sense.world_id} checks={len(sense.permission_checks)}", flush=True)


if __name__ == "__main__":
    asyncio.run(_main())
