"""O180 (45L: OLLAMA_CONTEXT_LENGTH unset, so classify / the extractors ran at Ollama's default and every call reloaded
qwen3:8b next to the planner's 4096): every model call sends the planner's num_ctx (EVE_PLANNER_NUM_CTX)."""
import re
from pathlib import Path

from eve_mind.config import num_ctx

SRC = Path(__file__).resolve().parents[1] / "src" / "eve_mind"


def test_o180_one_context_size_for_every_model_call(monkeypatch):
    monkeypatch.setenv("EVE_PLANNER_NUM_CTX", "4096")
    assert num_ctx() == 4096
    monkeypatch.delenv("EVE_PLANNER_NUM_CTX")
    assert num_ctx() == 8192                                   # the planner's own default
    for f in ("intent.py", "dialogue.py", "memory.py"):        # each /api/chat body carries it
        bodies = re.findall(r'"options": \{[^}]*\}', (SRC / f).read_text("utf-8"))
        assert bodies and all('"num_ctx": num_ctx()' in b for b in bodies), (f, bodies)
