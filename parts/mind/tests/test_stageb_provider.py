"""Stage B (S105/O377): the provider boundary behind the Planner - fake transport only, no model, no network."""
import json

import pytest

from conftest import mind_env, run
from eve_mind import planner as P
from eve_mind.provider import FakeProvider, select
from test_cp41 import CAPS, ENV, REG, STATE

WALK = json.dumps({"steps": [{"skill": "walk_to", "params": {"pos": {"x": 0, "y": 64, "z": 0}}, "depends_on": []}],
                   "question": None})


def test_planner_asks_the_provider_and_parses_its_answer():
    fake = FakeProvider([WALK])
    r = run(P.Planner(provider=fake).plan("go to 0 64 0", REG, CAPS, STATE, ENV))
    assert r["ok"] and r["plan"]["steps"][0]["skill"] == "walk_to" and r["model"] == "fake"
    assert r["prompt_version"] == P.PROMPT_VERSION and r["raw"] == WALK
    asked = fake.asked[0]
    assert asked["user"] == "go to 0 64 0" and asked["system"].startswith("You plan for Eve")
    assert "anyOf" in json.dumps(asked["schema"])                        # the planner's own schema, provider-neutral


def test_provider_errors_and_bad_text_never_become_plans():
    down = {"ok": False, "error": "planner_unavailable", "detail": "connection refused"}
    r = run(P.Planner(provider=FakeProvider([down])).plan("go home", REG, CAPS, STATE, ENV))
    assert r == {"ok": False, "error": "planner_unavailable", "detail": "connection refused", "model": "fake",
                 "prompt_version": P.PROMPT_VERSION, "input": "go home"}
    r = run(P.Planner(provider=FakeProvider(["not json {"])).plan("go home", REG, CAPS, STATE, ENV))
    assert r["ok"] is False and r["error"] == "malformed_output"


def test_ollama_request_body_is_unchanged(monkeypatch):
    """The body is exactly what Planner sent before the boundary (field order included)."""
    seen = []
    monkeypatch.setattr(P, "_post", lambda url, body, timeout: seen.append((url, json.dumps(body))) or {
        "message": {"content": WALK}, "done": True})
    pl = P.Planner(url="http://x", model="qwen3:8b", timeout_s=5, num_ctx=8192)
    assert run(pl.plan("go to 0 64 0", REG, CAPS, STATE, ENV))["ok"]
    url, body = seen[0]
    sent = json.loads(body)
    skills, _ = P.catalogue(REG, CAPS, STATE, "go to 0 64 0")
    want = {"model": "qwen3:8b", "stream": False, "format": P.format_schema(skills, REG.raw["$defs"]),
            "options": {"temperature": 0, "num_ctx": 8192}, "keep_alive": "10m",
            "messages": [{"role": "system", "content": sent["messages"][0]["content"]},
                         {"role": "user", "content": "go to 0 64 0"}], "think": False}
    assert url == "http://x/api/chat" and body == json.dumps(want)
    assert pl.url == "http://x" and pl.model == "qwen3:8b" and pl.num_ctx == 8192 and pl.timeout_s == 5


def test_selection_is_config_and_never_falls_back(monkeypatch):
    monkeypatch.delenv("EVE_PLANNER_PROVIDER", raising=False)
    assert type(select(url="http://x")).__name__ == "OllamaProvider"
    monkeypatch.setenv("EVE_PLANNER_PROVIDER", "openai")
    with pytest.raises(ValueError, match="'ollama' or 'openai_plan'"):
        select()


def test_a_late_answer_for_another_world_is_discarded(tmp_path):
    """The world changes (reconnect to another level) while the planner thinks: its answer must not run here."""
    async def go():
        async with mind_env(tmp_path) as e:
            class Slow(FakeProvider):
                async def chat(self, system, user, schema):
                    e.mind.world_id = "other-world:000"                 # what sense's world declaration does
                    return await super().chat(system, user, schema)
            e.mind.planner = P.Planner(provider=Slow([WALK]))
            r = await e.mind.plan_and_run("walk to 0 64 0", "owner", step_wait=2)
            assert r == {"ok": False, "error": "stale_plan", "detail": "a stop, reconnect or world change came first"}
            assert sum(e.body.requests.values()) == 0                     # nothing dispatched
    run(go())
