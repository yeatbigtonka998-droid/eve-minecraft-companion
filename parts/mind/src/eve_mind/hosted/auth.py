"""Sign in with ChatGPT for an open-source, locally hosted app (developers.openai.com/siwc/token-sharing-open-source,
as summarised in records/HOSTED-BRAIN-SPIKE.md). First sign-in registers dynamically (client_id=dynamic_agent_client);
the callback carries the issued client id (oaiapp_...), saved and reused for every later sign-in. No client secret,
no API key.

Flow: Pending (fresh state, nonce, PKCE S256 verifier; redirect http://127.0.0.1:<port>/auth/callback) -> the system
browser opens authorize_url -> Loopback receives the callback -> finish() checks state / client id, exchanges the code,
verifies the ID token, requires the GRANTED scope chatgpt.tokens.use.direct, keeps the identity, saves the record.
access_token() refreshes (serialized, rotating) when needed; sign_out() revokes the refresh token, then clears.
"""
import base64
import hashlib
import http.server
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse

from . import idtoken
from .net import NetError, form, get_json, urllib_transport
from .store import Store

ISSUER = "https://auth.openai.com"
AUTHORIZE = ISSUER + "/api/accounts/authorize"            # the docs' endpoints; discovery wins where it names them
TOKEN = ISSUER + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
DYNAMIC = "dynamic_agent_client"
APP_NAME = "Eve"
DEFAULT_PORT = 1455
REFRESH_MARGIN_S = 300
SIGN_IN_AGAIN = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
                 "refresh_token_invalidated", "refresh_token_reused"}


