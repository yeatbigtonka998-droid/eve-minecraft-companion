"""`eve-brain-login` / `eve-brain-logout` / `eve-brain-status` (also `python -m eve_mind.hosted login|logout|status`).

login: opens OpenAI's own sign-in page in the default browser ("Continue with ChatGPT"), receives the answer on
http://127.0.0.1:<port>/auth/callback, checks that plan usage was GRANTED, lists the account's models, chooses one,
sends ONE small planning request with no game connection (nothing can be dispatched: this command never talks to
Eve's hub), then prints connection status, model and billing mode. Tokens are never printed.
"""
import argparse
import asyncio
import os
import sys
import webbrowser

from .auth import DEFAULT_PORT, Auth, AuthError, Loopback
from .openai_plan import OpenAIPlanProvider
from .store import Store

USAGE = "ChatGPT -> Settings -> Usage (per-app limits; Plus's five-hour limit is shared with your other ChatGPT apps)"
CHECK_SCHEMA = {"type": "object", "properties": {"steps": {"type": "array", "items": {"type": "string"}, "maxItems": 3}},
                "required": ["steps"], "additionalProperties": False}
MESSAGES = {
    "denied": "Sign-in was cancelled in the browser. Nothing was saved.",
    "scope_not_granted": "ChatGPT did not grant plan usage to Eve. Nothing was saved; nothing is billed.",
    "state_mismatch": "The sign-in answer did not belong to this attempt. Run eve-brain-login again.",
    "client_mismatch": "The sign-in answered for a different app registration. Run eve-brain-login again.",
    "identity_mismatch": "That is a different ChatGPT account than Eve's. Run eve-brain-logout first.",
    "timeout": "No answer from the browser in time. Run eve-brain-login again.",
    "network": "OpenAI could not be reached. Check the internet connection and try again.",
}
STATE_LINES = {
    "not_eligible": "Signed in, but this ChatGPT account is not eligible for app plan usage. Nothing is billed and "
                    "Eve keeps her local brain.",
    "limit_reached": "Signed in, but your ChatGPT usage limit is reached for now. Try later; manage it at " + USAGE + ".",
    "consent": "Signed in, but plan usage was not authorized. Run eve-brain-login again and allow it.",
    "unavailable": "Signed in, but OpenAI's plan route is unavailable right now. Try again in a few minutes.",
}


def out(line=""):
    print(line, flush=True)


def login(argv=None, store=None, transport=None, opener=webbrowser.open, loopback=Loopback):
    a = argparse.ArgumentParser(prog="eve-brain-login", description="Connect Eve's brain to your ChatGPT plan.")
    a.add_argument("--port", type=int, default=DEFAULT_PORT)
    a.add_argument("--model", help="model slug to use (default: the first one your account lists)")
    a.add_argument("--no-browser", action="store_true", help="print the sign-in link instead of opening it")
    a.add_argument("--timeout", type=float, default=300.0)
    args = a.parse_args(argv)
    store = store or Store()
    kw = {"transport": transport} if transport else {}
    auth = Auth(store, **kw)
    out("Eve: Continue with ChatGPT")
    out("You're using your ChatGPT plan: Eve's planning requests count against your ChatGPT usage, never a credit or "
        "API key.")
    try:
        lb = loopback(args.port)
        pending = auth.begin(lb.port)
        url = auth.authorize_url(pending)
        if args.no_browser or not opener(url):
            out("Open this link in your browser to sign in:")
            out(url)
        else:
            out("Your browser opened OpenAI's sign-in page. Sign in and allow Eve, then come back here.")
        rec = auth.finish(pending, lb.wait(args.timeout))
    except AuthError as e:
        out(MESSAGES.get(e.kind, f"Sign-in failed ({e.kind}): {e}"))
        return 2
    out(f"Signed in as {rec.get('email') or rec['sub']}. Plan usage: granted.")
    if not os.environ.get("EVE_BRAIN_HOME"):       # foundation: one fixed folder, or two sessions sign in twice
        out(f"Saved in {store.dir}. Set EVE_BRAIN_HOME={store.dir} where Eve's mind starts, so it finds this sign-in.")
    p = OpenAIPlanProvider(store, **kw)
    m = p.client.models()
    if not m["ok"]:
        out(STATE_LINES.get(m["error"]["kind"], f"Could not list models: {m['error']['code']} {m['error']['message']}"))
        return 3
    slugs = [x["slug"] for x in m["models"]]
    out("Models on this account: " + (", ".join(slugs) or "(none listed)"))
    model = args.model or os.environ.get("EVE_OPENAI_PLAN_MODEL") or (rec.get("model") if rec.get("model") in slugs
                                                                       else None) or (slugs[0] if slugs else None)
    if not model or model not in slugs:
        out(f"Model {model!r} is not on this account; run again with --model and one of the listed ones.")
        return 3
    rec = store.account()
    rec["model"] = model
    store.save_account(rec)
    r = asyncio.run(p.chat("You plan for Eve, a Minecraft companion. This is a connection check: game actions are "
                           "disconnected, so just answer.", "Plan how to greet the player, in at most 3 short steps.",
                           CHECK_SCHEMA))
    out()
    if r["ok"]:
        out(f"Connection: connected ({r['latency_ms']} ms for one planning request)")
    else:
        out("Connection: " + STATE_LINES.get(r["status"], f"not working ({r['detail']})"))
    out(f"Model: {model}")
    out("Billing: your ChatGPT plan (no API key, no credits, no fallback). Manage usage: " + USAGE)
    out("Eve still uses her local brain until EVE_PLANNER_PROVIDER=openai_plan is set where her mind runs.")
    return 0 if r["ok"] else 4


def logout(argv=None, store=None, transport=None):
    argparse.ArgumentParser(prog="eve-brain-logout", description="Disconnect Eve from ChatGPT.").parse_args(argv)
    kw = {"transport": transport} if transport else {}
    r = Auth(store or Store(), **kw).sign_out()
    out("Signed out: local tokens removed." + (" ChatGPT revoked the token." if r["revoked"] else
                                                f" Revocation not confirmed ({r['detail']})."))
    return 0


def status(argv=None, store=None):
    s = OpenAIPlanProvider(store or Store()).status()
    out(f"Signed in: {'yes' if s['signed_in'] else 'no'}" + (f" as {s['account']}" if s["account"] else ""))
    out(f"Plan usage granted: {'yes' if s['plan_usage_granted'] else 'no'}; model: {s['model'] or '(none)'}; "
        f"billing: ChatGPT plan")
    return 0


def main_login():
    sys.exit(login())


def main_logout():
    sys.exit(logout())


def main_status():
    sys.exit(status())
