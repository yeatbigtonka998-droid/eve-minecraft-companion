"""The plan-usage inference route: POST https://api.openai.com/v1/responses with the OAuth access token.

Every request: store:false, stream:true, the full context in `input`, `instructions` (no role:"system" items), no
temperature / max_output_tokens / previous_response_id / the other unsupported fields. Function tools only. Success
only on response.completed; response.failed / response.incomplete / a cut stream are errors.

Error classes (developers.openai.com/siwc errors-and-recovery): not_eligible and limit_reached and consent and
bad_request are final (no retry, no other billing route - there is none); auth gets one forced refresh; unavailable
(503s, network) gets bounded backoff: at most RETRIES more tries, waits of 1 s then 2 s, Retry-After honoured up to
10 s.
"""
import json
import time

from .auth import AuthError
from .net import NetError, get_json, urllib_transport

API = "https://api.openai.com/v1"
FORBIDDEN = {"background", "conversation", "max_output_tokens", "max_tool_calls", "metadata", "moderation",
             "multi_agent", "prompt", "prompt_cache_retention", "safety_identifier", "temperature", "top_logprobs",
             "top_p", "truncation", "user", "previous_response_id"}
RETRIES = 2
MAX_WAIT_S = 10.0
FINAL = ("not_eligible", "limit_reached", "consent", "bad_request", "forbidden", "signed_out")


def classify(status, body):
    """(kind, code, message, param) from an error answer (HTTP status + JSON body) or a response.failed error."""
    err = body.get("error") if isinstance(body, dict) else None
    err = err if isinstance(err, dict) else {}
    code = str(err.get("code") or err.get("type") or "")
    msg = str(err.get("message") or (body.get("detail") if isinstance(body, dict) else "") or "")
    param = err.get("param")
    if code.endswith("user_not_eligible") or code.endswith("route_not_supported"):
        return "not_eligible", code, msg, param
    if code.endswith("usage_limit_exceeded"):
        return "limit_reached", code, msg, param
    if code == "chatpass_v2_scope_not_authorized" or code.endswith("invalid_authorization_context"):
        return "consent", code, msg, param
    if code.endswith("unsupported_capability"):
        return "bad_request", code, msg, param
    if code.endswith("invalid_user") or status == 401:
        return "auth", code or "http_401", msg, param
    if code.endswith("usage_unavailable") or code.endswith("user_unavailable") or status in (500, 502, 503, 504):
        return "unavailable", code or f"http_{status}", msg, param
    if status == 429:
        return "limit_reached", code or "http_429", msg, param
    if status == 403:
        return "forbidden", code or "http_403", msg, param
    return "bad_request", code or f"http_{status}", msg, param


def error(kind, code, message, param=None, retry_after_s=None):
    return {"ok": False, "error": {"kind": kind, "code": code, "message": message[:300], "param": param,
                                   "retry_after_s": retry_after_s}}


def parse_stream(lines):
    """Server-sent events -> the completed response object, or an error dict. Only response.completed counts."""
    event, data = None, []
    for line in lines:
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif line == "" and data:
            raw, data = "\n".join(data), []
            if raw == "[DONE]":
                continue
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            kind = ev.get("type") or event
            if kind == "response.completed":
                return {"ok": True, "response": ev.get("response") or {}}
            if kind == "response.failed":
                r = ev.get("response") or {}
                k, code, msg, param = classify(200, {"error": r.get("error") or {}})
                return error(k, code, msg or "the response failed", param)
            if kind == "response.incomplete":
                why = ((ev.get("response") or {}).get("incomplete_details") or {}).get("reason") or "incomplete"
                return error("incomplete", why, f"the model stopped early ({why})")
            if kind == "error":
                k, code, msg, param = classify(200, {"error": ev.get("error") or ev})
                return error(k, code, msg or "stream error", param)
    return error("unavailable", "stream_cut", "the answer stream ended before the response completed")


