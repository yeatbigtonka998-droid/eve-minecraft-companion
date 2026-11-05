"""O175 part 4 / O177: one owner command grammar over every skill (design: side note mind-b-O175-grammar-design.md,
approved by kickoff). parse(line, ctx) -> {verb, object, qty, place} or None; pure (no model, no I/O). The existing
dispatchers consume it; the per-module regexes stay behind it as the fallback.

  verb:   collect|mine|chop|craft|smelt|kill|harvest|find|place|give|go|come|follow|build|deposit|withdraw|shoot
  object: {kind: block|tag|item|entity|pronoun|none, id, words}
  qty:    {kind: exact|up_to|at_least|nearest|all|some|open|none, n}
  place:  {kind: near_owner|near_her|here|there|anchor|radius|none, anchor, radius}
"""
import re

OWNER_RADIUS = 100           # O177/O238: the spoken-radius cap (registry 0.15.3); mind's workstations.owner_radius
                             # clamps to the served registry's max, so 0.15.2 stays at 32
AROUND_HERE = 16             # "around here" / "nearby" default radius, capped at OWNER_RADIUS
SOME = 8                     # "some" / "a few" / one open-ended chunk

LEAD = re.compile(r"^(?:(?:hey|ok|okay|alright|right|now|so)\s+)*(?:eve[\s,:!]+)?(?:(?:please|pls|can you|could you|would you|"
                  r"will you|i want you to|i need you to|go and|go|try to|start|begin)\s+)*", re.I)
TAIL = re.compile(r"(?:\s+(?:please|pls|for me|for us|now|right now|thanks|thank you))+$", re.I)

VERBS = [   # (his words, canonical verb) - longest first where they overlap
    ("chop down", "chop"), ("cut down", "chop"), ("chop", "chop"), ("cut", "chop"), ("fell", "chop"),
    ("mine up", "mine"), ("dig up", "mine"), ("mine", "mine"), ("dig", "mine"), ("break", "mine"), ("quarry", "mine"),
    ("pick up", "collect"), ("gather up", "collect"), ("collect", "collect"), ("gather", "collect"), ("get", "collect"),
    ("grab", "collect"), ("fetch", "collect"), ("bring", "collect"),
    ("craft", "craft"), ("make", "craft"),
    ("smelt", "smelt"), ("cook", "smelt"), ("bake", "smelt"),
    ("kill", "kill"), ("attack", "kill"), ("fight", "kill"), ("slay", "kill"), ("hunt", "kill"),
    ("shoot", "shoot"),
    ("harvest", "harvest"), ("farm", "harvest"),
    ("look for", "find"), ("search for", "find"), ("find", "find"), ("locate", "find"),
    ("where can i find", "find"), ("where is", "find"), ("where's", "find"), ("where are", "find"),
    ("place", "place"), ("put down", "place"), ("set down", "place"),
    ("give", "give"), ("hand", "give"), ("pass", "give"), ("throw", "give"), ("toss", "give"),
    ("deposit", "deposit"), ("store", "deposit"), ("stash", "deposit"),
    ("withdraw", "withdraw"), ("take out", "withdraw"),
    ("go to", "go"), ("walk to", "go"), ("head to", "go"), ("come", "come"), ("follow", "follow"), ("build", "build"),
]
VERB_RE = re.compile(r"^(" + "|".join(re.escape(w) + (r"(?!\s+to\b)" if w.endswith(" up") else "") for w, _ in
                                      sorted(VERBS, key=lambda v: len(v[0]), reverse=True)) + r")\b(.*)$", re.I)
#                                      "mine up some logs" is mine; "mine up to 32 coal" is mine + a limit
VERB_OF = dict(VERBS)
GERUND = {"mining": "mine", "chopping": "chop", "collecting": "collect", "gathering": "collect", "digging": "mine",
          "killing": "kill", "fighting": "kill", "harvesting": "harvest", "cutting": "chop", "getting": "collect"}

UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
WORDNUM = re.compile(r"\b(?:(" + "|".join(TENS) + r")(?:[\s-](" + "|".join(k for k in UNITS if UNITS[k] < 10) + r"))?|("
                     + "|".join(UNITS) + r")|(a hundred|one hundred)|(a dozen|one dozen|dozen)|(a couple of|a couple|couple of|"
                     r"a pair of))\b", re.I)


def numbers(t):
    """Number words -> digits ("seventeen" -> 17, "twenty one" -> 21, "a dozen" -> 12, "a couple of" -> 2)."""
    def one(m):
        if m[1]:
            return str(TENS[m[1].lower()] + (UNITS[m[2].lower()] if m[2] else 0))
        if m[3]:
            if m[3].lower() == "one" and re.search(r"\b(?:the|that|this|which|each|any|every|no)\s+$", m.string[:m.start()],
                                                   re.I):
                return m[3]                              # "that one", "the one I just placed": which, not how many
            return str(UNITS[m[3].lower()])
        return "100" if m[4] else "12" if m[5] else "2"
    return WORDNUM.sub(one, t)


PRONOUN = re.compile(r"^(?:it|that|this|them|those|these|one|that one|this one)$", re.I)
WOOD = re.compile(r"^(?:(?:wood(?:en)?\s+)?logs?|wood|woods|trees?|timber|lumber|(?:the\s+)?forest)$", re.I)
SPECIES = ("dark oak", "pale oak", "oak", "spruce", "birch", "jungle", "acacia", "mangrove", "cherry", "crimson", "warped")
ENTITIES = {"zombie", "husk", "drowned", "skeleton", "stray", "spider", "cave spider", "creeper", "witch", "slime",
            "pillager", "vindicator", "phantom", "enderman", "silverfish", "blaze", "piglin", "hoglin", "ghast",
            "magma cube", "warden", "sheep", "cow", "pig", "chicken", "rabbit", "goat", "horse", "llama", "wolf", "fox",
            "squid", "cod", "salmon", "turtle", "mob", "monster", "hostile", "animal"}
HOSTILE_ALL = {"mob", "monster", "hostile"}

PLACE_RULES = [   # (pattern at the end of the rest, kind, radius) - stripped before the object is read
    (r"\s+within (\d+) blocks?(?: of (?:me|you|us|here))?$", "radius", None),
    (r"\s+(?:near|by|next to|around|close to|beside) (?:me|us)$", "near_owner", AROUND_HERE),
    (r"\s+(?:near|by|next to|around|close to|beside) you$", "near_her", AROUND_HERE),
    (r"\s+(?:around|near|over) here$|\s+here$|\s+(?:in|from) this area$|\s+around$", "here", AROUND_HERE),
    (r"\s+over there$|\s+there$|\s+(?:that|this) way$", "there", AROUND_HERE),
    (r"\s+(?:that you can see|you can see|in sight|close by|nearby|around you|near you)$", "near_her", AROUND_HERE),
    (r"\s+(?:from|off|of) (?:the |those |these |some )?(?:nearby|closest|nearest) (trees?)$", "near_her", AROUND_HERE),
    (r"\s+(?:near|by|next to|around|at|from|close to|beside|in|on|off|of|over by) (?:the |that |this |those |our |my |your )?"
     r"((?:nearby |closest |nearest )?[a-z ]+?)$", "anchor", None),
]


def _place(rest):
    for pat, kind, r in PLACE_RULES:
        m = re.search(pat, rest, re.I)
        if not m:
            continue
        if kind == "radius":
            return rest[:m.start()], {"kind": "radius", "anchor": None, "radius": min(int(m[1]), OWNER_RADIUS)}
        if kind == "anchor":
            anchor = re.sub(r"^(?:nearby|closest|nearest)\s+", "", m[1]).strip()
            if anchor in ("me", "us", "you", "here", "there"):
                continue
            return rest[:m.start()], {"kind": "anchor", "anchor": anchor, "radius": AROUND_HERE}
        if kind == "near_her" and m.lastindex:            # "from nearby trees": where (near her) AND an anchor word
            return rest[:m.start()], {"kind": "near_her", "anchor": m[1], "radius": AROUND_HERE}
        return rest[:m.start()], {"kind": kind, "anchor": None, "radius": r}
    return rest, {"kind": "none", "anchor": None, "radius": None}


