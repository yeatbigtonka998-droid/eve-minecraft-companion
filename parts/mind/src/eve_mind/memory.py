"""CP-44 memory: facts with provenance, corrections that supersede, world scoping, journal grounding.

Facts come only from a trusted owner's words (`owner_chat`/`correction`) or from observation (an ok action result, a
fresh storage index). Planner/model output is never stored as a fact. Nothing is deleted: a correction sets the old
row's `superseded_by`. World-specific facts carry the world id; owner preferences are world-independent (NULL).
"""
import asyncio
import functools
import json
import math
import os
import re
import time
import urllib.request
import uuid

from .commands import coords, item_id
from .config import ROOT, strict_loads
from .config import num_ctx

MAX_LEN = 1000
FRESH_MS = 2000                       # an observation older than this is not "current" (CP-44 step 4)
WOODS = ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry", "pale_oak", "bamboo",
         "crimson", "warped")
SOURCES = ("owner_chat", "correction", "observation")    # `planner` and `journal` never become facts
JOB_SKILLS = ("walk_to", "mine_block", "place_block", "open_container", "attack_entity", "use_item")


def now_ms():
    return int(time.time() * 1000)


def material(name):
    name = name.strip().lower().replace(" ", "_")
    if name.endswith("_wood") or name.endswith("_planks"):
        name = name.rsplit("_", 1)[0]
    return item_id(f"{name}_planks" if name in WOODS else name)


def parse_fact(text):
    """Owner words -> {"kind": "facts", "facts": [...], "correction": bool} | {"kind": "question", "text"} | None.
    None means: not a memory statement (the command mapper / planner get it)."""
    raw = text.strip()
    t = re.sub(r"^(hey\s+)?eve[\s,:!]+", "", raw.lower()).strip()
    if t.endswith("?"):
        return None                                        # a question is never a statement
    correction = bool(re.match(r"^(no|nope|actually|wrong|correction)\b[\s,:!.-]*", t))
    t = re.sub(r"^(no|nope|actually|wrong|correction)\b[\s,:!.-]*", "", t)
    m = re.match(r"^remember\s*:\s*(.+)$", raw.strip(), re.I | re.S)      # explicit "remember: <note>" only
    if m and not correction:
        return {"kind": "facts", "correction": False,
                "facts": [{"world": False, "subject": "owner", "predicate": "note", "object": m[1].strip()}]}
    homes = [h for h in re.split(r"\b(?:and|but|;)\b", t) if re.search(r"\b(my\s+)?home\s+is\s+(at\s+)?", h)]
    if homes:
        spots = []
        for h in homes:
            pos, _ = coords(h)
            if pos is None:
                return {"kind": "question", "text": "Where is your home exactly? Give me x y z."}
            spots.append(f"{pos['x']} {pos['y']} {pos['z']}")
        if len(set(spots)) > 1:
            return {"kind": "question", "text": f"Which one is your home: {' or '.join(dict.fromkeys(spots))}?"}
        return {"kind": "facts", "correction": correction,
                "facts": [{"world": True, "subject": "home", "predicate": "location", "object": spots[0]}]}
    m = re.match(r"^i\s+(?:prefer|like)\s+(?:using\s+)?([a-z_ ]+?)\s+(?:for|in)\s+(?:my\s+)?(?:builds?|building)\b", t)
    if m:
        return {"kind": "facts", "correction": correction,
                "facts": [{"world": False, "subject": "build materials", "predicate": "preference",
                           "object": material(m[1])}]}
    return None


class Memory:
    def __init__(self, db):
        self.db = db

    def remember(self, world_id, subject, predicate, obj, source, source_ref=None, confidence=1.0, confirmed=0,
                 correction=False):
        """Store one fact; a newer fact for the same (world, subject, predicate) supersedes the current one."""
        if source not in SOURCES:
            return {"ok": False, "error": "untrusted_source", "detail": f"{source} output is never stored as a fact"}
        if not all(isinstance(x, str) and x for x in (subject, predicate, obj)) or \
                max(len(subject), len(predicate), len(obj)) > MAX_LEN:
            return {"ok": False, "error": "bad_fact", "detail": f"empty or longer than {MAX_LEN} characters"}
        old = self.current(world_id, subject, predicate)
        if old and old["object"] == obj:                   # observed again: same fact, newer time
            self.db.execute("UPDATE facts SET created_ts=?, source_ref=coalesce(?, source_ref) WHERE fact_id=?",
                            (now_ms(), source_ref, old["fact_id"]))
            return {"ok": True, "fact_id": old["fact_id"], "unchanged": True}
        fid = str(uuid.uuid4())
        self.db.execute("INSERT INTO facts (fact_id, world_id, subject, predicate, object, confidence, source, "
                        "source_ref, created_ts, confirmed) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (fid, world_id, subject, predicate, obj, confidence, "correction" if correction and old else source,
                         source_ref, now_ms(), confirmed))
        if old:
            self.db.execute("UPDATE facts SET superseded_by=? WHERE fact_id=?", (fid, old["fact_id"]))
        r = {"ok": True, "fact_id": fid, "supersedes": old["fact_id"] if old else None}
        if correction and not old:
            r["note"] = "nothing to correct; stored as a new fact"
        return r

    def retire_locations(self, world_id, subject, by_fact_id):
        """O69a: a removal ("the X is gone, I tore it down") supersedes X's location facts - it no longer stands
        there - instead of adding a status beside a location that still reads as current."""
        # O81: only the very same subject (the extractor reuses KNOWN subjects) - "the farm is gone" never retires
        # the wheat farm; a false retirement is harm, a missed one only a miss
        key = (thing(subject) or "").lower()
        rows = self.db.execute("SELECT fact_id, subject FROM facts WHERE world_id IS ? AND superseded_by IS NULL AND "
                               "predicate IN ('location', 'possible_location', 'relation')", (world_id,)).fetchall()
        hit = [r["fact_id"] for r in rows if key and (thing(r["subject"]) or "").lower() == key]
        for fid in hit:
            self.db.execute("UPDATE facts SET superseded_by=? WHERE fact_id=?", (by_fact_id, fid))
        return len(hit)

    def retract(self, prefix, subject, predicate):
        """O111-b: the owner retires one false fact by its id prefix (kept, never deleted: superseded_by "retracted").
        The subject and predicate must match too, so a short prefix can never retire the wrong fact."""
        rows = self.db.execute("SELECT * FROM facts WHERE fact_id LIKE ? AND superseded_by IS NULL",
                               (f"{prefix}%",)).fetchall() if len(prefix) >= 8 else []
        if len(rows) != 1 or rows[0]["subject"] != subject or rows[0]["predicate"] != predicate:
            return {"ok": False, "error": "no_fact", "detail": f"{len(rows)} current facts match {prefix!r}" +
                    ("" if len(rows) != 1 else f"; it is {rows[0]['subject']!r} {rows[0]['predicate']!r}")}
        self.db.execute("UPDATE facts SET superseded_by='retracted' WHERE fact_id=?", (rows[0]["fact_id"],))
        return {"ok": True, "retracted": dict(rows[0])}

    PLACES = ("location", "possible_location", "relation")

    def retired(self, removal_id):
        """O69j: the places a removal retired - kept, never deleted, so 'where was X' still answers."""
        return [dict(r) for r in self.db.execute(f"SELECT * FROM facts WHERE superseded_by=? AND predicate IN "
                                                 f"({','.join('?' * len(self.PLACES))})", (removal_id, *self.PLACES))]

    def restore(self, removal_id):
        """O69j: the owner says it still stands - the places that removal retired are current again."""
        return self.db.execute(f"UPDATE facts SET superseded_by=NULL WHERE superseded_by=? AND predicate IN "
                               f"({','.join('?' * len(self.PLACES))})", (removal_id, *self.PLACES)).rowcount

    def current(self, world_id, subject, predicate):
        row = self.db.execute("SELECT * FROM facts WHERE world_id IS ? AND subject=? AND predicate=? AND "
                              "superseded_by IS NULL ORDER BY created_ts DESC LIMIT 1",
                              (world_id, subject, predicate)).fetchone()
        return dict(row) if row else None

    def recall(self, subject, predicate=None, world_id=None):
        """Current facts about a subject in the bound world plus world-independent ones; [] = unknown (no guess).
        Observations older than FRESH_MS come back with reduced confidence and stale=True."""
        rows = self.db.execute(
            "SELECT * FROM facts WHERE subject=? AND (? IS NULL OR predicate=?) AND superseded_by IS NULL AND "
            "(world_id IS NULL OR world_id=?) ORDER BY created_ts DESC",
            (subject, predicate, predicate, world_id)).fetchall()
        out, now = [], now_ms()
        for r in rows:
            f = {k: r[k] for k in ("fact_id", "world_id", "subject", "predicate", "object", "confidence", "source",
                                   "source_ref", "created_ts", "confirmed")}
            if f["source"] == "observation" and f["subject"].startswith("container ") and \
                    now - f["created_ts"] > FRESH_MS:
                f["confidence"], f["stale"] = min(f["confidence"], 0.5), True
            out.append(f)
        return out

    def journal(self, world_id, text, grounded_in=(), kind="day_summary", generated_by="mind"):
        if not isinstance(text, str) or not text or len(text) > 4 * MAX_LEN:
            return {"ok": False, "error": "bad_journal"}
        cur = self.db.execute("INSERT INTO journal (world_id, ts, kind, text, generated_by, grounded_in) VALUES "
                              "(?,?,?,?,?,?)", (world_id, now_ms(), kind, text, generated_by,
                                                json.dumps(list(grounded_in))))
        return {"ok": True, "id": cur.lastrowid, "confirmed": self.grounded(list(grounded_in))}

    def grounded(self, ids):
        """Confirmed history only when every cited id is a real action that ended ok."""
        if not ids:
            return False
        q = f"SELECT count(*) FROM actions WHERE status='ok' AND action_id IN ({','.join('?' * len(ids))})"
        return self.db.execute(q, ids).fetchone()[0] == len(set(ids))

    def all(self, world_id):
        """Every current fact in the bound world plus the world-independent ones (for `eve recall --all`)."""
        rows = self.db.execute("SELECT DISTINCT subject FROM facts WHERE superseded_by IS NULL AND "
                               "(world_id IS NULL OR world_id=?)", (world_id,)).fetchall()
        return [f for (subj,) in rows for f in self.recall(subj, None, world_id)]

    def history(self, world_id):
        """Journal entries that count as confirmed history (grounded in ok actions); ungrounded text is left out."""
        rows = self.db.execute("SELECT * FROM journal WHERE world_id=? ORDER BY ts", (world_id,)).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "text": r["text"], "grounded_in": json.loads(r["grounded_in"] or "[]")}
                for r in rows if self.grounded(json.loads(r["grounded_in"] or "[]"))]


