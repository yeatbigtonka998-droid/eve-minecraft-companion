"""Eve's DeepSeek/OpenRouter cap is her only spending guard (the key has no provider-side limit): prove it is hard.
Drive spend to the cap and show the next call is refused before sending; a failed or timed-out call stays counted at
its worst case; a restart (new adapter, same DB) does not reset month-to-date; `eve spend` and the status show it."""
import asyncio
import sqlite3
import time

from conftest import envelope, mind_env, run
from eve_mind.planner import DeepThinker, spend_summary
from fake_http import FakeHTTP

M = {"flash": "deepseek/flash-id-from-cp06"}
PRICE = {M["flash"]: [0.45, 1.80]}        # foundation's 1.5x prices on the 45L
CAP = 0.01                                # a tiny cap so the test reaches it fast; same code path as US$20
ENV = {"budgets": {"deepseek_monthly_usd": CAP}}
MSG = [{"role": "user", "content": "x" * 200}]


def thinker(db, url):
    return DeepThinker(db, ENV, key="k-not-real", base_url=url, models=M, prices=PRICE, enabled=True)


def test_spend_is_driven_to_the_cap_and_the_next_call_is_refused(tmp_path):
    async def go():
        fake = FakeHTTP()
        fake.reply = {"choices": [{"message": {"content": "ok"}}],
                      "usage": {"prompt_tokens": 300, "completion_tokens": 900, "cost": 0.0018}}
        try:
            async with mind_env(tmp_path) as e:
                d = thinker(e.db, fake.url)
                worst = 1000 * 1.80 / 1e6 + (64 + 200 + 16) * 0.45 / 1e6     # what the cap check reserves
                results = []
                for _ in range(20):
                    r = await d.ask("fixture", MSG, max_tokens=1000)
                    results.append(r)
                    if not r["ok"]:
                        break
                sent = len(fake.requests)
                assert results[-1]["error"] == "budget_exhausted" and sent == len(results) - 1
                assert d.month_spend() <= CAP and d.month_spend() + worst > CAP
                # the refused call never reached the provider, and nothing else will this month
                assert (await d.ask("fixture", MSG, max_tokens=1000))["error"] == "budget_exhausted"
                assert len(fake.requests) == sent
                # restart: a new adapter on the same DB sees the same month-to-date
                d2 = thinker(sqlite3.connect(tmp_path / "eve.db", isolation_level=None), fake.url)
                assert (await d2.ask("fixture", MSG, max_tokens=1000))["error"] == "budget_exhausted"
                assert len(fake.requests) == sent
                s = (await e.mind.cli("spend", {}))
                assert s["ok"] and s["calls"] == sent and s["unsettled"] == 0
                assert "spend" in e.mind.status()
        finally:
            fake.close()
    run(go())


def test_failed_and_timed_out_calls_stay_counted_at_worst_case(tmp_path):
    async def go():
        fake = FakeHTTP()
        fake.delay_s = 2.0                                   # the reply comes too late: a timeout
        fake.reply = {"choices": [{"message": {"content": "ok"}}], "usage": {"cost": 0.0001}}
        try:
            async with mind_env(tmp_path) as e:
                d = thinker(e.db, fake.url)
                r = await d.ask("fixture", MSG, max_tokens=1000, timeout_s=0.3)
                assert r["error"] == "deep_thinker_unavailable" and r["usd"] > 0
                s = spend_summary(e.db, CAP)
                assert s["unsettled"] == 1 and abs(s["month_to_date_usd"] - r["usd"]) < 1e-9
                fake.delay_s = 0
                fake.reply = {"choices": [{"message": {"content": "ok"}}]}       # no usage reported at all
                r2 = await d.ask("fixture", MSG, max_tokens=1000)
                assert r2["ok"] and spend_summary(e.db, CAP)["unsettled"] == 2   # kept at its worst case
        finally:
            fake.close()
    run(go())


def test_daily_spend_line_in_the_mind_log(tmp_path, caplog):
    async def go():
        async with mind_env(tmp_path, env=envelope()) as e:
            e.mind._spend_day = None
            with caplog.at_level("INFO", logger="eve.mind"):
                e.mind.daily_spend_line()
                e.mind.daily_spend_line()                       # once per day only
            lines = [r.message for r in caplog.records if r.message.startswith("spend ")]
            assert len(lines) == 1 and "month-to-date US$" in lines[0] and "cap" in lines[0]
    run(go())