class AuthError(Exception):
    """kind: signed_out | denied | state_mismatch | client_mismatch | sign_in_again | scope_not_granted |
    identity_mismatch | id_token | config | network | timeout | server."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class Pending:
    """One sign-in attempt: everything fresh, nothing reused from an earlier attempt."""

    def __init__(self, client_id, host_id, port):
        self.client_id, self.host_id = client_id, host_id
        self.state, self.nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.verifier, self.challenge = pkce_pair()
        self.redirect_uri = f"http://127.0.0.1:{port}/auth/callback"


class Auth:
    def __init__(self, store=None, transport=urllib_transport, issuer=ISSUER, clock=time.time):
        self.store, self.transport, self.issuer, self.clock = store or Store(), transport, issuer, clock
        self._conf = None

    # ---- discovery
    def conf(self):
        if self._conf is None:
            try:
                r = get_json(self.transport, self.issuer + "/.well-known/openid-configuration")
            except NetError as e:
                raise AuthError("network", f"OpenAI sign-in is unreachable: {e}") from None
            c = r.json() if r.status == 200 else {}
            if not c.get("jwks_uri"):
                raise AuthError("server", f"OpenAI's sign-in configuration was not readable (HTTP {r.status})")
            self._conf = c
        return self._conf

    # ---- sign-in
    def begin(self, port):
        acct = self.store.account() or {}
        return Pending(acct.get("client_id") or DYNAMIC, self.store.host_id(), port)

    def authorize_url(self, p):
        q = {"response_type": "code", "client_id": p.client_id, "redirect_uri": p.redirect_uri, "scope": SCOPES,
             "state": p.state, "nonce": p.nonce, "code_challenge": p.challenge, "code_challenge_method": "S256",
             "resource": RESOURCE, "ext_agent_host_id": p.host_id}
        if p.client_id == DYNAMIC:
            q["agent_name_hint"] = APP_NAME
        return (self.conf().get("authorization_endpoint") or AUTHORIZE) + "?" + urlencode(q)

    def finish(self, p, params):
        """The callback's query -> the saved account record (tokens redacted by the caller before any print)."""
        get = lambda k: (params.get(k) or [None])[0] if isinstance(params.get(k), list) else params.get(k)  # noqa
        if get("state") != p.state:
            raise AuthError("state_mismatch", "the sign-in answer did not match this attempt; start again")
        if get("error"):
            raise AuthError("denied", f"sign-in was not completed ({get('error')})")
        issued = get("client_id")
        if p.client_id != DYNAMIC and issued and issued != p.client_id:
            raise AuthError("client_mismatch", "the sign-in answered for a different app registration")
        client_id = issued if p.client_id == DYNAMIC else p.client_id
        if not client_id or client_id == DYNAMIC or not get("code"):
            raise AuthError("server", "the sign-in answer had no code or no issued client id")
        tok = self._token({"grant_type": "authorization_code", "client_id": client_id, "code": get("code"),
                           "code_verifier": p.verifier, "redirect_uri": p.redirect_uri, "resource": RESOURCE})
        claims = self._claims(tok.get("id_token"), client_id, p.nonce)
        granted = set((tok.get("scope") or get("scope") or "").split())
        if PLAN_SCOPE not in granted:
            raise AuthError("scope_not_granted", "ChatGPT did not grant plan usage (chatgpt.tokens.use.direct) - "
                                                 "nothing was saved")
        old = self.store.account() or {}
        if old.get("sub") and old.get("client_id") == client_id and old["sub"] != claims["sub"]:
            raise AuthError("identity_mismatch", "this is a different ChatGPT account than the one Eve has; run "
                                                 "eve-brain-logout first")
        rec = {**{k: v for k, v in old.items() if k in ("model",)}, "client_id": client_id, "sub": claims["sub"],
               "email": claims.get("email"), "host_id": p.host_id, "signed_in_at": int(self.clock())}
        rec.update(self._tokens(tok, granted))
        self.store.save_account(rec)
        return rec

    def _token(self, fields):
        try:
            r = form(self.transport, self.conf().get("token_endpoint") or TOKEN, fields)
        except NetError as e:
            raise AuthError("network", f"OpenAI sign-in is unreachable: {e}") from None
        j = r.json()
        if r.status != 200:
            code = j.get("error") or f"http_{r.status}"
            if code in SIGN_IN_AGAIN:
                raise AuthError("sign_in_again", f"sign in again ({code})")
            if code == "invalid_client":
                raise AuthError("config", "the app registration was refused (invalid_client)")
            raise AuthError("server", f"token endpoint answered {r.status} ({code})")
        if not j.get("access_token"):
            raise AuthError("server", "token endpoint answered without an access token")
        return j

    def _claims(self, token, client_id, nonce):
        if not token:
            raise AuthError("id_token", "no ID token in the sign-in answer")
        try:
            jwks = get_json(self.transport, self.conf()["jwks_uri"]).json()
            return idtoken.verify(token, jwks, self.conf().get("issuer") or self.issuer, client_id, nonce,
                                  now=self.clock())
        except idtoken.IdTokenError as e:
            raise AuthError("id_token", str(e)) from None
        except NetError as e:
            raise AuthError("network", f"OpenAI sign-in keys are unreachable: {e}") from None

    def _tokens(self, tok, granted):
        now = self.clock()
        out = {"access_token": tok["access_token"], "expires_at": int(now + int(tok.get("expires_in") or 3600)),
               "scope": " ".join(sorted(granted))}
        if tok.get("refresh_token"):
            out["refresh_token"] = tok["refresh_token"]
        if tok.get("id_token"):
            out["id_token"] = tok["id_token"]
        if tok.get("earliest_refresh_at"):
            out["earliest_refresh_at"] = int(tok["earliest_refresh_at"])
        return out

    # ---- use
    def access_token(self):
        """A usable access token, refreshed (serialized across processes) when within REFRESH_MARGIN_S of expiry."""
        rec = self.store.account()
        if not rec or not rec.get("access_token"):
            raise AuthError("signed_out", "Eve is not signed in to ChatGPT (run eve-brain-login)")
        if rec.get("expires_at", 0) - REFRESH_MARGIN_S > self.clock():
            return rec["access_token"]
        return self.refresh(rec["access_token"])

    def refresh(self, stale=None):
        """Rotate the refresh token. Under the lock, a token another process already renewed is used as it is."""
        with self.store.lock():
            rec = self.store.account() or {}
            if rec.get("access_token") and rec["access_token"] != stale and \
                    rec.get("expires_at", 0) - REFRESH_MARGIN_S > self.clock():
                return rec["access_token"]
            if not rec.get("refresh_token"):
                raise AuthError("signed_out", "Eve is not signed in to ChatGPT (run eve-brain-login)")
            if rec.get("earliest_refresh_at", 0) > self.clock() and rec.get("expires_at", 0) > self.clock():
                return rec["access_token"]
            try:
                tok = self._token({"grant_type": "refresh_token", "client_id": rec["client_id"],
                                   "refresh_token": rec["refresh_token"], "resource": RESOURCE})
            except AuthError as e:
                if e.kind == "sign_in_again":
                    self.store.clear_tokens(str(e))
                raise
            granted = set((tok.get("scope") or rec.get("scope") or "").split())
            rec.update(self._tokens(tok, granted))
            if PLAN_SCOPE not in granted:
                self.store.save_account(rec)
                raise AuthError("scope_not_granted", "plan usage is no longer granted; sign in again")
            self.store.save_account(rec)
            return rec["access_token"]

    def sign_out(self):
        """Revoke the refresh token (an empty 200 = done), then clear local tokens whatever the answer."""
        rec = self.store.account() or {}
        revoked, why = False, "no refresh token"
        if rec.get("refresh_token"):
            try:
                url = self.conf().get("revocation_endpoint")
                if url:
                    r = form(self.transport, url, {"token": rec["refresh_token"], "token_type_hint": "refresh_token",
                                                   "client_id": rec.get("client_id", "")})
                    revoked, why = r.status == 200, f"HTTP {r.status}"
                else:
                    why = "no revocation endpoint in OpenAI's configuration"
            except (AuthError, NetError) as e:
                why = str(e)
        self.store.clear_tokens("signed out")
        return {"revoked": revoked, "detail": why}


class Loopback:
    """The callback listener on 127.0.0.1 (never localhost), path /auth/callback; started before the browser opens.
    Port DEFAULT_PORT, else the next free one (only the port may change between attempts)."""

    def __init__(self, port=DEFAULT_PORT, tries=10):
        self.result, self._done = None, threading.Event()
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                u = urlparse(self.path)
                if u.path != "/auth/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                outer.result = parse_qs(u.query)
                ok = "error" not in outer.result
                page = ("<h2>Eve is connected to ChatGPT.</h2><p>You can close this tab and go back to the terminal.</p>"
                        if ok else "<h2>Sign-in was not completed.</h2><p>You can close this tab.</p>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(page.encode())
                outer._done.set()

            def log_message(self, *a):     # the query holds the one-time code: never logged
                pass

        for p in range(port, port + tries):
            try:
                self.server = http.server.HTTPServer(("127.0.0.1", p), H)
                break
            except OSError:
                continue
        else:
            raise AuthError("config", f"no free port for the sign-in callback in {port}..{port + tries - 1}")
        self.port = self.server.server_address[1]
        self._t = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._t.start()

    def wait(self, timeout_s=300.0):
        try:
            if not self._done.wait(timeout_s):
                raise AuthError("timeout", f"no sign-in answer within {timeout_s:.0f} s")
            return self.result
        finally:
            self.server.shutdown()
            self.server.server_close()
