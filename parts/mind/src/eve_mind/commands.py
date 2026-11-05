"""Owner-command mapper (CP-42), rules first. Returns one of:
  {"kind": "control", "op": stop|cancel|resume|home|mode [, "mode"]}
  {"kind": "skill", "skill": name, "params": {...}}
  {"kind": "decide", "verb": approve|reject, "project_id", "version"}
  {"kind": "question", "text": one focused question}
  {"kind": "unknown"}                     -> the LLM planner gets it second
Stop wins: any stop word anywhere maps to stop, so ordinary conversation cannot talk past it.
"""
import math
import re

ORD = {w: i for i, ws in enumerate([("1", "one", "first"), ("2", "two", "second"), ("3", "three", "third"),
                                     ("4", "four", "fourth"), ("5", "five", "fifth"), ("6", "six", "sixth"),
                                     ("7", "seven", "seventh"), ("8", "eight", "eighth"), ("9", "nine", "ninth")], 1)
       for w in ws}
NUM = r"-?\d+(?:\.\d+)?"
STOP_WORDS = {"stop", "halt", "freeze"}
WALK = r"\b(walk|go|head|move|come|run|travel|get)\b"
# O111 (live: "come to me" asked for x y z): named referents for the OWNER'S OWN place, whole-line imperatives only -
# "my base / my house" are not his position (home handles "base"), a line with coordinates keeps its coordinates
ASK_POS, ASK_WHERE_YOU = "Where exactly? Give me the x y z coordinates.", "I can't see you from here - where are you? Give me x y z."
def _num(s):
    f = float(s)
    return int(f) if f.is_integer() else f


def coords(t):
    """x y z in the text (optionally labelled x/y/z). Returns (pos, partial) - partial when only 1-2 numbers."""
    m = re.search(rf"(?:x\s*=?\s*)?({NUM})[\s,]+(?:y\s*=?\s*)?({NUM})[\s,]+(?:z\s*=?\s*)?({NUM})", t)
    if m:
        return {"x": _num(m[1]), "y": _num(m[2]), "z": _num(m[3])}, False
    return None, bool(re.search(NUM, t))


def item_id(name):
    """A spoken item name as a 26.3 item id: exact, else singular/plural ("oak plank" -> minecraft:oak_planks)."""
    name = name.strip().lower().replace(" ", "_")
    iid = name if ":" in name else f"minecraft:{name}"
    from .memory import known_items
    items = known_items()
    if not items or iid in items:
        return iid
    for cand in (iid + "s", iid[:-1] if iid.endswith("s") else None, iid[:-2] if iid.endswith("es") else None):
        if cand and cand in items:
            return cand
    return iid


QUESTION_START = re.compile(r"^(what|why|did|where|when|who|how|are|is|do|does|have|has|were|was|which|whose|can|"
                            r"could|would|will|should)\b")
REPORT_START = re.compile(r"^(the|that|this|it|its|it's|your|my|our|his|her|their|a|an|i|i'd|i've|i'm|we|we've|we'd|"
                          r"he|she|they|someone|somebody)\b")      # "I had to stop the server earlier" is a report


STOP_VERB = r"(?<![-\w])(?:stop|halt|freeze|hold on|hold it|hold up|stay put|stay there|don't move)(?![-\w])"
LEAD = re.compile(r"^(?:(?:hey|eve|ok|okay|whoa|no|yo|please|just|now|right|alright|wait|guys?|that's enough)\b[\s,!.]*)+")
REQUEST = re.compile(r"^(?:can|could|would|will) you (?:please |just |now )?" + STOP_VERB)
DIRECTIVE = re.compile(r"\b(?:you (?:need|have|got|must|better|gotta|should|ought)(?: to)?|i (?:need|want) you to|"
                       r"time to|let's|lets) " + STOP_VERB)
NEGATED = re.compile(r"\b(?:don't|do not|dont|never|not|no need to|won't|can't|cannot|couldn't|didn't|doesn't|"
                     r"wouldn't|shouldn't) (?:\w+ )?" + STOP_VERB)