# ---- model-assisted extraction (CP-44 independent fixture): the model only structures the OWNER's words; every
# coordinate and item id it returns must come from those words (or the fact it corrects), questions store nothing.
PREDICATES = ("location", "relation", "possible_location", "preference", "rule", "status", "identity", "access", "ownership",
              "schedule")
EXTRACT_PROMPT = """You record what a trusted Minecraft player (the owner) TELLS his companion Eve, as facts for her memory.
Return JSON only. At most two facts, ONE per separate claim. No side facts: a description that only says WHICH thing is
meant (its colour, which floor) belongs in its subject, never in a fact of its own; where the owner says a thing IS
is a fact (location or relation).
Return "facts": [] for a question, a request for Eve to act now, small talk, a hypothetical ("if I ever...", "would"),
or anything someone else claims.
- subject: what the fact is about, in the owner's words, specific ("blue bed upstairs", "iron farm at 60 70 -20",
  "storage room door", "village to the north"). The owner himself is "owner" (his name, his habits).
  A taste or rule is filed under WHAT it is about, one topic per subject ("roofs", "lighting", "doors"), never
  "owner". The owner's home or base is "home". If KNOWN has a fact about the same thing, use exactly its subject -
  also when the owner calls it by another name or says he moved it ("I moved the smithing station to ..." after
  KNOWN "forge" -> subject "forge"; "the anvil room" after KNOWN "smithy" when it is the same place).
- predicate, one of:
  location = where it is, ONLY when the message gives x y z (object: the "x y z" exactly as written);
  relation = where it is without x y z, relative to other places, in the owner's words (object: "beside the well",
    "halfway between the docks and the market", "a short walk north of the town square", "up on the cliff");
  possible_location = where the owner is unsure it is (object: his words, e.g. "around 200 11 -300");
  preference = what the owner likes or dislikes (subject = the topic; object names every item in full, plural is
    fine: building materials -> "spruce planks and cobblestone", doors -> "spruce doors over oak doors", lighting ->
    "torches, not lanterns", roofs -> "steep roofs, no flat roofs");
  rule = what Eve must or must not do about a thing, as an imperative (subject = the thing: wheat farm east of the
    house -> "only harvest when the owner asks"; lighting -> "only use torches");
  status = the current state of a thing, in the owner's own words ("broken", "no water, nothing growing");
  identity = what or who something is, or a name ("Northwatch"; "Call me Hunter" -> subject "owner", object
    "Hunter");
  access = who may use or take from something (subject = the thing, object = who: "Priya", "everyone"; taken
    away -> "not Priya");
  ownership = whose something is;
  schedule = when something happens or when the owner usually does something ("I usually log on after 7 pm" ->
    subject "owner", object "usually logs on after 7 pm"); a habit is a schedule, not a preference.
  A favourite colour is its own topic: subject "colours" (never merged into building materials).
- world_specific: true for anything in this world (places, buildings, farms, chests, people's access here); false for
  the owner's own tastes, name and habits, and for anything he says holds in every world he plays.
- removed: true only when the owner says the thing is gone for good (destroyed, torn down, burned, flattened, melted, used up,
  got rid of) and it really happened; false otherwise (also for "almost" or "nearly").
KNOWN (current facts): {known}"""
EXTRACT_SCHEMA = {"type": "object", "required": ["facts"], "properties": {"facts": {"type": "array", "maxItems": 2,
                  "items": {"type": "object", "required": ["subject", "predicate", "object", "world_specific", "removed"],
                            "properties": {"subject": {"type": "string", "maxLength": 80},
                                           "predicate": {"enum": list(PREDICATES)},
                                           "object": {"type": "string", "maxLength": 200},
                                           "world_specific": {"type": "boolean"},
                                           "removed": {"type": "boolean"}}}}}}   # O69j: a 3-way enum confused qwen3:8b
# the owner's own hedges (certainty is his, not the model's guess; "don't count on it" is not a hedge)
HEDGE = re.compile(r"\b(i think|i guess|i believe|maybe|might|probably|perhaps|not (?:totally |completely |100% )?sure|"
                   r"not (?:a )?(?:hundred|100) ?(?:percent|%)|"      # O86x W04: "Not a hundred percent on this, but"
                   r"could be|i'?m not certain|somewhere around|roughly|pretty sure|fairly sure|reasonably sure|"
                   r"don'?t quote me|if i remember (?:right|correctly)|iirc|i reckon|last i (?:checked|looked|saw)|"
                   r"(?:haven'?t|have not|didn'?t) (?:checked|verified)|unless i'?m wrong|i could be wrong)\b", re.I)
HYPOTHETICAL = re.compile(r"^\s*(?:if|when|once|suppose|imagine)\b[^.]*\b(?:would|will|could|'d)\b|\bwould go\b", re.I)
WISH = re.compile(r"\b(?:some ?day|one day|eventually|in the future|(?:i|we)(?:'d| would)(?: really)? (?:like|love)|"
                  r"(?:i|we)(?: really)? (?:want|wish|hope)|(?:i'm|i am|we're|we are) (?:planning|hoping|thinking)|"
                  r"(?:wouldn't it|it would|it'd) be (?:nice|cool|great|fun)|dream(?:s|ing)? of|plan(?:s|ning)? (?:to|on)|"
                  r"imagine|picture (?:this|it)|envision|visuali[sz]e|wonder(?:ing)? (?:whether|if)|"
                  r"down the (?:line|road)|later on|before long|sooner or later|at some point|in due course)\b",
                  re.I)   # set 12 T07: "Someday I want a cherry orchard beside the herb garden" - not there yet;
#                           O86x veto: "Picture this: a waterfall ..." is imagined (the focused call read it as there)
WISH_ANY = re.compile(r"\bwould (?:be|look) (?:so |really )?(?:lovely|nice|cool|great|fun|awesome|neat|perfect|amazing|"
                      r"beautiful)\b|\bwould love\b|\bdream(?:s|ing)? of\b", re.I)   # set 13 Q07: "X up in Y would be lovely"
EXISTS = re.compile(r"\b(?:still|already)\b", re.I)    # set 13 Q09: "I hope the cellar behind the kitchen STILL has bread"
HEDGE_WORDS = {"about", "around", "roughly", "approximately", "maybe", "probably", "perhaps", "possibly", "somewhere",
               "likely"}       # O81: only his uncertainty may be reworded - a direction he never said would be invented