QTY_RULES = [     # (pattern on the rest, kind) - group 1 = n, group 2 = what is left
    (r"^(?:the\s+)?(\d+)\s+(?:nearest|closest)\s+(.*)$", "nearest"),
    (r"^(?:the\s+)?(?:nearest|closest)\s+(\d+)\s+(.*)$", "nearest"),
    (r"^(?:the\s+)?(?:nearest|closest)\s+(.*?)\s+up to\s+(\d+)$", "nearest_up_to"),
    (r"^(?:the\s+)?(?:nearest|closest)\s+(.*)$", "nearest_one"),
    (r"^(?:up to|no more than|at most|max(?:imum)?(?: of)?)\s+(\d+)\s+(.*)$", "up_to"),
    (r"^(.*?)\s+up to\s+(\d+)$", "up_to_tail"),
    (r"^(?:at least|no less than|minimum(?: of)?|a minimum of)\s+(\d+)\s+(.*)$", "at_least"),
    (r"^(\d+)\s*(?:-|to)\s*(\d+)\s+(.*)$", "range"),
    (r"^(\d+)\s+(.*)$", "exact"),
    (r"^(?:all(?: of)?(?: the| your| my)?|every|each|everything|all the)\s+(.*)$", "all"),
    (r"^(?:some|a few|a bunch of|a load of|a lot of|lots of|several|a stack of|any)\s+(.*)$", "some"),
    (r"^(?:a|an|the|one)\s+(.*)$", "one"),
]
OPEN = re.compile(r"\s+(?:until i (?:say|tell you)(?: (?:to )?stop| so| otherwise)?|until (?:you're told|told)(?: otherwise)?|"
                  r"nonstop|non-stop|forever|all day|until i say stop|till i say(?: stop)?|until further notice|"
                  r"for a while|continuously)$", re.I)


def _qty(rest):
    for pat, kind in QTY_RULES:
        m = re.match(pat, rest, re.I)
        if not m:
            continue
        if kind == "nearest_up_to":
            return m[1], {"kind": "up_to", "n": int(m[2])}   # O200: a cap, nearest first
        if kind == "nearest_one":
            return m[1], {"kind": "nearest", "n": 1}
        if kind == "up_to_tail":
            return m[1], {"kind": "up_to", "n": int(m[2])}
        if kind == "range":
            return m[3], {"kind": "up_to", "n": max(int(m[1]), int(m[2]))}
        if kind == "all":
            return m[1], {"kind": "all", "n": None}
        if kind == "some":
            return m[1], {"kind": "some", "n": SOME}
        if kind == "one":
            return m[1], {"kind": "exact", "n": 1} if re.match(r"^(?:a|an|one)\s", rest, re.I) else {"kind": "none", "n": None}
        return m[2], {"kind": kind, "n": int(m[1])}
    return rest, {"kind": "none", "n": None}


def _singular(w):
    if w.endswith("ies") and w[:-1] in ENTITIES | {"cookie", "pie"}:
        return w[:-1]                                  # zombies -> zombie, not "zomby"
    return re.sub(r"ies$", "y", w) if w.endswith("ies") else re.sub(r"(?<=[sxz]h|ch)es$|(?<=[sxz])es$", "", w) \
        if re.search(r"(?:[sxz]|ch|sh)es$", w) else w[:-1] if w.endswith("s") and not w.endswith("ss") else w


def _said(words):
    """What he named, before any stripping but the determiners and "exposed"; and whether he said exposed/open."""
    w = words.strip().lower()
    return (re.sub(r"^(?:(?:the|some|my|your|our|of|that|this|those|these|exposed|open|visible|surface)\s+)+", "", w).strip(),
            bool(re.match(r"^(?:(?:the|some)\s+)?(?:exposed|open|visible|surface)\b", w)))


def _object(words, verb, ctx):
    w = re.sub(r"^(?:(?:the|some|my|your|our|of|that|this|those|these|exposed|open|visible|surface)\s+)+", "",
               words.strip().lower()).strip()                  # "that creeper", "exposed stone": what, not which
    w = re.sub(r"\s+(?:blocks?|ones?)$", "", w).strip() if not re.search(r"\bblocks?\s+of\b", w) else w
    if not w:
        return {"kind": "none", "id": None, "words": ""}
    if PRONOUN.match(w):
        ref = (ctx or {}).get("last_item") if verb not in ("kill", "shoot") else (ctx or {}).get("last_hostile")
        return {"kind": "pronoun", "id": ref, "words": w}
    if w in ("me", "us"):
        return {"kind": "owner", "id": None, "words": w}
    if w in ("water", "lava"):
        return {"kind": "block", "id": f"minecraft:{w}", "words": w}
    if WOOD.match(w):
        return {"kind": "tag", "id": "minecraft:logs", "words": w}
    for sp in SPECIES:                                       # "oak", "oak logs", "oak trees", "dark oak wood"
        if re.fullmatch(rf"{sp}(?:\s+(?:logs?|trees?|wood))?", w):
            return {"kind": "block", "id": f"minecraft:{sp.replace(' ', '_')}_log", "words": w}
    sing = " ".join(_singular(x) for x in w.split())
    if verb in ("kill", "shoot") or sing in ENTITIES:
        if sing in ENTITIES:
            return {"kind": "entity", "id": None if sing in HOSTILE_ALL or sing == "animal" else
                    f"minecraft:{sing.replace(' ', '_')}", "words": sing}
    if verb in ("mine", "chop", "collect", "find", "harvest"):
        from .planner import resource_params
        p = resource_params(w)
        if p:
            return {"kind": "tag" if "tag" in p else "block", "id": p.get("tag") or p.get("block"), "words": w}
    from .owner_phrases import resolve_item
    iid, _ = resolve_item(w)
    if iid:
        return {"kind": "item", "id": iid, "words": w}
    return {"kind": "unknown", "id": None, "words": w}


def parse(line, ctx=None):
    """One owner line -> {verb, object, qty, place} or None (not an order the grammar knows: the old paths decide)."""
    t = TAIL.sub("", LEAD.sub("", line.strip().rstrip(".!?").strip()))
    t = numbers(t.lower())
    keep = re.match(r"^keep (\w+ing)\b\s*(.*)$", t)
    open_ = False
    if keep and keep[1] in GERUND:                           # "keep mining coal" = mine coal, open-ended
        t, open_ = f"{GERUND[keep[1]]} {keep[2]}".strip(), True
    m = VERB_RE.match(t)
    if not m:
        return None
    verb, rest = VERB_OF[m[1].lower()], m[2].strip()
    rest = re.sub(r"^(?:me|us|you)\s+(?=\S)", "", rest) if verb in ("collect", "craft", "give", "find") else rest
    if OPEN.search(rest):
        rest, open_ = OPEN.sub("", rest).strip(), True
    rest, place = _place(" " + rest)
    rest, qty = _qty(rest.strip())
    if m[1].lower().startswith("where") and re.fullmatch(r"(?:you|me|we|us|they|it|things)", rest.strip()):
        return None                                       # "where are you": a question about her, not a find
    obj = _object(rest, verb, ctx)
    if obj["kind"] == "none" and place["kind"] == "anchor" and verb in ("mine", "chop", "collect"):
        obj = _object(place["anchor"], verb, ctx)            # "mine near trees": the anchor IS what to mine
        if obj["kind"] != "unknown":
            open_ = qty["kind"] == "none"
    if open_:
        qty = {"kind": "open", "n": SOME}
    if verb in ("chop",) and obj["kind"] == "none":
        obj = {"kind": "tag", "id": "minecraft:logs", "words": ""}
    said, exposed = _said(rest)
    return {"verb": verb, "object": obj, "qty": qty, "place": place, "said_verb": m[1].lower(), "raw": rest.strip(),
            "said": said, "exposed": exposed}


