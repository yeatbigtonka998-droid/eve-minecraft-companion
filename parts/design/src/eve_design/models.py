"""Model-facing steps (CP-61 generation, CP-63 critique) behind small interfaces, each with a deterministic fake.

Real adapter: DeepSeek models through OpenRouter, matching the mind's adapter (S15, mind 681cf0f): POST
https://openrouter.ai/api/v1/chat/completions with OPENROUTER_API_KEY; model ids ONLY from EVE_DEEPSEEK_MODELS =
{"flash": id, "pro": id} (generator = pro, image critic = flash); headers HTTP-Referer (EVE_OPENROUTER_REFERER) and
X-Title; body asks for usage.cost. DISABLED unless EVE_DEEPSEEK_ENABLED=1 (foundation sets it after CP-06 PASS), the
key, the model id, the envelope's budgets.deepseek_monthly_usd, EVE_DEEPSEEK_PRICES = {id: [usd/Mtok in, out]} and the
mind's spend reservation (agreed with the mind, ef469c1): before each call `spend_reserve {job, model, usd_worst}` over the
mind's /cli (ws://127.0.0.1:8951/cli, EVE_LINK_TOKEN) — refused = nothing is sent, mind down = nothing is sent — then
`spend_commit {reservation_id, usd: usage.cost, tokens}`; an uncommitted reservation stays counted at its worst case.
The check and the reservation are one atomic step in the mind, shared with its own DeepThinker: one US$20 cap for Eve.

Model output is data. A generator returns program TEXT that only the restricted compiler reads; a critic returns
scores that never override the grid checks.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import sqlite3
import time
import urllib.request
from pathlib import Path

from .gamedata import REPO

CATEGORIES = ("brief", "style", "proportions", "depth_detail", "palette", "site")   # Build Side critique categories
PROMPT_VERSION = "design-prompts/9"   # 2: purpose legible; 3: defects first (O34c); 4: elevations (O34d); 5: concrete defect kinds; 6-7: bounded output, square pass kept, defects with coordinates (O34h); 8: consensus caps; 9: targeted elevation check (adbba7b reasoned past 16000)
CRITIC_VIEWS = ("SE", "SW", "NE", "NW", "top", "elev-S", "elev-E", "elev-N", "elev-W")
MAX_PROGRAM_CHARS = 16_000
FENCE = re.compile(r"```(?:eve)?\n(.*?)```", re.S)
UNCLOSED = re.compile(r"```(?:eve)?\n(.*)\Z", re.S)


class ModelError(RuntimeError):
    """A model step could not run (disabled, over budget, unavailable, malformed output). Never fatal to the caller."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


class SpendCapReached(Exception):
    """O87u (Hunter's money rule): the next provider call could take the job past its spend cap, so it is not sent.
    Deliberately not a ModelError: no step treats it as a failed call and goes on; the brief stops where it is."""


class JobCap:
    """One job's spend cap over all its clients (generator and critic), checked before every provider call against
    that call's worst case, so the job never passes it (O87u; before, run_jobs checked only between briefs)."""

    def __init__(self, usd: float, clients: list):
        self.usd, self.clients = usd, clients

    def spent(self) -> float:
        return sum(c.job_usd for c in self.clients)

    def check(self, worst: float) -> None:
        if self.spent() + worst > self.usd:
            raise SpendCapReached(f"job spend US${self.spent():.4f} plus this call's worst case US${worst:.4f} would "
                                  f"pass the job cap US${self.usd:g}")


# --- spend shared with the mind ---------------------------------------------------------------------------
class MindCliLedger:
    """The agreed path: reserve/commit through the mind's /cli. One JSON request per connection."""

    def __init__(self, port: int | None = None, token: str | None = None, timeout: float = 10.0):
        self.port = int(port or os.environ.get("EVE_MIND_PORT", 8951))
        self.token = token if token is not None else os.environ.get("EVE_LINK_TOKEN", "")
        self.timeout = timeout

    def why_disabled(self) -> str | None:
        return None if len(self.token) >= 16 else "EVE_LINK_TOKEN (to reserve spend through the mind's /cli)"

    def _call(self, cmd: str, args: dict) -> dict:
        import asyncio

        from websockets.asyncio.client import connect

        async def go():
            async with connect(f"ws://127.0.0.1:{self.port}/cli", proxy=None, open_timeout=3, max_size=1 << 26) as ws:
                await ws.send(json.dumps({"token": self.token, "cmd": cmd, "args": args}))
                return json.loads(await asyncio.wait_for(ws.recv(), self.timeout))
        try:
            return asyncio.run(go())
        except Exception as e:  # mind down, refused, timeout: never spend without a reservation
            raise ModelError("mind_unavailable", f"{cmd}: {type(e).__name__}") from None

    def reserve(self, job: str, model: str, usd_worst: float, cap=None, tokens_in: int = 0) -> int:
        r = self._call("spend_reserve", {"job": job, "model": model, "usd_worst": usd_worst, "tokens_in": tokens_in})
        if not r.get("ok"):
            raise ModelError("budget_exhausted" if r.get("error") == "budget_exhausted" else "spend_refused",
                             f"{r.get('error')}: {r.get('detail', '')}")
        return int(r["reservation_id"])

    def commit(self, rid: int, usd, tokens_in=None, tokens_out=None, cached=None) -> None:
        try:
            self._call("spend_commit", {"reservation_id": rid, "usd": usd, "tokens_in": tokens_in,
                                        "tokens_out": tokens_out, "cached": cached})
        except ModelError:
            pass                    # the worst case stays counted in the mind: safe side