NUMS = re.compile(r"-?\d+(?:\.\d+)?")
IDS = re.compile(r"minecraft:[a-z0-9_]+")


@functools.lru_cache(maxsize=1)
def known_items():
    try:
        return set(json.loads((ROOT / "data" / "game" / "26.3" / "reports" / "items.json").read_text("utf-8")))
    except (OSError, ValueError):
        return set(_jar_ids("assets/minecraft/items/"))   # 45L 13:30: no reports/ there, only jars/recipe/tags


def _jar_ids(prefix):
    """Ids from the pinned client jar's json entries under `prefix` (one per block / item); empty without the jar."""
    import zipfile
    try:
        with zipfile.ZipFile(ROOT / "data" / "game" / "26.3" / "jars" / "client-26.3.jar") as z:
            return frozenset(f"minecraft:{n[len(prefix):-5]}" for n in z.namelist()
                             if n.startswith(prefix) and n.endswith(".json") and "/" not in n[len(prefix):])
    except (OSError, zipfile.BadZipFile):
        return frozenset()


@functools.lru_cache(maxsize=1)
def known_blocks():
    """O128: the 26.3 block ids (one blockstates/*.json per block) - the extracted dir, else the client jar (the 45L
    mind root has no blockstates/: "mine 3 grass block" was asked back live, 13:30)."""
    try:
        got = frozenset(f"minecraft:{p.stem}" for p in (ROOT / "data" / "game" / "26.3" / "blockstates").glob("*.json"))
    except OSError:
        got = frozenset()
    return got or _jar_ids("assets/minecraft/blockstates/")