def parse_all(line, ctx=None):
    """Several orders in one line ("make a furnace and place it near me") -> [intent, ...]; [] when none parse."""
    from .owner_phrases import split_it
    parts = [p for q in re.split(r",?\s+(?:and\s+)?then\s+|;\s*", line.strip()) for p in split_it(q) if p.strip()]
    out = [parse(p, ctx) for p in parts]
    return out if out and all(out) else []


# ---- O185: the labelled corpus vocabulary (parts/learning/phrase_corpus/labels.py, eaf1270e) - one owner line ->
# [{verb, object, qty, place}]. verbs: mine craft place give deposit drop pickup equip fight go follow unfollow stop
# resume find ride build dig_down query report answer chat progress; objects: ids, "#tags", "tree", "hostiles", "owner",
# "context", query topics; qty kind "unspecified" when none; places: owner near_eve front_of_eve nearby home surface water
# crafting_table chest near_owner "x y z".

TYPOS = [(r"\bcoem\b", "come"), (r"\bdoyou\b", "do you"), (r"\binfront\b", "in front"), (r"\bdandilions?\b", "dandelions"),
         (r"\bpickup\b", "pick up"), (r"\bwhats\b", "what's"), (r"\blets\b", "let's"), (r"^come to m$", "come to me")]
GENERIC = {"plank": "planks", "boat": "boats", "bed": "beds", "sword": "swords", "pickaxe": "pickaxes", "axe": "axes",
           "shovel": "shovels", "hoe": "hoes", "log": "logs", "door": "doors", "sign": "signs"}
COUNTABLE = re.compile(r"(?:pickaxe|sword|axe|shovel|hoe|helmet|chestplate|leggings|boots|boat|bed|table|chest|furnace|"
                       r"shield|bow|crossbow|door|bucket|torch|stick)s?$")
XYZ_RE = re.compile(r"(-?\d+)[\s,]+(-?\d+)[\s,]+(-?\d+)")
Q0 = {"kind": "unspecified", "n": None}


def _st(verb, obj=None, qty=None, place=None):
    return {"verb": verb, "object": obj, "qty": dict(qty or Q0), "place": place}


def _item(words):
    """His words for one thing -> an id, a "#tag" for a generic noun ("a boat" -> #minecraft:boats), or None."""
    w = re.sub(r"^(?:(?:the|a|an|your|my|some|of|raw)\s+)+", "", words.strip().lower()).strip()
    w = re.sub(r"\s+(?:you have|on you)$", "", w)
    if not w:
        return None
    sing = " ".join(_singular(x) for x in w.split())
    if WOOD.match(w):
        return "#minecraft:logs"
    last = sing.split()[-1]
    if last in GENERIC and (len(sing.split()) == 1 or last in ("boat", "plank", "sign", "door") and
                            re.fullmatch(r"(?:wood|wooden)", " ".join(sing.split()[:-1]))):   # a wooden shovel is one item
        return f"#minecraft:{GENERIC[last]}"            # "a boat", "the wood boat", "planks" - any kind
    if sing in ("grass", "grass block"):
        return "minecraft:grass_block"
    if sing == "cobble":
        return "minecraft:cobblestone"
    if re.fullmatch(r"(?:diamond|iron|coal|gold|copper|redstone|lapis|emerald) ores?", w):
        return f"#minecraft:{w.split()[0]}_ores"
    from .owner_phrases import resolve_item
    iid, _ = resolve_item(w)
    if iid:
        return iid
    from .planner import resource_params
    p = resource_params(w)
    return ("#" + p["tag"]) if p and "tag" in p else p["block"] if p else None


def _qty_of(intent_qty, words, verb):
    k, n = intent_qty["kind"], intent_qty["n"]
    if k == "none":
        if verb in ("craft", "place", "fight", "equip", "drop", "find", "ride", "build"):
            return {"kind": "exact", "n": 1}
        if verb in ("give", "deposit", "pickup"):
            last = words.strip().split()[-1] if words.strip() else ""
            return {"kind": "exact", "n": 1} if COUNTABLE.search(last) else {"kind": "all", "n": None}
        return dict(Q0)
    if k == "open":
        return {"kind": "open", "n": None}
    if k == "some":
        return {"kind": "some", "n": 8}
    return {"kind": k, "n": n}


def _where(place):
    k = place["kind"]
    if k == "anchor":
        a = place["anchor"]
        return "water" if a in ("water", "river", "lake", "sea", "pond", "ocean") else \
            "nearby" if a in ("trees", "tree", "forest") else a
    return {"near_owner": "near_owner", "near_her": "nearby", "here": "nearby", "there": "nearby",
            "radius": "nearby"}.get(k)


QUERIES = [
    (r"^(?:what do you have on you|what'?s in your inventory|what is in your inventory|what are you carrying|"
     r"show me your inventory)$", lambda m: "inventory"),
    (r"^where are you$", lambda m: "location"),
    (r"^what'?s on your agenda$|^what is on your agenda$|^what'?s the plan$", lambda m: "agenda"),
    (r"^what (?:can|could) you (?:craft|make)(?: now| right now| with what you have)?$", lambda m: "craftable"),
    (r"^what'?s next$|^what is next$|^what now$", lambda m: "progress:next"),
    (r"\b(?:did you (?:get|finish|find|collect|chop|mine|gather|make|craft|do) (?:all|it|them|everything|the|any|\d)|"
     r"how many (?:so far|have you|did you|you got|do you have now)|are you (?:done|finished)|"
     r"have you (?:finished|got them|got all)|is it (?:done|finished))\b", lambda m: "progress:last_task"),
]


def _query(t):
    for pat, topic in QUERIES:
        m = re.search(pat, t)
        if m:
            return topic(m)
    m = re.match(r"^(?:you (?:don'?t|do not) have|do you have|have you got|you have) (.+?)(?: on you| with you)?$", t)
    if m:
        iid = _item(m[1])
        return f"has:{iid}" if iid else None
    m = re.match(r"^how many (.+?) do you have$", t)
    if m:
        iid = _item(m[1])
        return f"count:{iid}" if iid else None
    m = re.match(r"^can you (?:make|craft) (.+)$", t)
    if m:
        iid = _item(m[1])
        return f"can_craft:{iid}" if iid else None
    m = re.match(r"^what do (?:you|we|i) need (?:for|to make|to craft) (.+)$", t)
    if m:
        iid = _item(m[1])
        return f"recipe:{iid}" if iid else None
    return None


def _clauses(text):
    t = re.sub(r"^\s*plan\s*:\s*", "", text.strip(), flags=re.I)
    return [p.strip(" .!;,") for p in re.split(r"(?<=[.!;])\s*(?=[A-Za-z0-9])", t) if p.strip(" .!;,")]


def _norm(c):
    c = c.strip().lower().rstrip(".!?").strip()
    c = re.sub(r"^(?:hey|ok|okay|so|and now|and|now)[\s,]+", "", c)
    c = re.sub(r"^eve[\s,:!]+", "", c)
    c = re.sub(r"^go and (?=(?:fight|kill|attack|mine|chop|get|craft|make|find)\b)", "", c)   # "go and fight it"
    for pat, rep in TYPOS:
        c = re.sub(pat, rep, c)
    return numbers(c).strip()