GOVERNED = re.compile(r"\b(?:i|we|he|she|they|it|i'll|we'll|i'm|i've|we're|to|should|could|would|will|might|may|"
                      r"must|did|does|do|had|has|have|gonna|going to|says|said|thinks|reckons|swears|told me) "
                      r"(?:\w+ ){0,2}" + STOP_VERB)
NOUN = re.compile(r"\b(?:the|a|an|bus|pit|full|your|my|that|this|our|non) " + STOP_VERB)


def clause_stop(c):
    """One clause: True = a stop command, False = placed as not a stop, None = has the stop verb but unplaceable."""
    if not re.search(STOP_VERB, c):
        return False
    c = LEAD.sub("", c).strip(" ,.!")
    if REQUEST.match(c) and re.search(r"\bstop (?:by|over|in)\b", c):
        return False            # "can you stop by later?" asks for a visit (a halt is never asked as "stop by")
    if re.match(STOP_VERB, c) or REQUEST.match(c) or DIRECTIVE.search(c):
        return True             # imperative, "can you stop?", "you need to stop"
    if NEGATED.search(c) or GOVERNED.search(c) or NOUN.search(c):
        return False            # "don't stop", "couldn't stop laughing", "I had to stop", "the bus stop"
    if c.endswith("?") or QUESTION_START.match(c) or REPORT_START.match(c):
        return False            # a question about stopping, or a report that contains the word
    return None


def stop_meant(t):
    """O25/O25a (orchestrator, 2026-10-20), clause level: a chat line stops Eve when a clause has the stop verb as its
    imperative ("stop", "Eve, stop!", "ok stop, that's far enough") or as a request to Eve ("can you stop?",
    "you need to stop"). A clause the rules place as not a stop - negated ("don't stop, keep going"), governed by a
    subject, modal or reporting verb ("couldn't stop laughing", "I had to stop the server"), the noun ("the bus
    stop") or a question/report about stopping - does not stop. Only a line with the stop verb that no rule can place
    defaults to stop (a missed stop can cost a life; a false stop costs a resume). A line without the stop verb never
    stops. The authenticated /eve stop and the CLI always stop (they do not come through here)."""
    t = t.lower().strip()
    verdicts = [clause_stop(c) for c in re.split(r"[,;:]|(?<=[.!?])\s+|\s+(?:and|but|so|then)\s+", t) if c.strip()]
    if any(v is True for v in verdicts):
        return True
    return any(v is None for v in verdicts)


