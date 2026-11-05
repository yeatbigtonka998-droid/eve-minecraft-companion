"""S109 track 2: the OpenAI plan-usage client against a FAKE OpenAI (no network, no sign-in, no real token). The fake
plays the browser + auth server + API: discovery, JWKS (a throwaway RSA key made here), authorize -> callback, code
exchange (checks PKCE and the redirect), rotating refresh, revocation, /v1/models and a streaming /v1/responses."""
import asyncio
import base64
import hashlib
import json
import random
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse

import pytest

from eve_mind.hosted import auth as A
from eve_mind.hosted import idtoken, login as L, responses as R
from eve_mind.hosted.net import NetError, Reply
from eve_mind.hosted.openai_plan import OpenAIPlanProvider
from eve_mind.hosted.store import Store, redact


# ---- a throwaway RSA key (tests only) ---------------------------------------------------------------------------
def _prime(bits, rnd):
    while True:
        n = rnd.getrandbits(bits) | (1 << bits - 1) | 1
        if all(pow(a, n - 1, n) == 1 for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)):
            return n


def _key(seed=7):
    rnd = random.Random(seed)
    e = 65537
    while True:
        p, q = _prime(512, rnd), _prime(512, rnd)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e:
            return p * q, e, pow(e, -1, phi)


N, E, D = _key()


def b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign(claims, kid="k1", d=D, n=N, alg="RS256"):
    h = b64(json.dumps({"alg": alg, "kid": kid}).encode())
    p = b64(json.dumps(claims).encode())
    k = (n.bit_length() + 7) // 8
    em = b"\x00\x01" + b"\xff" * (k - 3 - 19 - 32) + b"\x00" + idtoken.SHA256_DIGEST_INFO + \
        hashlib.sha256(f"{h}.{p}".encode()).digest()
    return f"{h}.{p}.{b64(pow(int.from_bytes(em, 'big'), d, n).to_bytes(k, 'big'))}"


JWKS = {"keys": [{"kty": "RSA", "kid": "k1", "n": b64(N.to_bytes(128, "big")), "e": b64(E.to_bytes(3, "big"))}]}