def _one(c, prev, ctx):
    """One clause (normalised, no "and then") -> [steps] or None (not understood)."""
    if XYZ_RE.fullmatch(c):
        return [_st("answer", place=" ".join(XYZ_RE.fullmatch(c).groups()))]
    m = re.match(r"^(-?\d+[\s,]+-?\d+[\s,]+-?\d+)\s+(.+)$", c)        # "55 62 -19 place boat"
    if m and re.match(r"(?:place|put)\b", m[2]):
        out = _one(m[2], prev, ctx)
        if out:
            out[0]["place"] = " ".join(XYZ_RE.match(m[1]).groups())
        return out
    if re.fullmatch(r"(?:yes|yeah|yep|yup|sure|ok|okay|correct|right)", c):
        return [_st("answer", "yes")]
    if re.fullmatch(r"(?:no|nope|nah)", c):
        return [_st("answer", "no")]
    if re.fullmatch(r"(?:near you|here|next to you|in front of you|by you)", c):
        return [_st("answer", place="front_of_eve" if "front" in c else "near_eve")]
    if re.fullmatch(r"(?:stop|halt|freeze|stop it|stop now)", c):
        return [_st("stop")]
    if re.fullmatch(r"(?:resume|carry on|continue|go on|you can go)", c):
        return [_st("resume")]
    if re.fullmatch(r"stop following(?: me)?|(?:(?:ok(?:ay)?|now)[\s,]+)?stay(?: here| there)?(?:[\s,]+(?:please|eve))*", c):
        return [_st("unfollow")]
    if re.fullmatch(r"follow(?: me)?", c):
        return [_st("follow", "owner")]
    if re.search(r"\bwe need to progress\b|\blet'?s progress\b|^keep going$|^progress$", c):
        m = re.search(r"progress to (.+)$", c)
        to = ("minecraft:iron_ingot" if m[1].strip() == "iron" else _item(m[1])) if m else None
        return [_st("progress", to)]
    if re.match(r"^do you love me|^do you like me|^i love you", c):
        return [_st("chat", "affection")]
    if re.search(r"\btell me what happened\b|\bwhat stopped you\b", c):
        return [_st("report", "progress:last_task")]
    q = _query(c)
    if q:
        return [_st("query", q)]
    if re.fullmatch(r"(?:let'?s )?(?:go|come|head|walk|get) (?:back )?(?:to )?(?:the )?(?:home|base)|(?:let'?s )?go home|come home", c):
        return [_st("go", place="home")]
    if re.fullmatch(r"(?:come|walk|go|get|run|head|move)(?: back| over)? (?:to me|here|over here|(?:to )?where i am|"
                    r"to my (?:position|location))(?:[\s,]+(?:please|now|eve))*", c):
        return [_st("go", place="owner")]
    m = re.match(r"^(?:come|walk|go|head|move|run)(?: back)? (?:to )?(-?\d+[\s,]+-?\d+[\s,]+-?\d+)$", c)
    if m:
        return [_st("go", place=" ".join(XYZ_RE.match(m[1]).groups()))]
    if re.fullmatch(r"(?:come|get|climb|go) (?:back )?(?:up|out)(?: of (?:the|this) hole| to the surface)?", c):
        return [_st("go", place="surface")]
    m = re.match(r"^(?:go|walk|head|come) (?:to|into|over to) (?:the )?(crafting table|water|chest|furnace)(?: nearby)?$", c)
    if m:
        return [_st("go", place={"crafting table": "crafting_table"}.get(m[1], m[1]))]
    m = re.match(r"^(?:dig|mine) down(?: (\d+)(?: blocks?)?)?$", c)
    if m:
        return [_st("dig_down", qty={"kind": "exact", "n": int(m[1])} if m[1] else None)]
    m = re.match(r"^build (?:a |an |the |me a )?(.+)$", c)
    if m:
        return [_st("build", "structure:" + re.sub(r"\s+", "_", m[1].strip()), {"kind": "exact", "n": 1})]
    m = re.match(r"^(?:get in|get into|ride|board|hop in) (?:the |a )?(.+)$", c)
    if m:
        return [_st("ride", _item(m[1]), {"kind": "exact", "n": 1})]
    m = re.match(r"^(?:equip|hold|wield) (.+)$", c)
    if m:
        return [_st("equip", _item(m[1]), {"kind": "exact", "n": 1})]
    m = re.match(r"^pick up (.+)$", c)
    if m:
        return [_st("pickup", _item(m[1]), _qty_of({"kind": "none", "n": None}, m[1], "pickup"))]
    m = re.match(r"^drop (?:to me|me) ?(.*)$", c)                      # "drop me the boat", "drop to me"
    if m:
        return [_st("give", _item(m[1]) if m[1] else "context", {"kind": "exact", "n": 1}, "owner")]
    m = re.match(r"^drop (.+)$", c)
    if m:
        return [_st("drop", _item(m[1]), {"kind": "exact", "n": 1})]
    m = re.match(r"^(?:give|hand|pass|bring) (?:me|us) (.+)$|^(?:give|hand|pass) (.+?) to me$", c)
    if m:
        words = (m[1] or m[2]).strip()
        if PRONOUN.match(words):                           # "craft a shovel and give it to me": it = the shovel
            ref = prev[-1]["object"] if prev and prev[-1]["verb"] in ("craft", "mine", "pickup") else "context"
            return [_st("give", ref, {"kind": "exact", "n": 1}, "owner")]
        n = re.match(r"^(\d+)\s+(.+)$", words)
        return [_st("give", _item(n[2] if n else words), {"kind": "exact", "n": int(n[1])} if n else
                    _qty_of({"kind": "none", "n": None}, words, "give"), "owner")]
    m = re.match(r"^(?:put|store|deposit|stash) (.+?) (?:into|in) (?:the |a )?chest\b.*$", c)
    if m:
        words = re.sub(r"^(?:your|my|the|all)\s+", "", m[1])
        n = re.match(r"^(\d+)\s+(.+)$", words)
        return [_st("deposit", _item(n[2] if n else words), {"kind": "exact", "n": int(n[1])} if n else
                    _qty_of({"kind": "none", "n": None}, words, "deposit"), "chest")]
    m = re.match(r"^(?:place|put|set)(?: down)? (.+?)(?: (?:on the ground )?(near you|next to you|beside you|by you|here)|"
                 r" in the block in front of you| in front of you| in the water| on the water)?$", c)
    if m:
        what = m[1].strip()
        place = "near_eve" if m[2] else "front_of_eve" if "front" in c else "water" if "water" in c else None
        if place is None and prev and prev[-1]["verb"] == "go" and prev[-1]["place"] == "water":
            place = "water"                              # "go to the water nearby and place the boat" - there
        obj = "context" if PRONOUN.match(what) else _item(what)
        if obj == "context" and prev and prev[-1]["verb"] == "craft":
            obj = prev[-1]["object"]                      # "craft a table and place it": it = the table
        return [_st("place", obj, {"kind": "exact", "n": 1}, place)] if obj else None
    m = re.match(r"^craft (?:the )?(\d+) logs? into planks$", c)        # 1 log = 4 planks
    if m:
        return [_st("craft", "#minecraft:planks", {"kind": "exact", "n": int(m[1]) * 4})]
    c = re.sub(r"^use (?:the |your )?[a-z ]+? to ", "", c)              # "use the wooden shovel to mine ..."
    c = re.sub(r"^clear\b", "mine", c)
    c = re.sub(r"^(?:go and|go) (?=(?:fight|kill|attack|mine|chop|get|craft|make)\b)", "", c)
    if re.fullmatch(r"(?:fight|attack|kill)(?: them)?", c):
        return [_st("fight", "hostiles")]
    if FIGHT_ALL.fullmatch(c):
        return [_st("fight", "hostiles", {"kind": "all", "n": None})]   # O165: every hostile, nearest first
    it = parse(c, ctx)
    if it is None:
        return None
    verb = {"chop": "mine", "collect": "mine", "harvest": "mine", "kill": "fight", "shoot": "fight"}.get(it["verb"], it["verb"])
    o, words = it["object"], it["object"]["words"]
    if verb == "fight":
        if o["kind"] == "pronoun":
            obj = "context"
        elif (o["kind"] == "entity" and o["id"] is None) or re.fullmatch(r"(?:all of )?them|everything|every ?one", words):
            obj = "hostiles"
        else:
            obj = "minecraft:" + re.sub(r"\s+", "_", " ".join(_singular(x) for x in words.split()))
    elif re.search(r"\btrees?\b", words) or (o["kind"] == "none" and it["place"]["anchor"] in ("trees", "tree")):
        obj = "tree"
    elif o["kind"] == "pronoun":
        obj = "context"
    elif o["kind"] == "owner":
        obj = "owner"
    else:
        obj = _item(words) if words else None
        if obj is None and o["id"]:
            obj = ("#" + o["id"]) if o["kind"] == "tag" else o["id"]
    qty = _qty_of(it["qty"], words, verb)
    if obj == "tree" and it["qty"]["kind"] == "nearest" and it["qty"]["n"] == 1 and re.search(r"\btrees\b", words):
        qty = {"kind": "nearest", "n": None}            # "the nearest trees" - open, nearest first
    if obj == "tree" and re.search(r"\bnearest\b.*\bup to (\d+)$", c):
        qty = {"kind": "up_to", "n": int(re.search(r"up to (\d+)$", c)[1])}
    if verb == "fight" and obj == "hostiles":
        qty = {"kind": "all", "n": None} if it["qty"]["kind"] == "all" else dict(Q0)
    place = _where(it["place"])
    if verb == "mine" and re.search(r"\b(?:to|near|by) me$", c):
        place = "near_owner"
    if verb == "find" and qty["kind"] in ("nearest", "unspecified"):
        qty = {"kind": "exact", "n": qty["n"] or 1}
    return [_st(verb, obj, qty, place)]