def map_command(text):
    raw = text.strip()
    t = raw.lower()
    t = re.sub(r"^(hey\s+)?eve[\s,:!]+", "", t)
    words = set(re.findall(r"[a-z']+", t))
    if (words & STOP_WORDS or re.search(r"\bhold (?:on|it|up)\b|\bstay put\b|\bdon't move\b", t)) and stop_meant(t):
        return {"kind": "control", "op": "stop"}
    if words & {"cancel", "abort", "nevermind"}:
        return {"kind": "control", "op": "cancel"}
    from .grammar import owner_place, wood_count   # O205 step 2: these phrases are the one grammar's readings
    o = owner_place(t)      # O111: his own place, whole-line imperative only (after STOP and cancel, which win)
    if o in ("come", "follow"):   # still the question the rules ask (CP-42: "come here") when his place is unknown;
        return {"kind": "question", "text": ASK_POS if o == "come" else ASK_WHERE_YOU,       # the mind resolves it
                "to_owner": o, "ask": {"skill": "walk_to", "params": {"tolerance": 2.0}}}
    if o == "stay":
        return {"kind": "to_owner", "op": "stay"}
    n = wood_count(t)       # O111-b (9) / O158: "chop 8 logs", "get me some wood" = gather {wood} (0.15.0: #minecraft:logs)
    if n:
        return {"kind": "skill", "skill": "gather", "params": {"product": "wood", "count": n}}
    m = re.match(r"^(approve|reject)\s+([a-z0-9][a-z0-9_-]*)\s+v?(\d+)$", t)
    if m:
        return {"kind": "decide", "verb": m[1], "project_id": m[2], "version": int(m[3])}
    m = re.search(r"\b(manual|supervised|autonomous)\b", t)
    if m and "mode" in words:
        return {"kind": "control", "op": "mode", "mode": m[1]}
    pos, partial = coords(t)
    if pos is None and re.search(r"\b(resume|carry on|keep going|go on|continue|you can go)\b", t):
        return {"kind": "control", "op": "resume"}
    if pos is None and re.search(rf"{WALK}.*\b(home|base)\b|^home$|\breturn home\b", t):
        return {"kind": "control", "op": "home"}
    m = re.match(r"^(?:say|tell (?:them|everyone|chat|people))\b\s*:?\s*(.+)$", re.sub(r"^(hey\s+)?eve[\s,:!]+", "", raw, flags=re.I), re.I)
    if m:       # agenda O94y-4 (CP-42 b3 live): say 'kill the lights, movie time' in chat -> the words, not the wrapper
        said = re.sub(r"\s+(?:in|to|into) (?:the )?chat\.?$", "", m[1].strip(), flags=re.I)
        said = re.sub(r"""^"([^"]*)"$|^'((?:[^']|(?<=\w)'(?=\w))*)'$""", lambda q: q[1] or q[2] or "", said)
        return {"kind": "skill", "skill": "chat_say", "params": {"text": said[:256]}}

    def need_pos(skill, extra=None, what=ASK_POS):
        if pos is None:        # O111: the skill her question is for, so the owner's next "x y z" answers it
            return {"kind": "question", "text": what, "ask": {"skill": skill, "params": dict(extra or {})}}
        p = pos if skill in ("walk_to", "look_at") else {k: math.floor(v) for k, v in pos.items()}
        return {"kind": "skill", "skill": skill, "params": {"pos": p, **(extra or {})}}

    if re.search(r"\b(look|face|turn)\b", t):
        return need_pos("look_at", what="What should I look at? Give me x y z.")
    if re.search(r"\b(mine|break|dig)\b", t):
        return need_pos("mine_block", what="Which block? Give me its x y z.")
    m = re.search(r"\b(?:place|put)\s+(?:a\s+|an\s+|some\s+)?([a-z_ ]+?)\s+(?:block\s+)?(?:at|on)\b", t)
    if re.search(r"\b(place|put)\b", t):
        if not m or m[1].strip() in ("block", "it", "that"):
            return {"kind": "question", "text": "Which block, and where? For example: place cobblestone at x y z."}
        return need_pos("place_block", {"item": item_id(m[1])}, "Where should I place it? Give me x y z.")
    if re.search(r"\bopen\b", t):
        return need_pos("open_container", what="Which container? Give me its x y z.")
    if re.search(r"\b(slot|hotbar)\b", t):
        n = next((ORD[w] for w in re.findall(r"[a-z0-9]+", t) if w in ORD), None)
        if n is None:
            return {"kind": "question", "text": "Which hotbar slot, 1 to 9?"}
        return {"kind": "skill", "skill": "select_hotbar", "params": {"slot": n - 1}}
    if words & {"crouch", "sneak"} and not words & {"un", "uncrouch"}:
        return {"kind": "skill", "skill": "sneak", "params": {"on": True}}
    if re.search(r"\b(stand up|uncrouch|stop crouching)\b", t):
        return {"kind": "skill", "skill": "sneak", "params": {"on": False}}
    if "jump" in words:
        return {"kind": "skill", "skill": "jump", "params": {}}
    # "there's lava at x y z" / "there is a door at ..." state a fact; they are not "go over there" (CP-44 set 2:
    # two owner statements became walks, one toward lava)
    statement = re.search(r"\bthere(?:'s|\s+is|\s+are|\s+was|\s+were|\s+might|\s+may|\s+could)\b|"
                          r"\b(?:is|are)\s+(?:at|in|under|near|around)\b", t)
    if not statement and (re.search(WALK, t) or re.search(r"\bover\b|\bhere\b|\bthere\b", t)):
        if partial and pos is None:
            return {"kind": "question", "text": "Which x y z exactly? I need all three coordinates."}
        return need_pos("walk_to")
    return {"kind": "unknown"}