class SpendLedger:
    """Direct SQLite on the mind's DB with the same reserve/commit semantics (tests; BEGIN IMMEDIATE like the mind)."""

    def __init__(self, db_path: Path | None = None):
        self.path = Path(db_path or os.environ.get("EVE_DB") or REPO / "parts" / "mind" / "var" / "eve.db")

    def why_disabled(self) -> str | None:
        return None

    def _db(self):
        if not self.path.exists():
            raise ModelError("no_spend_ledger", f"{self.path} does not exist (the mind's DB records all spend)")
        db = sqlite3.connect(self.path, isolation_level=None)
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='spend'").fetchone():
            db.close()
            raise ModelError("no_spend_ledger", f"{self.path} has no spend table")
        return db

    def month_usd(self, provider: str) -> float:
        start = time.mktime(time.strptime(time.strftime("%Y-%m-01"), "%Y-%m-%d")) * 1000
        db = self._db()
        try:
            return db.execute("SELECT coalesce(sum(usd), 0) FROM spend WHERE provider=? AND ts >= ?", (provider, start)).fetchone()[0]
        finally:
            db.close()

    def reserve(self, job: str, model: str, usd_worst: float, cap=None, tokens_in: int = 0) -> int:
        start = time.mktime(time.strptime(time.strftime("%Y-%m-01"), "%Y-%m-%d")) * 1000
        db = self._db()
        try:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute("SELECT coalesce(sum(usd), 0) FROM spend WHERE provider IN ('openrouter','deepseek') AND ts >= ?",
                              (start,)).fetchone()[0]
            if used + usd_worst > cap:
                db.execute("ROLLBACK")
                raise ModelError("budget_exhausted", f"cap US${cap}/month")
            cur = db.execute("INSERT INTO spend (ts, provider, model, job, tokens_in, tokens_out, usd, cached) VALUES "
                             "(?,?,?,?,?,?,?,?)", (int(time.time() * 1000), "openrouter", model, f"design:{job} [unsettled]",
                                                   tokens_in, 0, usd_worst, 0))
            db.execute("COMMIT")
            return cur.lastrowid
        finally:
            db.close()

    def commit(self, rid: int, usd, tokens_in=None, tokens_out=None, cached=None) -> None:
        if usd is None:
            return
        db = self._db()
        try:
            db.execute("UPDATE spend SET job=replace(job, ' [unsettled]', ''), usd=?, tokens_in=coalesce(?, tokens_in), "
                       "tokens_out=coalesce(?, tokens_out), cached=coalesce(?, cached) WHERE id=?",
                       (usd, tokens_in, tokens_out, cached, rid))
        finally:
            db.close()


def _envelope_cap() -> float | None:
    p = Path(os.environ.get("EVE_ENVELOPE") or REPO / "config" / "envelope.json")
    try:
        return json.loads(p.read_text()).get("budgets", {}).get("deepseek_monthly_usd")
    except (OSError, ValueError):
        return None