def _bare_answer(c):
    """A bare noun phrase answering her question ("the boat", "it being the chest you have to craft")."""
    m = re.match(r"^(?:it(?:'s| is| being)? |i mean |i meant )?(?:the |a |an )(.+?)(?: you have to (?:craft|make)| i mean)?$", c)
    if not m:
        return None
    iid = _item(m[1])
    return [_st("answer", iid)] if iid else None


def steps(text, ctx=None):
    """O185: one owner line in the corpus vocabulary -> [steps] ([] when not understood)."""
    from .owner_phrases import split_it
    out = []
    clauses = _clauses(text)
    if len(clauses) > 1 and re.match(r"^your goal is\b", clauses[0], re.I):
        goal = steps(re.sub(r"^your goal is to\s+", "", clauses[0], flags=re.I).split(" and ")[0], ctx)
        if goal:                                          # "Your goal is to mine diamond ore and collect at least one
            goal[0]["qty"] = {"kind": "at_least", "n": 1} #   diamond. First, stay somewhere safe and report: ..."
        return [_st("report", "status_report")] + goal
    for c in clauses:
        nc = _norm(c)
        if len(clauses) > 1 and (ABOUT_THE_TASK.match(nc) and not re.match(
                r"^(?:then )?stop and tell me\b|^tell me\b|^report\b", nc) or re.match(r"^(?:check directly|do not use)", nc)):
            continue                                       # how to do it / what she does anyway (O157)
        nc = re.sub(r"^(?:then )?stop and (?=tell me)", "", nc)
        nc = re.sub(r"^check your (?:own )?inventory right now$", "", nc)
        if not nc:
            continue
        which = re.match(r"^tell me which (\w+?)s? you", nc)
        if which:
            out.append(_st("query", f"inventory:#minecraft:{which[1]}s"))
            continue
        pieces = [p for q in re.split(r",?\s+(?:and\s+)?then\s+|,\s+(?=(?:make|craft|place|mine|get|come|go)\b)|"
                                      r"\s+and\s+(?=(?:then\s+)?(?:make|craft|place|mine|get|come|go|give|dig|fight)\b)", nc)
                  for p in split_it(q) if p.strip()]
        for p in pieces:
            got = _one(p.strip(), out, ctx) or _bare_answer(p.strip())
            if got is None:
                return out
            out += got
    return out


# ---- O200: the old per-module detectors, retired into the grammar (their callers keep their names and contracts)

FIGHT_ALL = re.compile(r"(?:fight|attack|kill|get|shoot|take out|deal with)(?: them)? (?:all(?: of)?(?: them)?|everything|"
                       r"every ?one(?: of them)?)(?: too| as well| now)?|(?:them all|all of them|every ?one of them|"
                       r"everything|all the mobs|all of the mobs)(?: too| as well| now)?|(?:keep (?:fighting|going at it|"
                       r"at it|at them|attacking|killing)|don'?t stop fighting)(?: them| all of them)?")
ORDER_VERB = re.compile(r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:please\s+|can you\s+|could you\s+)?(?:go\s+(?:and\s+)?)?"
                        r"(?:collect|chop|cut|mine|dig|get|gather|harvest|fetch|grab|bring|craft|make|build|place|put|go|"
                        r"walk|come|follow|kill|fight|attack|shoot|smelt|cook|store|deposit|take|find|clear|plant|light|"
                        r"equip|give)\b", re.I)
ABOUT_THE_TASK = re.compile(r"^\s*(?:and\s+)?(?:then\s+)?(?:(?:stop|halt)\b|tell me\b|explain\b|report\b|let me know\b|"
                            r"if you (?:can'?t|cannot|can not|don'?t|couldn'?t|fail)\b|break the (?:log|wood|tree)\w*\b|"
                            r"pick up\b|collect the drops\b|come back\b|when you'?re done\b)", re.I)


def is_fight_all(text):
    """O165: "fight all of them / all of them / everything / keep fighting / get them all"."""
    return bool(FIGHT_ALL.fullmatch(_norm(text)))


def query_topic(text):
    """The question topic of a line ("progress:last_task", "inventory", "has:<id>", ...), or None."""
    return _query(_norm(text))


def order_clauses(text):
    """O157: a message of several sentences that starts with an order -> its order sentences; sentences about the same
    task (how to do it, "then stop and tell me what happened", "if you cannot, explain") are dropped - she does them
    anyway - so their "stop" ends nothing. None = one sentence, or not an order."""
    parts = [p.strip(" .!;") for p in re.split(r"(?<=[.!;])\s+", text.strip()) if p.strip(" .!;")]
    if len(parts) < 2 or not ORDER_VERB.match(parts[0]):
        return None
    return [p for p in parts if not ABOUT_THE_TASK.match(p)] or None


GATHER_SAID = re.compile(r"^(?:chop|cut|mine|dig|gather|collect|get|harvest|fetch|grab|bring|pick up|gather up|mine up|"
                         r"dig up|chop down|cut down)$")
ASK_VERBS = re.compile(r"^(?:chop|cut|mine|gather|collect|harvest|mine up|chop down|cut down|gather up)$")


def resource_order(text):
    """planner.gather_order's contract from the grammar: "chop 8 logs" / "mine some coal" / "get 10 dirt" ->
    {"skill": "mine_resource", "params"} | {"question", "ask"} | None (not a gather order). No count means 8."""
    from .planner import resource_params
    if XYZ_RE.search(text):
        return None
    it = parse(text)
    if it is None or not GATHER_SAID.match(it["said_verb"]) or it["object"]["kind"] in ("pronoun", "owner", "none"):
        return None
    raw = it["raw"].lower()
    raw = re.sub(r"^(?:\d+|some|a few|a bunch of|a load of|a lot of|lots of|several|any|up to \d+|at least \d+|all(?: the)?|"
                 r"the \d+ nearest|\d+ nearest|nearest|closest)\s+", "", raw)
    if re.match(r"(?:it|that|this|them|those|there|here|a|an|the|my|your|our|his|her|me)\b", raw):
        return None                                       # "mine that block", "get me a pickaxe": not a resource order
    q = it["qty"]
    n = q["n"] if q["kind"] in ("exact", "up_to", "at_least", "nearest") and q["n"] else 8
    said = it["said"]
    p = resource_params(said) or resource_params(it["object"]["words"])
    if p is None and it["object"]["kind"] in ("tag", "block") and it["object"]["id"]:
        p = {("tag" if it["object"]["kind"] == "tag" else "block"): it["object"]["id"]}
    if p is None:
        if not ASK_VERBS.match(it["said_verb"]):
            return None                                   # "get/fetch/dig X": only a known resource is a gather order
        return {"question": f"Which block do you mean by '{said}'? Name one, like oak log or iron ore.",
                "ask": {"kind": "resource", "count": min(max(n, 1), 256)}}
    return {"skill": "mine_resource", "params": {**p, "count": min(max(n, 1), 256)}}


