"""The OpenAI plan-usage provider behind the Planner's boundary (provider.py, O377):

    await provider.chat(system, user, schema) -> {"ok": True, "content": str, "latency_ms": int, ...meta}
                                              | {"ok": False, "error": "planner_timeout" | "planner_unavailable" |
                                                 "planner_truncated" | "planner_incomplete", "detail": str, ...meta}

Default OFF: it runs only when EVE_PLANNER_PROVIDER=openai_plan selects it (provider.select) and Hunter has signed in
with eve-brain-login. No fallback of any kind lives here (S104): an error goes back to the Planner, whose local
validators and local skills carry on. The planner's JSON schema becomes one function tool ("answer"); its arguments
are the content (function tools are what the plan-route docs confirm; text.format json_schema is not confirmed there).
meta on every reply: provider, generation, model, billing, status (connected / not_eligible / limit_reached / consent /
unavailable / signed_out / incomplete / bad_request / no_model). Stale plans are the planner's (mind.plan_and_run).
"""
import asyncio
import os
import time

from .auth import PLAN_SCOPE, Auth
from .net import urllib_transport
from .responses import Client
from .store import Store

NAME = "openai_plan"
GENERATION = "openai-plan-1"            # bump when the request shape or the mapping changes
BILLING = "chatgpt_plan"


class OpenAIPlanProvider:
    def __init__(self, store=None, transport=urllib_transport, model=None, timeout_s=60.0, sleep=time.sleep):
        self.store = store or Store()
        self.auth = Auth(self.store, transport)
        self.client = Client(self.auth, transport, sleep=sleep)
        self.timeout_s = timeout_s
        self._model = model

    @property
    def model(self):
        return self._model or os.environ.get("EVE_OPENAI_PLAN_MODEL") or (self.store.account() or {}).get("model")

    @property
    def generation(self):
        return f"{GENERATION}:{self.model}"

    def available(self):
        """Signed in with plan usage granted and a model chosen. Says nothing about eligibility or limits - only a
        request can show those, and its error says which."""
        rec = self.store.account() or {}
        return bool(self.model and (rec.get("access_token") or rec.get("refresh_token")) and
                    PLAN_SCOPE in (rec.get("scope") or "").split())

    def meta(self, state):
        return {"provider": NAME, "generation": self.generation, "model": self.model, "billing": BILLING,
                "status": state}

    async def chat(self, system, user, schema):
        t0 = time.monotonic()
        if not self.model:
            return {"ok": False, "error": "planner_unavailable", "detail": "no ChatGPT model chosen (eve-brain-login)",
                    **self.meta("no_model")}
        tools = [{"type": "function", "name": "answer", "description": "Give the answer as JSON matching the schema.",
                  "parameters": schema}] if schema else None
        r = await asyncio.to_thread(self.client.respond, self.model, system, [{"role": "user", "content": user}],
                                    tools, "required" if tools else None, self.timeout_s)
        ms = int((time.monotonic() - t0) * 1000)
        if r["ok"]:
            call = next((c for c in r["tool_calls"] if c["name"] == "answer"), None)
            content = call["raw_arguments"] if call else r["text"]
            if schema and (call is None or not isinstance(call["arguments"], dict)):
                return {"ok": False, "error": "malformed_output", "detail": "no JSON answer from the model",
                        "latency_ms": ms, **self.meta("connected"), "content": (content or "")[:500]}
            return {"ok": True, "content": content or "", "latency_ms": ms, **self.meta("connected"),
                    "model": r["model"] or self.model}
        e = r["error"]
        kind = "planner_incomplete" if e["kind"] == "incomplete" else \
            "planner_timeout" if "timed out" in e["message"].lower() or e["code"] == "TimeoutError" else \
            "planner_unavailable"
        if e["kind"] == "incomplete" and "max_output_tokens" in str(e["code"]):
            kind = "planner_truncated"
        return {"ok": False, "error": kind, "detail": f"{e['kind']}: {e['code']} {e['message']}".strip(),
                "latency_ms": ms, **self.meta(e["kind"]), "retry_after_s": e.get("retry_after_s")}

    def status(self):
        rec = self.store.account() or {}
        signed = bool(rec.get("access_token") or rec.get("refresh_token"))
        return {"provider": NAME, "signed_in": signed, "plan_usage_granted": PLAN_SCOPE in (rec.get("scope") or ""),
                "account": rec.get("email"), "model": self.model, "billing": BILLING, "generation": self.generation,
                "expires_at": rec.get("expires_at")}