# ---- the fake OpenAI -----------------------------------------------------------------------------------------------
class FakeOpenAI:
    def __init__(self, clock):
        self.clock, self.calls, self.codes, self.sub, self.email = clock, [], {}, "user-1", "hunter@example.test"
        self.issued = "oaiapp_test123"
        self.grant_scope = A.SCOPES
        self.refresh_n, self.refresh_tokens, self.revoked = 0, set(), []
        self.models = [{"slug": "gpt-test", "display_name": "GPT Test", "visibility": "list"},
                       {"slug": "hidden", "visibility": "hide"}]
        self.responses = []          # per call: Reply, or a list of SSE events, or an Exception
        self.refresh_error = None
        self.revoke_status = 200
        self.bad_sig = False

    def browser(self, url, deny=False, client_id=None, state=None):
        """The user signs in: OpenAI redirects to the app's loopback with these query params."""
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if deny:
            return {"error": ["access_denied"], "state": [q["state"]]}
        code = f"code-{len(self.codes)}"
        self.codes[code] = q
        cid = client_id or (self.issued if q["client_id"] == A.DYNAMIC else q["client_id"])
        return {"code": [code], "state": [state or q["state"]], "client_id": [cid], "scope": [self.grant_scope]}

    def _tokens(self, client_id, nonce=None):
        self.refresh_n += 1
        rt = f"rt-{self.refresh_n}"
        self.refresh_tokens.add(rt)
        t = {"access_token": f"at-{self.refresh_n}", "refresh_token": rt, "expires_in": 3600, "scope": self.grant_scope,
             "token_type": "Bearer"}
        if nonce is not None:
            t["id_token"] = sign({"iss": A.ISSUER, "aud": client_id, "sub": self.sub, "email": self.email,
                                  "exp": self.clock() + 3600, "nonce": nonce}, d=D + 2 if self.bad_sig else D)
        return t

    def __call__(self, method, url, headers=None, body=None, stream=False, timeout=60.0):
        self.calls.append((method, url, headers or {}, body))
        u = urlparse(url)
        if url == A.ISSUER + "/.well-known/openid-configuration":
            return Reply(200, body=json.dumps({"issuer": A.ISSUER, "jwks_uri": A.ISSUER + "/jwks",
                                               "authorization_endpoint": A.AUTHORIZE, "token_endpoint": A.TOKEN,
                                               "revocation_endpoint": A.ISSUER + "/revoke"}).encode())
        if url == A.ISSUER + "/jwks":
            return Reply(200, body=json.dumps(JWKS).encode())
        if url == A.TOKEN:
            f = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            if f["grant_type"] == "authorization_code":
                q = self.codes.pop(f["code"], None)
                ch = b64(hashlib.sha256(f["code_verifier"].encode()).digest())
                if not q or ch != q["code_challenge"] or f["redirect_uri"] != q["redirect_uri"] or \
                        f["resource"] != A.RESOURCE:
                    return Reply(400, body=b'{"error": "invalid_grant"}')
                return Reply(200, body=json.dumps(self._tokens(f["client_id"], q["nonce"])).encode())
            if f["grant_type"] == "refresh_token":
                if self.refresh_error:
                    return Reply(400, body=json.dumps({"error": self.refresh_error}).encode())
                if f["refresh_token"] not in self.refresh_tokens or "scope" in f:
                    return Reply(400, body=b'{"error": "refresh_token_reused"}')
                self.refresh_tokens.discard(f["refresh_token"])      # rotating: the old one is spent
                return Reply(200, body=json.dumps(self._tokens(f["client_id"])).encode())
        if url == A.ISSUER + "/revoke":
            self.revoked.append(parse_qs(body.decode())["token"][0])
            return Reply(self.revoke_status, body=b"")
        if u.path == "/v1/models":
            return Reply(200, body=json.dumps({"models": self.models}).encode())
        if u.path == "/v1/responses":
            nxt = self.responses.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            if isinstance(nxt, Reply):
                return nxt
            return Reply(200, lines=iter(sse(nxt)))
        return Reply(404, body=b"{}")


def sse(events):
    out = []
    for ev in events:
        out += [f"event: {ev['type']}", "data: " + json.dumps(ev), ""]
    return out


def completed(text="", call=None, model="gpt-test"):
    output = []
    if text:
        output.append({"type": "message", "content": [{"type": "output_text", "text": text}]})
    if call:
        output.append({"type": "function_call", "name": call[0], "arguments": json.dumps(call[1]), "call_id": "c1"})
    return [{"type": "response.created", "response": {"id": "r1"}},
            {"type": "response.output_text.delta", "delta": text[:3]},
            {"type": "response.completed", "response": {"id": "r1", "model": model, "output": output}}]


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("eve_mind.hosted.store.protect", lambda folder: None)
    clock = Clock()
    fake = FakeOpenAI(clock)
    store = Store(tmp_path / "brain")
    auth = A.Auth(store, fake, clock=clock)
    return clock, fake, store, auth


def sign_in(auth, fake, **kw):
    p = auth.begin(1455)
    return auth.finish(p, fake.browser(auth.authorize_url(p), **kw)), p


# ---- auth ----------------------------------------------------------------------------------------------------------
def test_pkce_state_nonce_fresh_and_s256(env):
    _, _, store, auth = env
    p1, p2 = auth.begin(1455), auth.begin(1456)
    assert p1.challenge == b64(hashlib.sha256(p1.verifier.encode()).digest()) and "=" not in p1.challenge
    assert len({p1.state, p2.state}) == 2 and p1.nonce != p2.nonce and p1.verifier != p2.verifier
    assert p1.host_id == p2.host_id == store.host_id() and p1.host_id.startswith("urn:uuid:")   # persisted first
    assert p1.redirect_uri == "http://127.0.0.1:1455/auth/callback" and p2.redirect_uri.endswith(":1456/auth/callback")


