"""O240 (one source of truth): every prospect line in agenda's progression table, sent through the owner path,
dispatches the count that milestone wants (its `have` amount) - the mind keeps no table of its own to drift."""
import json
import sys

import pytest

from conftest import ROOT, envelope, mind_env, run, until

from eve_mind import progress

sys.path.insert(0, str(ROOT / "parts" / "mind"))
from agenda import progression as table   # noqa: E402

GROUND = {(x, y, z): ("minecraft:stone" if y < 64 else "minecraft:air")
          for x in range(-8, 9) for y in range(60, 68) for z in range(-8, 9)}
LINES = [(m["id"], c, max(progress.wanted(m["have"]))) for m in table.MILESTONES for c in m["commands"]
         if progress.PROSPECT.match(c)]


def test_o240_the_table_has_prospect_lines():
    assert {i for i, _, _ in LINES} >= {"raw_iron", "diamond", "obsidian"}, LINES


@pytest.mark.parametrize("milestone,line,want", LINES, ids=[i for i, _, _ in LINES])
def test_o240_a_prospect_line_dispatches_the_tables_count(tmp_path, milestone, line, want):
    async def go():
        async with mind_env(tmp_path, env=envelope(zones=[])) as e:
            e.body.walk_s = 0.05

            async def near(st, r, dy):
                return GROUND
            e.mind.blocks_near = near
            await until(lambda: e.mind.fresh_state() is not None)
            await e.mind.owner_text(line, "Hunter")
            await until(lambda: e.mind.db.execute("SELECT count(*) FROM actions WHERE skill='prospect'").fetchone()[0],
                        timeout=5, what="the prospect")
            p = json.loads(e.mind.db.execute("SELECT params FROM actions WHERE skill='prospect'").fetchone()[0])
            assert p["count"] == want, (milestone, line, p, want)
    run(go())


def test_o240_an_ore_the_table_lacks_gets_the_default(tmp_path):
    class M:
        cfg = type("C", (), {"root": ROOT})
    assert progress.table_count(M, "coal") == next((w for _, line, w in LINES if "coal" in line), None)
    assert progress.table_count(M, "emerald") is None                  # off-table: the caller's DEFAULT_COUNT
