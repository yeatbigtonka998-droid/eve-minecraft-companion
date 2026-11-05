"""Paths, envelope and link settings. The token comes only from the environment."""
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# parts/mind/src/eve_mind/config.py -> repo root is parents[4]; EVE_ROOT overrides (45L deploy).
ROOT = Path(os.environ.get("EVE_ROOT") or Path(__file__).resolve().parents[4])
MODES = ("manual", "supervised", "autonomous")


def num_ctx():
    """O180: every model call sends the planner's context size (EVE_PLANNER_NUM_CTX, else the planner's 8192) - Ollama
    reloads qwen3:8b whenever a request's num_ctx differs from the loaded one (45L: OLLAMA_CONTEXT_LENGTH unset)."""
    return int(os.environ.get("EVE_PLANNER_NUM_CTX") or Config.planner_num_ctx)


def lane() -> str:
    """O18 evidence label: EVE_LANE (`45L` or `shark`), else the known host's lane, else this machine's name."""
    host = __import__("platform").node().lower()
    return os.environ.get("EVE_LANE") or {"shark": "shark", "silvershark": "45L"}.get(host, host)


def canonical(obj) -> bytes:
    """Canonical JSON used for registry_hash / envelope_hash: sorted keys, no whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def strict_loads(text):
    """json.loads that refuses NaN/Infinity (Python accepts them by default)."""
    def bad(c):
        raise ValueError(f"non-finite number {c}")
    return json.loads(text, parse_constant=bad)


@dataclass
class Config:
    token: str
    root: Path = ROOT
    envelope_path: Path = None
    db_path: Path = None
    designs_dir: Path = None          # designs/<project_id>/v<N>/ (contracts/design-project.md)
    host: str = "127.0.0.1"          # loopback only, never configurable to a public address
    port: int = 8951
    mc_version: str = "26.3"
    frame_max: int = 262_144
    replay_s: float = 600.0
    ping_s: float = 5.0
    dead_s: float = 15.0
    state_hz: int = 4
    stale_ms: int = 2000
    ack_timeout_s: float = 0.5
    ack_resends: int = 2
    result_grace_s: float = 2.0
    stuck_window_s: float = 30.0     # CP-22: no world progress for this long while running -> stuck (tune live)
    cancel_grace_s: float = 3.0      # a cancel not honoured within this -> hard control.stop
    retry_limit: int = 3             # same skill+params failing this often in 10 min -> refused
    db_timeout_s: float = 5.0        # bounded wait on a locked SQLite file (CP-24)
    parked_ttl_s: float = 3600.0     # paused/blocked tasks older than this are cancelled (backstop)
    planner_timeout_s: float = 90.0  # bounded wait on Ollama (CP-24); cold qwen3:8b load is 14-71 s on the 45L
    planner_num_ctx: int = 8192      # O94e-shark: the planner's context on every call (EVE_PLANNER_NUM_CTX)
    chat_gap_s: float = 2.1          # O114: the live body drops a chat line within 2 s of the last (EVE_CHAT_GAP_S)
    reply_model_s: float = 10.0      # O111: no model call in front of an owner reply waits longer (EVE_REPLY_MODEL_S)
    intent_gate: str = "rules"       # O94k: agenda's gate before the O30 model call - rules (3cf44c73, no model) |
    #                                  model (9fd81158, only after blind batch 3 passes under O94j) | off; EVE_INTENT_GATE
    envelope: dict = field(default=None, repr=False)

    def __post_init__(self):
        self.root = Path(self.root)
        if self.envelope_path is None:
            real = self.root / "config" / "envelope.json"
            self.envelope_path = real if real.exists() else self.root / "config" / "envelope.example.json"
        if self.envelope is None:
            self.envelope = strict_loads(Path(self.envelope_path).read_text("utf-8"))
        ttl = (self.envelope.get("limits") or {}).get("parked_ttl_s")
        if ttl is not None:                  # O31: lanes may set 21600 for long dev sessions; the 45L keeps 3600
            self.parked_ttl_s = float(ttl)
        if self.db_path is None:
            self.db_path = Path(os.environ.get("EVE_DB") or self.root / "parts" / "mind" / "var" / "eve.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.designs_dir = Path(self.designs_dir or os.environ.get("EVE_DESIGNS") or self.root / "designs")
        self.planner_num_ctx = int(os.environ.get("EVE_PLANNER_NUM_CTX") or self.planner_num_ctx)
        self.intent_gate = os.environ.get("EVE_INTENT_GATE") or self.intent_gate
        self.chat_gap_s = float(os.environ.get("EVE_CHAT_GAP_S") or self.chat_gap_s)
        self.reply_model_s = float(os.environ.get("EVE_REPLY_MODEL_S") or self.reply_model_s)
        if self.intent_gate not in ("rules", "model", "off"):
            raise ValueError(f"intent_gate {self.intent_gate!r}: rules | model | off")

    @property
    def registry_path(self) -> Path:
        """O134: EVE_REGISTRY (relative to the root, or absolute) serves another registry, e.g. the 0.15.1 draft
        (0.15.0 + drop_item) that body's 0.15.1 client embeds; unset = contracts/skills/registry.json."""
        p = os.environ.get("EVE_REGISTRY")
        return (self.root / p if p and not Path(p).is_absolute() else Path(p)) if p else \
            self.root / "contracts" / "skills" / "registry.json"

    @property
    def schema_path(self) -> Path:
        return self.root / "contracts" / "memory-schema.sql"

    @property
    def envelope_hash(self) -> str:
        return sha256(self.envelope)


def env_token() -> str:
    tok = os.environ.get("EVE_LINK_TOKEN", "")
    if len(tok) < 16:
        raise SystemExit("EVE_LINK_TOKEN is missing or shorter than 16 characters; refusing to start")
    return tok


def load(**overrides) -> Config:
    if "token" not in overrides:
        overrides["token"] = env_token()
    if "envelope_path" not in overrides and os.environ.get("EVE_ENVELOPE"):
        overrides["envelope_path"] = Path(os.environ["EVE_ENVELOPE"])
    if "port" not in overrides and os.environ.get("EVE_PORT"):
        overrides["port"] = int(os.environ["EVE_PORT"])
    return Config(**overrides)