def test_authorize_url_first_sign_in_registers_dynamically(env):
    _, _, _, auth = env
    p = auth.begin(1455)
    q = {k: v[0] for k, v in parse_qs(urlparse(auth.authorize_url(p)).query).items()}
    assert auth.authorize_url(p).startswith(A.AUTHORIZE + "?")
    assert q["client_id"] == "dynamic_agent_client" and q["agent_name_hint"] == "Eve"
    assert q["response_type"] == "code" and q["code_challenge_method"] == "S256" and q["resource"] == A.RESOURCE
    assert set(q["scope"].split()) >= {"openid", "offline_access", "resource.invoke", "chatgpt.tokens.use.direct"}
    assert q["redirect_uri"] == "http://127.0.0.1:1455/auth/callback" and q["ext_agent_host_id"] == p.host_id
    assert "token" not in auth.authorize_url(p).lower().replace("chatgpt.tokens", "")


def test_sign_in_saves_issued_client_and_granted_scope_then_reuses_registration(env):
    _, fake, store, auth = env
    rec, _ = sign_in(auth, fake)
    saved = store.account()
    assert saved["client_id"] == "oaiapp_test123" and saved["sub"] == "user-1" and "chatgpt.tokens.use.direct" in \
        saved["scope"] and saved["access_token"] == "at-1" and saved["refresh_token"] == "rt-1"
    assert redact(saved)["access_token"] == "<set>" and "at-1" not in json.dumps(redact(saved))
    p = auth.begin(1455)                                           # a later sign-in reuses the issued client id
    q = parse_qs(urlparse(auth.authorize_url(p)).query)
    assert q["client_id"] == ["oaiapp_test123"] and "agent_name_hint" not in q
    sign_in(auth, fake)
    assert store.account()["refresh_token"] == "rt-2"


@pytest.mark.parametrize("case,kind", [
    ("state", "state_mismatch"), ("deny", "denied"), ("scope", "scope_not_granted"), ("client", "client_mismatch"),
    ("nonce", "id_token"), ("sig", "id_token"), ("identity", "identity_mismatch")])
def test_sign_in_refusals_save_nothing_new(env, case, kind):
    clock, fake, store, auth = env
    if case in ("client", "identity"):
        sign_in(auth, fake)                                         # a registration exists
    before = store.account()
    p = auth.begin(1455)
    url = auth.authorize_url(p)
    if case == "state":
        params = fake.browser(url, state="someone-else")
    elif case == "deny":
        params = fake.browser(url, deny=True)
    elif case == "client":
        params = fake.browser(url, client_id="oaiapp_other")
    else:
        if case == "scope":
            fake.grant_scope = "openid profile email offline_access"
        if case == "identity":
            fake.sub = "user-2"
        params = fake.browser(url)
        if case == "nonce":
            fake.codes[params["code"][0]]["nonce"] = "wrong"
        fake.bad_sig = case == "sig"                                  # signed with the wrong key
    with pytest.raises(A.AuthError) as e:
        auth.finish(p, params)
    assert e.value.kind == kind, e.value
    assert store.account() == before                                 # nothing saved or replaced


def test_id_token_checks():
    t = sign({"iss": A.ISSUER, "aud": "c", "sub": "s", "exp": 2e9, "nonce": "n"})
    assert idtoken.verify(t, JWKS, A.ISSUER, "c", "n", now=1e9)["sub"] == "s"
    for args, msg in [((JWKS, "https://evil", "c", "n"), "issuer"), ((JWKS, A.ISSUER, "x", "n"), "audience"),
                      ((JWKS, A.ISSUER, "c", "m"), "nonce"), (({"keys": []}, A.ISSUER, "c", "n"), "signature")]:
        with pytest.raises(idtoken.IdTokenError, match=msg):
            idtoken.verify(t, *args, now=1e9)
    with pytest.raises(idtoken.IdTokenError, match="expired"):
        idtoken.verify(t, JWKS, A.ISSUER, "c", "n", now=3e9)
    with pytest.raises(idtoken.IdTokenError, match="ES256"):
        idtoken.verify(sign({"sub": "s"}, alg="ES256"), JWKS, A.ISSUER, "c", "n", now=1e9)