def test_design_reserve_and_commit_share_the_one_cap(tmp_path):
    """Two spenders, one cap: design reserves its worst case via /cli before each call and commits the reported
    cost; the mind's DeepThinker reserves through the same atomic check. Neither can push past the cap."""
    async def go():
        fake = FakeHTTP()
        fake.reply = {"choices": [{"message": {"content": "ok"}}], "usage": {"cost": 0.0012}}
        try:
            async with mind_env(tmp_path, env=envelope(budgets={"deepseek_monthly_usd": CAP})) as e:
                cli = e.mind.cli
                assert (await cli("spend_reserve", {"job": "x", "model": "m", "usd_worst": 0}))["error"] == "bad_args"
                assert (await cli("spend_reserve", {"job": "x", "model": "m", "usd_worst": float("nan")}))["error"] == "bad_args"
                r = await cli("spend_reserve", {"job": "critic", "model": "deepseek/pro", "usd_worst": 0.004})
                assert r["ok"] and r["remaining_usd"] == round(CAP - 0.004, 6)
                c = await cli("spend_commit", {"reservation_id": r["reservation_id"], "usd": 0.001, "tokens_in": 5})
                assert c["settled"] and spend_summary(e.db, CAP)["month_to_date_usd"] == 0.001
                assert (await cli("spend_commit", {"reservation_id": r["reservation_id"], "usd": 0}))["error"] == \
                    "unknown_reservation"                                         # once only
                held = await cli("spend_reserve", {"job": "long", "model": "deepseek/pro", "usd_worst": 0.006})
                assert held["ok"]                                                 # 0.007 of 0.01 now counted
                assert (await cli("spend_commit", {"reservation_id": held["reservation_id"]}))["settled"] is False
                # the mind's own DeepThinker sees design's reservations: its worst case no longer fits
                d = thinker(e.db, fake.url)
                assert (await d.ask("fixture", MSG, max_tokens=2000))["error"] == "budget_exhausted"
                assert len(fake.requests) == 0
                r3 = await cli("spend_reserve", {"job": "y", "model": "m", "usd_worst": 0.0031})
                assert r3["error"] == "budget_exhausted"
                s = spend_summary(e.db, CAP)
                assert s["month_to_date_usd"] <= CAP and s["unsettled"] == 1
                # a mind-internal row cannot be committed through the design door
                rid = e.db.execute("INSERT INTO spend (ts, provider, model, job, usd) VALUES (?, 'openrouter', 'm', "
                                   "'planner [unsettled]', 0.0001)", (int(time.time() * 1000),)).lastrowid
                assert (await cli("spend_commit", {"reservation_id": rid, "usd": 0}))["error"] == "unknown_reservation"
        finally:
            fake.close()
    run(go())


def test_reservations_are_atomic_across_processes(tmp_path):
    """BEGIN IMMEDIATE: a second connection (another process) cannot pass the check against the same budget."""
    from eve_mind.planner import reserve_spend
    db1 = sqlite3.connect(tmp_path / "s.db", isolation_level=None, timeout=5)
    db1.execute("PRAGMA journal_mode=WAL")
    db1.execute("CREATE TABLE spend (id INTEGER PRIMARY KEY, ts INTEGER, provider TEXT, model TEXT, job TEXT, "
                "tokens_in INTEGER, tokens_out INTEGER, usd REAL, cached INTEGER)")
    db2 = sqlite3.connect(tmp_path / "s.db", isolation_level=None, timeout=5)
    got = [reserve_spend(db, 0.01, "openrouter", "m", "j", 0, 0, 0.004) for db in (db1, db2, db1, db2)]
    assert [g is not None for g in got] == [True, True, False, False]


def test_brief_job_is_capped_at_one_dollar_for_the_checkpoint(tmp_path):
    """O56a: design's CP-54 `brief` wording calls reserve through the mind like any design call, but the job as a whole
    stops at US$1.00 (settled + open), whatever the monthly cap; each commit is logged."""
    async def go():
        async with mind_env(tmp_path, env=envelope(budgets={"deepseek_monthly_usd": 20})) as e:
            cli = e.mind.cli
            r = await cli("spend_reserve", {"job": "brief", "model": "deepseek/pro", "usd_worst": 0.6})
            assert r["ok"]
            assert (await cli("spend_commit", {"reservation_id": r["reservation_id"], "usd": 0.55}))["settled"]
            assert (await cli("spend_reserve", {"job": "brief", "model": "deepseek/pro", "usd_worst": 0.5}))["error"] \
                == "job_cap"                                                      # 0.55 + 0.5 > 1.00
            assert (await cli("spend_reserve", {"job": "brief", "model": "deepseek/pro", "usd_worst": 0.4}))["ok"]
            assert (await cli("spend_reserve", {"job": "critic", "model": "deepseek/pro", "usd_worst": 0.5}))["ok"]
            assert e.db.execute("SELECT count(*) FROM events WHERE kind='spend.commit'").fetchone()[0] == 1
    run(go())
