"""Stage B (S105/O377): the one replaceable inference boundary behind the Planner. Provider-neutral: the Planner builds
the prompt and the JSON schema; a provider only turns (system, user, schema) into the model's text. Every reply still
goes through the planner's parsing and the mind's local validators (validate_plan, permissions, envelope) - a provider
never dispatches anything.

Contract: `await provider.chat(system, user, schema)` ->
  {"ok": True, "content": str, "latency_ms": int, ...provider meta}
  {"ok": False, "error": "planner_timeout"|"planner_unavailable"|"planner_truncated"|"planner_incomplete",
   "detail": str, ...provider meta}
plus `provider.model` (recorded with each decision) and `provider.available()`.

Selection: EVE_PLANNER_PROVIDER, default "ollama" (today's behaviour, byte-identical requests). There is no automatic
fallback between providers (S104), and no hosted provider exists here until Hunter picks a route.
"""
import os


class FakeProvider:
    """Tests only: answers from a list (str content, or a dict returned as-is), records what it was asked."""
    model = "fake"

    def __init__(self, replies):
        self.replies, self.asked = list(replies), []

    def available(self):
        return True

    async def chat(self, system, user, schema):
        self.asked.append({"system": system, "user": user, "schema": schema})
        r = self.replies.pop(0)
        return r if isinstance(r, dict) else {"ok": True, "content": r, "latency_ms": 0}


def select(**ollama):
    """The configured provider; unknown names fail at start-up rather than silently picking another."""
    name = os.environ.get("EVE_PLANNER_PROVIDER") or "ollama"
    if name == "ollama":
        from .planner import OllamaProvider
        return OllamaProvider(**ollama)
    if name == "openai_plan":           # S104: Sign in with ChatGPT plan usage (mind-c, eve_mind/hosted); never a fallback
        from .hosted.openai_plan import OpenAIPlanProvider
        return OpenAIPlanProvider()
    raise ValueError(f"EVE_PLANNER_PROVIDER={name!r}: 'ollama' or 'openai_plan'")