FLUID_IDS = {"water": "minecraft:water", "lava": "minecraft:lava"}


def find_order(text):
    """owner_phrases.find_target's contract from the grammar: find / locate / look for / where is (the nearest) X ->
    {"what", "ids", "suffix", "exposed"}; None when it is not that, or X is no block (a chest, a player, home)."""
    from .planner import resource_params
    it = parse(text)
    if it is None or it["verb"] != "find":
        return None
    what = it["said"]
    what = re.sub(r"^(?:me\s+)?(?:some\s+|any\s+|a\s+|an\s+|the\s+)?(?:(?:nearest|closest)\s+)?", "", what).strip()
    if not what or re.match(r"(?:you|me|it|that|home|base|my|your|chest|barrel|furnace|bed|crafting)\b", what):
        return None
    exposed = it["exposed"]
    if what in FLUID_IDS:
        return {"what": what, "ids": [FLUID_IDS[what]], "suffix": None, "exposed": False}
    p = resource_params(what)
    if p is None:
        return None
    if "block" in p:
        return {"what": what, "ids": [p["block"]], "suffix": None, "exposed": exposed or what in ("stone", "rock")}
    tag = p["tag"].split(":")[-1]                       # logs -> any *_log; coal_ores -> *coal_ore
    suffix = "_log" if tag == "logs" else tag[:-1] if tag.endswith("_ores") else None
    return {"what": what, "ids": None, "suffix": suffix, "exposed": exposed} if suffix else None


# ---- O205: owner_phrases' skill families read by the grammar - one intent per line for combat, builder, transfer,
# smelt, craft, equip, harvest (and the skills sheet's prospect / dig_up / shear / milk / open). The rules in
# owner_phrases now take their params from intent(); their own phrase regexes are gone.

PHRASE_VERBS = [
    ("gear up", "equip"), ("suit up", "equip"), ("put on your armour", "equip"), ("put on your armor", "equip"),
    ("put your armour on", "equip"), ("put your armor on", "equip"), ("put on armour", "equip"), ("put on armor", "equip"),
    ("wear your armour", "equip"), ("wear your armor", "equip"),
    ("use your bow", "shoot"), ("take it out from here", "shoot"), ("take them out from here", "shoot"), ("snipe", "shoot"),
    ("use your best", "equip"), ("use your", "equip"), ("get your best", "equip"), ("get your", "equip"),
    ("take your best", "equip"), ("take your", "equip"), ("get out your", "equip"), ("take out your", "equip"),
    ("hold your best", "equip"), ("hold your", "equip"), ("draw your", "equip"),
    ("guard", "guard"), ("defend", "guard"), ("hold", "guard"),
    ("run away", "flee"), ("get away", "flee"), ("back off", "flee"), ("flee", "flee"), ("run", "flee"),
    ("fight with me", "kill"), ("help me fight", "kill"), ("help me", "kill"), ("get them", "kill"), ("deal with", "kill"),
    ("clear out", "clear"), ("clear", "clear"),
    ("build a bridge", "bridge"), ("build me a bridge", "bridge"), ("bridge over", "bridge"), ("bridge", "bridge"),
    ("make a path across", "bridge"), ("make a path", "bridge"), ("make me a path", "bridge"), ("make a bridge", "bridge"),
    ("make me a bridge", "bridge"), ("get across", "bridge"),
    ("finish", "build"), ("complete", "build"),
    ("fix", "repair"), ("repair", "repair"), ("restore", "repair"),
    ("get out of the hole", "dig_up"), ("get out of this hole", "dig_up"), ("dig your way up", "dig_up"),
    ("dig your way out", "dig_up"), ("dig yourself up", "dig_up"), ("dig yourself out", "dig_up"),
    ("come up to the surface", "dig_up"), ("come up", "dig_up"), ("climb out", "dig_up"), ("get to the surface", "dig_up"),
    ("shear", "shear"), ("milk", "milk"),
    ("bring in", "harvest"), ("pick", "harvest"),
    ("check", "open"), ("look in", "open"), ("open", "open"),
    ("put", "deposit"), ("leave", "deposit"), ("drop off", "deposit"), ("empty", "deposit"),
    ("take", "withdraw"), ("pull", "withdraw"), ("turn", "turn"), ("burn", "smelt"),
    ("remove", "mine"), ("dig out", "mine"),
]
IDIOMS = [   # whole-line rewrites before the verb is read
    (r"^undo the (?:creeper |explosion )?damage (?:at|to|on) ", "repair "),
    (r"^put (the |my |our )?(\w+) back\b", r"repair \1\2"),
    (r"^(dig|branch[- ]?mine|tunnel|look|search|find|hunt)( (?:north|south|east|west))?( deep)? (?:down )?for ", r"prospect\2\3 "),
    (r"^(branch[- ]?mine|tunnel)( (?:north|south|east|west))?( deep)? ", r"prospect\2\3 "),
    (r"^(?:go )?find (\S+(?: ores?)?) underground$", r"prospect \1 underground"),
    (r"^dig (?:that|this)(?: block)? out$", "mine that block"),
]
ALL_VERBS = VERBS + PHRASE_VERBS + [("prospect", "prospect")]
PVERB_RE = re.compile(r"^(" + "|".join(re.escape(w) + (r"(?!\s+to\b)" if w in ("mine up", "dig up", "gather up") else "")
                                       for w, _ in
                                       sorted(ALL_VERBS, key=lambda v: len(v[0]), reverse=True)) + r")\b(.*)$", re.I)
PVERB_OF = dict(VERBS)
PVERB_OF.update(dict(PHRASE_VERBS))
PVERB_OF["prospect"] = "prospect"
DIRS = ("north", "south", "east", "west")


def extras(t):
    """What a skill needs beyond verb/object/qty/place, from his words: x y z (and a second corner), a direction, a
    depth, a design version, here / to me, replant, tools too, yesterday, and "for <block>"."""
    xs = XYZ_RE.findall(t)
    d = re.search(r"\b(" + "|".join(DIRS) + r")\b", t)
    y = re.search(r"\bat y (-?\d+)\b", t)
    v = re.search(r"\bversion (\d+)\b|\bv(\d+)\b", t)
    f = re.search(r"\bfor (\w+)$", t)
    return {"xyz": {a: int(c) for a, c in zip("xyz", xs[0])} if xs else None,
            "xyz2": {a: int(c) for a, c in zip("xyz", xs[1])} if len(xs) > 1 else None,
            "direction": d[1] if d else None,
            "depth": int(y[1]) if y else 12 if re.search(r"\bdeep\b", t) else None,
            "underground": bool(re.search(r"\bunderground\b", t)),
            "version": int(v[1] or v[2]) if v else None,
            "here": bool(re.search(r"\bhere$", t)), "to_me": bool(re.search(r"\bto me$", t)),
            "replant": not re.search(r"\b(?:don'?t|do not) replant\b", t),
            "tools_too": bool(re.search(r"\btools too\b", t)), "yesterday": "yesterday" in t,
            "for_block": f[1] if f else None, "everything": bool(re.search(r"\beverything\b", t))}