@functools.lru_cache(maxsize=1)
def _lang():
    """The pinned client jar's en_us.json; {} without the game data."""
    import zipfile
    try:
        with zipfile.ZipFile(ROOT / "data" / "game" / "26.3" / "jars" / "client-26.3.jar") as z:
            return json.loads(z.read("assets/minecraft/lang/en_us.json").decode("utf-8"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return {}


@functools.lru_cache(maxsize=1)
def item_names():
    """O137: the game's English item names ("Stone Pickaxe", "Cobblestone") -> item id: item.* and block.* entries
    that are 26.3 items, lower-case; {} without the game data."""
    items, out = known_items(), {}
    for k, v in _lang().items():
        for pre in ("item.minecraft.", "block.minecraft."):
            if k.startswith(pre) and k.count(".") == 2 and f"minecraft:{k[len(pre):]}" in items:
                out.setdefault(v.lower(), f"minecraft:{k[len(pre):]}")
    return out


@functools.lru_cache(maxsize=1)
def block_names():
    """O128 addendum: the game's English block names ("rose bush") -> block id, from the pinned client jar's
    en_us.json; empty without the game data."""
    lang = _lang()
    if not lang:
        return {}
    blocks = known_blocks()
    out = {}
    for k, v in lang.items():
        if k.startswith("block.minecraft.") and k.count(".") == 2 and f"minecraft:{k[16:]}" in blocks:
            out.setdefault(v.lower(), f"minecraft:{k[16:]}")
    return out


def item_ids_in(obj):
    """Every 26.3 item named in free text, as ids in place ("spruce doors over oak doors" ->
    "minecraft:spruce_door over minecraft:oak_door"); a wood name alone means its planks."""
    items = known_items()
    if not items:
        return obj
    words = re.findall(r"[a-z_]+|[^a-z_]+", obj.lower().replace("minecraft:", ""))
    out, k = [], 0
    while k < len(words):
        hit = None
        for n in (5, 3, 1):                          # tokens alternate word/sep: 5 tokens = 3 words
            if k + n > len(words) or not re.match(r"[a-z_]", words[k]):
                continue
            name = "".join(words[k:k + n]).strip().replace(" ", "_")
            for cand in (name, name[:-1] if name.endswith("s") else None, name[:-2] if name.endswith("es") else None,
                         re.sub(r"ies$", "y", name)):
                if cand in WOODS and f"minecraft:{cand}_planks" in items:      # "dark oak" alone = its planks
                    nxt = "".join(words[k + n:k + n + 2]).strip()
                    if not re.match(r"(?:log|wood|door|fence|stair|slab|sapling|leave|boat|sign|button|trapdoor|plank)",
                                    nxt):
                        hit = (n, f"minecraft:{cand}_planks")
                        break
                    continue
                if cand and f"minecraft:{cand}" in items:
                    hit = (n, f"minecraft:{cand}")
                    break
            if hit:
                break
        if hit:
            out.append(hit[1])
            k += hit[0]
        else:
            out.append(words[k])
            k += 1
    return "".join(out)


def guard(fact, text, prior=""):
    """Deterministic checks on a model-structured fact. Returns a reason to drop it, or None."""
    if fact["predicate"] in ("ownership", "access") and fact["object"].strip().lower() in ("eve", "eve's") and \
            re.match(r"^\s*(?:hey\s+)?eve[\s,:!]", text, re.I) and len(re.findall(r"\beve\b", text, re.I)) == 1:
        return "Eve is only addressed here, not named as the owner"          # set 6 S02: "Eve, please rename ..."
    words = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    for c in re.findall(r"-?\d+ -?\d+ -?\d+", fact["object"]):  # "home is NOT at 0 64 0" states no location
        if re.search(r"\b(not|never|isn'?t|no longer)\b[^.?!]{0,30}?" + re.escape(c), text, re.I):
            return f"the owner negated {c}"
    for n in NUMS.findall(fact["object"]):                     # numbers only from the owner's words
        if n not in NUMS.findall(text) and n not in NUMS.findall(prior):
            return f"number {n} not in the message"
    items = known_items()
    for i in IDS.findall(fact["object"]):
        name = i.split(":", 1)[1]
        if items and i not in items:
            return f"{i} is not a 26.3 item"
        if i in prior:
            continue
        stem = [w for w in name.split("_") if w not in ("planks", "block", "log", "wood")] or [name]
        if not all(w in words for w in stem):
            return f"{i} not named in the message"
    if not fact["subject"].strip() or not fact["object"].strip():
        return "empty subject or object"
    if fact["predicate"] == "location" and not re.search(r"-?\d+ -?\d+ -?\d+", fact["object"]):
        return "a location without coordinates (a description, not a place)"
    if self_placed(fact):
        return "the place names the thing itself (a misread)"       # O86x: "north tower" at "top of the north tower"
    own = stems(text + " " + prior)
    if fact["predicate"] in ("status", "identity", "possible_location", "relation", "wish") and not fact.get("removal"):
        missing = [w for w in re.findall(r"[a-z]{3,}", fact["object"].lower()) if w not in STOP and w not in HEDGE_WORDS
                   and word_stem(w) not in own]                   # O69j: his hedge words, reworded, are no invention
        if missing:
            return f"object word(s) {missing} not in the message"
    content = {w for w in re.findall(r"[a-z]{3,}", (fact["subject"] + " " + fact["object"]).lower())} - STOP
    verbatim = re.search(r"(?<![a-z0-9])" + re.escape(fact["object"].strip().lower()) + r"(?![a-z0-9])", text.lower())
    if fact["subject"] != "home" and not prior and not {word_stem(w) for w in content} & own and not verbatim:
        return "shares no word with the message (invented)"
    return None


def self_placed(fact):
    own = set(re.findall(r"[a-z]{3,}", fact["subject"].lower())) - STOP
    return fact["predicate"] in PLACES + ("wish",) and bool(own) and own <= set(re.findall(r"[a-z]{3,}",
                                                                                         fact["object"].lower()))


def word_stem(w):
    """A plain word stem: lowercase, common English endings off (growing -> grow, farms -> farm)."""
    w = w.lower()
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]
    return w


def stems(text):
    return {word_stem(w) for w in re.findall(r"[a-z]{3,}", text.lower())}


STOP = {"the", "owner", "owners", "and", "for", "with", "minecraft", "planks", "block", "eve", "keep", "safe", "only",
        "when", "not", "never", "use"}
PETS = re.compile(r"\b(dog|cat|horse|wolf|parrot|pet|donkey|llama|pig|cow|sheep|chicken|fox)s?\b", re.I)
EVERYWHERE = re.compile(r"\b(anywhere|everywhere|in (?:any|every) world|(?:for )?every world|all (?:the |my )?worlds|"
                        r"any world|no matter (?:which|what) (?:world|map|server)|whatever (?:world|map|server|save)|"
                        r"every single (?:world|map|server|save)|throughout (?:all )?(?:my |our |the )?(?:worlds|maps|"
                        r"servers|saves)|"
                        r"(?:on |in )?(?:every|any|all) (?:maps?|servers?|saves?)|across (?:all )?worlds|"
                        r"wherever (?:i|we) (?:play|am|are|go|build|live|end up)|whichever (?:world|map|server|save)|"
                        r"(?:each|every one|all|any) of (?:my|our|the) (?:worlds|maps|servers|saves)|"
                        r"(?:in |on )?each (?:world|map|server|save)|no matter where|"
                        r"(?:every|each|any|all)(?: single| one)? (?:of )?(?:my |our |the )?(?:realms?|playthroughs?)|"
                        r"(?:world|map|save|server|realm|seed|game)s? (?:to|after) (?:world|map|save|server|realm|seed|"
                        r"game)s?|"
                        r"(?:every|each|any|all)(?: single| one)? (?:of )?(?:my |our |the )?(?:new|fresh|other|survival|"
                        r"creative) (?:worlds?|saves?|seeds?|realms?|playthroughs?|maps?|games?)|"
                        r"(?:in|on|across|throughout|for) (?:all|every|each|any)(?: single| one)? (?:of )?(?:my |our |the )?"
                        r"(?:\w+ )?(?:games?|seeds?|runs?)|"
                        r"(?:(?:every|each) time|whenever) (?:i|we) (?:start|begin|make|create|load|spin up|roll|play)\b"
                        r"[^.,;!?]{0,30}?\b(?:new|fresh|different|another) (?:worlds?|maps?|saves?|games?|seeds?|"
                        r"playthroughs?)|"
                        r"regardless of (?:the |which |what )?(?:world|map|save|server|game|seed)s?|"
                        r"(?:doesn'?t|does not|don'?t|do not) matter (?:which|what) (?:world|map|save|server|game|seed)|"
                        r"no matter (?:the|which|what) (?:world|map|save|server|game|seed)|"
                        r"(?:any|every|each|all)(?: single)? minecraft(?: (?:worlds?|saves?|maps?|servers?|games?|seeds?|"
                        r"realms?|playthroughs?))?|"
                        r"(?:every|each)(?: single)? one of (?:my|our) (?:worlds|maps|servers|saves|games|realms|seeds|"
                        r"playthroughs)|"
                        r"(?:every|each|any|all)(?: single)? (?:\w+ )?(?:worlds?|saves?|servers?|games?|seeds?|realms?|"
                        r"playthroughs?) of (?:mine|ours)|"
                        r"no (?:\w+ )?(?:worlds?|saves?|maps?|servers?|games?|realms?|seeds?|playthroughs?) of (?:mine|ours)|"
                        r"(?:from )?one (?:world|save|map|server|game|realm|seed|playthrough) to (?:the )?(?:next|another)|"
                        r"(?:whichever|whatever|every|any) (?:version|edition|update|patch)(?:e?s)?|"
                        r"no matter (?:the|which|what) (?:version|edition|update|patch)|"
                        r"ever since my (?:very )?first (?:world|save|map|game|playthrough|realm|seed)|"
                        r"whenever (?:i|we) (?:reset|restart|start over|start (?:a )?fresh|begin again|reroll))\b",
                        re.I)   # O69f: word families; O84 (set 10 N07/N08): realms, playthroughs; set 11 E01-E03: "from
#                                 world to world", "in all my games", "every time I start over in a new world"; maps/
#                                 games/servers count only after a preposition ("all my maps are in the chest" are items);
#                                 set 12 T01-T03: "any Minecraft I play", "every single one of my servers", "save of mine";
#                                 set 13 Q01-Q03: "no world of mine", "from one save to the next", "whichever version";
#                                 set 14 V01-V03: "every patch", "ever since my first world", "whenever I reset"
# gate 2 G06: "no matter which world / whatever map / on every server / across worlds" = global as well


def scope(fact, text):
    """Deterministic world-scope corrections on top of the model: pets/animals live in a world; a rule the owner says
    holds anywhere/ever is world-independent."""
    if PETS.search(fact["subject"] + " " + fact["object"]) or PETS.search(text) and fact["predicate"] == "identity":
        return True
    if everywhere(text):
        return False                             # O42: "every world / all worlds / everywhere" = global
    if fact["predicate"] in ("preference", "schedule") or fact["subject"] == "owner":
        return False                             # the owner's own tastes, name and habits go with him
    return True                                  # O81: anything else is this world unless HE says every world - the
    #                                              model's "false" alone is never trusted (a false global is harm)


LOCAL_SCOPE = re.compile(r"\b(?:this|the current)\s+(?:\w+\s+)?(?:world|map|server|save|game|seed|playthrough|realm|run)\b|"
                         r"\bhere\b|\b(?:only|just)\s+this\s+one\b",
                         re.I)   # O86x W03: "Of all my worlds, only this one has ..." is this world


# O101q (probe 2026-10-22 02:44: "Pretty sure there's a mineshaft near 150 30 -90 in the seed I'm about to try" was
# stored as a place here): a place said about ANOTHER or an UPCOMING world is never a place in this one - rule first,
# model second. Named forms only; "the world I'm building / the save I'm playing" may be this one and are not here.
OTHER_WORLD = re.compile(
    r"\b(?:in|on|for) (?:my|the|a|an|our) (?:other|next|new|old|previous|last|test|creative|backup|second|third|another|"
    r"different|upcoming|future) (?:\w+ )?(?:seeds?|worlds?|saves?|maps?|servers?|realms?|playthroughs?|runs?|instances?|"
    r"mod ?packs?)\b|"
    r"\b(?:in|on) (?:another|a different|some other) (?:seed|world|save|map|server|realm|playthrough|instance)\b|"
    r"\b(?:in|on|for) (?:the|a|my|our) (?:\w+ )?(?:seed|world|save|map|server|realm|playthrough|instance|mod ?pack|"
    r"version) (?:that )?(?:i'?m|i am|we'?re|we are|i'?ll|we'?ll|i will|we will) (?:about to|going to|gonna|planning to|"
    r"thinking of|testing|trying|designing|drawing|starting|setting up)\b", re.I)


def other_world(text):
    """O101q: the named other/upcoming-world form, unless he says every world (that is scope, not another world)."""
    m = OTHER_WORLD.search(text)
    return m[0] if m and not everywhere(text) else ""


def everywhere(text):
    """The every-world families, but never negated ("not in every world", set 9 W09) and never beside "here / this
    world": a false global is harm, a missed one only a miss (O81)."""
    if LOCAL_SCOPE.search(text):
        return False
    return any(not re.search(r"(?:\bnot|\bnever|n't)\s+(?:\w+\s+)?(?:(?:in|on|across|for)\s+)?$", text[:m.start()], re.I)
               for m in EVERYWHERE.finditer(text))


def _ollama(url, body, timeout):
    req = urllib.request.Request(url + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


PLACES = ("location", "possible_location", "relation")
# O86x: whether a place the extractor read is really there is one focused question of its own (marker lists and the
# deterministic existence gate both failed held-out sets 11-15). Only an explicit "there" keeps a place; anything else
# - not_there, unclear, a timeout, a bad answer - is a wish: a wish is never sent to, a false place is harm (O81).
PLACE_PROMPTS = ("""The owner of a Minecraft world said the message below to his companion Eve. Eve's notes read it as:
"{thing}" is "{place}". Does the owner say that "{thing}" is really there now?

Answer "there" when he says or believes it is there now: he built, put, keeps, left, found or saw it there, or simply
says where it is. However he hedges how sure he is ("I think", "if memory serves", "my guess is", "last I checked",
"not a hundred percent", "should be around", "might be misremembering"), a place he believes in is still there - only
uncertain.
Answer "not_there" when it is not there now: something he wants, wishes, hopes for, plans, intends, is aiming, thinking
or dreaming of, would like or love, imagines, pictures, wonders about or asks about; something that happens only later
or only if something holds ("if", "once", "provided", "as long as"); something already gone (destroyed, torn down); or
something only someone else claims. Time words decide it even in the present tense: a sentence placed in the future
("eventually", "next", "one day", "later on", "a few sessions from now", "in time") describes a plan, not what is
there now.
Answer "unclear" when the message does not tell.
Also say "hedged": true when he shows any doubt about it - where it is, or his memory of it - else false.
Judge only what he says about "{thing}" at "{place}": a hope or plan about something else in the same message does not
change it ("the bird bath sits by the roses, and I hope the robins use it" -> there).""",
                 """The owner of a Minecraft world said the message below to his companion Eve. Eve's notes read it as:
"{thing}" is "{place}". Eve will send people to that place only if "{thing}" really exists there now - a wrong
"there" sends someone to a place that does not exist, so be strict.

Answer "there" only when his words state as a fact about the world as it is now (or as he left it) that "{thing}" is
there: he built, put, keeps, left, stored or found it there, saw it there, or tells where it stands, the way one states a
plain fact ("the melon farm is south of the village", "you'll find the ladder in the shed").
Doubt about WHERE exactly is fine: if he believes it exists and only hedges on the spot or on his memory ("I think",
"if memory serves", "my guess is", "last I checked", "not a hundred percent", "should be around", "might be
misremembering"), answer "there".
Answer "not_there" when the words show it does not exist there now: he wants, wishes, hopes for, fancies, plans,
intends, is aiming, thinking, toying or dreaming of it, would like or love it, imagines or pictures it, has it only in
his head, wonders or asks about it, it happens only later or only if something holds ("if", "once", "provided",
"as long as", "eventually", "next", "one day", "a few sessions from now" - even in the present tense), it is already
gone (destroyed, torn down), or only someone else claims it and he has not seen it.
Answer "unclear" when you cannot tell whether it exists now.
Also say "hedged": true when he shows any doubt about it - where it is, or his memory of it - else false.
Judge only what he says about "{thing}" at "{place}": a hope or plan about something else in the same message does not
change it ("the bird bath sits by the roses, and I hope the robins use it" -> there).""")   # O86x: both must answer "there" (fail-safe AND)
PLACE_SCHEMA = {"type": "object", "required": ["answer", "hedged"],
                "properties": {"answer": {"enum": ["there", "not_there", "unclear"]}, "hedged": {"type": "boolean"}}}


async def place_check(text, thing, place, url, model, timeout=10):
    """O86x: the owner's message + one place the extractor read -> "there" (or "there (hedged)" when either framing
    hears his doubt, or a reply lacks it) only when every framing says "there" (each with thinking); else the first
    other answer - "not_there", "unclear" or "error: ..." (",strict" when the second framing refused it). qwen3:8b on
    89 bench triples: one framing 3-5 harmful, both 1 (vetoed: "Picture this")."""
    hedged = False
    for i, prompt in enumerate(PLACE_PROMPTS):
        body = {"model": model, "stream": False, "format": PLACE_SCHEMA, "options": {"temperature": 0, "num_ctx": num_ctx()},
                "keep_alive": "10m", "messages": [{"role": "system", "content": prompt.format(thing=thing, place=place)},
                                                  {"role": "user", "content": text}]}
        if model.startswith("qwen3"):
            body["think"] = True     # one framing on the bench: thinking 1-3 harmful at 3 s a call, without 5
        try:
            raw = await asyncio.wait_for(asyncio.to_thread(_ollama, url, body, timeout), timeout)
            j = strict_loads((raw.get("message") or {}).get("content", ""))
            a, hedged = j["answer"], hedged or j.get("hedged") is not False
        except (asyncio.TimeoutError, OSError, ValueError, KeyError, TypeError) as e:
            a = f"error: {type(e).__name__}"
        if a != "there":
            return a + (",strict" if i else "")
    return "there (hedged)" if hedged else "there"   # either framing hearing doubt makes it uncertain


SCOPE_PROMPT = """The owner of a Minecraft world said the message below to his companion Eve. Eve's notes read it as:
"{thing}": "{what}". Does he say this holds in EVERY world he plays - all his worlds, saves, servers or playthroughs -
or only in this world (or only some worlds)? Eve will repeat it in every other world only if he says every world, so
be strict: "this is the only world where...", "out of all my worlds, only here...", "not in every world" -> this_world.
Answer "every_world", "this_world" or "unclear"."""
SCOPE_SCHEMA = {"type": "object", "required": ["answer"],
                "properties": {"answer": {"enum": ["every_world", "this_world", "unclear"]}}}


async def scope_check(text, thing, what, url, model, timeout=10):
    """O86x: every-world words are only a candidate - a false global is harm, so the focused call must confirm it."""
    body = {"model": model, "stream": False, "format": SCOPE_SCHEMA, "options": {"temperature": 0, "num_ctx": num_ctx()}, "keep_alive": "10m",
            "messages": [{"role": "system", "content": SCOPE_PROMPT.format(thing=thing, what=what)},
                         {"role": "user", "content": text}]}
    if model.startswith("qwen3"):
        body["think"] = True
    try:
        raw = await asyncio.wait_for(asyncio.to_thread(_ollama, url, body, timeout), timeout)
        return strict_loads((raw.get("message") or {}).get("content", ""))["answer"]
    except (asyncio.TimeoutError, OSError, ValueError, KeyError, TypeError) as e:
        return f"error: {type(e).__name__}"


def embedded_places(facts):
    """Set 13 Q09: qwen kept the place inside the subject ("cheese cellar behind the kitchen": status "still has
    bread") and small talk drops statuses, so the real place was lost. A non-place fact whose subject is "X <prep> Y"
    is filed under X, plus X's relation "<prep> Y" - the owner's words, through the same wish/guard checks."""
    embed = re.compile(r"^(?P<x>[a-z][a-z' -]*?)\s+(?P<prep>" + PREP + r")\s+(?P<y>.+)$", re.I)
    out = []
    for f in facts:
        m = embed.match(str(f.get("subject", "")).strip()) if isinstance(f, dict) else None
        if m and m["prep"].lower() != "from" and thing(m["x"]) and not f.get("removed") and \
                f.get("predicate") not in ("location", "possible_location", "relation"):
            f["subject"] = m["x"]
            if not any(isinstance(g, dict) and g.get("predicate") == "relation" and g.get("subject") == m["x"]
                       for g in facts + out):
                out.append({"subject": m["x"], "predicate": "relation", "object": f"{m['prep'].lower()} {m['y']}",
                            "world_specific": f.get("world_specific", True), "removed": False})
        out.append(f)
    return out


async def extract_facts(text, known, url=None, model=None, timeout=10):
    """The owner's message -> [fact...] via the local model, then guard(). known: [{subject, predicate, object}].
    Returns {"ok", "facts", "dropped"} or {"ok": False, "error"}."""
    if text.strip().endswith("?"):
        return {"ok": True, "facts": [], "dropped": [], "question": True}
    if HYPOTHETICAL.search(text):
        return {"ok": True, "facts": [], "dropped": [], "hypothetical": True}
    url = (url or os.environ.get("EVE_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")
    model = model or os.environ.get("EVE_PLANNER_MODEL") or "qwen3:8b"
    ktxt = json.dumps([{k: f[k] for k in ("subject", "predicate", "object")} for f in known])[:3000]
    body = {"model": model, "stream": False, "format": EXTRACT_SCHEMA, "options": {"temperature": 0, "num_ctx": num_ctx()},
            "keep_alive": "10m", "messages": [{"role": "system", "content": EXTRACT_PROMPT.format(known=ktxt)},
                                              {"role": "user", "content": text}]}
    if model.startswith("qwen3"):
        body["think"] = False
    try:
        raw = await asyncio.wait_for(asyncio.to_thread(_ollama, url, body, timeout), timeout)
        facts = strict_loads((raw.get("message") or {}).get("content", ""))["facts"]
    except (asyncio.TimeoutError, OSError, ValueError, KeyError, TypeError) as e:
        return {"ok": False, "error": "extractor_unavailable", "detail": f"{type(e).__name__}: {str(e)[:120]}"}
    out, dropped = [], []
    for f in embedded_places(facts if isinstance(facts, list) else []):
        if not isinstance(f, dict) or f.get("predicate") not in PREDICATES:
            continue
        f["subject"] = f["subject"].strip().lower()[:80]
        said = {" ".join(c.split()) for c in re.findall(r"-?\d+\s+-?\d+\s+-?\d+", text)}
        if f["predicate"] in ("location", "possible_location") and len(said) == 1:
            # set 11 E13: qwen copied "-210 70 400" as "-210 70 403" and the guard dropped the correction; with one
            # place in his words, his coordinates are the place (never the model's digits)
            f["object"] = re.sub(r"-?\d+ -?\d+ -?\d+", next(iter(said)), f["object"])
        if f["predicate"] == "location" and not re.search(r"-?\d+ -?\d+ -?\d+", f["object"]):
            f["predicate"] = "relation"              # O69j: a place in words is a relation, never dropped
        elsewhere = other_world(text) if f["predicate"] in PLACES + ("wish",) else ""
        if elsewhere:                                # O101q: never a place (or wish) in THIS world - not stored
            dropped.append({**f, "dropped": f"other_world ({elsewhere}): a place in another world is not one here"})
            continue
        wish, at = WISH.search(text), text.lower().find(f["subject"])
        if (wish and (at < 0 or wish.start() < at) and not EXISTS.search(text[max(at, 0):]) or WISH_ANY.search(text)) \
                and f["predicate"] in ("location", "possible_location", "relation"):
            f["predicate"] = "wish"                  # O86k (set 12 T07): wished, not there - never a (hedged) place
        if f.get("removed") is True and not NEAR_MISS.search(text):   # O69j: the extractor's reading decides a removal;
            said = '"' + text.strip()[:300] + '"'   # O69j: kept with HIS sentence, never a paraphrase ("gone (down)")
            f["predicate"], f["object"], f["removal"] = "status", f"gone ({said})", True
        elif STILL.search(text):
            f["restore"] = True                      # O69j/O81: undone by his next sentence - his own still-words
        if f["predicate"] == "rule" and TASTE.search(text):
            f["predicate"] = "preference"            # "I can't stand flat roofs": the owner's taste, not an order
        elif f["predicate"] == "preference" and not TASTE.search(text) and \
                re.search(r"\b(?:never|don'?t|do not|must not)\b", text, re.I):
            f["predicate"] = "rule"                  # "Never use TNT, anywhere": an order, not a taste
        if f["predicate"] in ("preference", "rule", "access"):
            f["object"] = wood_for_block(named_only(item_ids_in(f["object"]), text,
                                                    next((k["object"] for k in known if k["subject"] == f["subject"]),
                                                         "")), f["subject"], text)
        if f["subject"] in ("owner", "the owner", "me", "eve", "rule", "rules", "preference", "preferences") and \
                f["predicate"] in ("rule", "preference"):
            f["subject"] = topic_of(f["object"])      # a taste or rule is filed under what it is about, never "owner"
        use = FOR_USE.search(text)
        if use and f["predicate"] in ("preference", "rule") and not stems(f["subject"]) & stems(text) and \
                f["subject"] not in {k["subject"] for k in known}:
            f["subject"] = use[1].lower()             # "glass panes ... for windows" is about windows, not the model's guess
        if not f.get("removal"):                     # O81: a removal keeps its exact subject - never re-filed under a
            f["subject"] = moved_thing(f, known, text) or same_thing(f, known)   # look-alike it could wrongly retire
        prior = next((k["object"] for k in known if k["subject"] == f["subject"] and k["predicate"] == f["predicate"]), "")
        placed = any(k["subject"] == f["subject"] and k["predicate"] in PLACES for k in known)
        if f["predicate"] in PLACES and not placed:  # a correction of a KNOWN place is exempt (he told her it is there)
            f["place_check"] = "skipped" if self_placed(f) else await place_check(text, f["subject"], f["object"], url,
                                                                                    model)
            if not f["place_check"].startswith("there"):
                f["predicate"], prior = "wish", ""   # O86x: fail-safe - a wish, plan or idea, never a place to send to
        why = guard(f, text, prior)
        if why and prior and not why.startswith(("number", "the owner negated")) and f["predicate"] in (
                "status", "identity", "possible_location", "location", "preference", "rule", "access"):
            # O42: an owner correction always supersedes; the model's paraphrase is not his - store HIS words
            f["object"] = owner_words(text, f)
            why = None if f["object"] else why
        elif why and prior:
            f["confirm"] = why                       # O42: a correction a guard doubts is asked about, never dropped
        f["world_specific"] = scope(f, text)
        if not f["world_specific"] and everywhere(text) and f["predicate"] not in ("preference", "schedule") and \
                f["subject"] != "owner":             # O86x (set 16 U04 "this is the only one where..."): the every-
            f["scope_check"] = await scope_check(text, f["subject"], f"{f['predicate']} {f['object']}", url, model)
            f["world_specific"] = f["scope_check"] != "every_world"     # world words only propose; a false global is harm
        f["certain"] = not HEDGE.search(text) and f["predicate"] != "possible_location" and \
            "hedged" not in f.get("place_check", "")   # O86x: his doubt, heard by the list OR the focused call
        f["corrects"] = bool(prior)
        (dropped if why else out).append({**f, "dropped": why} if why else f)
    return {"ok": True, "facts": out, "dropped": dropped}


TASTE = re.compile(r"\b(?:i (?:really )?(?:like|love|hate|prefer|enjoy)|i'?m into|can'?t stand|looks? (?:better|worse|"
                   r"awful|bad|good|great|nice|ugly)|favou?rite|to me\b|"
                   r"(?:should|ought to|must|has to|have to|needs? to) be (?!\w+(?:ed|en)\b)(?!lit\b|kept\b|left\b)\w+)",
                   re.I)      # "doors should be iron" is a taste; "should be harvested only when I ask" stays a rule


WOOD_BLOCKS = {"fence gate": "fence_gate", "gate": "fence_gate", "pressure plate": "pressure_plate",
               "hanging sign": "hanging_sign", "trapdoor": "trapdoor", "fence": "fence", "door": "door",
               "stair": "stairs", "slab": "slab", "button": "button", "sign": "sign", "boat": "boat", "log": "log"}


def wood_for_block(obj, subject, text):
    """CP-44 set 4 S2: "for fences I always want dark oak" names the wood of the fences - minecraft:dark_oak_fence, not
    the planks a bare wood name otherwise means. Only when the topic is one block type and the owner never said
    planks."""
    if "plank" in text.lower():
        return obj
    subj = subject.lower()
    kind = next((v for k, v in WOOD_BLOCKS.items() if re.search(rf"\b{k}s?\b", subj)), None)
    if kind is None:
        return obj
    items = known_items()
    return re.sub(r"minecraft:([a-z_]+)_planks",
                  lambda mo: f"minecraft:{mo[1]}_{kind}" if f"minecraft:{mo[1]}_{kind}" in items else mo[0], obj)


FOR_USE = re.compile(r"\bfor (?:the |my |all |any )?([a-z]+(?: [a-z]+)?)\s*[.!]?\s*$", re.I)   # "... for windows."


def named_only(obj, text, prior):
    """Keep only the item ids the owner named (or the corrected fact had); the model sometimes lists every related
    item ("dark oak" -> logs, leaves, doors ...). The rest of the object stays."""
    words, ids = text.lower(), list(dict.fromkeys(IDS.findall(obj)))
    keep = [i for i in ids if i in prior or all(w[:4] in words for w in i.split(":", 1)[1].split("_")
                                                if w not in ("planks", "block"))]
    if keep == ids:
        return obj
    rest = re.sub(r"\s*(?:,|and|or|over|not)?\s*minecraft:[a-z0-9_]+", "", obj).strip(" ,.;")
    return " and ".join(keep) + (f", {rest}" if rest else "") if keep else rest


def topic_of(obj):
    """The topic of a taste/rule from its own object: the items it names ("never use minecraft:tnt" -> "tnt"), else its
    first content words."""
    ids = [i.split(":", 1)[1].replace("_", " ") for i in IDS.findall(obj)]
    if ids:
        return " and ".join(dict.fromkeys(ids))[:60]
    ws = [w for w in re.findall(r"[a-z]{3,}", obj.lower()) if w not in STOP and w not in ("anywhere", "ever", "always")]
    return " ".join(ws[:3]) or "owner"


def owner_words(text, f):
    """The owner's own statement as the value of a correction: the sentence minus a leading interjection; a location
    correction keeps only the coordinates he gave."""
    if f["predicate"] == "location":
        c = re.findall(r"-?\d+ -?\d+ -?\d+", text)
        return c[-1] if c else ""
    t = re.sub(r"^\s*(?:wait|oops|okay|ok|actually|so|hey|eve)[,!:]*\s+", "", text.strip(), flags=re.I)
    return t.rstrip(" .!")[:200]


MOVED = re.compile(r"\b(?:moved|relocated|shifted|rebuilt|put it|placed it|carried|dragged)\b", re.I)
MOVE_NEAR = 16


def moved_thing(f, known, text):
    """O42 gate 2 G08 / set 3 C1: "I relocated the leather workshop to 6 64 -24" after KNOWN "tannery 5 64 -22" - the
    owner moved a known thing and called it something else. A move to a place within 16 blocks of exactly one KNOWN
    location (and no KNOWN subject by this name) is that thing, so the correction supersedes it."""
    if f["predicate"] != "location" or not MOVED.search(text) or f["subject"] in {k["subject"] for k in known}:
        return None
    new = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", f["object"])[:3]]
    if len(new) < 3:
        return None
    near = set()
    for k in known:
        if k["predicate"] in ("location", "possible_location"):
            old = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", k["object"])[:3]]
            if len(old) == 3 and math.dist(old, new) <= MOVE_NEAR:
                near.add(k["subject"])
    return near.pop() if len(near) == 1 else None


# ---- O69a: three statement shapes parsed without the model (the label of the line never matters)
LEAD = r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:please\s+)?(?:let'?s\s+|we'?ll\s+)?"
SCOPE_LEAD = re.compile(r"^\s*(?:(?:in|on|across|for)\s+)?(?:all|every|any|each)(?:\s+one|\s+single)?\s+(?:of\s+)?"
                        r"(?:my\s+|our\s+)?(?:worlds?|maps?|servers?|saves?|realms?|playthroughs?)\s*,?\s*|^\s*(?:wherever|(?:whichever|whatever) (?:world|map|server|save)|"
                        r"no matter (?:which|what|where)(?: (?:world|map|server))?)(?:\s+(?:i|we)(?:'m|\s+am|\s+are|\s+play|"
                        r"\s+go|\s+build|\s+live|\s+end up)(?:\s+(?:in|on))?)?\s*,?\s*", re.I)    # O69f: the scope families
ARTICLE = re.compile(r"^(?:the|my|our|this|that|a|an|some)\s+", re.I)
CAPS_TAIL = re.compile(r"^(?P<x>.+?)\s+(?P<y>(?:[A-Z][\w'-]*)(?:\s+[A-Z][\w'-]*)*)$")
RENAME_TO = re.compile(LEAD + r"(?:rename|name|call)\s+(?P<x>.+?)\s+(?:to|as)\s+(?!of\b)(?P<y>.+?)$", re.I)
RENAME_BARE = re.compile(LEAD + r"(?:call|name)\s+(?P<rest>.+)$", re.I)          # "let's call the cave Echo Hollow"
RENAME_SAID = re.compile(LEAD + r"(?:i'?m\s+|i\s+am\s+|we'?re\s+|i'?ve\s+|we'?ve\s+|i\s+|we\s+)?(?:renam(?:e|ed|ing)|"
                         r"call(?:ed|ing)|nam(?:ed|ing)|dub(?:bed|bing)?|christen(?:ed|ing)?)\s+"
                         r"(?P<rest>(?:the|my|our|that|this)\s+.+)$", re.I)   # "I'm renaming the hall The Exchange": a thing
TIME_TAIL = re.compile(r"\s*,?\s+(?:from now on|from here on(?: out)?|going forward|henceforth|instead|from today(?: on)?|"
                       r"for good)\s*$", re.I)     # set 11 E18: "Let's call the guild hall The Exchange from now on"
TIME_LEAD = re.compile(r"^\s*(?:from now on|going forward|starting (?:today|now)|from here on(?: out)?|henceforth)\s*,?\s+",
                       re.I)
RENAME_FROM_NOW = re.compile(LEAD + r"(?P<x>.+?)\s+is\s+(?:called\s+|named\s+)?(?P<y>.+?)\s+from now on$", re.I)
POSS = r"(?:'s|s'|’s)"
RENAME_FORMS = [re.compile(LEAD + p, re.I) for p in (       # O69f: the name-change family (the new name capitalised)
    r"change\s+the\s+name\s+of\s+(?P<x>.+?)\s+to\s+(?P<y>.+?)$",
    r"(?:change|switch|swap|flip|move)\s+(?P<x>.+?)" + POSS + r"\s+name\s+(?:over\s+)?to\s+(?P<y>.+?)$",   # set 14 V15
    r"(?:the\s+)?new\s+name\s+(?:of|for)\s+(?P<x>.+?)\s+is\s+(?P<y>.+?)$",
    r"(?P<x>.+?)" + POSS + r"\s+new\s+name\s+is\s+(?P<y>.+?)$",
    r"(?P<x>.+?)\s+(?:is|'s)\s+now\s+(?:called|named)\s+(?P<y>.+?)$",
    r"(?P<x>.+?)\s+(?:is|'s)\s+(?:called|named)\s+(?P<y>.+?)\s+now$",
    r"(?P<x>.+?)\s+(?:goes|will go)\s+by\s+(?P<y>.+?)$",
    r"(?P<x>.+?)\s+(?:is|'s|will be|shall be)\s+(?:now\s+)?known\s+as\s+(?P<y>.+?)$",
    r"(?P<x>.+?)\s+(?:will|shall)\s+be\s+(?:called|named)\s+(?P<y>.+?)$",
    r"(?:give|giving)\s+(?P<x>.+?)\s+(?:a\s+)?(?:new|fresh|different|proper)\s+name\s*[:,-]?\s+(?:of\s+)?(?P<y>.+?)$",
    r"(?P<x>.+?)\s+gets\s+(?:a\s+)?(?:new|fresh|different)\s+name\s*[:,-]?\s+(?P<y>.+?)$")]   # set 13 Q15
PLACE_VERB = (r"(?:is|are|sits|sit|stands|stand|lies|lie|goes|go|belongs|belong|stays|stay|is kept|are kept|"
              r"is stored|are stored|lives|live|runs|run|leads|lead|stretches|stretch|hangs|hang|is hanging|juts out|"
              r"jut out|perches|perch|is perched|are perched|rests|rest|is tucked|are tucked|is nestled|nestles)")
CORRECTION = re.compile(LEAD + r"(?:actually,?\s+|sorry,?\s+|no,?\s+|oops,?\s+|hold on,?\s+|hang on,?\s+|wait,?\s+)?"
                        r"(?:make (?:that|it)|change (?:that|it) to|correction\b|scratch that|i meant|it'?s actually|"
                        r"that should be|i misspoke|i mis-?spoke|i got (?:that|it) wrong|i was wrong|my mistake|my bad|"
                        r"ignore (?:my|that) last|wrong numbers)\b", re.I)    # set 14 V12: "Hold on, I misspoke: ..."
PREP = (r"(?:next to|beside|near|by|behind|in front of|underneath|under|below|above|on top of|across from|opposite|"
        r"inside|(?:at|on|in|to) the (?:(?:far|other|north|south|east|west|left|right|back|front)\s+)?(?:end|edge|foot|"
        r"top|bottom|back|front|side|centre|center|middle|corner|entrance|mouth|north|south|east|west|left|right) of|"
        r"in|on|between|from)")         # between A and B / from A to B (set 7); "at the end of the pier" (set 8 H16)
RELATION = re.compile(LEAD + r"(?P<x>[a-z][a-z' -]*?)\s+(?:always\s+|usually\s+|now\s+)?(?P<verb>" + PLACE_VERB +
                      r")\s+(?:right\s+|just\s+|always\s+)?(?P<prep>" + PREP + r")\s+(?P<y>.+?)$", re.I)
KEEP = re.compile(LEAD + r"I\s+(?:always\s+|usually\s+|now\s+)?(?:keep|put|store|stash|leave)\s+(?![a-z]+ing\b)(?P<x>[a-z][a-z' -]*?)"
                  r"\s+(?:right\s+|just\s+)?(?P<prep>" + PREP + r")\s+(?P<y>.+?)$", re.I)    # set 7 Y10: "I keep X by Y"
TRAIL_SCOPE = re.compile(r"[\s,]+(?:(?:in|on|across|for)\s+)?(?:" + EVERYWHERE.pattern + r")\s*$", re.I)
FRONT_LEAD = r"(?:(?:halfway|right|just|directly|somewhere|far|deep|way)\s+)?"
WAY = r"(?:" + PREP + r"|up|down|along|past|beyond|across|around|over|through|outside|among)"  # directions, not states
FRONTED = re.compile(LEAD + r"(?P<lead>" + FRONT_LEAD + r")(?P<prep>" + WAY + r")\s+(?P<y>.+?)\s*,?\s+(?P<verb>sits|sit|"
                     r"stands|stand|lies|lie|is|are)\s+(?P<x>(?:the|my|our|a|an)\s+[a-z][a-z' -]*?)$", re.I)
THERE = re.compile(LEAD + r"there(?:'s|\s+is|\s+are)\s+(?P<x>(?:a|an|the|some|my|our)\s+[a-z][a-z' -]*?)\s+"
                   r"(?P<lead>" + FRONT_LEAD + r")(?P<prep>" + WAY + r")\s+(?P<y>.+?)$", re.I)   # O69f: "there's X by Y"
# O69f: one closed family of gone-states and past-tense gone-verbs, for the parser AND the extractor's status meaning
GONE_STATE = (r"(?:(?:all\s+)?gone|(?:all\s+)?used up|torn down|demolished|destroyed|removed|knocked down|pulled down|"
              r"taken down|blown up|burn(?:ed|t) down|dismantled|wrecked|no longer there|not there any ?more|no more)")
GONE_VERB = (r"(?:tore down|knocked down|pulled down|took down|blew up|burn(?:ed|t) down|demolished|destroyed|dismantled|"
             r"removed|wrecked|got rid of|blasted)")
GONE_NO_COPULA = (r"(?:no longer exists?|(?:doesn'?t|does not|don'?t|do not) exist any ?more|(?:isn'?t|aren'?t) there "
                  r"any ?more|exists? no more|(?:has |have )?(?:collapsed|caved in|burn(?:ed|t) down|blown up))")
GONE = re.compile(r"\b(?:" + GONE_STATE + "|" + GONE_VERB + "|" + GONE_NO_COPULA + r"|exploded)\b", re.I)
NEAR_MISS = re.compile(r"\b(?:almost|nearly|barely|tried to|tries to|try to|wanted to|might|may have|could have|"
                       r"would have|if|didn'?t|did not|never|won'?t|will|going to|gonna|plan(?:ning)? to|should|"
                       r"damaged|broken|cracked|half|partly|partially|part of|some of|a bit of|leaking|flooded)\b",
                       re.I)            # set 9 W05 "almost destroyed"; O81: a damaged or planned thing still stands
STILL = re.compile(r"\b(?:still|after all|not gone|isn'?t gone|wasn'?t (?:destroyed|removed|gone|burned|torn down)|"
                   r"is back|are back|rebuilt|i was wrong|my mistake|scratch that)\b", re.I)   # O81: undo on his words
REMOVED = re.compile(LEAD + r"(?P<x>[a-z][a-z' -]*?)(?:\s+at\s+-?\d+\s+-?\d+\s+-?\d+)?\s+(?:(?:is|was|are|were|has been|"
                     r"have been|got)\s+(?:now\s+)?" + GONE_STATE + "|" + GONE_NO_COPULA + r")"
                     r"(?=\s*(?:now|for good|forever)?\s*(?:[,.!;]|$))", re.I)     # set 7 Y14: "X no longer exists"
REMOVED_BY_ME = re.compile(r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:i|we|(?:a|an|the|some|my|our)\s+[a-z][a-z' -]*?|[A-Z][a-z]+)\s+"
                           r"(?:just\s+|finally\s+|accidentally\s+)?" + GONE_VERB + r"\s+(?P<x>(?:the|my|our|that|this)\s+"
                           r"[a-z][a-z' -]*?)(?:\s+at\s+-?\d+\s+-?\d+\s+-?\d+)?\s*(?:[,.!;]|$)", re.I)  # any agent (set 8)
NOT_A_SUBJECT = re.compile(r"\b(?:can|could|may|might|will|would|should|must|shall|use|uses|used|has|have|had|"
                           r"likes?|wants?|needs?|lets?|allowed)\b", re.I)    # a clause, not a thing
SCOPE_ONLY = re.compile(r"^(?:this|that|the|my|our|every|all|any)?\s*(?:world|map|server|save)s?$", re.I)
NOT_A_THING = {"i", "it", "you", "we", "he", "she", "they", "this", "that", "there", "me", "myself", "everything"}


def thing(x):
    """'The cave by the river' -> 'cave by the river'; None for pronouns or empty."""
    x = ARTICLE.sub("", x.strip(" ,.!")).strip().lower()
    return None if not x or x in NOT_A_THING else x


LOCAL_LEAD = re.compile(r"^\s*(?:(?:right|over|up|down|out|back)\s+)?here\s*,?\s+|^\s*(?:in|on|for)\s+this\s+(?:world|"
                        r"map|server|save)\s*,?\s*", re.I)          # set 8 H19: "here the cauldron" is the cauldron


def plain(text):
    t = SCOPE_LEAD.sub("", text.strip())
    for _ in range(2):                                              # "In this world, right here, the X ..."
        t = LOCAL_LEAD.sub("", t)
    return t.rstrip(" .!")


def parse_rename(text):
    """(subject, name) for "rename/name/call X to|as Y", "let's call X Y" (Y capitalised) and "X is Y from now on";
    None for questions, coordinates, "call me ..." (the owner's own name goes through the extractor)."""
    if "?" in text or re.search(r"-?\d+\s+-?\d+\s+-?\d+", text):
        return None
    t = plain(text)
    if not RENAME_FROM_NOW.match(t):
        t = TIME_TAIL.sub("", TIME_LEAD.sub("", t))
    m = RENAME_TO.match(t)
    said = RENAME_SAID.match(t)
    if m:
        x, y = m["x"], m["y"]
    elif said and CAPS_TAIL.match(said["rest"]):
        c = CAPS_TAIL.match(said["rest"])
        x, y = c["x"], c["y"]
    elif RENAME_FROM_NOW.match(t):
        m = RENAME_FROM_NOW.match(t)
        x, y = m["x"], m["y"]
        if not y[:1].isupper():                    # "the farm is closed from now on" is a status, not a name
            return None
    elif RENAME_BARE.match(t) and CAPS_TAIL.match(RENAME_BARE.match(t)["rest"]):
        c = CAPS_TAIL.match(RENAME_BARE.match(t)["rest"])
        x, y = c["x"], c["y"]
    elif any(r.match(t) for r in RENAME_FORMS):
        m = next(r.match(t) for r in RENAME_FORMS if r.match(t))
        x, y = m["x"], m["y"]
        if not y.strip(" \"'")[:1].isupper():      # "the farm is now called for" / a lower-case value is not a name
            return None
    else:
        return None
    x, y = thing(x), y.strip(" ,.!\"'")
    return (x, y) if x and y and not y.lower().startswith(("me ", "it ")) else None


def parse_relation(text):
    """(subject, 'relation', '<prep> <place>') for a placement without coordinates ("the forge sits beside the
    well"); a bare is/are + in/on is a state ("the farm is in bad shape"), not a place."""
    if "?" in text or re.search(r"-?\d+\s+-?\d+\s+-?\d+", text):
        return None
    t = plain(text)
    m = RELATION.match(t) or KEEP.match(t) or FRONTED.match(t) or THERE.match(t)
    if not m or (m.re in (RELATION, FRONTED) and m["verb"].lower() in ("is", "are") and
                 m["prep"].lower() in ("in", "on", "from") and not m.groupdict().get("lead")):
        return None
    y = TRAIL_SCOPE.sub("", m["y"].strip(" ,.!")).strip(" ,.!")      # "... next to my bed in every world"
    if NOT_A_SUBJECT.search(m["x"]) or not y or SCOPE_ONLY.match(y) or GONE.search(text):
        return None                 # dev M05: "Priya can use the brewing stand on this world" - "stand" is a noun
    x = thing(m["x"])
    lead = (m.groupdict().get("lead") or "").lower()
    return (x, "relation", f"{lead}{m['prep'].lower()} {y}") if x else None


def parse_correction(text):
    """O84 (set 10 N19: "Make that -90 30 125 for the glow lichen cave." went to the command path): a correction is a
    statement - routed to the extractor with what she knows, never to a skill."""
    return "?" not in text and bool(CORRECTION.match(text.strip()))


def parse_keep(text):
    """The owner says where HE keeps a thing: always a statement, never a request to Eve."""
    return parse_relation(text) if KEEP.match(plain(text)) else None


def parse_removal(text):
    """The thing the owner says is gone / he tore down, or None."""
    if "?" in text or NEAR_MISS.search(text):
        return None                 # it nearly happened, or might: nothing is gone
    t = plain(text)
    m = REMOVED.match(t) or REMOVED_BY_ME.search(t)
    if not m or re.search(r"\b(?:from|out of|off|into|onto|with)\b", m["x"], re.I):
        return None                 # "I removed the torches from the chest" moves items, it removes no place
    return thing(m["x"])


def norm_words(x):
    return [w.rstrip("s") for w in re.findall(r"[a-z]{3,}", x.lower()) if w not in STOP]


def same_thing(f, known):
    """A fact about something KNOWN files under its existing subject, so a correction supersedes it: the same
    predicate and either all of the new subject's words in the known subject (or the reverse), or - for tastes and
    rules - an item both name ("lanterns are fine" corrects "torches, not lanterns")."""
    words = lambda x: {w.rstrip("s") for w in re.findall(r"[a-z]{3,}", x.lower())} - STOP  # noqa: E731
    mine, items = words(f["subject"]), set(IDS.findall(f["object"]))
    for k in known:
        if k["predicate"] != f["predicate"] and {k["predicate"], f["predicate"]} != {"location", "possible_location"}:
            continue
        theirs = words(k["subject"])
        if mine and theirs and (mine <= theirs or theirs <= mine):
            return k["subject"]
        if f["predicate"] in ("preference", "rule") and items & set(IDS.findall(k["object"])):
            return k["subject"]
    return f["subject"]