def _env_json(name: str) -> dict:
    try:
        v = json.loads(os.environ.get(name) or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def configured_model(role: str) -> str:
    """'pro' (generator) or 'flash' (critic) -> the verified OpenRouter id from EVE_DEEPSEEK_MODELS, or ''."""
    return str(_env_json("EVE_DEEPSEEK_MODELS").get(role, ""))


PROVIDERS = ("openrouter", "deepseek")      # both count against Eve's cap


class DeepSeekClient:
    def __init__(self, model: str, key=None, cap=None, price=None, ledger=None, base_url=None,
                 opener=None, enabled: bool | None = None):
        self.model = model
        self.switch = enabled if enabled is not None else os.environ.get("EVE_DEEPSEEK_ENABLED") == "1"
        self.key = key if key is not None else os.environ.get("OPENROUTER_API_KEY")
        self.cap = cap if cap is not None else _envelope_cap()
        pr = price or _env_json("EVE_DEEPSEEK_PRICES").get(model)
        self.price = (float(pr[0]), float(pr[1])) if pr and len(pr) == 2 else None
        self.ledger = ledger or MindCliLedger()
        self.job_usd = 0.0                                   # what this client has spent (reserved, then settled)
        self.job_cap: JobCap | None = None                   # O87u: run_jobs' cap, checked before every call
        self.calls: list[dict] = []                          # per call: finish_reason, tokens (reasoning too), usd
        self.base_url = (base_url or os.environ.get("EVE_OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
        self.referer = os.environ.get("EVE_OPENROUTER_REFERER", "http://127.0.0.1:8951/eve")
        self.opener = opener or urllib.request.urlopen     # injectable for tests; nothing else is ever opened

    def why_disabled(self) -> str | None:
        missing = [n for n, ok in (("EVE_DEEPSEEK_ENABLED=1 (set by foundation after CP-06 PASS)", self.switch),
                                    ("OPENROUTER_API_KEY", self.key), ("a model id in EVE_DEEPSEEK_MODELS", self.model),
                                    ("budgets.deepseek_monthly_usd", self.cap),
                                    ("EVE_DEEPSEEK_PRICES for this model", self.price),
                                    (self.ledger.why_disabled() or "", not self.ledger.why_disabled())) if not ok]
        return f"missing {', '.join(missing)} (DECISIONS S9/S15/S16)" if missing else None

    def chat(self, job: str, messages: list[dict], max_tokens: int = 2048, timeout_s: float = 120.0,
             temperature: float | None = None, json_mode: bool = False, reasoning: dict | None = None) -> str:
        if (why := self.why_disabled()):
            raise ModelError("model_disabled", why)
        chars = sum(len(json.dumps(m["content"])) for m in messages)
        worst = chars * self.price[0] / 1e6 + max_tokens * self.price[1] / 1e6   # every char a token, all output used
        if self.job_cap is not None:
            self.job_cap.check(worst)                        # over the job cap -> nothing reserved, nothing sent
        rid = self.ledger.reserve(job, self.model, round(worst, 8), self.cap, chars)   # refused -> nothing is sent
        self.job_usd += worst
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "stream": False,
                "usage": {"include": True}}
        if temperature is not None:
            body["temperature"] = temperature
        if json_mode:                                       # O34r: provider JSON mode for the critique
            body["response_format"] = {"type": "json_object"}
        if reasoning:                                       # O34r: OpenRouter reasoning control, if configured
            body["reasoning"] = reasoning
        body = json.dumps(body).encode()
        req = urllib.request.Request(self.base_url + "/chat/completions", body,
                                     {"Content-Type": "application/json", "Authorization": f"Bearer {self.key}",
                                      "HTTP-Referer": self.referer, "X-Title": "Eve (Project Sentience)"})
        try:
            with self.opener(req, timeout=timeout_s) as r:
                raw = json.loads(r.read(4 << 20))
        except (OSError, ValueError) as e:
            self.ledger.commit(rid, None)                    # no usage known: the worst case stays counted
            raise ModelError("model_unavailable", type(e).__name__) from None
        u = raw.get("usage", {})
        tin, tout = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
        usd = u.get("cost")
        usd = float(usd) if isinstance(usd, (int, float)) else tin * self.price[0] / 1e6 + tout * self.price[1] / 1e6
        self.ledger.commit(rid, usd, tin, tout, int((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)))
        self.job_usd += usd - worst
        choice = (raw.get("choices") or [{}])[0] if isinstance(raw.get("choices"), list) else {}
        self.calls.append({"job": job, "finish_reason": choice.get("finish_reason"), "prompt_tokens": tin,
                           "completion_tokens": tout, "max_tokens": max_tokens, "usd": round(usd, 6),
                           "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                           **({"provider": raw["provider"]} if raw.get("provider") else {})})   # O87: routing differs
        try:                                     # O34n/o: every call's usage is evidence, truncated or not
            text = str(choice["message"]["content"] or "")
        except (KeyError, IndexError, TypeError):
            raise ModelError("malformed_output", "no choices[0].message.content") from None
        if choice.get("finish_reason") == "length":       # reasoning + answer ran past max_tokens: the answer is cut
            raise ModelError("truncated", f"{tout} output tokens hit max_tokens={max_tokens}")
        return text


def parse_json(text: str, what: str):
    """The first JSON value in a model answer: tolerates ```json fences and prose around it (attempt 1 on the 45L)."""
    t = text.strip()
    for i, ch in enumerate(t):
        if ch in "[{":
            try:
                return json.JSONDecoder().raw_decode(t, i)[0]
            except ValueError:
                pass
    raise ModelError("malformed_output", f"{what} are not JSON: {t[:160]!r}")


# --- generation (CP-61) -----------------------------------------------------------------------------------
EXAMPLES_DIR = REPO / "parts" / "design" / "examples"
COMMAND_NOTES = {
    "foundation": "first; footprint w x d (x east, z south), h layers of base", "walls": "storeys of height h; setbacks shrink upper storeys",
    "floor": "floors for every storey", "door": "ground-storey door on a side; at = 'center' or offset from the corner",
    "windows": "count windows of w x h on a side of a storey", "room": "rooms split each storey in order; furnished by type",
    "stairs": "from storey to storey+1 along the inside of a side; landing=True turns at a level landing",
    "roof": "gable or flat over the top storey", "chimney": "outside a side, above the roof", "porch": "deck + roof on a side",
    "wing": "an attached volume on a side of main; later commands use part=<name>", "dormer": "3-wide window bay on a gable slope",
    "courtyard": "low wall + gate enclosing ground on a side", "detail": "style-card detailing rules ('style' = the card's set)"}
FEATURES = ["porch", "chimney", "dormer", "courtyard", "wing", "stairs", "detail"]


def language_spec() -> str:
    """The exact vocabulary, generated from the compiler's command table (so it can never drift from what compiles)."""
    from .compiler import COMMANDS, HEADER

    def arg(n, kind, default):
        k = kind[0]
        t = f"int {kind[1]}..{kind[2]}" if k == "int" else "|".join(f'"{o}"' for o in kind[1]) if k == "choice" else \
            {"block": "block id", "family": "material family", "bool": "True|False", "at": '"center"|int',
             "part": "part name", "str": "string"}.get(k, k)
        return f"{n}: {t}" + (" (required)" if default == "REQ" else "" if default is None else f" = {default!r}")
    lines = ["b = Build(" + ", ".join(arg(*a) for a in HEADER) + ")   # always the first line"]
    for name, spec in COMMANDS.items():
        lines.append(f"b.{name}(" + ", ".join(arg(*a) for a in spec) + f")   # {COMMAND_NOTES.get(name, '')}")
    return "\n".join(lines)


def _examples() -> str:
    return "\n\n".join(f"```eve\n{(EXAMPLES_DIR / f'{n}.eve').read_text(encoding='utf-8')}```" for n in ("cabin", "two_storey"))


def generator_messages(kind: str, brief: dict, style_card: dict, concept: dict | None = None,
                       previous: str | None = None, feedback: list[str] | None = None, taste: dict | None = None) -> list:
    if kind == "concepts":
        return [{"role": "system", "content":
                 "You plan Minecraft buildings for Eve. Return ONLY a JSON array of 2 or 3 distinct concept objects with "
                 "keys: name (string), summary (one sentence), footprint [w, d] (3..64), storeys (1..12), roof ('gable' or "
                 "'flat'), features (subset of " + json.dumps(FEATURES) + "). Respect the brief's rooms, site and budget; "
                 "vary layout, not just colours."},
                {"role": "user", "content": json.dumps({"brief": brief, "style": style_card.get("style"),
                                                        "eve_preferences": taste} if taste else {"brief": brief,
                                                        "style": style_card.get("style")})}]
    system = ("You write programs in Eve's restricted building language. Only these commands exist; there are no loops, "
              "variables, expressions or imports, one statement per line, strings in double quotes:\n\n" + language_spec() +
              "\n\nRules: sides are north/south/east/west; storeys count from 0; a building with more than one storey needs "
              "stairs; every room the brief asks for needs a room(); omitted blocks come from the style card.\n\n"
              "The building is judged from outside views only, so make its purpose readable from outside (the critic's "
              "most common findings): a workshop, storage or kitchen part is a wing with its own outside door "
              "(door(part=...)), a different height or roof from the main house, and a chimney for a workshop or kitchen; a "
              "storehouse gets a porch or courtyard at its door for loading and few, small windows; give the walls depth "
              "with detail(\"style\") and at least one of porch, chimney or dormer when the size allows; pick a roof block "
              "that contrasts with the wall frame so the roof does not blend into the walls. Examples:\n\n" +
              _examples() + "\n\nStyle card:\n" + json.dumps(style_card) +
              "\n\nAnswer with exactly one fenced block ```eve ... ``` and nothing after it.")
    user = {"brief": brief, "concept": concept}
    if previous is not None:
        user |= {"previous_program": previous, "fix_these_problems": feedback or []}
    elif feedback:
        user["note"] = feedback                  # e.g. the last answer was cut off before the program
    if taste:
        user["eve_preferences"] = taste
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user)}]


def extract_program(text: str) -> str:
    """The model must answer with exactly one fenced program block. Anything else is malformed output."""
    blocks = FENCE.findall(text)
    if not blocks and text.count("```") == 1 and (m := UNCLOSED.search(text)):
        blocks = [m.group(1)]   # O87 (45L h08): a stopped answer that never closed its fence; the compiler checks the rest
    if len(blocks) != 1:
        raise ModelError("malformed_output", f"expected one fenced program block, got {len(blocks)}: {text.strip()[:160]!r}")
    if len(blocks[0]) > MAX_PROGRAM_CHARS:
        raise ModelError("malformed_output", "program too long")
    return blocks[0]


class DeepSeekGenerator:
    name = "deepseek"

    def __init__(self, client: DeepSeekClient | None = None, taste: dict | None = None):
        self.client = client or DeepSeekClient(configured_model("pro"))
        self.version = f"{self.client.model}+{PROMPT_VERSION}"
        self.taste = taste            # Eve's own generated preferences (taste.preferences), never the owner's

    def concepts(self, brief: dict, style_card: dict, n: int = 3) -> list[dict]:
        """O87 (45L attempt 7, h08: a 299-token concepts answer with no thinking, then 9 invalid programs): the
        generator's reasoning setting (EVE_DEEPSEEK_PROGRAM_REASONING) reaches the concepts too, and a summary under
        CONCEPT_MIN_WORDS words is asked once more for the layout it should say."""
        env = os.environ.get("EVE_DEEPSEEK_PROGRAM_REASONING")
        kw = {"reasoning": json.loads(env)} if env else {}
        msgs = generator_messages("concepts", brief, style_card, taste=self.taste)
        for attempt in range(2):
            text = self.client.chat("concepts", msgs, max_tokens=8000, **kw)   # Flash/Pro reason (45L attempts 1-2)
            out = parse_json(text, "concepts")
            if not isinstance(out, list) or not 2 <= len(out) <= 3 or not all(isinstance(c, dict) for c in out):
                raise ModelError("malformed_output", "expected 2-3 concept objects")
            if attempt or all(len(str(c.get("summary") or "").split()) >= CONCEPT_MIN_WORDS for c in out):
                return out[:n]
            msgs = msgs + [{"role": "assistant", "content": text}, {"role": "user", "content": CONCEPTS_THIN}]

    def program(self, brief: dict, concept: dict, style_card: dict, previous: str | None = None,
                feedback: list[str] | None = None) -> str:
        """Tiered output budget: the first call keeps the provider's reasoning (EVE_DEEPSEEK_PROGRAM_REASONING overrides)
        with PROGRAM_TOKENS; a call after a cut-off (generate's PROGRAM_TRUNCATED note) reasons low with
        PROGRAM_TOKENS_TIGHT. A cut-off is a clean ModelError("truncated") either way: generate logs it on that
        candidate and retries within its repair budget (45L h07: a program call ended exactly at 16000 tokens)."""
        tight = bool(feedback) and PROGRAM_TRUNCATED in feedback
        env = os.environ.get("EVE_DEEPSEEK_PROGRAM_REASONING")
        reasoning = {"effort": "low"} if tight else json.loads(env) if env else None
        return extract_program(self.client.chat("program", generator_messages(
            "program", brief, style_card, concept, previous, feedback, self.taste),
            max_tokens=PROGRAM_TOKENS_TIGHT if tight else PROGRAM_TOKENS, **({"reasoning": reasoning} if reasoning else {})))


CONCEPT_MIN_WORDS = 12          # a reasoned concept's summary runs ~30 words (45L attempt 7, h02)
CONCEPTS_THIN = ("those summaries are too thin to build from: answer again with the same JSON shape, each summary a full "
                 "sentence saying the layout (which rooms on which storey), the shape and what sets it apart")
PROGRAM_TOKENS = 16000          # 45L attempt 2: 8000 were all spent on reasoning for h03/h08
PROGRAM_TOKENS_TIGHT = 8000
PROGRAM_TRUNCATED = ("your last answer ran out of room while reasoning and had no program: think briefly, then answer "
                     "with the fenced program")


class FakeGenerator:
    """Deterministic stand-in: concepts and programs are templates seeded by the brief. `faults` injects scripted
    mistakes on first attempts ({"syntax": n, "unknown_command": n, "drop_room": n, "too_many_windows": n}) so the
    pipeline's repair path is exercised; feedback that names the problem makes the fake fix it, like a model should."""
    name = "fake"
    version = f"fake-1+{PROMPT_VERSION}"

    def __init__(self, faults: dict | None = None, broken: bool = False):
        self.faults = dict(faults or {})
        self.broken = broken          # True: every call raises like an unavailable API
        self.shrink: dict[str, int] = {}

    def _rng(self, brief, salt):
        return random.Random(hashlib.sha256((json.dumps(brief, sort_keys=True) + salt).encode()).hexdigest())

    def concepts(self, brief: dict, style_card: dict, n: int = 3) -> list[dict]:
        if self.broken:
            raise ModelError("model_unavailable", "fake configured broken")
        rooms = list(brief.get("rooms", ["living", "bedroom"]))
        storeys = int(brief.get("storeys", 1 if len(rooms) <= 2 else 2))
        per = -(-len(rooms) // storeys)
        w = 7 + 4 * per if per > 1 else 9
        w += (w + 1) % 2                                      # odd widths centre the door
        d = 9 if storeys == 1 else 11
        plot = brief.get("site", {}).get("plot")
        if plot:                                              # keep the footprint (+1 overhang each side) on the plot
            w = min(w, plot["max"]["x"] - plot["min"]["x"] - 1)
            d = min(d, plot["max"]["z"] - plot["min"]["z"] - 1)
        roofs = [("gable", "east_west"), ("gable", "north_south"), ("flat", None)]
        if brief.get("roof"):
            roofs = [r for r in roofs if r[0] == brief["roof"]] or roofs
        rng = self._rng(brief, "concepts")
        rng.shuffle(roofs)
        shapes = [(w, d, r, ridge) for r, ridge in roofs]
        if len(shapes) < 2:                                   # one roof kind asked for: vary the footprint instead
            shapes.append((w + 2, d, *roofs[0]) if not plot else (w, max(7, d - 2), *roofs[0]))
        return [{"name": f"concept-{i + 1}", "footprint": [cw, cd], "storeys": storeys, "roof": r, "ridge": ridge,
                 "rooms": rooms, "features": list(brief.get("features", []))} for i, (cw, cd, r, ridge) in enumerate(shapes[:n])]

    def program(self, brief: dict, concept: dict, style_card: dict, previous: str | None = None,
                feedback: list[str] | None = None) -> str:
        if self.broken:
            raise ModelError("model_unavailable", "fake configured broken")
        w, d = concept["footprint"]
        if any("outside the plot" in f for f in feedback or []):          # repair: remember a smaller footprint
            self.shrink[concept["name"]] = self.shrink.get(concept["name"], 0) + 2
        w, d = max(5, w - self.shrink.get(concept["name"], 0)), max(5, d - self.shrink.get(concept["name"], 0))
        storeys = concept["storeys"]
        rooms = list(concept["rooms"])
        lines = [f'b = Build(style="{brief.get("style", "cozy_cabin")}", seed={self._rng(brief, concept["name"]).randrange(100_000)})',
                 f"b.foundation(w={w}, d={d})", f"b.walls(h=4, storeys={storeys})", "b.floor()",
                 'b.door(side="south", at="center")']
        for k in range(storeys):
            lines += [f'b.windows(side="north", count={2 if w >= 9 else 1}, w=2, storey={k})', f'b.windows(side="east", count=1, w=1, storey={k})']
        if storeys > 1:
            lines.append('b.stairs(storey=0, side="north", at=2)')
        per = -(-len(rooms) // storeys)
        for i, r in enumerate(rooms):
            lines.append(f'b.room("{r}", storey={min(i // per, storeys - 1)})')
        roof = f'b.roof("{concept["roof"]}"' + (f', ridge="{concept["ridge"]}"' if concept.get("ridge") else "") + ")"
        lines.append(roof)
        if "porch" in concept["features"]:
            lines.append('b.porch(side="south", depth=2)')
        if "chimney" in concept["features"]:
            lines.append('b.chimney(side="west")')
        if "detail" in concept["features"] or any("detail(" in f for f in feedback or []):
            lines.append('b.detail("style")')
        fixed = " ".join(feedback or [])
        for fault, line in (("syntax", "b.walls(h=4"), ("unknown_command", 'b.teleport(x=1)'),
                            ("too_many_windows", 'b.windows(side="west", count=9, w=3)')):
            if self.faults.get(fault, 0) > 0 and not (previous and fixed):
                self.faults[fault] -= 1
                lines.append(line)
        if self.faults.get("drop_room", 0) > 0 and not (previous and fixed) and len(rooms) > 1:
            self.faults["drop_room"] -= 1                     # "silent simplification": a requested room goes missing
            lines = [ln for ln in lines if f'room("{rooms[-1]}"' not in ln]
        return "\n".join(lines) + "\n"


# --- critique (CP-63) ---------------------------------------------------------------------------------
# 45L self-test at f7360a7: a vague "missing part" kind was listed for every design, the reference too (bar fell to 3)
DEFECT_CAPS = {"hole": "depth_detail", "damage": "depth_detail", "no_roof": "proportions", "floating": "site"}


def _critique_shape(out) -> dict:
    """Scores 1..10 per category; each defect the critic itself listed caps its category at 3 (O34c: it saw a wall hole
    and a floating cube yet scored them 5 and 6)."""
    if not isinstance(out, dict) or not isinstance(out.get("scores"), dict) or not isinstance(out.get("fixes"), list) \
            or not isinstance(out.get("defects", []), list):
        raise ModelError("malformed_output", "critique needs {defects:[...], scores:{...}, fixes:[...]}")
    scores = {}
    for c in CATEGORIES:
        v = out["scores"].get(c)
        if not isinstance(v, (int, float)) or not 1 <= v <= 10:
            raise ModelError("malformed_output", f"score {c!r} must be 1..10")
        scores[c] = float(v)
    defects = [{"kind": d["kind"], "where": str(d.get("where", ""))[:120]} for d in out.get("defects", [])
               if isinstance(d, dict) and d.get("kind") in DEFECT_CAPS][:12]
    raw = dict(scores)
    for d in defects:
        c = DEFECT_CAPS[d["kind"]]
        scores[c] = min(scores[c], 3.0)
    fixes = [str(f)[:300] for f in out.get("fixes", [])][:12]
    return {"scores": scores, "raw_scores": raw, "fixes": fixes, "defects": defects}


def median_critique(runs: list[dict]) -> dict:
    """Per-category median of several critiques of the same images (their own scores, before any cap); a defect kind
    caps its category only when most runs list it (45L 945f4fe: one run's stray "hole" swung a median by 4); the fixes
    of the run closest to the result."""
    scores = {c: sorted(r.get("raw_scores", r["scores"])[c] for r in runs)[len(runs) // 2] for c in CATEGORIES}
    agreed = [k for k in DEFECT_CAPS if sum(any(d["kind"] == k for d in r.get("defects", [])) for r in runs) > len(runs) // 2]
    for k in agreed:
        scores[DEFECT_CAPS[k]] = min(scores[DEFECT_CAPS[k]], 3.0)
    near = min(runs, key=lambda r: sum(abs(r["scores"][c] - scores[c]) for c in CATEGORIES))
    return {"scores": scores, "fixes": near["fixes"], "defects": [d for d in near.get("defects", []) if d["kind"] in agreed],
            "samples": [r["scores"] for r in runs], "defect_samples": [r.get("defects", []) for r in runs]}


RUBRIC = {
    "brief": "does the building visibly provide what the brief asks for (size, storeys, features)?",
    "style": "does it read as the named style next to the reference builds?",
    "proportions": "are mass, roof pitch and silhouette well proportioned? a missing roof or a box with no roof is a 1-3",
    "depth_detail": "are walls given depth (frames, pillars, trim) rather than flat? holes and damage are a 1-3",
    "palette": "do the blocks form a harmonious palette? random clashing colours are a 1-3",
    "site": "does it sit sensibly on the ground; anything floating or detached is a 1-3"}


def critic_messages(brief: dict, style_card: dict, previews: dict[str, Path], references: list[Path] = ()) -> list:
    """Images as OpenAI-style data-URL parts (the format foundation verifies in CP-06). The critic sees the brief,
    the style name, the rubric and images only: never the validator's report or the generator's claims."""
    parts = [{"type": "text", "text": json.dumps({"brief": brief, "style": style_card.get("style"), "rubric": RUBRIC,
                                                  "images": [f"design {k}" for k in previews] +
                                                            [f"reference build {i + 1}" for i in range(len(references))]})}]
    for p in [*previews.values(), *references]:
        parts.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," +
                                                         base64.b64encode(Path(p).read_bytes()).decode()}})
    return [{"role": "system", "content": "You are a strict architecture critic for Minecraft builds. First inspect "
                                          "every DESIGN image for physical build defects. In each elevation image "
                                          "(straight-on, one side each; hot pink = open air, normal around the building and "
                                          "under porch roofs; YELLOW = you see into a room) check just two things: any "
                                          "YELLOW square, and any block with hot pink below it. Keep the check short. "
                                          "Only these kinds count, and only when you can point to them: \"hole\" = a "
                                          "gap through a wall or roof that is not a window or door (in an elevation: a "
                                          "YELLOW square: you see into a room there; pink under a porch roof is NOT a hole); \"floating\" = a block or blob "
                                          "touching neither the building nor the ground (hot pink all round or under it); "
                                          "\"no_roof\" = the building or one of its parts has no roof over it; "
                                          "\"damage\" = blocks torn out of walls or roof leaving ragged gaps. Design "
                                          "choices (an open porch, a gable end, a chimney, a plain wall, few details) "
                                          "are NOT defects; if there are none, the list is empty. "
                                          "Then score the DESIGN images 1-10 in each rubric category, judging only what "
                                          "is visible, and list at most 5 specific fixes. Output is bounded: at most 8 "
                                          "defects, each with \"where\" = elevation and square, e.g. \"elev-W col 5 "
                                          "row 3\" (counted from the bottom left, starting at 1). Answer JSON only: "
                                          "{\"defects\": "
                                          "[{\"kind\": string, \"where\": string}], \"scores\": {category: number}, "
                                          "\"fixes\": [string]}"},
            {"role": "user", "content": parts}]


def reference_images(style: str, gd, cache: Path | None = None, n: int = 3) -> list[Path]:
    """SE renders of the style's first n measured reference builds (vanilla structures), cached as PNGs."""
    from . import preview, style as style_mod
    refs_file = EXAMPLES_DIR.parent / "styles" / f"{style}.references.json"
    if not refs_file.exists():
        return []
    cache = Path(cache or REPO / "designs" / "_refs" / style)
    out = []
    for r in json.loads(refs_file.read_text())["references"][:n]:
        png = cache / f"{Path(r['file']).stem}.png"
        if not png.exists():
            src = next((gd.root / "structure").rglob(r["file"]), None)
            if src is None:
                continue
            g, _ = style_mod.load_structure(src, gd)
            preview.render(g, gd, cache / Path(r["file"]).stem)
            png.parent.mkdir(parents=True, exist_ok=True)
            png.write_bytes((cache / Path(r["file"]).stem / "SE.png").read_bytes())
        out.append(png)
    return out


class DeepSeekCritic:
    name = "deepseek"

    SAMPLES = 3   # 45L attempt 3: one call drifted 2.0 on the same images; the median of 3 is what the critic says

    def __init__(self, client: DeepSeekClient | None = None, references: list[Path] | None = None):
        self.client = client or DeepSeekClient(configured_model("flash"))
        # O34r: OpenRouter reasoning control for the critique, e.g. {"effort": "low"}; part of the critic's identity
        self.reasoning = json.loads(os.environ.get("EVE_DEEPSEEK_CRITIC_REASONING") or "null")
        r = "+r" + json.dumps(self.reasoning, sort_keys=True, separators=(",", ":")) if self.reasoning else ""
        self.version = f"{self.client.model}+{PROMPT_VERSION}+median{self.SAMPLES}{r}"
        self.references = references

    def critique(self, brief: dict, style_card: dict, previews: dict[str, Path], references: list[Path] = (),
                 design=None) -> dict:
        if (why := self.client.why_disabled()):
            raise ModelError("model_disabled", why)
        refs = list(references) or list(self.references or [])
        msgs = critic_messages(brief, style_card, previews, refs)
        runs = []
        for _ in range(self.SAMPLES):
            for attempt in (0, 1):
                # O34h: a cut-off answer (finish_reason length) raises here: a loud error, never retried or parsed
                text = self.client.chat("critique", msgs, max_tokens=16000, temperature=0, json_mode=True,
                                        reasoning=self.reasoning)
                try:
                    runs.append(_critique_shape(parse_json(text, "critique")))
                    break
                except (ModelError, ValueError) as e:  # O34r: empty / schema-invalid with finish stop: one logged retry
                    if self.client.calls:
                        self.client.calls[-1]["invalid"] = str(e)[:160]
                    if attempt:
                        raise ModelError("malformed_output", f"critique invalid twice: {str(e)[:160]}") from None
        return median_critique(runs)


def write_requests(brief: dict, gd, out: Path, design_dir: Path | None = None) -> dict:
    """Write the exact DeepSeek requests (concepts, first program, critique) WITHOUT sending them: image data replaced
    by sha256 + size. For inspection before EVE_DEEPSEEK_ENABLED=1, and for foundation's CP-06 checks."""
    from .compiler import load_style
    card, _ = load_style(brief.get("style", "cozy_cabin"), 0)
    concept = FakeGenerator().concepts(brief, card, 1)[0]
    reqs = {"concepts": generator_messages("concepts", brief, card),
            "program (example concept)": generator_messages("program", brief, card, concept)}
    if design_dir is not None:
        prev = {k: Path(design_dir) / "previews" / f"{k}.png" for k in CRITIC_VIEWS}
        reqs["critique"] = critic_messages(brief, card, prev, reference_images(card["style"], gd))

    def scrub(m):
        if isinstance(m, dict) and m.get("type") == "image_url":
            raw = base64.b64decode(m["image_url"]["url"].split(",", 1)[1])
            return {"type": "image_url", "image_url": {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}}
        if isinstance(m, dict):
            return {k: scrub(v) for k, v in m.items()}
        if isinstance(m, list):
            return [scrub(v) for v in m]
        return m
    price = (_env_json("EVE_DEEPSEEK_PRICES").get(configured_model("pro")) or [None])[0]
    summary = {k: {"chars": sum(len(json.dumps(x["content"])) for x in v),
                   "images": sum(1 for x in v if isinstance(x["content"], list) for part in x["content"]
                                 if part.get("type") == "image_url")} for k, v in reqs.items()}
    for v in summary.values():
        v["worst_case_usd_in"] = round(v["chars"] * float(price) / 1e6, 4) if price else "prices not configured"
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "requests.json").write_text(json.dumps({"note": "NOT SENT; image token cost unknown until CP-06",
                                                   "summary": summary, "requests": scrub(reqs)}, indent=1) + "\n")
    return summary


class FakeCritic:
    """Deterministic stand-in. With `script` it returns the given score dicts round by round (for loop tests:
    oscillation, still-poor, API failure via None). Without it, it scores simple grid facts so revisions can improve."""
    name = "fake"
    version = f"fake-1+{PROMPT_VERSION}"

    def __init__(self, script: list | None = None):
        self.script = list(script) if script is not None else None
        self.calls = 0

    def critique(self, brief: dict, style_card: dict, previews: dict[str, Path], references: list[Path] = (),
                 design=None) -> dict:
        self.calls += 1
        if self.script is not None:
            if not self.script:
                raise ModelError("model_unavailable", "fake script exhausted")
            s = self.script.pop(0)
            if s is None:
                raise ModelError("model_unavailable", "scripted API failure")
            return _critique_shape({"scores": {c: s.get(c, s.get("all", 7)) for c in CATEGORIES},
                                    "fixes": s.get("fixes", [])})
        rules = set(design.meta.get("provenance", {}).get("detailing_rules", [])) if design is not None else set()
        depth = 8 if {"pillars", "shutters"} <= rules else 5
        palette = 8 if "gradient" in rules else 6
        scores = {"brief": 8, "style": 7 if rules else 6, "proportions": 7, "depth_detail": depth, "palette": palette, "site": 7}
        fixes = [] if rules else ['add the style card detailing: detail("style")']
        return {"scores": {c: float(v) for c, v in scores.items()}, "fixes": fixes}


def agreement(critic_scores: dict[str, dict], hunter_keep: dict[str, bool], threshold: float = 7.0) -> dict:
    """CP-63 calibration: does the critic's acceptable/unacceptable call (every category >= threshold) match Hunter's
    keep/reject on the same held-out designs? Target 16/20 (programme); disagreements are listed, never smoothed."""
    ids = sorted(set(critic_scores) & set(hunter_keep))
    calls = {i: min(critic_scores[i].values()) >= threshold for i in ids}
    dis = [{"id": i, "critic_accepts": calls[i], "hunter_keeps": hunter_keep[i]} for i in ids if calls[i] != hunter_keep[i]]
    return {"cases": len(ids), "agree": len(ids) - len(dis), "disagreements": dis,
            "missing": sorted(set(hunter_keep) - set(critic_scores))}