def intent(text, ctx=None):
    """O205: one owner line -> {verb, said_verb, object, qty, place, said, raw, extras} for the owner_phrases rules,
    or None. The generic parse with the skill families' verbs and idioms."""
    t = TAIL.sub("", LEAD.sub("", text.strip().lower().rstrip(".!?").strip()))
    t = numbers(t)
    for pat, rep in IDIOMS:
        t = re.sub(pat, rep, t)
    m = PVERB_RE.match(t)
    if not m:
        return None
    verb, rest = PVERB_OF[m[1].lower()], m[2].strip()
    ex = extras(t)
    rest = re.sub(r"^(?:me|us)\s+(?=\S)", "", rest) if verb in ("collect", "craft", "give", "find", "kill", "withdraw") \
        else rest
    core = XYZ_RE.sub("", rest).strip()
    core = re.sub(r"\s+(?:to|at)\s*$", "", core)
    core = re.sub(r"\b(?:" + "|".join(DIRS) + r")\b", "", core) if verb == "prospect" else core
    core = re.sub(r"\s+(?:underground|deep|at y -?\d+|for \w+|and replant|and (?:don'?t|do not) replant|out)$", "",
                  core.strip()).strip()
    core = re.sub(r"^(?:deep|down|for|some)\s+", "", core) if verb == "prospect" else core
    core, place = _place(" " + core)
    core, qty = _qty(core.strip())
    said, exposed = _said(core)
    obj = _object(core, verb if verb in ("mine", "chop", "collect", "find", "harvest", "kill", "shoot") else "item", ctx) \
        if core.strip() else {"kind": "none", "id": None, "words": ""}
    return {"verb": verb, "said_verb": m[1].lower(), "object": obj, "qty": qty, "place": place, "said": said,
            "raw": rest, "core": core.strip(), "extras": ex, "text": t}


# ---- O205 step 2: craftkit's and map_command's phrase rules, read by the grammar

def owner_place(text):
    """map_command's TO_OWNER / STAY: "come to me / here / where I am" -> "come", "follow me" -> "follow",
    "stay (here)" -> "stay"; None otherwise. Whole-line orders only, as before."""
    c = _norm(text)
    p = re.sub(r"^please\s+", "", c)    # as TO_OWNER: "please follow me", "please come here" (STAY took no please)
    if re.fullmatch(r"follow me(?:[\s,]+(?:please|now|eve))*", p):
        return "follow"
    got = _one(p, [], None) if p else None
    if got and len(got) == 1 and got[0]["verb"] == "go" and got[0]["place"] == "owner":
        return "come"
    got = _one(c, [], None) if c else None
    if got and len(got) == 1 and got[0]["verb"] == "unfollow" and c.split()[-1] != "me" and "following" not in c:
        return "stay"
    return None


WOOD_SAID = ("chop", "cut", "get", "collect", "gather", "fetch", "harvest", "chop down", "cut down", "gather up")


def wood_count(text):
    """map_command's CHOP: "chop 8 logs" / "collect 8 wood logs from nearby trees" / "get me some wood" -> the count
    (some = 8), or None. Any species ("chop 3 oak logs") is gather {wood} too."""
    it = parse(text)
    if it is None or it["said_verb"] not in WOOD_SAID or XYZ_RE.search(text):
        return None
    o = it["object"]
    if not (o["id"] == "minecraft:logs" and o["kind"] == "tag" or (o["id"] or "").endswith("_log")) or \
            re.search(r"\btrees?\b|\bforest\b|\bwoods\b", o["words"]):
        return None
    q = it["qty"]
    n = q["n"] if q["kind"] == "exact" else 8 if q["kind"] == "some" else None
    return n if n and 1 <= n <= 256 else None


class Groups:
    """A grammar reading in the shape a regex match had for its callers: m[0] the line, m[1].. the parts."""
    def __init__(self, line, *parts):
        self.parts = (line,) + parts

    def __getitem__(self, i):
        return self.parts[i]


class Phrase:
    """craftkit's phrase names (CAN_MAKE, PLACE, ...) as grammar readings: .match(line) -> Groups or None."""
    def __init__(self, fn):
        self.fn = fn

    def match(self, line):
        got = self.fn(line)
        return None if got is None else Groups(line, *got)


CAN_ASK = re.compile(r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:please\s+)?(?:can|could) you\b", re.I)


def _can_make(line):
    if not CAN_ASK.match(line):
        return None
    it = parse(line)
    if it is None or it["verb"] != "craft":
        return None
    words = re.sub(r"^(?:(?:me|us|a|an|some|one|\d+)\s+)+", "", it["raw"].lower()).strip(" ?.!")
    return (words,) if words else None


def _what_craft(line):
    return () if query_topic(line) == "craftable" else None


PLACE_WHERE = ("near you", "in front of you", "next to you", "beside you", "by you", "here", "down", "on the ground")


def _place_it(line):
    it = intent(line)
    if it is None or it["said_verb"] not in ("place", "put", "set", "put down", "set down", "place down"):
        return None
    t = it["text"]
    for w in PLACE_WHERE:
        t = re.sub(r"\s+" + w + r"$", "", t)
    t = re.sub(r"\s+down$", "", t)
    m = re.match(r"^(?:place|put|set)(?: down)? (it|that|this|(?:the|a|an|your|my) [a-z_ ]+?)$", t)
    return (m[1],) if m else None


def _give(line):
    it = intent(line)
    if it is None or it["verb"] != "give" or it["said_verb"] not in ("give", "hand", "pass", "throw", "toss"):
        return None
    words = re.sub(r"\s+(?:to me|over|here)$", "", re.sub(r"^(?:me|us)\s+", "", it["raw"].lower())).strip(" .!")
    m = re.match(r"^(?:(\d+|a|an|one|some|all the|all|the|your|my)\s+)?([a-z_ ]+?)$", words)
    return (m[1], m[2]) if m else None


def _craft_any(line):
    it = parse(line)
    if it is None or it["said_verb"] not in ("craft", "make"):
        return None
    words = re.sub(r"(?:\s+(?:for me|please|now))+$", "", re.sub(r"^(?:me|us)\s+", "", it["raw"].lower())).strip(" .!")
    m = re.match(r"^([a-z_ ]+?)$", words)
    q = it["qty"]
    n = str(q["n"]) if q["kind"] == "exact" and q["n"] else "some" if q["kind"] == "some" else None
    return (n, m[1]) if m else None


CLEAR_SAID = ("mine", "chop", "cut", "clear", "fell", "harvest", "log", "chop down", "cut down", "mine up", "clear out")


def _clear_trees(line):
    it = intent(line)
    if it is None or it["said_verb"] not in CLEAR_SAID or it["qty"]["kind"] not in ("none", "all", "nearest") or \
            re.search(r"\b\d+\s+(?:nearest|closest)\b|\b(?:nearest|closest)\s+\d+\b|\bup to\b", it["text"]):
        return None                                       # "the 17 nearest trees" / "up to 18" are the grammar's limits
    words = re.sub(r"^(?:(?:all|of|the|these|those|nearest|nearby|closest|surrounding)\s+)+", "", it["core"]).strip()
    if not re.fullmatch(r"trees?|forest|woods", words) or re.search(r"\d", it["core"]):
        return None
    if it["place"]["kind"] not in ("none", "here", "near_owner", "radius"):
        return None
    r = re.search(r"\bwithin (\d+)", it["text"])
    return (r[1] if r else None,)


CAN_MAKE, WHAT_CRAFT, PLACE, GIVE, CRAFT_ANY, CLEAR_TREES = (
    Phrase(_can_make), Phrase(_what_craft), Phrase(_place_it), Phrase(_give), Phrase(_craft_any), Phrase(_clear_trees))


# ---- O205 step 3: mind-c's workstation / outdoors / tool / bucket and portal / planks phrases (workstations.py),
# moved here unchanged (same groups, m[1], m[2] ...); workstations.py keeps the routes and their order (mind-c, 2026-10-30)
ORDER_LEAD = r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:please\s+)?"   # = craftkit.LEAD
WS = (r"(?:the|that|this|my|your|our)\s+(crafting table|workbench|blast furnace|furnace|smoker|anvil|chest|barrel)"
      r"(?:\s+(?:near|by|next to|beside|over by)\s+(?:you|me|us|here))?")
