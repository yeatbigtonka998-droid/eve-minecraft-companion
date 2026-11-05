r"""Dev rehearsal for CP-43 (NOT evidence): the objective runner with the REAL planner (Ollama qwen3:8b) against a fake
body that models what matters for the column objective - mining a supply adds a cobblestone, placing removes one and
fails `occupied` when Eve stands in the target block or `no_item` without cobblestone. Finds planner/validator loops
before spending lane-B time.
    python tools\rehearse_cp43.py [--runs 3] [--envelope C:\eve-shark\mind\config\envelope.json]
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

MIND = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(MIND / "tests"), str(MIND / "tools"), str(MIND / "src")]
from conftest import envelope, mind_env, until  # noqa: E402
from eve_mind.planner import Planner  # noqa: E402

SPEC = json.loads((MIND / "fixtures" / "CP-43.json").read_text("utf-8"))
COB = "minecraft:cobblestone"


def model_world(body):
    """Wrap the fake's execute: blocks and inventory change like the real thing for this objective."""
    world = {tuple(SPEC["fixture"]["supplies"]["M1"]): COB, tuple(SPEC["fixture"]["supplies"]["M2"]): COB}
    orig = body.execute

    def inv():
        return sum(s["count"] for s in body.inventory if s["item"] == COB)

    def set_cob(n):
        body.inventory = [s for s in body.inventory if s["item"] != COB] + ([{"slot": 0, "item": COB, "count": n}] if n else [])

    async def execute(aid, p, req_id):
        skill, params = p["skill"], p["params"]
        pos = params.get("pos")
        key = tuple(int(pos[a] // 1) for a in "xyz") if isinstance(pos, dict) else None
        if skill == "mine_block":
            body.result_override = None if world.get(key) else ("failed", "wrong_block")
            if world.pop(key, None) == COB:
                set_cob(inv() + 1)
        elif skill == "place_block":
            feet = tuple(int(body.pos[a] // 1) for a in "xyz")
            if key in (feet, (feet[0], feet[1] + 1, feet[2])):
                body.result_override = ("failed", "occupied")
            elif key[1] - feet[1] >= 2 and max(abs(key[0] - feet[0]), abs(key[2] - feet[2])) < 2:
                body.result_override = ("failed", "obstructed")     # the support face is above her eyes
            elif inv() < 1 or key in world:
                body.result_override = ("failed", "no_item" if inv() < 1 else "occupied")
            else:
                body.result_override = None
                world[key] = COB
                set_cob(inv() - 1)
        else:
            body.result_override = getattr(body, "user_override", None)     # tests inject other outcomes here
        return await orig(aid, p, req_id)
    body.execute = execute
    return world


async def one(env_path):
    lane = json.loads(Path(env_path).read_text("utf-8"))
    env = envelope(zones=lane["zones"], containers=lane.get("containers", {}))     # fakes' world/account, lane rules
    async with mind_env(Path(MIND / "var" / "rehearse43"), env=env, db_path=None) as e:
        e.mind.planner = Planner(timeout_s=120)
        e.body.pos = dict(zip("xyz", SPEC["fixture"]["eve"]["pos"]))
        e.body.inventory = [{"slot": 0, "item": COB, "count": 1}, {"slot": 1, "item": "minecraft:wooden_pickaxe", "count": 1},
                            {"slot": 2, "item": "minecraft:golden_apple", "count": 2}]
        e.body.walk_s = 0.05
        world = model_world(e.body)
        await until(lambda: (e.mind.fresh_state() or {}).get("inventory"), timeout=5)
        r = await e.mind.cli("objective", {"text": SPEC["objective_text"]})
        oid = r["objective_id"]
        await until(lambda: e.mind.objectives.get(oid).status != "running", timeout=420)
        o = e.mind.objectives.get(oid)
        col = [world.get(tuple(c)) == COB for c in SPEC["fixture"]["target_column"]]
        return {"status": o.status, "column": col, "report": o.report(), "actions": o.actions, "replans": o.replans,
                "rejections": o.rejections, "failed": [(f["skill"], f["where"], f["code"]) for f in o.failed]}


async def main_async(a):
    import shutil
    for i in range(a.runs):
        shutil.rmtree(MIND / "var" / "rehearse43", ignore_errors=True)
        (MIND / "var" / "rehearse43").mkdir(parents=True)
        print(json.dumps(await one(a.envelope)), flush=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--envelope", default=r"C:\eve-shark\mind\config\envelope.json")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
