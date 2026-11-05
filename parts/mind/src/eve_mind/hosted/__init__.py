"""S104/S109: Eve's hosted brain through OpenAI's "Sign in with ChatGPT" plan-usage route (open-source apps).

auth.py       registration + browser OAuth (loopback, state/nonce/PKCE S256), exchange, serialized rotating refresh, sign-out
idtoken.py    ID token check (RS256 against the issuer's JWKS, iss/aud/exp/nonce) - standard library only
store.py      the per-user token record outside the repo (atomic, owner-only, never logged)
responses.py  POST /v1/responses (store:false, stream:true) + GET /v1/models, error classes, bounded backoff
openai_plan.py  the provider behind the Planner's boundary (provider.chat(system, user, schema)); default OFF
login.py      `eve-brain-login` / `eve-brain-logout`

Rules (records/HOSTED-BRAIN-SPIKE.md, all from developers.openai.com/siwc): no API key, no billing fallback, no
temperature / max_output_tokens / previous_response_id, no system-role items, success only on response.completed.
Every network call goes through a `transport` callable, so tests never touch the network.
"""