THEN = r"(?:\s*(?:,\s*)?(?:and then|and|then)\s+(.+?))?[\s.!]*$"
GO = re.compile(ORDER_LEAD + r"(?:go|walk|head|run|come)\s+(?:over\s+|back\s+)?(?:to|into|in|inside|up to)\s+" + WS
                + THEN, re.I)
WS_OPEN = re.compile(ORDER_LEAD + r"(?:open|use)\s+" + WS + THEN, re.I)
AT = re.compile(ORDER_LEAD + r"((?:craft|make|smelt|cook)\s+.+?)\s+(?:at|on|in|using|with)\s+" + WS + r"[\s.!]*$", re.I)
WS_TAIL = re.compile(r"\s+(-?\d+)[\s,]+(-?\d+)[\s,]+(-?\d+)\s*$")      # his answer to "Where is it?", appended by rerun
CRAFT = re.compile(r"^(?:craft|make)(?: me| us)? (?:(\d+|a|an|one|some) )?(.+?)(?: for me| please| now)*$", re.I)
SMELT = re.compile(r"^(?:smelt|cook|bake|burn)\b", re.I)
NATURE = (r"(?:the\s+|a\s+|some\s+)?(?:nearest\s+|closest\s+)?(water|lake|river|pond|ocean|sea|trees|tree|forest|cave|hill)"
          r"(?:\s+(?:nearby|near (?:you|me|us|here)|close by|around here|over there))?")
GO_OUT = re.compile(ORDER_LEAD + r"(?:go|walk|head|run|come)\s+(?:over\s+|down\s+|back\s+)?(?:to|into|in|toward|towards)\s+"
                    + NATURE + THEN, re.I)
USE = re.compile(ORDER_LEAD + r"(?:place|put|set|use|drop|launch)\s+(?:down\s+)?(?:the\s+|a\s+|an\s+|my\s+|your\s+|our\s+)?"
                 r"((?:[a-z]+\s+)?(?:boat|raft|minecart|bucket))(?:\s+down)?"
                 r"(?:\s+(?:in|on|at|into|onto|to|by)\s+(?:the\s+)?(.+?))?[\s.!]*$", re.I)
TOOL = r"((?:[a-z]+\s+)?(?:shovel|spade|pickaxe|pick|axe|hoe|sword|shears))"
HANDS = r"(?:(?:my|your|her|bare)\s+)*hands?"
TOOL_LEAD = re.compile(ORDER_LEAD + r"(?:use|using|with)\s+(?:the\s+|your\s+|my\s+|a\s+|an\s+)?(" + TOOL[1:-1] + r"|" + HANDS +
                       r")\s*(?:,\s*|\s+(?:to|and(?:\s+then)?)\s+|\s+(?=(?:mine|dig|chop|break|cut|get|gather|collect|"
                       r"harvest|clear)\b))(.+)$", re.I)
TOOL_TAIL = re.compile(r"^(.+?)\s+(?:with|using)\s+(?:the\s+|your\s+|my\s+|a\s+|an\s+)?(" + TOOL[1:-1] + r"|" + HANDS +
                       r")[\s.!]*$|^(.+?)\s+by\s+hand[\s.!]*$", re.I)
FILL = re.compile(ORDER_LEAD + r"(?:fill (?:up )?(?:the |a |my |your )?bucket(?: (?:up )?with (water|lava))?|"
                  r"(?:get|fetch|grab|collect) (?:me |us )?(?:some |a bucket of |a bucketful of )?(water|lava))[\s.!]*$", re.I)
POUR = re.compile(ORDER_LEAD + r"(?:pour|put|place|dump|empty|tip) (?:out )?(?:the |your |my |some )?(water|lava)"
                  r"(?: bucket)?(?:\s+(?:out\s+)?(?:on|onto|over|at|in|into|by)?\s*(?:the |that )?(lava|water|here|there))?"
                  r"[\s.!]*$", re.I)
OBSIDIAN = re.compile(ORDER_LEAD + r"(?:make|get|craft|create|mine) (?:me |us )?(?:some |an? |\d+ )?obsidian(?: blocks?)?"
                      r"(?: for me| please| now)*[\s.!]*$", re.I)
LIGHT = re.compile(ORDER_LEAD + r"(?:light|ignite|activate|open) (?:up )?(?:the |that |this |my )?(?:nether )?portal|"
                   + ORDER_LEAD[1:] + r"use (?:the |your |my )?flint(?: and steel)? on (?:the |that |this )?(?:nether )?"
                   r"(?:portal|frame|obsidian)", re.I)
INTO = re.compile(ORDER_LEAD + r"(?:craft|turn|make|convert|process)\s+(?:the\s+|my\s+|all\s+(?:of\s+)?(?:the\s+|my\s+)?)?"
                  r"(\d+|all)?\s*(?:of\s+(?:the\s+|my\s+)?)?((?:[a-z]+\s+)?)(?:logs?|stems?)\s+(?:in)?to\s+(?:wooden\s+)?"
                  r"planks?[\s.!]*$", re.I)


# ---- O205 step 4: progress's and build_flow's owner phrases, moved here unchanged (same groups; build_flow's
# what / where names). owner_phrases' LEAD / XYZ as they are (build-flow, 2026-10-30)
from .owner_phrases import LEAD as OWNER_LEAD, XYZ as OWNER_XYZ  # noqa: E402
PROGRESS = re.compile(ORDER_LEAD + r"(?:let'?s\s+(?:progress|keep going|continue)|what'?s\s+next|keep\s+going|continue\s+"
                             r"(?:progression|progressing)|progress|we\s+need\s+to\s+progress)"
                             r"(?:\s+(?:to|until|up to)\s+(?:the\s+|a\s+|an\s+)?([a-z_ ]+?))?[\s.!?]*$", re.I)
NEED_FOR = re.compile(ORDER_LEAD + r"what\s+do\s+(?:we|you|i)\s+need\s+(?:for|to\s+(?:get|make))\s+(?:the\s+|a\s+|an\s+)?"
                             r"([a-z_ ]+?)[\s.!?]*$", re.I)
PROSPECT = re.compile(ORDER_LEAD + r"prospect\s+(?:for\s+)?([a-z]+)(?:\s+ores?)?(?:\s+down\s+to\s+y\s+(-?\d+))?"
                             r"(?:\s+for\s+(\d+)\s+minutes?)?[\s.!]*$", re.I)
SHEAR = re.compile(ORDER_LEAD + r"shear\s+(\d+|a|one|two|three|four|five|some)\s+sheep[\s.!]*$", re.I)
CHARCOAL = re.compile(r"\s+into\s+charcoal\s*$", re.I)
WHY = re.compile(ORDER_LEAD + r"(?:why\s+(?:did|'?d)\s+you\s+stop|why\s+(?:did|'?d)\s+(?:that|it)\s+(?:fail|stop)|what\s+happened|"
                        r"what\s+went\s+wrong|why\s+(?:are\s+you|you'?re)\s+stopped)[\s?.!]*$", re.I)
AGAIN = re.compile(ORDER_LEAD + r"(?:try\s+again|go\s+again|retry|have\s+another\s+go)[\s.!]*$", re.I)
BRIEF = re.compile(OWNER_LEAD + r"(?:build|make)\s+(?:me\s+|us\s+)?(?:a\s+|an\s+|the\s+)?"
                   r"(?P<what>[a-z0-9 ]*?\b(?:house|hut|cabin|home|shelter|shack|cottage|portal))"
                   r"(?:\s+(?P<where>here|(?:at\s+)?" + OWNER_XYZ + r"|(?:by|near|next to|on|in|beside|at)\s+.+?))?\s*$")
