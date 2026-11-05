"""O185: learning's labelled owner-phrase corpus (parts/learning/phrase_corpus, eaf1270e; 148 rows, 225 owner lines
from sessions 1-4, Hunter's S88 paraphrases and the O175 phrase family) through grammar.steps - every row exact; and
natural-place anchors ("near the river") resolved through mind-c's O140-b search instead of asking for x y z."""
import json
from pathlib import Path

import pytest

from conftest import mind_env, run, until
from eve_mind.grammar import steps
from eve_mind.planner import Planner

ROWS = [json.loads(l) for l in (Path(__file__).parent / "fixtures" / "phrase_corpus.jsonl").read_text("utf-8").splitlines()]


def norm(s):
    q = s.get("qty") or {"kind": "unspecified", "n": None}
    return {"verb": s.get("verb"), "object": s.get("object"), "qty": {"kind": q.get("kind"), "n": q.get("n")},
            "place": s.get("place")}


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_corpus_row(row):
    assert [norm(s) for s in steps(row["text"])] == [norm(s) for s in row["intents"]], row["text"]


def test_corpus_size_and_score():
    assert len(ROWS) == 148
    exact = sum([norm(s) for s in steps(r["text"])] == [norm(s) for s in r["intents"]] for r in ROWS)
    assert exact / len(ROWS) >= 0.95, exact          # kickoff's bar for retiring the old regexes


# ---- natural-place anchors (O140-b's search) for the grammar's orders

@pytest.fixture
def no_planner(monkeypatch):
    async def refused(self, *a, **k):
        return {"ok": False, "error": "planner_unavailable", "detail": "test stub", "model": "stub",
                "prompt_version": "stub"}
    monkeypatch.setattr(Planner, "plan", refused)


def world(e, cells):
    real = e.mind.hub.request

    async def request(peer, typ, payload, timeout=1.0):
        if typ != "query.blocks":
            return await real(peer, typ, payload, timeout=timeout)
        lo, hi = payload["min"], payload["max"]
        pal, data = ["minecraft:air", "minecraft:dirt"], []
        for y in range(lo["y"], hi["y"] + 1):
            for z in range(lo["z"], hi["z"] + 1):
                for x in range(lo["x"], hi["x"] + 1):
                    b = cells.get((x, y, z), "minecraft:dirt" if y < 64 else "minecraft:air")
                    if b not in pal:
                        pal.append(b)
                    data.append(pal.index(b))
        return {"palette": pal, "data": data, "unloaded": []}
    e.mind.hub.request = request


POOL = {(x, 63, z): "minecraft:water" for x in range(10, 14) for z in range(-2, 2)}


async def hear(e, text):
    q = len(e.body.received)
    await e.sense.send("owner.chat", {"sender": "Hunter", "trusted": True, "text": text})
    return (await e.body.wait_for(lambda m: m["type"] == "chat.send", start=q, timeout=6))["payload"]["text"]


def acts(e):
    return [(r[0], json.loads(r[1])) for r in e.db.execute("SELECT skill, params FROM actions ORDER BY rowid")]


def test_mine_near_the_river_walks_there_first(tmp_path, no_planner):
    async def go():
        async with mind_env(tmp_path) as e:
            world(e, POOL)
            await until(lambda: e.mind.fresh_state() is not None)
            assert await hear(e, "mine 5 sand near the river") == "Going to the river first."
            await until(lambda: any(s == "mine_resource" for s, _ in acts(e)), timeout=5, what="the mine after the walk")
            (w, wp), (m, mp) = acts(e)[-2:]
            assert w == "walk_to" and 9 <= wp["pos"]["x"] <= 15 and mp == {"block": "minecraft:sand", "count": 5,
                                                                          "radius": 16}
    run(go())


def test_kill_all_the_zombies_by_the_lake(tmp_path, no_planner):
    async def go():
        async with mind_env(tmp_path) as e:
            world(e, POOL)
            await until(lambda: e.mind.fresh_state() is not None)
            assert await hear(e, "kill all the zombies by the lake") == "Going to the lake first."
            await until(lambda: any(s == "fight_melee" for s, _ in acts(e)), timeout=5, what="the fight after the walk")
            assert [s for s, _ in acts(e)][-2:] == ["walk_to", "fight_melee"]
    run(go())


def test_no_such_place_and_the_hill(tmp_path, no_planner):
    async def go():
        async with mind_env(tmp_path) as e:
            world(e, {})
            await until(lambda: e.mind.fresh_state() is not None)
            assert await hear(e, "mine 5 sand near the river") == "I don't see the river within 32 blocks."
            assert await hear(e, "mine 5 coal near the hill") == "Where is the hill? Give me its x y z."
            assert not [s for s, _ in acts(e) if s in ("mine_resource", "walk_to")]
    run(go())