def test_loopback_receives_the_callback_on_127_0_0_1_only():
    lb = A.Loopback(port=0)
    assert lb.server.server_address[0] == "127.0.0.1"
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{lb.port}/other", timeout=5)
    except urllib.error.HTTPError as e:
        assert e.code == 404
    page = urllib.request.urlopen(f"http://127.0.0.1:{lb.port}/auth/callback?code=c1&state=s1", timeout=5).read()
    assert b"connected" in page
    assert lb.wait(5) == {"code": ["c1"], "state": ["s1"]}


def test_refresh_rotates_serialized_and_sign_in_again_clears(env):
    clock, fake, store, auth = env
    sign_in(auth, fake)
    assert auth.access_token() == "at-1"
    clock.t += 3600 - 100                                            # inside the 300 s margin
    tokens = []
    threads = [threading.Thread(target=lambda: tokens.append(auth.access_token())) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert set(tokens) == {"at-2"} and fake.refresh_n == 2           # one refresh, not four (rotation never races)
    assert store.account()["refresh_token"] == "rt-2" and "rt-1" not in fake.refresh_tokens
    form_bodies = [parse_qs(b.decode()) for m, u, h, b in fake.calls if u == A.TOKEN]
    assert "scope" not in form_bodies[-1] and form_bodies[-1]["grant_type"] == ["refresh_token"]
    clock.t += 3600
    fake.refresh_error = "refresh_token_expired"
    with pytest.raises(A.AuthError) as e:
        auth.access_token()
    assert e.value.kind == "sign_in_again" and not store.account().get("access_token") and \
        store.account()["client_id"] == "oaiapp_test123"             # registration kept for the next sign-in
    with pytest.raises(A.AuthError) as e:
        auth.access_token()
    assert e.value.kind == "signed_out"


def test_sign_out_revokes_then_clears_even_if_revoke_fails(env):
    _, fake, store, auth = env
    sign_in(auth, fake)
    assert auth.sign_out() == {"revoked": True, "detail": "HTTP 200"} and fake.revoked == ["rt-1"]
    assert not store.account().get("refresh_token")
    sign_in(auth, fake)
    fake.revoke_status = 500
    r = auth.sign_out()
    assert r["revoked"] is False and not store.account().get("access_token")


# ---- responses ---------------------------------------------------------------------------------------------------
def client(env):
    _, fake, _, auth = env
    sleeps = []
    return R.Client(auth, fake, sleep=sleeps.append), sleeps


def test_request_shape_and_completed_stream(env):
    _, fake, _, auth = env
    sign_in(auth, fake)
    c, _ = client(env)
    fake.responses.append(completed(text="hi", call=("answer", {"steps": ["wave"]})))
    r = c.respond("gpt-test", "be Eve", [{"role": "user", "content": "plan"}],
                  tools=[{"type": "function", "name": "answer", "parameters": {"type": "object"}}], tool_choice="required")
    assert r["ok"] and r["text"] == "hi" and r["tool_calls"][0]["arguments"] == {"steps": ["wave"]}
    m, url, h, body = [x for x in fake.calls if x[1].endswith("/v1/responses")][-1]
    b = json.loads(body)
    assert url == "https://api.openai.com/v1/responses" and h["Authorization"] == "Bearer at-1"
    assert b["store"] is False and b["stream"] is True and b["instructions"] == "be Eve" and not (R.FORBIDDEN & set(b))
    with pytest.raises(ValueError):
        c.body("m", "x", [{"role": "system", "content": "no"}])


@pytest.mark.parametrize("answer,kind,calls", [
    (Reply(403, body=b'{"error": {"code": "subscription_sharing_user_not_eligible", "message": "no"}}'), "not_eligible", 1),
    (Reply(429, body=b'{"error": {"code": "subscription_sharing_usage_limit_exceeded"}}'), "limit_reached", 1),
    (Reply(403, body=b'{"error": {"code": "chatpass_v2_scope_not_authorized"}}'), "consent", 1),
    (Reply(400, body=b'{"error": {"code": "subscription_sharing_unsupported_capability", "param": "temperature"}}'),
     "bad_request", 1),
    ([{"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}}],
     "limit_reached", 1),
    ([{"type": "response.incomplete", "response": {"incomplete_details": {"reason": "content_filter"}}}], "incomplete", 1),
    (Reply(503, body=b'{"detail": "route unavailable"}'), "unavailable", 3),
    (NetError("timed out"), "unavailable", 3),
    ([{"type": "response.created", "response": {}}], "unavailable", 3),          # stream cut before completion
])
def test_error_classes_final_or_bounded_backoff(env, answer, kind, calls):
    _, fake, _, auth = env
    sign_in(auth, fake)
    c, sleeps = client(env)
    fake.responses += [answer] * calls
    r = c.respond("gpt-test", "x", [{"role": "user", "content": "y"}])
    assert not r["ok"] and r["error"]["kind"] == kind, r
    assert len([x for x in fake.calls if x[1].endswith("/v1/responses")]) == calls and fake.responses == []
    assert sleeps == ([1.0, 2.0] if calls == 3 else [])                 # bounded, and never another billing route


def test_retry_after_capped_and_401_refreshes_once(env):
    clock, fake, _, auth = env
    sign_in(auth, fake)
    c, sleeps = client(env)
    fake.responses += [Reply(503, {"Retry-After": "120"}, b"{}"), completed(text="ok")]
    assert c.respond("gpt-test", "x", [])["ok"] and sleeps == [10.0]
    fake.responses += [Reply(401, body=b'{"detail": "expired"}'), completed(text="ok")]
    r = c.respond("gpt-test", "x", [])
    assert r["ok"] and fake.refresh_n == 2                                # one forced refresh, then success
    fake.responses += [Reply(401, body=b"{}"), Reply(401, body=b"{}")]
    assert c.respond("gpt-test", "x", [])["error"]["kind"] == "auth"      # no refresh loop


def test_models_lists_visible_only(env):
    _, fake, _, auth = env
    sign_in(auth, fake)
    c, _ = client(env)
    assert c.models() == {"ok": True, "models": [{"slug": "gpt-test", "display_name": "GPT Test"}]}


# ---- provider ------------------------------------------------------------------------------------------------------
def test_provider_contract_default_off_and_generation_stamped(env):
    _, fake, store, auth = env
    p = OpenAIPlanProvider(store, fake, sleep=lambda s: None)
    assert not p.available()                                          # signed out: off
    sign_in(auth, fake)
    assert not p.available()                                          # no model chosen yet: off
    rec = store.account()
    rec["model"] = "gpt-test"
    store.save_account(rec)
    assert p.available() and p.model == "gpt-test"
    fake.responses.append(completed(call=("answer", {"steps": ["a"]})))
    r = asyncio.run(p.chat("sys", "user", {"type": "object"}))
    assert r["ok"] and json.loads(r["content"]) == {"steps": ["a"]} and isinstance(r["latency_ms"], int)
    assert r["generation"] == "openai-plan-1:gpt-test" and r["billing"] == "chatgpt_plan" and r["provider"] == "openai_plan"
    b = json.loads([x for x in fake.calls if x[1].endswith("/v1/responses")][-1][3])
    assert b["instructions"] == "sys" and b["input"] == [{"role": "user", "content": "user"}] and \
        b["tools"][0]["name"] == "answer" and b["tool_choice"] == "required"
    fake.responses.append(Reply(403, body=b'{"error": {"code": "subscription_sharing_user_not_eligible"}}'))
    r = asyncio.run(p.chat("sys", "user", {"type": "object"}))
    assert r == {**r, "ok": False, "error": "planner_unavailable", "status": "not_eligible"}
    fake.responses.append([{"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}}}])
    assert asyncio.run(p.chat("s", "u", None))["error"] == "planner_truncated"
    fake.responses += [NetError("timed out")] * 3
    assert asyncio.run(p.chat("s", "u", None))["error"] == "planner_timeout"
    fake.responses.append(completed(text="no tool used"))
    r = asyncio.run(p.chat("s", "u", {"type": "object"}))
    assert r["ok"] is False and r["error"] == "malformed_output"            # brain-speed's contract


def test_provider_matches_brain_speed_boundary_if_present():
    prov = pytest.importorskip("eve_mind.provider")
    fp = prov.FakeProvider(["x"])
    for attr in ("model", "available", "chat"):
        assert hasattr(fp, attr) and hasattr(OpenAIPlanProvider, attr)


# ---- CLI -----------------------------------------------------------------------------------------------------------
class FakeLoopback:
    def __init__(self, fake, **kw):
        self.fake, self.kw, self.port = fake, kw, 1455

    def __call__(self, port):
        return self

    def wait(self, timeout):
        return self.fake.browser(self.url, **self.kw)


def test_login_end_to_end_prints_status_model_billing_and_never_a_token(env, capsys):
    _, fake, store, _ = env
    lb = FakeLoopback(fake)
    opened = []

    def opener(url):
        opened.append(url)
        lb.url = url
        return True
    fake.responses.append(completed(call=("answer", {"steps": ["wave", "say hi"]})))
    rc = L.login([], store=store, transport=fake, opener=opener, loopback=lb)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert opened and opened[0].startswith(A.AUTHORIZE) and "Continue with ChatGPT" in out
    assert "Plan usage: granted" in out and "Connection: connected" in out and "Model: gpt-test" in out
    assert "Billing: your ChatGPT plan" in out and "Settings -> Usage" in out
    assert not any(t in out for t in ("at-1", "rt-1", "eyJ")) and store.account()["model"] == "gpt-test"
    hub = [x for x in fake.calls if "127.0.0.1" in x[1]]
    assert hub == []                                                  # never talks to Eve's hub: no game action
    assert L.logout([], store=store, transport=fake) == 0 and "Signed out" in capsys.readouterr().out
    assert not store.account().get("access_token")


@pytest.mark.parametrize("answer,line", [
    (Reply(403, body=b'{"error": {"code": "subscription_sharing_user_not_eligible"}}'), "not eligible"),
    (Reply(429, body=b'{"error": {"code": "subscription_sharing_usage_limit_exceeded"}}'), "usage limit"),
])
def test_login_states_said_plainly(env, capsys, answer, line):
    _, fake, store, _ = env
    lb = FakeLoopback(fake)
    fake.responses.append(answer)
    rc = L.login([], store=store, transport=fake, opener=lambda u: setattr(lb, "url", u) or True, loopback=lb)
    assert rc == 4 and line in capsys.readouterr().out


def test_login_denied_in_browser_saves_nothing(env, capsys):
    _, fake, store, _ = env
    lb = FakeLoopback(fake, deny=True)
    rc = L.login([], store=store, transport=fake, opener=lambda u: setattr(lb, "url", u) or True, loopback=lb)
    assert rc == 2 and "cancelled" in capsys.readouterr().out and store.account() is None


def test_no_arg_constructor_reads_its_own_home(tmp_path, monkeypatch):
    """provider.select() builds OpenAIPlanProvider() with no arguments: config from EVE_BRAIN_HOME, off until sign-in."""
    monkeypatch.setenv("EVE_BRAIN_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("EVE_OPENAI_PLAN_MODEL", raising=False)
    p = OpenAIPlanProvider()
    assert p.store.dir == tmp_path / "home" and p.model is None and not p.available()
    assert asyncio.run(p.chat("s", "u", {}))["error"] == "planner_unavailable"