def read_output(resp):
    """Text and function calls from a completed response's output items."""
    text, calls = [], []
    for item in resp.get("output") or []:
        if item.get("type") == "message":
            text += [c.get("text", "") for c in item.get("content") or [] if c.get("type") == "output_text"]
        elif item.get("type") == "function_call":
            try:
                args = json.loads(item.get("arguments") or "{}")
            except ValueError:
                args = None
            calls.append({"name": item.get("name"), "arguments": args, "raw_arguments": item.get("arguments")})
    return "".join(text), calls


class Client:
    def __init__(self, auth, transport=urllib_transport, api=API, sleep=time.sleep):
        self.auth, self.transport, self.api, self.sleep = auth, transport, api, sleep

    def models(self):
        """[{slug, display_name}] the signed-in account may use (visibility == "list"), or an error dict."""
        try:
            r = get_json(self.transport, self.api + "/models", bearer=self.auth.access_token())
        except AuthError as e:
            return error("signed_out" if e.kind in ("signed_out", "sign_in_again") else "auth", e.kind, str(e))
        except NetError as e:
            return error("unavailable", "network", str(e))
        if r.status != 200:
            return error(*classify(r.status, r.json()))
        j = r.json()
        rows = j.get("models") if isinstance(j.get("models"), list) else j.get("data") or []
        return {"ok": True, "models": [{"slug": m.get("slug") or m.get("id"), "display_name": m.get("display_name")
                                        or m.get("slug") or m.get("id")} for m in rows
                                       if m.get("visibility", "list") == "list"]}

    def body(self, model, instructions, input_items, tools=None, tool_choice=None):
        b = {"model": model, "instructions": instructions, "input": input_items, "store": False, "stream": True}
        if tools:
            b["tools"] = tools
            if tool_choice:
                b["tool_choice"] = tool_choice
        bad = FORBIDDEN & set(b) or [i for i in input_items if isinstance(i, dict) and i.get("role") == "system"]
        if bad:
            raise ValueError(f"not allowed on the plan route: {bad}")
        return b

    def respond(self, model, instructions, input_items, tools=None, tool_choice=None, timeout_s=60.0):
        """{ok, text, tool_calls, model, response_id} or an error dict (see module doc for retries)."""
        body = json.dumps(self.body(model, instructions, input_items, tools, tool_choice)).encode()
        refreshed, tries = False, 0
        while True:
            try:
                token = self.auth.access_token()
            except AuthError as e:
                return error("signed_out" if e.kind in ("signed_out", "sign_in_again") else "auth", e.kind, str(e))
            except Exception as e:                 # a held refresh lock (TimeoutError) and the like
                return error("unavailable", type(e).__name__, str(e))
            try:
                r = self.transport("POST", self.api + "/responses",
                                   {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                    "Accept": "text/event-stream"}, body, True, timeout_s)
            except NetError as e:
                out, wait = error("unavailable", "network", str(e)), None
            else:
                if r.status == 200 and r.lines is not None:
                    out, wait = parse_stream(r.lines), None
                else:
                    out = error(*classify(r.status, r.json() if r.body else {}))
                    try:
                        wait = float(r.headers.get("retry-after")) if r.headers.get("retry-after") else None
                    except ValueError:
                        wait = None
                    out["error"]["retry_after_s"] = wait
            if out["ok"]:
                text, calls = read_output(out["response"])
                return {"ok": True, "text": text, "tool_calls": calls, "model": out["response"].get("model") or model,
                        "response_id": out["response"].get("id")}
            kind = out["error"]["kind"]
            if kind == "auth" and not refreshed:
                refreshed = True
                try:
                    self.auth.refresh(token)
                except AuthError as e:
                    return error("signed_out", e.kind, str(e))
                continue
            if kind != "unavailable" or tries >= RETRIES:
                return out
            tries += 1
            self.sleep(min(MAX_WAIT_S, wait if wait is not None else float(tries)))
