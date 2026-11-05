"""O30 intent routing: the local model makes ONE constrained choice per chat line - an intent from a fixed enum, a
registry skill (or none), a records topic and a few words of slots - and nothing else. It never decides a stop (the
deterministic O25a classifier runs first), never runs anything (a `command` still goes through map_command, the
planner and approvals), and never states a fact (the facts layer resolves the question from the records).
Unavailable or invalid output -> None, and the caller falls back to the rule router."""
import asyncio
import json
import os
import urllib.request
from .config import num_ctx

INTENTS = ("command", "capability_question", "records_question", "remember_fact", "passed_on_claim",
           "mechanics_question", "advice", "small_talk", "about_eve")
TOPICS = ("location", "preference", "action_outcome", "objective", "untrusted", "status", "day", "memory", "other")

PROMPT = """You sort ONE chat line from Hunter (the owner) to Eve, a Minecraft companion, into exactly one intent.
Intents:
- command: Hunter tells Eve to DO something now ("go mine some coal", "put the planks in the chest", "come here").
  A short imperative addressed to Eve is a command even with no coordinates ("duck", "spin around", "fetch my
  sword", "wave at Alex").
- capability_question: Hunter asks whether Eve CAN do something ("could you smelt iron?", "are you able to swim?").
  A "can you ...?" question is ALWAYS capability_question, never command. Set skill to the one skill below that is
  made for exactly that ("take things out of a chest" -> withdraw, "dig up coal" -> mine_resource, "make a pickaxe"
  -> craft), or "none" when no skill is made for it (sleeping, fishing, trading, swimming, riding, writing -> none;
  use_item is NOT a way to do those).
- records_question: Hunter asks about something that happened, where something is, what he told Eve, Eve's state,
  progress of a job, or what another player asked ("did the wool go in?", "where's my base?", "what's my favourite
  block?", "how's the wall going?", "what did Sam want?", "are you busy?"). Set topic:
  location (where something is) | preference (what Hunter likes) | action_outcome (whether/how/why an action Eve did
  went: placed, mined, moved items, crafted, smelted, walked, stopped) | objective (a bigger project's progress) |
  untrusted (what another player asked) | status (what Eve is doing now / stopped / idle) | day (summary of today) |
  memory (anything else Hunter told Eve) | other. For action_outcome also set skill to the skill of that action
  (digging/breaking = mine_block, putting into a container = deposit, taking out = withdraw, building a block =
  place_block, cooking = smelt, making = craft, going somewhere = walk_to).
- remember_fact: Hunter states a fact for Eve to remember ("my base is at 10 64 20", "I prefer spruce doors").
- passed_on_claim: Hunter repeats what SOMEONE ELSE says, thinks or read ("my friend says...", "I read that...").
- mechanics_question: a general question about how Minecraft works, not about this world's records.
- advice: Hunter asks how to do something in Minecraft ("best way to find diamonds?").
- small_talk: chatting, feelings, jokes, stories about Hunter's day, thanks, greetings.
- about_eve: questions about Eve's own nature, feelings, dreams or favourites ("do you get lonely?").
Slots: subject = the thing asked about in Hunter's words (short), item = an item/block named (plain words, "" if
none), who = the other person for passed_on_claim/untrusted ("" if none).
Skills (name: what it does):
{skills}
Answer with JSON only."""


def schema(skills):
    return {"type": "object", "required": ["intent", "skill", "topic", "subject", "item", "who"],
            "properties": {"intent": {"enum": list(INTENTS)}, "skill": {"enum": list(skills) + ["none"]},
                           "topic": {"enum": list(TOPICS)}, "subject": {"type": "string", "maxLength": 60},
                           "item": {"type": "string", "maxLength": 40}, "who": {"type": "string", "maxLength": 40}}}


def _post(url, body, timeout):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def validate(out, skills):
    """The model's choice, or None if it is not exactly one of the allowed values."""
    if not isinstance(out, dict) or out.get("intent") not in INTENTS or out.get("topic") not in TOPICS:
        return None
    sk = out.get("skill")
    if sk not in skills:
        sk = None
    return {"intent": out["intent"], "skill": sk, "topic": out["topic"],
            "subject": str(out.get("subject") or "")[:60].lower(), "item": str(out.get("item") or "")[:40].lower(),
            "who": str(out.get("who") or "")[:40]}


async def classify(m, text, timeout=20):
    """Cached per line (owner_text and the dialogue share one call). None = unavailable; use the rule router."""
    cache = m.__dict__.setdefault("_intents", {})
    if text in cache:
        return cache[text]
    fn = getattr(m, "intent_fn", "model")
    if fn is None:                                   # unit tests and runs without a model: rule router only
        return None
    skills = {k: v for k, v in m.registry.skills.items() if k != "stop"}     # stop is a control, never a capability
    if fn != "model":
        out = await fn(text, sorted(skills))
    else:
        lines = "\n".join(f"- {n}: {s.get('summary', '')[:110]}" for n, s in sorted(skills.items()))
        url = (os.environ.get("EVE_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/") + "/api/chat"
        model = os.environ.get("EVE_PLANNER_MODEL") or "qwen3:8b"
        body = {"model": model, "stream": False, "format": schema(sorted(skills)), "options": {"temperature": 0, "num_ctx": num_ctx()},
                "keep_alive": "10m", "messages": [{"role": "system", "content": PROMPT.format(skills=lines)},
                                                  {"role": "user", "content": text}]}
        if model.startswith("qwen3"):
            body["think"] = False
        try:
            r = await asyncio.wait_for(asyncio.to_thread(_post, url, body, timeout), timeout + 2)
            out = json.loads(r["message"]["content"])
        except Exception:                            # model down, timeout, bad JSON: rule router
            return None
    res = validate(out, skills)
    if len(cache) > 200:
        cache.clear()
    cache[text] = res
    return res
