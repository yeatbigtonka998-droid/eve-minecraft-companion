"""Mind core: control (stop/resume/mode), durable tasks and actions, reconciliation, world data, approvals.

Invariants (CP-11, CP-19..CP-24):
- every action has a status row before it is sent; transitions are guarded UPDATEs, so a duplicate or late
  message can never move an action backwards or multiply it (idempotency by action_id);
- an action is re-sent only inside the body session it was first sent on (the body dedupes by action_id there);
  after a mind restart or a new body session it is reconciled against observed state, never replayed;
- STOPPED is durable (meta table), survives restarts, and is cleared only by a trusted resume;
- nothing is dispatched while body/sense are down, disagree on the world, or the state is stale.
"""
import asyncio
import functools
import contextlib
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

from websockets.exceptions import ConnectionClosed

from .candidates import Candidates
from .commands import ASK_POS, ASK_WHERE_YOU, REPORT_START, item_id, map_command
from .creeper import SELF_DEFENCE, CreeperGuard
from .memory import (HEDGE, everywhere, JOB_SKILLS, TASTE, Memory, extract_facts, parse_correction, parse_fact, parse_keep,
                     parse_relation, parse_removal, parse_rename)
from .objective import Runner
from .supply import Repair, Supply
from .bridge import Bridge
from .ironkit import IronKit
from .nightsurvival import DUSK, SEAL_AT, NightSurvival, night as is_night
from .follow import BOAT, Follow
from .intent import classify
from .db import Db, open_mem
from .hub import grant_region_ok
from . import build_flow, craftkit, dialogue, grammar, owner_phrases, progress, recover, workstations
from .config import MODES, lane, strict_loads
from .hub import now_ms, refusal
from .planner import (EFFECTFUL, FACE, DeepThinker, Planner, block_effects, bounds, entity_check, envelope_allows,
                      gather_order, normalize_plan, reserve_spend, resource_params, spend_summary, validate_plan)

log = logging.getLogger("eve.mind")
FIND_R, FIND_DY = 32, 8      # O164: the owner radius (mind-c sets 32) and the height band a find searches
SURVEY_MAX_QUERIES = 128           # v1.9 survey: 128 x 32 768 blocks at most per call
JOB_CAPS_USD = {"brief": 1.00}     # O56a: design's CP-54 wording calls, the whole checkpoint


def _mind_commit(here=None):
    """The code this process runs, read once at startup (O53c: a stale process after a checkout update shows up).
    A MIND_COMMIT file beside this package (written by foundation's archive step) wins - an unpacked archive may sit
    inside some other git repo; then the package's git checkout; then env EVE_MIND_COMMIT; else None, never a guess."""
    here = here or Path(__file__).resolve().parent
    f = here / "MIND_COMMIT"
    if f.is_file():
        return f.read_text(encoding="utf-8").strip() or None
    try:
        git = lambda *a: subprocess.run(["git", *a], cwd=here, capture_output=True, text=True, timeout=5)  # noqa: E731
        if git("ls-files", "--error-unmatch", "mind.py").returncode == 0:   # this file is tracked: our checkout
            head = git("rev-parse", "--short", "HEAD").stdout.strip()
            if head:
                return head
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get("EVE_MIND_COMMIT", "").strip() or None


def _mind_src(here=None):
    """O83h: (commit, tree) the RUNNING parts/mind/src came from - not the checkout's HEAD (lane E ran daf8fd6's src
    under HEAD 6b666a9, so --expect-mind against mind_commit refused). The installed MIND_COMMIT file wins; else the
    newest commit on any ref whose parts/mind/src tree equals this checkout's (src unedited in place); else None."""
    here = here or Path(__file__).resolve().parent
    f = here / "MIND_COMMIT"
    if f.is_file():
        return f.read_text(encoding="utf-8").strip() or None, None
    src = here.parent
    try:
        def git(*a, inp=None):
            return subprocess.run(["git", *a], cwd=src, input=inp, capture_output=True, text=True, timeout=10)
        prefix = git("rev-parse", "--show-prefix").stdout.strip().rstrip("/")
        if not prefix or git("diff", "--quiet", "--", ".").returncode != 0:
            return None, None                          # not a checkout, or edited in place: no commit holds it
        tree = git("write-tree", f"--prefix={prefix}/").stdout.strip()
        revs = git("rev-list", "--all", "-n", "500", "--", ".").stdout.split()
        trees = git("cat-file", "--batch-check=%(objectname)", inp="\n".join(f"{r}:{prefix}" for r in revs) + "\n")
        hit = next((r for r, t in zip(revs, trees.stdout.split("\n")) if tree and t.strip() == tree), None)
        short = git("rev-parse", "--short", hit).stdout.strip() if hit else None   # git's own length (516ae696
        return short or None, tree or None                                        # needs 8: "516ae69" was ambiguous)
    except (OSError, subprocess.SubprocessError):
        return None, None


MIND_COMMIT, MIND_STARTED = _mind_commit(), time.strftime("%Y-%m-%dT%H:%M:%S%z")
MIND_SRC, MIND_SRC_TREE = _mind_src()

OPEN = ("pending", "sent", "accepted", "running")
KEEP_GOING = re.compile(r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:please\s+)?keep\s+going[\s.!]*$", re.I)
RESULT_FROM = OPEN + ("uncertain",)          # a body result may still settle an uncertain action (late result)
TASK_FOR = {"ok": "done", "failed": "failed", "timeout": "failed", "rejected": "failed",
            "cancelled": "cancelled", "uncertain": "blocked"}
PERMISSION_EFFECT = {"break": "break", "place": "place", "container": "open_container", "attack": "attack",
                     "use_item": "use_item", "hazard": "use_hazard"}
IMPERATIVE = re.compile(r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:please\s+|can you\s+|could you\s+)?"
                        r"(?:build|make|craft|bring|fetch|go|get|mine|dig|place|put|walk|come|follow|attack|kill|do|"
                        r"open|give|collect|gather|chop|cut|plant|harvest|smelt|cook|eat|clear|fill|move|take|"
                        r"find|explore|guard|defend|light|fix|repair|drop|store|deposit|withdraw)\b(?![^?]*\?\s*$)",
                        re.I)
NOT_ORDER = re.compile(r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:make|change|correct) (?:that|it)\b|"
                       r"^\s*(?:if|when|once|suppose|imagine)\b|"         # a condition or a musing (dev M27)
                       r"\byou(?:'d|'ll| will| would| can)? (?:spot|find|see|reach|hit|come (?:to|across))\b", re.I)
#   O94i: imperative-shaped but not an order - a correction ("Make that 120 40 -310", set 11 E11) or directions to a
#   real place ("Walk past the boathouse and you'd spot the lobster pot", set 15 W13): memory as before, no gate
QUESTION = re.compile(r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:what|why|did|where|when|who|how|are|is|do|does|have|"
                      r"has|were|was|which|whose)\b[^?]*\?\s*$", re.I)
NOT_A_FACT = re.compile(r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:(?:remind|tell|show|ask) me\b|i (?:need|have|want|should|"
                        r"ought|gotta|wanna|'m going) to\b|i'll\b|let's\b)|\b(?:lol|lmao|haha|hehe)\b|[:;]-?[)(dp]|"
                        r"\bi(?:'ll| will|'m going to| am going to| plan to| want to)(?: \w+)? (?:start|build|make|"
                        r"put|set up|dig|move|try)\b",
                        re.I)      # "remind me where…" asks; "I need to stop eating cookies lol" is chat
ASKING = ("records_question", "capability_question", "passed_on_claim", "mechanics_question", "advice", "about_eve")
BRIDGE_CMD = re.compile(r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:please\s+)?bridge\s+(?:over\s+)?(?:to\s+)?"
                        r"(-?\d+)\s+(-?\d+)\s+(-?\d+)\s*[.!]*$", re.I)
# O30 safety: an owner statement of what he did or where something is never maps to a skill
STATEMENT = re.compile(r"\bi (?:just |already |finally |recently )?(?:moved|built|put|placed|relocated|made|set up|left|dug|"
                       r"finished|fixed|changed|switched|swapped|replaced|found|planted|started|stored|hid|rebuilt)\b|"
                       r"\b(?:it'?s|it is|they'?re|they are|\w+ (?:is|are)) (?:now |still )?(?:at|near|by) -?\d+",
                       re.I)
CHAT_MARK = re.compile(r"\b(?:lol|lmao|haha|hehe|ugh)\b|[:;]-?[)(dp]", re.I)
PREF_VERB = re.compile(r"\b(?:like|likes|love|loves|prefer|prefers|favou?rite|hate|hates|enjoy|enjoys|rather|better than|"
                       r"look(?:s)? (?:better|awful|bad|good|great|nice|ugly)|can'?t stand|only for|keep the|are fine|"
                       r"is fine|use either|don'?t use|never use|(?:always|usually|only ever) want)\b", re.I)
THIRD_PARTY = re.compile(r"\b(?:says|said|thinks|thought|reckons|claims|claimed|told me|tells me|keeps saying|swears|wrote|writes|posted|"
                         r"swore)\b|^\s*i (?:read|heard) that\b",
                         re.I)
CAN_YOU = re.compile(r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:can|could|would|will) you\b[^?]*\?\s*$", re.I)
# O111 (45L diagnosis 11:0x): her question waits ASK_TTL_S for his answer; a line of bare coordinates is an answer or
# a question back, never a fact; "go to X Y Z" is a rule; what she carries comes from the state frame
ASK_TTL_S = 120
INTERIM_S = 8.0           # O111: an owner line with no reply by then gets "give me a moment" (bar: 10 s)
OWNER_POS_S = 3.0         # O121: sense's owner.pos counts while at most this old
NIGHT_FIGHT_R = 24.0      # O126 (b): at night she fights his named mob only this close to the shelter door
CLEAR_ROUNDS = 40         # O133: gather calls per "clear the trees" order (each fells up to 256 logs or ends full)
HURT_HP, HURT_S, BACK_OFF_R = 8, 5.0, 24.0   # O139: back off at this HP or less within this long of a hit; to him within
HOLD_HP = 6               # S28-DEATH-2: at this HP or less, a hostile within 8 and body's reflex active, the mind holds
HOSTILE = re.compile(r"\b(" + owner_phrases.HOSTILE_WORDS + r")\b", re.I)          # O126: mobs named in chat
FIGHT_PRONOUN = re.compile(r"^(.*?\b(?:fight|attack|kill|slay|deal with|shoot|take out)\s+)(?:it|that one|that|this one|him|"
                           r"them)((?:\s+(?:please|now|for me|too|quickly|again|off|out|down))*\s*[.!?]*\s*)$", re.I)
#                            ^ the pronoun ends the order ("kill that zombie" names its mob); "get it" stays a fetch
def lost_words(r):
    """'mine resource (2 grass block)': an action dropped at a restart, as she says it."""
    p = json.loads(r["params"] or "{}")
    what = p.get("block") or p.get("tag") or p.get("item") or p.get("product") or ""
    bits = " ".join(str(x) for x in (p.get("count"), what.split(":")[-1].replace("_", " ")) if x)
    return r["skill"].replace("_", " ") + (f" ({bits})" if bits else "")


THEN_SPLIT = re.compile(r"\s*(?:,\s*|\s+)(?:and\s+)?then\s+", re.I)   # O122: "a and then b" / "a, then b" / "a then b"
# O111-b (5): a question is never executed - it ends with "?" or starts with one of these words; state questions are
# answered from the latest state frame
QUESTION_START = re.compile(r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:do|does|did|are|is|can|could|what|where|which|"
                            r"how|why|who)\b", re.I)
WHERE_YOU = re.compile(r"\bwhere are you\b|\b(?:your|ur) (?:position|location|coords?|coordinates)\b", re.I)
HEALTH_Q = re.compile(r"\b(?:how(?:'s| is) your health|how much health|your health|are you hurt|are you hungry|"
                      r"your food)\b", re.I)
HOME_Q = re.compile(r"\bwhere(?:'?s| is) (?:my |your |our |the )?home\b", re.I)
_N = r"(-?\d+(?:\.\d+)?)"
BARE_XYZ = re.compile(rf"^\s*(?:(?:at|to|it'?s at)\s+)?(?:x\s*[:=]?\s*)?{_N}[\s,]+(?:y\s*[:=]?\s*)?{_N}[\s,]+"
                      rf"(?:z\s*[:=]?\s*)?{_N}\s*[.!]*$", re.I)
WALK_XYZ = re.compile(r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:please\s+)?(?:come|walk|go|head|move|run|get)\s+(?:back\s+|over\s+)?"
                      r"(?:to\s+)?(?:x\s*)?-?\d+(?:\.\d+)?[\s,]+(?:y\s*)?-?\d+(?:\.\d+)?[\s,]+(?:z\s*)?-?\d+(?:\.\d+)?"
                      r"(?:[\s,]+(?:please|now))*\s*[.!]*$", re.I)
YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|correct|right|that'?s right|exactly)\b[\s.!]*$", re.I)
NO = re.compile(r"^\s*(?:no|nope|nah|wrong|not quite)\b[\s.!]*$", re.I)
INV_ALL = re.compile(r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:what(?:'?s| is)?\s+(?:do you have|have you got|are you "
                     r"(?:carrying|holding)|in (?:your|ur) (?:inventory|bag|pockets?))|what do you have (?:on|with) you|"
                     r"(?:show|list) (?:me )?(?:your|ur) inventory)\b[^?]*\??\s*$", re.I)   # 45L 11:18: "whats in your inventory?"
INV_ITEM = re.compile(r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:do |did )?you (?:still )?(?:don'?t |do not )?(?:have|got|"
                      r"carry|carrying)\s+(?:an?\s+|any\s+|some\s+)?([a-z_ ]+?)(?:\s+(?:on|with) you)?\s*\?\s*$", re.I)
# O156 (45L session 4: "check your own inventory right now, tell me which pickaxes you are carrying" -> "Noted: ..."):
# an inventory question anywhere in the line, by category - answered from the state frame, never a memory write
INV_KIND = re.compile(r"\b(?:which|what|how many)\s+([a-z_ ]+?)\s+(?:are you|you're|you are|you|do you|have you)\s*"
                      r"(?:carrying|holding|have|got|carry)\b|\b(?:check|look (?:in|at)|show me|list|go through)\s+(?:your|ur)"
                      r"\s+(?:own\s+)?(?:inventory|bag|pockets?|stuff)()\b", re.I)
# O111-b (45L 11:06-11:08: bare "5 100 -40" filed as a container's location three times): her open question takes the
# owner's next short line as its answer - coordinates, a number, a name, yes / no; a line of only numbers is never a fact
EXTRACT_TIMEOUT_S = 10.0             # O111-b: the whole fact extraction, model guards included (45L: 60 s stalls)
NUMBERS_ONLY = re.compile(r"^\s*(?:x\s*[:=]?\s*)?-?\d+(?:\.\d+)?(?:[\s,]+(?:[yz]\s*[:=]?\s*)?-?\d+(?:\.\d+)?)*\s*[.!]*$",
                          re.I)
ASK_VERB = re.compile(r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:please\s+)?(?:look|face|turn|select|switch|pick|use)\b", re.I)
#   order verbs IMPERATIVE does not list, whose rules answer is a question ("look at that" -> "What should I look at?")
PLACE_ANSWER = re.compile(r"^\s*(?:(?:right |over )?here|there|near you|next to you|beside you|by you|in front of you|"
                          r"by the door|near the door|at (-?\d+)[\s,]+(-?\d+)[\s,]+(-?\d+))\s*[.!]*$", re.I)   # O138
SHORT_ANSWER = re.compile(r"^\s*[\w' :,-]{1,40}[.!]*\s*$")   # a name or a few words: "oak logs", "slot 3", "Gullwatch"
SHIELD_LOW = 0.25           # O220 (4): durability share left that counts as nearly broken
NIGHT_LEAD, IDLE_S, NIGHT_RETRY_S = 2400, 10.0, 120.0   # S28-DEATH-3: ticks before dusk (2 min), idle s, retry s
TRUSTED_SOURCES = ("cli", "owner")    # CAN_YOU + no coordinates ("can you go fishing?") is a capability question             # manual mode: only direct trusted owner commands
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


DB_BUSY_S = 0.1
CANCEL_SETTLE_S = 5.0   # I25: a dispatch waits this long for our own cancel to settle before "busy"
PRUNE_CHUNK = 500       # I23: state rows one prune statement may delete (lane D: one unbounded DELETE, 27 s stall)


def open_db(cfg):
    """I23c/O82 stage 1: prepare the file (schema, pragmas, migration), then hand the mind an in-memory working copy
    whose writes one writer thread replays on the file (db.py) - the event loop never waits on the disk."""
    con = sqlite3.connect(cfg.db_path, isolation_level=None, timeout=DB_BUSY_S, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(cfg.schema_path.read_text("utf-8"))
    con.execute(f"PRAGMA busy_timeout = {int(DB_BUSY_S * 1000)}")
    # I23c/O82 stage 0: the journal commits without an fsync (WAL keeps it consistent; a power loss may lose about a
    # second of observations); a bigger cache and mmap keep reads off a busy disk. STOPPED and the action row before a
    # dispatch are made durable one by one (Mind.durable).
    for pragma in ("synchronous = NORMAL", "cache_size = -65536", "mmap_size = 268435456", "temp_store = MEMORY"):
        con.execute(f"PRAGMA {pragma}")
    mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    if mode.lower() != "wal":
        log.error("memory DB journal_mode is %s, not wal", mode)
    con.execute("INSERT OR IGNORE INTO meta VALUES ('created_at', ?)", (str(now_ms()),))
    migrate(con)
    return Db(open_mem(con), con)


def migrate(con):
    """Schema v1 -> v1.1, idempotent: add actions.note and move notes kept in evidence before v1.1 into it."""
    if "note" not in {r[1] for r in con.execute("PRAGMA table_info(actions)")}:
        con.execute("ALTER TABLE actions ADD COLUMN note TEXT")
    con.execute("UPDATE actions SET note = json_extract(evidence, '$.note'), evidence = json_remove(evidence, '$.note') "
                "WHERE note IS NULL AND json_valid(evidence) AND json_extract(evidence, '$.note') IS NOT NULL")
    con.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', '1.1')")
    con.execute("CREATE INDEX IF NOT EXISTS obs_ts ON observations(ts)")    # I23: prune and stood_at by time, no scan


def design_hash(design):
    """contracts/design-project.md hash, as published by the design chat (parts/design grid.py canonical_bytes)."""
    try:
        doc = {"blocks": design["blocks"], "origin": [design["origin"][a] for a in "xyz"],
               "palette": design["palette"], "roles": design["roles"], "size": [design["size"][a] for a in "xyz"]}
    except (KeyError, TypeError):
        return None
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                          .encode("utf-8")).hexdigest()


def dist(a, b):
    return math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"]))


def postcondition(skill, params, st, env):
    """Check an action's postcondition against observed state. Returns (holds, detail)."""
    tick = st["freshness"]["tick"]
    if skill == "walk_to":
        tol = params.get("tolerance", 1.5)
        d = dist(st["pos"], params["pos"])
        return d <= tol, f"distance to goal {d:.2f} (tolerance {tol}) at tick {tick}"
    if skill == "go_home":
        d = dist(st["pos"], env["home"]["center"])
        return st["dim"] == env["home"]["dim"] and d <= 3, f"distance to home {d:.2f} in {st['dim']} at tick {tick}"
    if skill == "select_hotbar":
        return st.get("hotbar_slot") == params["slot"], f"hotbar_slot={st.get('hotbar_slot')} at tick {tick}"
    if skill == "sneak":
        return st.get("sneaking") == params["on"], f"sneaking={st.get('sneaking')} at tick {tick}"
    # ponytail: block/inventory skills need query.blocks / inventory diff reconciliation (next CP-21 cases)
    return False, "no observable postcondition implemented for this skill"


def split_box(mn, mx):
    """A box as grant_region boxes of at most 32 768 blocks, split along y; a single layer over the limit is not
    grantable as one region and is left out."""
    layer = (mx["x"] - mn["x"] + 1) * (mx["z"] - mn["z"] + 1)
    if layer > 32768:
        return []
    step, out, y = max(1, 32768 // max(1, layer)), [], mn["y"]
    while y <= mx["y"]:
        top = min(mx["y"], y + step - 1)
        out.append(({**mn, "y": y}, {**mx, "y": top}))
        y = top + 1
    return out


GRANT_MAX_TICKS = 72000 - 20       # sense accepts (now, now+72000]; keep a margin below the edge
USE_GRANT_SKILLS = ("repair_zone", "harvest_replant", "harvest_crop")   # v1.7 `use`: tilling, planting, buckets in
#                                                                          task_only zones (harvest_crop: registry 0.16.0)

AREA_SKILLS = ("gather", "mine_resource", "prospect")   # O159: owner-ordered area work - a task, so task_only zones must admit it


def area_grants(sk, params, st, env):
    """O159 (session 4: "chop 8 logs" -> break zone:home:break=task_only): [(effect, min, max)] - her work box (the
    skill's radius around her, y the same) cut to each zone where that effect is task_only, split to sense's 32 768.
    Only task_only cells are sent: sense refuses a whole region that holds a none cell."""
    if sk["name"] not in AREA_SKILLS or not st or not isinstance(st.get("pos"), dict):
        return []
    r = int(params.get("radius") or sk["params"].get("properties", {}).get("radius", {}).get("default", 16))
    c = {a: int(st["pos"][a] // 1) for a in "xyz"}
    out = []
    for eff in ("break", "place"):
        if eff not in sk["effects"]:
            continue
        for z in env.get("zones", []):
            if z.get("dim") != st.get("dim") or z.get(eff) != "task_only":
                continue
            mn = {a: max(c[a] - r, z["min"][a]) for a in "xyz"}
            mx = {a: min(c[a] + r, z["max"][a]) for a in "xyz"}
            if all(mn[a] <= mx[a] for a in "xyz"):
                out += [(eff, lo, hi) for lo, hi in split_box(mn, mx)]
    return out


class Mind:
    def __init__(self, cfg, registry, db):
        self.cfg, self.registry, self.db, self.env = cfg, registry, db, cfg.envelope
        self.hub = None
        self.stopped = self.meta("control.stopped", "0") == "1"
        self.mode = self.meta("control.mode", self.env.get("mode_default", "manual"))
        self.epoch = int(self.meta("control.epoch", "0"))
        self.world_id = None              # last world the sense link declared
        self.state, self.state_rx = None, 0.0
        self.reconcile_needed = True      # until the first reconcile against a fresh, bound state
        self.sent_session, self.requests, self.done, self.watchers = {}, {}, {}, {}
        self.creeper = CreeperGuard(self)          # O53: a creeper still following after creeper_flee
        self.last_stop_assert = 0.0
        self.last_prune = 0.0
        self.prune_backlog = False
        self.last_ack = None
        self.last_progress = {}
        self.progress_note = {}                   # O160: action_id -> the body's last progress note ("3/8")
        self.world_progress_ms = now_ms()        # last observed world change: moved, inventory, eve block event
        self.anchor = None                        # (pos, inventory_hash, dim) the progress detector compares against
        self.cancelling = {}                      # action_id -> monotonic time a cancel was sent
        self.unsent = set()                       # O81a: rows written, request not yet handed to the link (flush)
        self.retry_frames = []                    # (link, msg) body action frames a DB lock kept from being applied
        self.settle_next = {}                     # uncertain action_id -> came from a body result
        self.sense_tick = None                    # (server tick, monotonic time) from the last sense frame
        self.denials = {}                         # open action_id -> server permission.denied seen during it
        self.granted = {}                         # action_id -> [(effect, dim)] task.grants to revoke when it ends
        self.split_seen = set()                   # container ids of a full index split over partial frames
        self.planner = Planner(timeout_s=cfg.planner_timeout_s, num_ctx=cfg.planner_num_ctx)
        self.memory = Memory(db)
        self.extractor = functools.partial(extract_facts, timeout=cfg.reply_model_s)   # None = rule parser only (tests);
        #   O111 (45L 11:06:18: a 60 s extractor timeout sat in front of an ask): at most reply_model_s
        self.objectives = Runner(self)
        self.candidates = Candidates(self)       # O94f: eve-candidates/1 rows (non-safety runner decisions only)
        self.supply = Supply(self)               # CP-51
        with contextlib.suppress(Exception):     # O82: the recipe file is read here, never on the event loop
            self.supply.book
        self.repair = Repair(self)               # CP-52
        self.bridge = Bridge(self)               # CP-53 planner
        self.ironkit = IronKit(self)             # CP-40 (O41)
        self.night = NightSurvival(self)         # O54
        self.recover, self.recovering = recover.Recover(self), False   # H-recover
        self._idle_since, self._night_auto_at = None, -1e9   # S28-DEATH-3: the night guard
        self._shield = {"had": False, "warned": False, "due": False}   # O220 (4): shield wear
        self.dig_starts, self.climbing, self.home_retried = {}, None, set()   # CP-86 run 4: climb out, home retry
        self.autofight, self.af_held = None, set()   # H-autofight: the engagement now; actions it paused
        self.resumable = set()                          # O214: owner actions resumed when a reflex interrupts them
        self.followups = {}                             # action_id -> Future(fallback action_id): dig_for
        self.progress_gen, self.progress_marks = 0, set()   # O175: a run ends on his next order; built marks
        self.follow = Follow(self)               # O111
        self.owner_orders = set()                # O120: owner-ordered action ids whose failure she says
        self.asked = None                        # O111: her open question {kind, sender, at, ...}
        self.last_line, self.last_stop, self.last_failure = "", None, None   # S93: the conversation layer
        self.steps_task = None                   # O138: the last detached multi-step order
        self.owner_uuid = {}                     # O111: the trusted owner's player uuid once seen by name (body)
        self.owner_live = {}                     # O121: sense's latest owner.pos per trusted player (+ rx monotonic)
        self.mob_targets = {}                    # O166: sense mob.targeting by entity_id (+ rx monotonic)
        self.last_item = None                    # O123: the item she last crafted or placed ("place it")
        self.last_hostile = None                 # O126: (mob, monotonic) last named in chat
        self._hp, self.hurt_at, self.backing_off = None, -1e9, False   # O139
        self.holding, self.deferred, self.back_off_dest = False, None, None   # S28-DEATH-2 / O169
        self.said_n, self._last_say, self._chat_sent = 0, 0.0, {}   # O114: chat spacing and one resend
        self._say_lock = asyncio.Lock()
        self.repair_answers = {}                 # CP-52: (zone, snapshot) -> steps sense answered
        self.talker = dialogue.answer          # None = no model (unit tests): plain factual fallback
        self.deep = DeepThinker(db, cfg.envelope)   # dialogue wording (O27); off unless EVE_DEEPSEEK_* + key + cap
        self.background = set()
        self.metrics = {k: 0 for k in ("ignored_transitions", "resends", "late_results", "unknown_action_results",
                                       "missing_evidence", "stop_reasserted", "untrusted_commands", "stuck",
                                       "unlisted_failure_codes", "db_errors", "rejected_after_accept",
                                       "server_denials")}

    # ---- small helpers
    def meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, kv):
        self.db.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", [(k, str(v)) for k, v in kv.items()])

    def durable(self):
        """O82: writes made inside commit to the file with synchronous=FULL; the caller awaits db.flush() before the
        effect - a power loss may lose observations, never a STOP or the row of an action already sent."""
        return self.db.durable()

    def event(self, source, kind, payload, tick=None, session=None):
        self.db.execute("INSERT INTO events (world_id, session_id, tick, ts, source, kind, payload) "
                        "VALUES (?,?,?,?,?,?,?)", (self.world_id or "unbound", session, tick, now_ms(), source,
                                                   kind, json.dumps(payload)))

    def action(self, aid):
        return self.db.execute("SELECT * FROM actions WHERE action_id=?", (aid,)).fetchone()

    def status_of(self, aid):
        r = self.action(aid)
        return r["status"] if r else None

    def transition(self, aid, new, allowed_from, **cols) -> bool:
        cols = {"status": new, **cols}
        cur = self.db.execute(
            f"UPDATE actions SET {', '.join(f'{k}=?' for k in cols)} WHERE action_id=? "
            f"AND status IN ({','.join('?' * len(allowed_from))})", [*cols.values(), aid, *allowed_from])
        if cur.rowcount == 0:
            self.metrics["ignored_transitions"] += 1
            return False
        return True

    def world_for_link(self, name):
        s = self.hub.links.get("sense") if self.hub else None
        if name == "sense":
            return s.hello["world_id"] if s else None
        return self.world_id

    def binding(self):
        b, s = self.hub.links["body"], self.hub.links["sense"]
        if b is None:
            return False, "link_down: body link is down"
        if s is None:
            return False, "link_down: sense link is down"
        if s.hello["world_id"] != self.env["world_id"]:
            return False, f"world_mismatch: sense reports {s.hello['world_id']}, envelope is for {self.env['world_id']}"
        if b.hello["account_name"] != self.env["owner"]["eve_account"]:
            return False, (f"account_mismatch: body is {b.hello['account_name']}, envelope rails apply to "
                           f"{self.env['owner']['eve_account']}")
        if b.hello["level_name"] != s.hello["level_name"]:
            return False, f"world_mismatch: body sees level {b.hello['level_name']}, sense {s.hello['level_name']}"
        if (s.hello["envelope_version"] != self.env["envelope_version"]
                or s.hello["envelope_hash"] != self.cfg.envelope_hash):
            return False, "envelope_mismatch: sense enforces a different envelope than the mind plans with"
        return True, s.hello["world_id"]

    def fresh_state(self):
        st = self.state
        if st is None or time.monotonic() - self.state_rx > self.cfg.stale_ms / 1000:
            return None
        return st if st["freshness"]["age_ms"] <= self.cfg.stale_ms else None

    def open_action(self):
        return self.db.execute(f"SELECT * FROM actions WHERE status IN ({','.join('?' * len(OPEN))}) "
                               "ORDER BY sent_ts DESC LIMIT 1", OPEN).fetchone()

    async def settled_open_action(self):
        """I25 (CP-40 a14: the body ended the cancelled prospect before acking our stop, and every next dispatch -
        go_home last - was refused busy on our own stop for 0.2 s): while the open action is our own stop or one we
        are cancelling, wait for it (CANCEL_SETTLE_S at most; the supervisor settles or escalates an ignored cancel
        after cancel_grace_s) instead of refusing. Returns what is still open after that."""
        deadline = time.monotonic() + CANCEL_SETTLE_S
        cur = self.open_action()
        while cur is not None and (cur["skill"] == "stop" or cur["action_id"] in self.cancelling) and \
                time.monotonic() < deadline:
            ev = self.done.get(cur["action_id"])
            try:
                await asyncio.wait_for(ev.wait() if ev else asyncio.sleep(0.25), max(0.05, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                pass
            cur = self.open_action()
        return cur

    def spawn(self, coro):
        """Run owner text off the sense link's loop: planning must not block permission.result replies."""
        t = asyncio.get_running_loop().create_task(coro)
        self.background.add(t)
        t.add_done_callback(self.background.discard)
        return t

    async def send_body(self, type_, payload) -> bool:
        b = self.hub.links["body"]
        if b is None:
            return False
        try:
            await b.send(type_, payload)
            return True
        except ConnectionClosed:
            return False

    # ---- link lifecycle (called by the hub)
    def on_link_up(self, link):
        h = link.hello
        world = h.get("world_id") if link.name == "sense" else self.world_id
        if link.name == "sense":
            self.world_id = world
            self.db.execute("INSERT INTO worlds VALUES (?,?,?,?,?) ON CONFLICT(world_id) DO UPDATE SET "
                            "last_seen_ts=excluded.last_seen_ts", (world, h["level_name"], h["seed_hash"],
                                                                    now_ms(), now_ms()))
        else:
            self.state = None
            self.reconcile_needed = True
        self.db.execute("INSERT INTO sessions (session_id, link, world_id, peer_version, registry_hash, started_ts) "
                        "VALUES (?,?,?,?,?,?)", (link.session, link.name, world, h.get("mod_version"),
                                                 h.get("registry_hash"), now_ms()))

    def on_link_down(self, link):
        self.db.execute("UPDATE sessions SET ended_ts=?, end_reason=? WHERE session_id=?",
                        (now_ms(), "closed", link.session))
        if link.name == "body":
            self.state = None
        log.info("%s link down (session %s)", link.name, link.session)

    async def after_hello(self, link):
        if link.name == "body" and self.stopped:      # STOPPED survives reconnects: re-assert, never auto-resume
            await self.send_body("control.stop", {"reason": "mind is STOPPED", "source": "mind"})

    # ---- inbound messages
    async def on_message(self, link, msg):
        t, p, tick = msg["type"], msg["payload"], msg.get("tick")
        if link.name == "body":
            if t == "error" and p.get("code") == "rate_limited":      # O114: falls through to the event below
                self.spawn(self.resay(p.get("about_id") or msg.get("reply_to")))
            if t == "state":
                return await self.on_state(p, link)
            if t == "action.accepted":
                return self.transition(p["action_id"], "accepted", ("sent", "pending"), accepted_ts=now_ms())
            if t == "action.progress":
                self.last_progress[p["action_id"]] = time.monotonic()
                if p.get("note"):                        # O160: "3/8" - what a progress question reports
                    self.progress_note[p["action_id"]] = str(p["note"])[:80]
                return self.transition(p["action_id"], "running", OPEN)
            if t == "action.rejected":
                aid, before = p["action_id"], self.status_of(p["action_id"])
                late = before in ("accepted", "running")      # contract: reject only before accepting
                if late:
                    self.metrics["rejected_after_accept"] += 1
                if self.transition(aid, "rejected", OPEN, code=p["code"],
                                   evidence=json.dumps({"reason": p.get("reason")}), result_ts=now_ms(),
                                   note=(f"rejected after {before} (contract violation): " if late else "")
                                   + (p.get("reason") or "")[:400] or None):
                    self.finish(aid, "rejected", p["code"])
                return
            if t == "action.result":
                return self.on_result(p)
            if t == "control.ack":
                self.last_ack = {**p, "rx_ts": now_ms()}
            if t == "event" and p.get("kind") == "reflex":
                self.creeper.on_reflex(p.get("detail") or {})
                self.on_autofight(p.get("detail") or {})
            if t == "event":
                self.night.on_event(p)                # O54: death / respawn / reflex while a night runs
                self.recover.on_event(p)              # H-recover: any other death
        else:
            if t == "storage.index":
                return self.on_storage(p)
            if t == "envelope.changed":     # sense now enforces this envelope; binding compares it with ours
                link.hello["envelope_version"], link.hello["envelope_hash"] = p["version"], p["hash"]
                self.event(link.name, t, p, tick, link.session)
                return self.reload_envelope("sense envelope.changed")
            if t == "block.event":
                return self.on_block_events(p["events"])
            if t == "owner.command":
                return await self.on_owner_command(p)
            if t == "permission.denied":
                self.event(link.name, t, p, tick, link.session)
                return self.attach_denial(p)
            if t == "mob.targeting":        # O166: <= 1 per mob per 2 s, stale after 3 s - kept in memory, never logged
                self.mob_targets[p["entity_id"]] = {**p, "rx": time.monotonic()}   # (H-autofight reads it)
                b = self.hub.links.get("body")    # O173: relayed as is to a body whose hello accepts it (ClientView)
                if b is not None and "mob.targeting" in (b.hello.get("accepts") or []):
                    self.spawn(b.send("mob.targeting", p))
                return
            if t == "owner.pos":            # O121: ~1 Hz per online trusted player - kept in memory, never logged
                if p["sender"] in self.env["owner"]["trusted_players"]:
                    self.owner_live[p["sender"]] = {**p, "rx": time.monotonic()}
                return
            if t == "owner.chat":
                self.event(link.name, t, p, tick, link.session)
                if p["trusted"] and p["sender"] in self.env["owner"]["trusted_players"]:
                    return self.spawn(self.owner_text(p["text"], p["sender"], source="owner",
                                                      sender_pos=p.get("sender_pos")))
                self.metrics["untrusted_commands"] += 1      # CP-45: untrusted chat is data
                return
        self.event(link.name, t, p, tick, link.session)

    def watch_hurt(self, p, now):
        """O139 (S28: shot from 20 to 0 HP over 16 s in daylight, the mind never reacted): damage is noted per frame;
        at HURT_HP or less within HURT_S of a hit (and no night run, which has its own rules) she backs off once."""
        hp = p.get("health")
        if not isinstance(hp, (int, float)):
            return
        if self._hp is not None and hp < self._hp:
            self.hurt_at = now
        self._hp = hp
        reflex = ((p.get("control") or {}).get("reflex_active"))
        near = [e for e in p.get("nearby_entities", []) if e.get("hostile") and e.get("distance", 99) <= 8]
        if reflex and 0 < hp <= HOLD_HP and near:         # S28-DEATH-2: body's reflex owns her; the mind holds
            if not self.holding:
                self.holding = True
                self.spawn(self.say("I'm hurt, holding."))
            return
        if self.holding and not reflex:                   # the reflex ended: his deferred order runs now
            self.holding = False
            if self.deferred:                             # his order wins over the back-off this hit would start
                args, self.deferred, self.hurt_at = self.deferred, None, -1e9
                self.spawn(self.owner_text(*args))
                return
        if 0 < hp <= HURT_HP and now - self.hurt_at <= HURT_S and not self.backing_off and not self.stopped and \
                not (self.night.task and not self.night.task.done()):
            self.backing_off = True
            self.spawn(self.back_off())

    def out_of_play(self, pos, st):
        """H-recover: why a spot is outside her play area (the envelope's bounds for a walk there), else empty."""
        return bounds({"pos": {a: float(pos[a]) for a in "xyz"}}, st, self.env) or ""

    def on_autofight(self, d):
        """H-autofight (parts/combat/H-AUTOFIGHT.md, body's auto_fight reflex): on start her frame is suspended - one
        line, and nothing of the mind's own is dispatched (owner orders and stop still are); on end one line, then the
        paused action: body resumes it (`resumes`), or, ended `interrupted`, the mind sends it again for what is left
        (O214's resume); `floor` / `timeout`: she says so and backs off."""
        if d.get("reflex") != "auto_fight":
            return
        if d.get("phase") == "start":
            trig = d.get("trigger") or {}
            paused = d.get("action_paused")
            self.autofight = {"paused": paused, "at": time.monotonic(), "trigger": trig}
            if paused:
                self.af_held.add(paused)
            what = str(trig.get("type") or "a mob").split(":")[-1].replace("_", " ")
            self.spawn(self.say(f"A {what} is after you - I'm on it." if trig.get("reason") == "owner_targeted" else
                                f"A {what} - dealing with it."))
            return
        if d.get("phase") != "end":
            return
        af, self.autofight = self.autofight or {}, None
        res, paused = str(d.get("result") or ""), af.get("paused")
        if res in ("floor", "timeout"):
            self.spawn(self.say("I'm hurt - backing off." if res == "floor" else "That fight dragged on - stepping away."))
            if not self.backing_off and not self.stopped:
                self.backing_off = True
                self.spawn(self.back_off())
            return
        if res == "stopped" or res.startswith("preempted_by_"):
            return                                        # stop said by the stop; a higher reflex speaks for itself
        line = "I can't reach it - carrying on." if res == "unreachable" else "Clear."
        if paused and not d.get("resumes") and paused not in self.resumable:   # a progression step resumes itself
            self.spawn(self.say(line + " Back to it."))
            self.spawn(self.refight_resume(paused))
        else:
            self.spawn(self.say(line + (" Back to it." if paused else "")))

    async def refight_resume(self, aid):
        """H-autofight: the paused action, ended `interrupted`, sent again for what is left (count - evidence)."""
        row = self.action(aid)
        t = self.db.execute("SELECT source FROM tasks WHERE task_id=?", (row["task_id"],)).fetchone()             if row and row["task_id"] else None
        src = t["source"] if t else "cli"
        await progress.resumed(self, aid, src, [1], lambda: not self.stopped and self.autofight is None)

    def night_guard(self, st):
        """S28-DEATH-3 (idle at night in manual mode after CP-86 run 2, zombies at the shelter, two deaths): parked or
        idle at night is night-safe - with no action of hers running for IDLE_S, at night or within NIGHT_LEAD ticks of
        dusk, the O54 night routine runs (shelter, seal, hold) in any mode; manual suppresses initiative, not survival."""
        t = st.get("time_of_day")
        if t is None or self.stopped or self.recovering or self.recover.needs_owner or self.autofight or (
                self.night.task and not self.night.task.done()):
            return
        t = int(t) % 24000
        if not (is_night(t) or 0 <= DUSK - t <= NIGHT_LEAD) or self.open_action() is not None:
            self._idle_since = None
            return
        now = time.monotonic()
        self._idle_since = self._idle_since or now
        if now - self._idle_since < IDLE_S or now - self._night_auto_at < NIGHT_RETRY_S:
            return
        self._night_auto_at = now
        if self.night.start(source="cli").get("ok"):          # CP-87: say what the quiet wait before sunset is
            self.spawn(self.say("It's getting dark - I'm going to the shelter." if t >= SEAL_AT else
                                "I'll seal up at dark."))

    def shield_watch(self, st):
        """O220 (4) (18:16:42: a 53 s hold broke her shield and she never knew): below SHIELD_LOW durability she says
        so once; a worn or broken shield queues "craft a shield", run when she is idle with 1 iron ingot + 6 planks."""
        inv, sh = st.get("inventory") or [], self._shield
        shields = [i for i in inv if i.get("item") == "minecraft:shield"]
        worn = [i for i in shields if i.get("max_damage") and
                (i["max_damage"] - (i.get("damage") or 0)) / i["max_damage"] < SHIELD_LOW]
        if worn and not sh["warned"]:
            sh["warned"] = sh["due"] = True
            self.spawn(self.say("My shield is nearly broken."))
        elif shields and not worn:
            sh["warned"] = False
        if sh["had"] and not shields:
            sh["due"] = True                              # broken (or lost): a new one when she can
        sh["had"] = bool(shields)
        if not sh["due"] or len(shields) - len(worn) >= 1 or self.open_action() is not None or self.stopped or self.recovering or self.backing_off or self.autofight:
            return
        n = lambda pred: sum(int(i.get("count") or 0) for i in inv if pred(i.get("item") or ""))   # noqa: E731
        if n(lambda x: x == "minecraft:iron_ingot") >= 1 and n(lambda x: x.endswith("_planks")) >= 6:
            sh["due"] = False
            n_have = sum(int(i.get("count") or 1) for i in shields)   # the solver counts held ones toward the target
            self.spawn(self._owner_text(f"craft {n_have + 1} shields" if n_have else "craft a shield", "cli", "cli"))

    def hostiles(self, st=None):
        """Hostiles in her frame, nearest first."""
        return sorted((e for e in (st or self.fresh_state() or {}).get("nearby_entities", []) if e.get("hostile")),
                      key=lambda e: e.get("distance", 99))

    async def back_off(self):
        """O139/O169: cancel whatever she is doing (an owner fight too), say so, then retreat - each destination tried
        once, an unreachable one falls through: his redirect ("go home" / "come" said meanwhile), him if he is within
        24 at walkable height (45L: never a pillar), home, else 14 blocks straight away from the nearest hostile. Eat
        only with no hostile within 3 (never retried), then "Ready." - nothing resumes until a new order."""
        try:
            await self.end_task("hurt, backing off", "cli", always_cancel=True)
            await self.say("I'm hurt, backing off.")
            st = self.fresh_state() or {}
            me = st.get("pos") or {}

            def walk(at):
                return ("walk_to", {"pos": {a: float(at[a]) for a in "xyz"}, "tolerance": 2.0})
            near = [at for at in (self.owner_pos(o) for o in self.env["owner"]["trusted_players"])
                    if at and at.get("dim") == st.get("dim") and me and abs(at["y"] - me["y"]) <= 3 and
                    math.dist([at[a] for a in "xyz"], [me[a] for a in "xyz"]) <= BACK_OFF_R]
            options = ([walk(near[0])] if near else []) + [("go_home", {})]
            foe = (self.hostiles(st) or [None])[0]
            if foe and me:
                dx, dz = me["x"] - foe["pos"]["x"], me["z"] - foe["pos"]["z"]
                n = math.hypot(dx, dz) or 1.0
                options.append(walk({"x": me["x"] + 14 * dx / n, "y": me["y"], "z": me["z"] + 14 * dz / n}))
            for skill, params in options:
                if self.back_off_dest:                    # his "go home" / "come" during the back-off wins
                    skill, params = self.back_off_dest
                    self.back_off_dest = None
                r = await self.do(skill, params, source="cli", wait=40)
                if r.get("status") == "ok":
                    break
            t0 = time.monotonic()
            while time.monotonic() - self.hurt_at < 3 and time.monotonic() - t0 < 30:
                await asyncio.sleep(0.2)
            st = self.fresh_state() or {}
            close = [e for e in self.hostiles(st) if e.get("distance", 99) <= 3]
            slot = next((s["slot"] for s in st.get("inventory", []) if isinstance(s.get("slot"), int) and s["slot"] <= 8
                         and any(f in str(s.get("item")) for f in ("bread", "apple", "cooked_", "carrot", "potato",
                                                                    "steak"))), None)
            if slot is not None and st.get("food", 20) < 20 and not close:   # O169: never eat with one at 3 m
                await self.do("select_hotbar", {"slot": slot}, source="cli", wait=3)
                await self.do("use_item", {"hold_ms": 2000}, source="cli", wait=5)
            await self.say("Ready.")
        finally:
            self.backing_off, self.back_off_dest = False, None

    async def on_state(self, p, link):
        self.state, self.state_rx = p, time.monotonic()
        self.creeper.on_state(p)
        now = time.monotonic()
        self.watch_hurt(p, now)
        self.night_guard(p)
        self.shield_watch(p)
        # storing the frame is best-effort: under a DB lock skip it for 1 s instead of a busy wait per frame (20 Hz
        # x 0.1 s would saturate the loop, 45L CP-24 a1), and never skip the in-memory work below (stop re-assert)
        if now >= getattr(self, "_obs_skip_until", 0):
            try:
                self.db.execute("INSERT INTO observations (world_id, session_id, tick, ts, kind, payload) VALUES "
                                "(?,?,?,?,?,?)", (self.world_id or "unbound", link.session, p["freshness"]["tick"],
                                                  now_ms(), "state", json.dumps(p)))
            except sqlite3.Error as e:
                self._obs_skip_until = now + 1.0
                self.metrics["db_errors"] += 1
                self.metrics["state_not_stored"] = self.metrics.get("state_not_stored", 0) + 1
                if now - getattr(self, "_obs_err_logged", 0) > 5:
                    self._obs_err_logged = now
                    log.error("body: state not stored: %s (db_errors=%d)", e, self.metrics["db_errors"])
        a = self.anchor
        if a is None or dist(p["pos"], a[0]) >= 0.5 or p["inventory_hash"] != a[1] or p["dim"] != a[2]:
            if a is not None:
                self.world_progress_ms = now_ms()
            self.anchor = (p["pos"], p["inventory_hash"], p["dim"])
        if now - self.last_prune > 60 or self.prune_backlog:
            self.last_prune = now
            try:
                keep = int(self.meta("state.retain_min", "10")) * 60_000     # I14: `eve retain <min>` for a run
                # I23 (lane D: after retain 300 -> 10 one DELETE scanned and removed ~85 min of rows on the loop thread,
                # links dropped on 15 s silence): bounded chunks on the ts index, one per state frame until caught up
                n = self.db.execute("DELETE FROM observations WHERE id IN (SELECT id FROM observations WHERE ts < ? "
                                    "AND pinned=0 LIMIT ?)", (now_ms() - keep, PRUNE_CHUNK)).rowcount
                self.prune_backlog = n >= PRUNE_CHUNK
            except sqlite3.Error:
                self.metrics["db_errors"] += 1
        if self.stopped and not p["control"]["stopped"] and now - self.last_stop_assert > 2:
            self.last_stop_assert = now
            self.metrics["stop_reasserted"] += 1
            await self.send_body("control.stop", {"reason": "mind is STOPPED", "source": "mind"})
        if self.reconcile_needed:
            self.reconcile()
        if self.settle_next:
            ok, world = self.binding()
            if ok and self.fresh_state() is not None:
                pending, self.settle_next = self.settle_next, {}
                for aid, from_body in pending.items():
                    r = self.action(aid)
                    if r["status"] == "uncertain" and r["reconciled"] == 0:
                        # a body that just reported a result is not still running it, whatever older state said
                        self.reconcile_one(r, p, world, None if from_body else p["control"].get("active_action_id"))

    def on_result(self, p):
        aid = p["action_id"]
        row = self.action(aid)
        if row is None:
            self.metrics["unknown_action_results"] += 1
            return self.event("body", "unknown_action_result", p)
        status, code, ev = p["status"], p.get("code"), p["evidence"]
        sk = self.registry.skills[row["skill"]]
        if status == "ok":
            missing = [k for k in sk["evidence"] if k not in ev]
            if missing:   # CP-19: success needs server-observed evidence, not "I sent the input"
                self.metrics["missing_evidence"] += 1
                status, code = "uncertain", "missing_evidence:" + ",".join(missing)
        if status in ("failed", "timeout", "cancelled") and code and code not in sk["failure_codes"]:
            self.metrics["unlisted_failure_codes"] += 1   # failure_codes is a closed list (skill-registry.md)
            code = f"unlisted:{code}"
        late = row["status"] == "uncertain"
        note = p.get("note")
        note = note[:500] if isinstance(note, str) and note else None    # the body's own explanation (schema v1.1)
        denials = self.denials.pop(aid, None)
        if denials:     # the server's view of the same action (CP-15: rejection reaches the task system)
            note = ((note + "; ") if note else "") + "server denied: " + ", ".join(denials)
        if self.transition(aid, status, RESULT_FROM, code=code, evidence=json.dumps(ev), note=note,
                           result_ts=now_ms()):
            if late:
                self.metrics["late_results"] += 1
                self.db.execute("UPDATE actions SET reconcile_note=coalesce(reconcile_note || '; ', '') || ? "
                                "WHERE action_id=?", (f"late body result {p['status']}", aid))
            self.finish(aid, status, code, from_body=True)

    def finish(self, aid, status, code, from_body=False):
        row = self.action(aid)
        if status == "ok" and row["skill"] in JOB_SKILLS:      # CP-44: observed history, with the action as source
            prm = json.loads(row["params"])
            p = prm.get("pos") or prm.get("target_pos")
            what = f"{p['x']} {p['y']} {p['z']}" if p else f"entity {prm['entity_id']}" if "entity_id" in prm else ""
            try:
                self.memory.remember(row["world_id"], "job", "completed", f"{row['skill']} {what}".strip(),
                                     "observation", source_ref=aid, confirmed=1)
            except sqlite3.Error:
                self.metrics["db_errors"] += 1
        if row["skill"] == "build_schematic" and status == "ok" and "portal" in str(row["params"]):
            self.progress_marks.add("eve:portal_frame")  # O175: agenda's portal_frame milestone (progression.PORTAL_FRAME)
        retry = (self.dig_retry(row, status, code) or workstations.dig_for(self, row, status, code) or
                 self.home_retry(row, status, code))              # O142, O163, CP-86 run 4
        self.climb_out(row, status)
        if aid in self.owner_orders:                            # O120: his order failed - she says why
            self.owner_orders.discard(aid)
            if status in ("failed", "timeout", "rejected"):
                self.last_failure = self.failure_line(row, code)   # S93: "why did that fail"
            if status in ("failed", "timeout", "rejected") and not retry and not (
                    code == "interrupted" and (aid in self.resumable or aid in self.af_held)):   # O214, H-autofight
                self.spawn(self.say(self.failure_line(row, code)))
        if row["task_id"]:
            # a stopped/paused or cancelled task is never revived by a late result
            self.db.execute("UPDATE tasks SET status=?, reason=?, updated_ts=? WHERE task_id=? "
                            "AND status IN ('running','blocked')",
                            (TASK_FOR[status], f"action {status}" + (f": {code}" if code else ""), now_ms(),
                             row["task_id"]))
            if TASK_FOR[status] in ("failed", "cancelled"):
                self.release(row["task_id"], f"action {status}")
        if aid in self.granted:
            self.spawn(self.revoke_grants(aid))
        w = self.watchers.pop(aid, None)
        if w and w is not asyncio.current_task():
            w.cancel()
        if aid in self.done:
            self.done[aid].set()
        if status == "uncertain" and row["reconciled"] == 0:
            # settle against the next state frame: on the ordered link it post-dates this result
            self.settle_next[aid] = from_body

    # ---- reconciliation (CP-21, CP-23)
    def reconcile(self) -> bool:
        ok, world = self.binding()
        st = self.fresh_state()
        if not ok or st is None:
            return False
        rows = self.db.execute("SELECT * FROM actions WHERE status IN ('pending','sent','accepted','running') "
                               "OR (status='uncertain' AND reconciled=0)").fetchall()
        active = st["control"].get("active_action_id")
        for r in rows:
            self.reconcile_one(r, st, world, active)
        notes = {r["action_id"]: str(self.action(r["action_id"])["reconcile_note"] or "") for r in rows}
        lost = [r for r in rows if r["skill"] != "stop" and r["action_id"] != active and
                ("not observed" in notes[r["action_id"]] or "world changed" in notes[r["action_id"]])]
        if lost:                                  # 45L 13:55: after a restart Hunter re-issued three orders blind
            self.spawn(self.say("I was restarted and lost: " + "; ".join(lost_words(r) for r in lost[:3]) +
                                ". Say it again if you still want it."))
        self.reconcile_needed = False
        log.info("reconciled %d open action(s) against tick %s", len(rows), st["freshness"]["tick"])
        return True

    def reconcile_one(self, r, st, world, active):
        aid = r["action_id"]
        if aid not in self.granted:
            # grants sent by the process before a restart live only in `events`: recover them so they get revoked
            for (payload,) in self.db.execute("SELECT payload FROM events WHERE source='mind' AND kind='task.grant' "
                                              "AND payload LIKE ?", (f'%"action_id": "{aid}"%',)):
                g = json.loads(payload)
                if g.get("action_id") == aid and g.get("positions"):
                    self.granted.setdefault(aid, []).append((g["effect"], g["dim"]))
        if r["world_id"] != world:
            return self.settle_uncertain(r, f"world changed ({r['world_id']} -> {world}); not replayed", False)
        if active == aid:
            if self.transition(aid, "running", OPEN + ("uncertain",),
                               reconcile_note="body reports it still active; adopted, not replayed"):
                self.watchers[aid] = asyncio.get_running_loop().create_task(
                    self.watch(aid, self.registry.skills[r["skill"]], resend=False))
            return
        holds, detail = postcondition(r["skill"], json.loads(r["params"]), st, self.env)
        note = (f"postcondition holds in observed state: {detail}; not replayed" if holds
                else f"postcondition not observed ({detail}); not replayed")
        self.settle_uncertain(r, note, holds)

    def settle_uncertain(self, r, note, holds):
        aid = r["action_id"]
        if aid in self.granted:
            self.spawn(self.revoke_grants(aid))
        self.transition(aid, "uncertain", OPEN + ("uncertain",), reconciled=1, reconcile_note=note,
                        code=r["code"] or "reconciled", result_ts=r["result_ts"] or now_ms())
        if r["task_id"]:
            self.db.execute("UPDATE tasks SET status=?, reason=?, updated_ts=? WHERE task_id=? "
                            "AND status IN ('running','blocked')",
                            ("done" if holds else "blocked",
                             "postcondition observed on reconcile" if holds
                             else "action outcome uncertain after reconcile; needs owner", now_ms(), r["task_id"]))
        if aid in self.done:
            self.done[aid].set()
        log.info("reconciled %s %s: %s", r["skill"], aid, note)

    # ---- dispatch (CP-11, CP-19, CP-41 gate)
    async def do(self, skill, params, source="cli", task_id=None, wait=0.0, epoch=None, bypass_busy=False,
                 grant=False, assumptions=None, bounds_center=None):
        """grant=True only for a direct trusted owner command (CLI, rule-mapped owner words, or continuing such a
        task): the mind then sends sense a task.grant for the exact break/place target. Planner steps never get one."""
        def refuse(code, detail=""):
            try:
                self.event("mind", "dispatch_refused", {"skill": skill, "params": params, "code": code,
                                                        "detail": detail, "source": source})
            except sqlite3.Error:
                self.metrics["db_errors"] += 1
            return refusal(code, detail)

        if not await self.db_wait():
            return refusal("db_unavailable", f"memory DB locked for {self.cfg.db_timeout_s} s; nothing sent")

        if epoch is not None and epoch != self.epoch:
            return refuse("stale_epoch", "this request predates the last stop; it cannot revive work")
        cancel = skill == "stop"   # cancelling only reduces activity: it skips the mode/busy/state/world gates
        if self.stopped and not cancel:
            return refuse("stopped", "STOPPED: only a trusted resume clears it")
        if source == SELF_DEFENCE and not cancel:   # O53m2: survival, not initiative - one skill, one target
            if not self.creeper.admits(skill, params):
                return refuse("mode_denied", "reflex:creeper runs only fight_ranged on the creeper it is engaging")
            self.event("mind", "self_defence", {"skill": skill, "params": params, "source": source})
        elif self.mode == "manual" and source not in TRUSTED_SOURCES and not cancel:
            return refuse("mode_denied", "manual mode runs only direct trusted owner commands")
        ok, why = self.binding()
        if cancel:
            if self.hub.links["body"] is None:
                return refuse("link_down", "body link is down")
            world, st = (why if ok else self.world_id or "unbound"), self.state
        else:
            if not ok:
                return refuse(why.split(":")[0], why)
            world = why
            if self.reconcile_needed and not self.reconcile():
                return refuse("reconcile_pending", "waiting for a fresh state to reconcile open actions")
            if not bypass_busy and (cur := await self.settled_open_action()) is not None:
                return refuse("busy", f"{cur['skill']} {cur['action_id']} is {cur['status']}")
            if self.stopped or (epoch is not None and epoch != self.epoch):     # a stop may have landed meanwhile
                return refuse("stopped" if self.stopped else "stale_epoch", "a stop landed while a cancel settled")
            st = self.fresh_state()
            if st is None:
                return refuse("stale_state", f"no state newer than {self.cfg.stale_ms} ms")
        if skill not in self.registry.skills:
            return refuse("unknown_skill", f"{skill!r} is not in registry {self.registry.version}")
        sk = self.registry.dispatchable(self.hub.links["body"].hello["caps"]).get(skill)
        if sk is None:
            return refuse("not_in_caps", f"body does not implement {skill} v{self.registry.skills[skill]['version']}")
        errs = self.registry.validate(skill, params)
        if errs:
            return refuse("bad_params", "; ".join(errs[:3]))
        if skill == "build_schematic":       # registry 0.4.0: only an owner-approved design, exactly as approved
            a = self.db.execute("SELECT design_hash, decision, invalidated_ts FROM approvals WHERE project_id=? AND "
                                "version=? ORDER BY ts DESC LIMIT 1", (params["project_id"], params["version"])).fetchone()
            if a is None or a["decision"] != "approved" or a["invalidated_ts"]:
                return refuse("not_approved", f"{params['project_id']} v{params['version']} has no valid approval")
            if a["design_hash"] != params["design_hash"]:
                return refuse("design_hash_mismatch", "the design_hash is not the approved one")
        pkey = json.dumps(params, sort_keys=True)
        if not cancel:
            if MODES.index(self.mode) < MODES.index(sk["min_mode"]):
                return refuse("mode_denied", f"{skill} needs mode {sk['min_mode']}")
            oob = bounds(params, st, self.env if bounds_center is None else   # O111 S38: following, explore_radius
                         {**self.env, "home": {**self.env["home"], "center": bounds_center}})   # is from him
            if oob:
                return refuse("out_of_bounds", oob)
            bad = entity_check(params, sk, st)
            if bad:
                return refuse(*bad)
            here = tuple(int(st["pos"][a] // 1) for a in "xyz")
            fails = sum(1 for (sent,) in self.db.execute(
                "SELECT sent_ts FROM actions WHERE skill=? AND params=? AND (status IN ('failed','timeout',"
                "'rejected') OR code='no_result') AND coalesce(result_ts, sent_ts, 0) > ? "
                "AND coalesce(reconcile_note, '') NOT LIKE 'retry_exempt%'",
                (skill, pkey, now_ms() - 600_000))
                if not (EFFECTFUL & set(sk["effects"])) or self.stood_at(sent) in (None, here))   # moves: any spot
            if fails >= self.cfg.retry_limit:   # CP-22: bounded retries, no retry storms
                return refuse("retry_limit", f"{skill} with these params failed {fails} times in the last 10 min")
            aid = str(uuid.uuid4())
            if skill in ("prospect", "mine_resource") and isinstance((st or {}).get("pos"), dict):
                self.dig_starts[aid] = dict(st["pos"])    # CP-86 run 4: climb back to / dig near the order
            if grant and source in TRUSTED_SOURCES:
                await self.send_grants(aid, sk, params, st)
            denied = await self.preflight(sk, params, st)
            if denied:
                if aid in self.granted:          # granted, then refused: nothing will run, end the grant now
                    self.spawn(self.revoke_grants(aid))
                return refuse(*denied)

        now = now_ms()
        aid = aid if not cancel else str(uuid.uuid4())
        try:   # the status row exists before anything is sent; a locked DB means nothing is sent (CP-24)
            with self.durable():                # O82: durable before the effect it records
                if task_id is None:
                    task_id = str(uuid.uuid4())
                    self.db.execute("INSERT INTO tasks (task_id, world_id, kind, goal, params, source, status, "
                                    "created_ts, updated_ts, assumptions) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                    (task_id, world, "skill", skill, pkey, source, "running", now, now,
                                     json.dumps(assumptions) if assumptions else None))
                self.db.execute("INSERT INTO actions (action_id, task_id, world_id, skill, skill_version, params, "
                                "status) VALUES (?,?,?,?,?,?, 'pending')", (aid, task_id, world, skill,
                                                                            sk["version"], pkey))
        except sqlite3.Error as e:
            self.metrics["db_errors"] += 1
            if aid in self.granted:
                self.spawn(self.revoke_grants(aid))
            return refusal("db_unavailable", f"memory DB not writable: {e}")
        ep0 = self.epoch
        self.unsent.add(aid)
        try:
            flushed = await self.db.flush(self.cfg.db_timeout_s)   # O82: the row is on the file before the request
        finally:
            self.unsent.discard(aid)
        if not flushed:
            self.db.execute("UPDATE actions SET status='failed', code='db_unavailable', result_ts=? WHERE action_id=?",
                            (now_ms(), aid))
            self.metrics["db_errors"] += 1
            return refusal("db_unavailable", f"memory DB file not writable within {self.cfg.db_timeout_s:g} s")
        if not cancel and (self.stopped or self.epoch != ep0):  # a STOP landed while the row was flushed: nothing
            self.db.execute("UPDATE actions SET status='cancelled', code='stopped', result_ts=? WHERE action_id=?",
                            (now_ms(), aid))                     # is sent - a stop is never overtaken (O82)
            return refusal("stopped", "a STOP landed before this request went out; nothing was sent")
        if not cancel and self.action(aid)["status"] != "pending":   # O81a: a cancel landed in the flush window -
            r = self.action(aid)                                      # cancel_active ended it; nothing is sent
            return {"ok": True, "action_id": aid, "task_id": task_id, "status": r["status"], "code": r["code"],
                    "evidence": None, "reconcile_note": None, "note": "cancelled before it was sent"}
        req = {"action_id": aid, "skill": skill, "version": sk["version"], "params": params,
               "deadline_ms": sk["timeout_ms"], "idempotency_key": aid}
        body = self.hub.links["body"]
        self.done[aid] = asyncio.Event()
        try:
            await body.send("action.request", req)
            log.info("action.request sent %s %s session=%s", aid, skill, body.session)   # CP-21 evidence: one line per send
        except ConnectionClosed:
            self.transition(aid, "uncertain", ("pending",), code="send_failed", result_ts=now_ms())
            self.finish(aid, "uncertain", "send_failed")
            return {"ok": False, "error": "send_failed", "action_id": aid, "task_id": task_id}
        try:
            self.transition(aid, "sent", ("pending",), sent_ts=now_ms())
        except sqlite3.Error:
            self.metrics["db_errors"] += 1
            self.reconcile_needed = True     # the row still says pending: reconcile before anything else
        self.sent_session[aid], self.requests[aid] = body.session, req
        self.watchers[aid] = asyncio.get_running_loop().create_task(self.watch(aid, sk))
        if wait:
            try:
                await asyncio.wait_for(self.done[aid].wait(), wait)
                while aid in self.creeper.resuming:      # O53: the walk was cancelled for a creeper and dispatched again
                    nxt = await asyncio.wait_for(asyncio.shield(self.creeper.resuming[aid]), wait)
                    if not nxt:
                        break
                    aid = nxt
                    await asyncio.wait_for(self.done[aid].wait(), wait)
            except asyncio.TimeoutError:
                pass
        r = self.action(aid)
        return {"ok": True, "action_id": aid, "task_id": task_id, "status": r["status"], "code": r["code"],
                "evidence": json.loads(r["evidence"]) if r["evidence"] else None,
                "reconcile_note": r["reconcile_note"],
                "note": r["note"]}

    def attach_denial(self, p):
        """A server permission.denied during (or right after) an action is recorded on that action."""
        self.metrics["server_denials"] += 1
        what = f"{p['effect']} {p.get('rule_id')}"
        cur = self.open_action()
        if cur is not None:
            self.denials.setdefault(cur["action_id"], []).append(what)
            return
        last = self.db.execute("SELECT action_id, note FROM actions WHERE result_ts > ? ORDER BY result_ts DESC "
                               "LIMIT 1", (now_ms() - 3000,)).fetchone()
        if last is not None:
            self.db.execute("UPDATE actions SET note=? WHERE action_id=?",
                            (((last["note"] + "; ") if last["note"] else "") + "server denied: " + what,
                             last["action_id"]))

    def grant_regions(self, skill, params):
        """grant_region boxes (link v1.3) for the skills that change a whole area, never from free request fields:
        build_schematic from the approved design; harvest_replant its farm box (the skill refuses > 32x4x32 itself);
        repair_zone the bounding box of exactly the steps sense's own zone.repair.result gave (CP-52)."""
        if skill == "build_schematic":
            return self.build_regions(params)
        if skill in ("harvest_replant", "harvest_crop"):    # skills: harvest_crop {min, max, ...} works the same box
            mn, mx = params.get("min"), params.get("max")
            if not (isinstance(mn, dict) and isinstance(mx, dict)):
                return []
            lo = {a: int(min(mn[a], mx[a])) for a in "xyz"}
            hi = {a: int(max(mn[a], mx[a])) for a in "xyz"}
            return [(lo, hi)]
        if skill == "repair_zone":
            told = self.repair_answers.get((params.get("zone_id"), params.get("snapshot_id")), set())
            cells = [(st["op"], st["pos"]["x"], st["pos"]["y"], st["pos"]["z"], st["block"])
                     for st in params.get("steps", [])]
            cells += [("replant", r["pos"]["x"], r["pos"]["y"], r["pos"]["z"], r["block"])
                      for r in params.get("replant", [])]
            if not cells or any(c not in told for c in cells):
                return []                            # only what sense itself answered gets a grant
            ps = [c[1:4] for c in cells]
            lo = {a: min(p[i] for p in ps) for i, a in enumerate("xyz")}
            hi = {a: max(p[i] for p in ps) for i, a in enumerate("xyz")}
            hi["y"] += 1                             # replanting/tilling acts on the block above the farmland too
            return split_box(lo, hi)
        return []

    def build_regions(self, params):
        """grant_region boxes for an approved build_schematic (link v1.3), from the APPROVED design folder, never from
        request fields: design box O..O+size-1 (O = owner-stated origin, else design.json origin) and the scaffold box
        shifted by the same offset; both place+break; boxes over 32 768 blocks are split along y."""
        r = self._load_design(params["project_id"], params["version"])
        if isinstance(r, dict):
            return []
        d, _v, design, _brief = r
        if design["hash"] != params["design_hash"]:
            return []
        D, size = design.get("origin") or {"x": 0, "y": 0, "z": 0}, design.get("size")
        if not isinstance(size, dict):
            return []
        O = params.get("origin") or D
        boxes = [({a: O[a] for a in "xyz"}, {a: O[a] + size[a] - 1 for a in "xyz"})]
        sc = self._read(d, "scaffold.json")
        if isinstance(sc, dict) and isinstance(sc.get("origin"), dict) and isinstance(sc.get("size"), dict):
            mn = {a: O[a] + sc["origin"][a] - D[a] for a in "xyz"}
            boxes.append((mn, {a: mn[a] + sc["size"][a] - 1 for a in "xyz"}))
        return [r for mn, mx in boxes for r in split_box(mn, mx)]

    async def grant_expiry(self, sk):
        """link v1.7: expires_tick on SENSE's clock = the last server tick sense reported + ttl, inside sense's window
        (now, now+72000]. The ttl covers the action's timeout plus the ticks since that report, capped at 72000 (so it
        can never pass now+72000, server ticks only advance); a report older than 600 ticks is refreshed first."""
        tick, at = self.sense_tick or (0, -1e9)       # O159: none yet (just linked) = stale: ask sense for one
        if (time.monotonic() - at) * 20 > 600:
            await self.hub.request("sense", "storage.request", {}, timeout=2.0)     # any sense frame carries its tick
            tick, at = self.sense_tick or (0, -1e9)
            if (time.monotonic() - at) * 20 > 600:
                self.event("mind", "grant.skipped", {"why": "no fresh server tick from sense"})
                return None
        since = math.ceil((time.monotonic() - at) * 20)
        return max(0, tick + min(math.ceil(sk["timeout_ms"] / 50) + 100 + since, GRANT_MAX_TICKS))   # S93-ci-1

    async def send_grants(self, aid, sk, params, st):
        """task.grant for task_only zones: exactly the positions this action changes, until its deadline; for an
        approved build_schematic, grant_region boxes of the approved design (revoked like any grant)."""
        effects = block_effects(sk["name"], sk, params)
        regions = self.grant_regions(sk["name"], params)
        area = area_grants(sk, params, st, self.env)          # O159
        if not (effects or regions or area) or self.hub.links["sense"] is None:
            return
        expires = await self.grant_expiry(sk)
        if expires is None:
            return
        region_effects = ("place", "break") + (("use",) if sk["name"] in USE_GRANT_SKILLS else ())
        per = 64 // len(region_effects)              # sense: at most 64 live regions in all
        for eff in region_effects if regions else ():
            self.granted.setdefault(aid, []).append((eff, st["dim"]))
            for mn, mx in regions[:per]:
                g = {"action_id": aid, "effect": eff, "dim": st["dim"], "min": mn, "max": mx,
                     "expires_tick": expires}
                bad = grant_region_ok(g)
                if bad:                              # never send a region sense would refuse
                    self.event("mind", "grant_region.refused", {"action_id": aid, "why": bad, "min": mn, "max": mx})
                    continue
                try:
                    await self.hub.links["sense"].send("grant_region", g)
                    self.event("mind", "grant_region", g)
                except ConnectionClosed:
                    pass
        for eff, mn, mx in area[:64]:                    # O159: sense keeps at most 64 live regions
            if (eff, st["dim"]) not in self.granted.get(aid, []):
                self.granted.setdefault(aid, []).append((eff, st["dim"]))
            g = {"action_id": aid, "effect": eff, "dim": st["dim"], "min": mn, "max": mx, "expires_tick": expires}
            bad = grant_region_ok(g)
            if bad:
                self.event("mind", "grant_region.refused", {"action_id": aid, "why": bad, "min": mn, "max": mx})
                continue
            try:
                await self.hub.links["sense"].send("grant_region", g)
                self.event("mind", "grant_region", g)
            except ConnectionClosed:
                pass
        for eff, positions in effects:
            self.granted.setdefault(aid, []).append((eff, st["dim"]))
            g = {"action_id": aid, "effect": eff, "dim": st["dim"], "positions": positions,
                 "expires_tick": expires}
            try:
                await self.hub.links["sense"].send("task.grant", g)
                self.event("mind", "task.grant", g)
            except ConnectionClosed:
                pass

    async def revoke_grants(self, aid):
        """End an action's grants as soon as the action ends (sense: positions [] revokes), not at expires_tick."""
        s = self.hub.links["sense"]
        for eff, dim in self.granted.pop(aid, []):
            g = {"action_id": aid, "effect": eff, "dim": dim, "positions": [],
                 "expires_tick": (self.sense_tick[0] if self.sense_tick else 0) + 1}
            try:
                if s is not None:
                    await s.send("task.grant", g)
                    self.event("mind", "task.grant.revoke", g)
            except ConnectionClosed:
                pass

    def reload_envelope(self, why):
        try:
            env = strict_loads(Path(self.cfg.envelope_path).read_text("utf-8"))
        except (OSError, ValueError) as e:
            log.error("envelope reload (%s) failed, keeping v%s: %s", why, self.env.get("envelope_version"), e)
            return False
        self.cfg.envelope = self.env = env
        ok, detail = self.binding()
        log.info("envelope reloaded (%s): v%s %s; binding %s", why, env.get("envelope_version"),
                 self.cfg.envelope_hash[:12], "ok" if ok else detail)
        return True

    async def permission_query(self, args):
        """Owner diagnostic (`eve check`): one permission.check over /sense; nothing is dispatched or granted."""
        eff = args.get("effect")
        if eff not in PERMISSION_EFFECT.values():
            return {"ok": False, "error": "bad_effect", "detail": f"effect must be one of {sorted(PERMISSION_EFFECT.values())}"}
        q = {"effect": eff, "dim": args.get("dim") or self.env["home"]["dim"], "actor": "eve"}
        pos, ent = args.get("pos"), args.get("entity_id")
        if isinstance(ent, int) and not isinstance(ent, bool) and ent >= 0:
            q["entity_id"] = ent
        elif isinstance(pos, dict) and all(isinstance(pos.get(a), int) and not isinstance(pos.get(a), bool)
                                           for a in "xyz"):
            q["pos"] = {a: pos[a] for a in "xyz"}
        else:
            return {"ok": False, "error": "bad_target", "detail": "give integer x y z or an entity id"}
        face = args.get("face")
        if face is not None:            # sense >= 0.1.2: use_item checks the clicked block + its face neighbour
            if face not in FACE:
                return {"ok": False, "error": "bad_face", "detail": f"face must be one of {sorted(FACE)}"}
            q["face"] = face
        if self.hub.links["sense"] is None:
            return {"ok": False, "error": "link_down", "detail": "sense link is down"}
        t0 = time.monotonic()
        r = await self.hub.request("sense", "permission.check", q, timeout=1.0)
        latency = int((time.monotonic() - t0) * 1000)
        self.event("mind", "permission.query", {"query": q, "result": r, "latency_ms": latency})
        if r is None:
            return {"ok": False, "error": "permission_unavailable", "detail": "no permission.result within 1 s"}
        return {"ok": True, "query": q, "allowed": r["allowed"], "rule_id": r.get("rule_id"),
                "reason": r.get("reason"), "latency_ms": latency}

    async def preflight(self, sk, params, st):
        """permission.check for what the server will actually check (block_effects), plus container/attack/item
        effects on their target. use_item on a block is checked as place on both positions, as sense enforces it."""
        queries = [{"effect": e, "pos": p} for e, ps in block_effects(sk["name"], sk, params) for p in ps]
        for eff in sk["effects"]:
            pe = PERMISSION_EFFECT.get(eff)
            if pe is None or eff in ("break", "place") or (eff == "use_item" and params.get("target_pos")):
                continue
            target, ent = params.get("pos") or params.get("target_pos"), params.get("entity_id")
            if target is not None:
                queries.append({"effect": pe, "pos": target})
            elif ent is not None:
                queries.append({"effect": pe, "entity_id": ent})
        for q in queries:
            r = await self.hub.request("sense", "permission.check", {**q, "dim": st["dim"], "actor": "eve"},
                                       timeout=1.0)
            if r is None:
                return "permission_unavailable", f"no permission.result for {q['effect']} within 1 s"
            if not r["allowed"]:
                return "zone_denied", f"{r.get('rule_id')}: {r.get('reason')}"
        return None

    async def watch(self, aid, sk, resend=True):
        """Re-send a lost request (same action_id, same body session only), then enforce the deadline."""
        t0 = time.monotonic()
        try:
            for _ in range(self.cfg.ack_resends if resend else 0):
                await asyncio.sleep(self.cfg.ack_timeout_s * 2)
                if self.status_of(aid) != "sent":
                    break
                body = self.hub.links["body"]
                if body is None or body.session != self.sent_session.get(aid):
                    break     # a different body session may not remember the id: reconcile, never resend
                self.metrics["resends"] += 1
                await body.send("action.request", self.requests[aid])
                log.info("action.request RESENT %s same session=%s", aid, body.session)
            await asyncio.sleep(max(0.0, sk["timeout_ms"] / 1000 + self.cfg.result_grace_s - (time.monotonic() - t0)))
            if self.transition(aid, "uncertain", OPEN, code="no_result", result_ts=now_ms()):
                self.finish(aid, "uncertain", "no_result")
        except (asyncio.CancelledError, ConnectionClosed):
            pass

    # ---- progress supervision (CP-22) and cancellation
    async def supervise(self):
        while True:
            await asyncio.sleep(0.5)
            try:
                await self.retry_lost_frames()
                await self.check_stuck()
                self.expire_parked()
                if time.monotonic() - getattr(self, "_spend_at", 0) > 5:      # O82: the ledger, read off the loop
                    self._spend_at = time.monotonic()
                    self.spawn(self.refresh_spend())
                self.daily_spend_line()
            except sqlite3.Error as e:
                self.metrics["db_errors"] += 1
                log.error("supervisor: %s", e)

    async def retry_lost_frames(self):
        """Apply body action frames that arrived while the DB was locked, in arrival order; a frame that still hits
        the lock stays queued (the sqlite3.Error goes to the supervisor) and nothing after it is applied."""
        while self.retry_frames:
            link, msg = self.retry_frames[0]
            await self.on_message(link, msg)
            self.retry_frames.pop(0)
            self.metrics["frames_retried"] = self.metrics.get("frames_retried", 0) + 1
            log.info("applied %s %s after a DB lock", msg["type"], msg["payload"].get("action_id"))

    async def check_stuck(self):
        cur = self.open_action()
        st = self.fresh_state()
        if cur is None or cur["skill"] == "stop" or st is None or self.retry_frames:
            return
        aid = cur["action_id"]
        late = aid in self.cancelling and time.monotonic() - self.cancelling[aid] > self.cfg.cancel_grace_s
        stuck = aid not in self.cancelling and \
            now_ms() - max(self.world_progress_ms, cur["sent_ts"] or now_ms()) > self.cfg.stuck_window_s * 1000
        ok, world = self.binding()
        if (late or stuck) and ok and st["control"].get("active_action_id") != aid:
            # the body is not running it (its result was lost or never came): settle from observed state, never
            # cancel or STOP an action nobody is executing (45L CP-24 a3: a lost result became a false STOP)
            self.metrics["settled_body_idle"] = self.metrics.get("settled_body_idle", 0) + 1
            self.cancelling.pop(aid, None)
            self.event("mind", "stuck_body_idle", {"action_id": aid, "skill": cur["skill"]})
            return self.reconcile_one(cur, st, world, None)
        if aid in self.cancelling:
            if late and not self.stopped:
                await self.stop("mind", f"{cur['skill']} {aid} ignored cancel")   # escalate: hard stop
            return
        if not stuck:
            return
        self.metrics["stuck"] += 1
        reason = f"stuck: no observed world progress for {self.cfg.stuck_window_s:g} s during {cur['skill']}"
        self.db.execute("UPDATE tasks SET status='failed', reason=?, updated_ts=? WHERE task_id=? "
                        "AND status IN ('running','blocked')", (reason, now_ms(), cur["task_id"]))
        self.release(cur["task_id"], reason)
        self.event("mind", "stuck", {"action_id": aid, "skill": cur["skill"], "reason": reason})
        self.cancelling[aid] = time.monotonic()
        r = await self.cancel_active(reason)
        if not r.get("ok"):
            await self.stop("mind", f"{reason}; cancel refused: {r.get('error')}")

    async def read_only(self, cmd, a):
        """Link v1.9 (CP-54): /cli survey|resources|storage forward sense's queries unchanged - nothing moves, nothing
        is placed or broken, no memory is written; refused while STOPPED. Every result names the server tick."""
        if self.stopped:
            return {"ok": False, "error": "stopped", "detail": "STOPPED: read-only queries wait for resume"}
        t = self.sense_tick
        tick = lambda: int(t[0] + (time.monotonic() - t[1]) * 20) if t else None  # noqa: E731
        if cmd == "storage":
            rows = self.db.execute("SELECT * FROM containers WHERE gone=0 AND world_id=?", (self.world_id,)).fetchall()
            items = {}
            for r in self.db.execute("SELECT container_id, item, count FROM container_items"):
                items.setdefault(r["container_id"], []).append({"item": r["item"], "count": r["count"]})
            return {"ok": True, "tick": tick(), "containers": [
                {"id": r["container_id"], "dim": r["dim"], "pos": {"x": r["x"], "y": r["y"], "z": r["z"]},
                 "kind": r["kind"], "tag": r["tag"], "loaded": bool(r["loaded"]), "observed_tick": r["observed_tick"],
                 "items": items.get(r["container_id"], [])} for r in rows]}
        dim = a.get("dim") or self.env["home"]["dim"]
        if cmd == "resources":
            q = {"dim": dim, **{k: a[k] for k in ("kind", "center", "radius") if a.get(k) is not None}}
            ans = await self.hub.request("sense", "query.resources", q, timeout=10)
            return {"ok": True, "tick": tick(), **ans} if isinstance(ans, dict) else                 {"ok": False, "error": "no_answer", "detail": "sense did not answer query.resources in 10 s"}
        try:
            lo, hi = ({k: int(b[k]) for k in "xyz"} for b in (a["min"], a["max"]))
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": "bad_args", "detail": "survey needs min and max {x,y,z}"}
        lo, hi = {k: min(lo[k], hi[k]) for k in "xyz"}, {k: max(lo[k], hi[k]) for k in "xyz"}
        boxes = [({"x": x, "y": y, "z": z}, {"x": min(x + 31, hi["x"]), "y": min(y + 31, hi["y"]), "z": min(z + 31, hi["z"])})
                 for y in range(lo["y"], hi["y"] + 1, 32) for z in range(lo["z"], hi["z"] + 1, 32)
                 for x in range(lo["x"], hi["x"] + 1, 32)]                    # 32^3 = 32 768 blocks per query
        if len(boxes) > SURVEY_MAX_QUERIES:
            return {"ok": False, "error": "bad_args", "detail": f"{len(boxes)} queries; at most {SURVEY_MAX_QUERIES}"}
        chunks = []
        for bmin, bmax in boxes:                    # in order: y, then z, then x of each chunk's min corner
            if self.stopped:
                return {"ok": False, "error": "stopped", "detail": f"STOPPED after {len(chunks)} of {len(boxes)}"}
            ans = await self.hub.request("sense", "query.blocks", {"dim": dim, "min": bmin, "max": bmax}, timeout=10)
            if not isinstance(ans, dict):
                return {"ok": False, "error": "no_answer", "detail": f"query.blocks {bmin}..{bmax} unanswered"}
            chunks.append({"min": bmin, "max": bmax, **ans, "tick": tick()})
        return {"ok": True, "dim": dim, "min": lo, "max": hi, "tick": tick(), "chunks": chunks}

    def spend(self):
        """O82: the month's spend as last read off the event loop (refresh_spend: every 5 s and after each reserve or
        commit); only the very first read, at start, is direct."""
        if getattr(self, "_spend", None) is None:
            self._spend = spend_summary(self.db, self.env.get("budgets", {}).get("deepseek_monthly_usd"))
        return self._spend

    async def refresh_spend(self):
        self._spend_at = time.monotonic()
        self._spend = await asyncio.to_thread(spend_summary, self.db,
                                              self.env.get("budgets", {}).get("deepseek_monthly_usd"))

    async def spend_reserve(self, a):
        """/cli for other spenders (eve-design on the 45L): reserve a call's worst case against Eve's one US$20 cap
        BEFORE sending it, then `spend_commit` the provider's reported cost. Rows: provider openrouter, job
        "design:<job>"; an uncommitted reservation stays counted at its worst case."""
        worst, model, job = a.get("usd_worst"), a.get("model"), a.get("job")
        cap = self.env.get("budgets", {}).get("deepseek_monthly_usd")
        if not (isinstance(worst, (int, float)) and math.isfinite(worst) and 0 < worst <= (cap or 0)) or \
                not (isinstance(model, str) and 0 < len(model) <= 200) or not (isinstance(job, str) and 0 < len(job) <= 200):
            return {"ok": False, "error": "bad_args", "detail": "needs usd_worst (0 < x <= cap), model, job"}
        jcap = JOB_CAPS_USD.get(job)                # a whole-job cap (all time), e.g. CP-54 brief US$1.00
        rid = await asyncio.to_thread(reserve_spend, self.db, cap, "openrouter", model, f"design:{job}",
                                      int(a.get("tokens_in") or 0), int(a.get("tokens_out") or 0), float(worst),
                                      job_cap=jcap)      # O82: the ledger's file I/O off the event loop
        await self.refresh_spend()
        if rid is None:
            used = (await asyncio.to_thread(lambda: self.db.execute(
                "SELECT coalesce(sum(usd), 0) FROM spend WHERE job IN (?, ?)",
                (f"design:{job}", f"design:{job} [unsettled]")).fetchone()))[0]
            if jcap is not None and used + float(worst) > jcap:
                return {"ok": False, "error": "job_cap", "detail": f"job {job}: US${used:.4f} of its US${jcap} cap used"}
            return {"ok": False, "error": "budget_exhausted", "detail": f"Eve's cap US${cap}/month", **self.spend()}
        self.event("mind", "spend.reserve", {"reservation_id": rid, "job": job, "model": model, "usd_worst": worst})
        return {"ok": True, "reservation_id": rid, "remaining_usd": self.spend()["remaining_usd"]}

    async def spend_commit(self, a):
        """Replace a design reservation's worst case with the reported cost. usd null/absent = no usage reported:
        the worst case stays counted (unsettled). Only open design reservations can be committed, once."""
        rid, usd = a.get("reservation_id"), a.get("usd")
        row = (await asyncio.to_thread(lambda: self.db.execute("SELECT * FROM spend WHERE id=?", (rid,)).fetchone())
               if isinstance(rid, int) else None)
        if row is None or not row["job"].startswith("design:") or not row["job"].endswith(" [unsettled]"):
            return {"ok": False, "error": "unknown_reservation", "detail": f"{rid!r} is not an open design reservation"}
        if usd is None:
            return {"ok": True, "reservation_id": rid, "usd": row["usd"], "settled": False}
        if not (isinstance(usd, (int, float)) and math.isfinite(usd) and usd >= 0):
            return {"ok": False, "error": "bad_args", "detail": "usd must be a finite number >= 0"}
        await asyncio.to_thread(self.db.execute, "UPDATE spend SET job=?, usd=?, tokens_in=coalesce(?, tokens_in), "
                                "tokens_out=coalesce(?, tokens_out), cached=coalesce(?, cached) WHERE id=?",
                                (row["job"][:-len(" [unsettled]")], float(usd), a.get("tokens_in"),
                                 a.get("tokens_out"), a.get("cached"), rid))
        await self.refresh_spend()
        self.event("mind", "spend.commit", {"reservation_id": rid, "job": row["job"][:-len(" [unsettled]")],
                                            "usd": float(usd), "tokens_in": a.get("tokens_in"),
                                            "tokens_out": a.get("tokens_out")})   # every call logged (O56a)
        return {"ok": True, "reservation_id": rid, "usd": float(usd), "settled": True}

    def daily_spend_line(self):
        day = time.strftime("%Y-%m-%d")
        if getattr(self, "_spend_day", None) != day:
            self._spend_day = day
            s = self.spend()
            log.info("spend %s: month-to-date US$%.6f of US$%s cap, remaining US$%.6f, today US$%.6f, %d calls "
                     "(%d unsettled)", day, s["month_to_date_usd"], s["cap_usd"], s["remaining_usd"], s["today_usd"],
                     s["calls"], s["unsettled"])

    async def cancel_active(self, reason, source="mind"):
        """Cancel the running action with the `stop` skill (not a hard STOP). O81a: a request still waiting on its
        row's flush is never sent - it ends cancelled here (eve-watch run 3: a walk_to went out 16 ms after the cancel
        began, overtook its stop, and the supervisor hard-stopped 3.5 s later)."""
        self.event("mind", "cancel", {"reason": reason, "source": source})
        self.follow.end("cancelled")                         # O111: a cancel ends a follow too
        for aid in [a for a in self.unsent if self.action(a)["skill"] != "stop"]:
            if self.transition(aid, "cancelled", ("pending",), code="cancelled", result_ts=now_ms()):
                self.finish(aid, "cancelled", "cancelled")
        cur = self.open_action()
        if cur is not None and cur["skill"] != "stop":    # I25: every cancel is supervised (settle or escalate)
            self.cancelling.setdefault(cur["action_id"], time.monotonic())
        return await self.do("stop", {}, source=source, bypass_busy=True)

    def release(self, task_id, reason):
        if task_id:
            self.db.execute("UPDATE reservations SET released_ts=?, release_reason=? WHERE task_id=? "
                            "AND released_ts IS NULL", (now_ms(), reason, task_id))

    # ---- parked tasks: a stop pauses, a resume never continues; continue or drop is the owner's (or a harness's)
    PARKED = ("paused", "blocked", "queued")

    def drop_tasks(self, task_id, parked_all, source, reason=None):
        if parked_all:
            rows = self.db.execute(f"SELECT * FROM tasks WHERE status IN ({','.join('?' * 3)})", self.PARKED).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM tasks WHERE task_id=? OR task_id LIKE ?",
                                   (task_id, f"{task_id}%")).fetchall() if task_id else []
            if len(rows) != 1:
                return {"ok": False, "error": "no_task", "detail": f"{len(rows)} tasks match {task_id!r}"}
            if rows[0]["status"] not in self.PARKED:
                return {"ok": False, "error": "not_parked",
                        "detail": f"task is {rows[0]['status']}" + (" - use eve cancel" if rows[0]["status"] == "running"
                                                                      else "")}
        why = reason or f"dropped by {source}"
        for t in rows:
            self.db.execute(f"UPDATE tasks SET status='cancelled', reason=?, updated_ts=? WHERE task_id=? "
                            f"AND status IN ({','.join('?' * 3)})", (why, now_ms(), t["task_id"], *self.PARKED))
            self.release(t["task_id"], why)
        if rows:
            self.event("mind", "tasks_dropped", {"tasks": [t["task_id"] for t in rows], "reason": why})
        return {"ok": True, "dropped": [t["task_id"] for t in rows]}

    def expire_parked(self):
        """Backstop: a task parked longer than parked_ttl_s is cancelled (its assumptions are stale by then)."""
        cutoff = now_ms() - int(self.cfg.parked_ttl_s * 1000)
        old = self.db.execute("SELECT task_id FROM tasks WHERE status IN ('paused','blocked') AND updated_ts < ?",
                              (cutoff,)).fetchall()
        for t in old:
            self.drop_tasks(t["task_id"], False, "mind",
                            f"expired: parked longer than {self.cfg.parked_ttl_s / 60:g} min; re-issue the command")

    # ---- changed world / instructions (CP-23)
    def approval_valid(self, project_id, version, want_hash):
        row = self.db.execute("SELECT * FROM approvals WHERE project_id=? AND version=? ORDER BY ts DESC LIMIT 1",
                              (project_id, version)).fetchone()
        if row is None or row["decision"] != "approved":
            return False, "no current approval"
        if row["invalidated_ts"]:
            return False, f"approval revoked: {row['invalidated_reason']}"
        try:
            now_hash = design_hash(json.loads((self.cfg.designs_dir / project_id / f"v{version}" / "design.json")
                                              .read_text("utf-8")))
        except (OSError, ValueError):
            now_hash = None
        if row["design_hash"] != want_hash or now_hash != want_hash:
            return False, "design changed since approval"
        return True, row["approval_id"]

    def owner_assumptions(self, specs):
        """`eve do ... --assume container_has:<id>:<item>:<count> --assume approval:<project>:v<N>` -> task assumptions
        (CP-23). An approval assumption binds the hash of the approval that is valid right now."""
        out = []
        for spec in specs:
            parts = str(spec).split(":")
            try:
                if parts[0] == "container_has" and len(parts) >= 4:
                    # container ids contain colons (sense: "minecraft:overworld:-26,99,-32"): count is last, then
                    # the item ("<ns>:<name>" when the part before it is a bare namespace, else a bare name); the rest
                    # is the id
                    ns = len(parts) >= 5 and re.fullmatch(r"[a-z0-9_.-]+", parts[-3])
                    cid, item = ((":".join(parts[1:-3]), ":".join(parts[-3:-1])) if ns
                                 else (":".join(parts[1:-2]), parts[-2]))
                    if not cid:
                        raise ValueError
                    out.append({"kind": "container_has", "container_id": cid,
                                "item": item if ":" in item else f"minecraft:{item}", "count": int(parts[-1])})
                elif parts[0] == "approval" and len(parts) == 3:
                    v = int(parts[2].lstrip("v"))
                    row = self.db.execute("SELECT design_hash, decision, invalidated_ts FROM approvals WHERE "
                                          "project_id=? AND version=? ORDER BY ts DESC LIMIT 1", (parts[1], v)).fetchone()
                    if row is None or row["decision"] != "approved" or row["invalidated_ts"]:
                        return {"ok": False, "error": "no_current_approval",
                                "detail": f"{parts[1]} v{v} has no valid approval to assume"}
                    out.append({"kind": "approval", "project_id": parts[1], "version": v,
                                "design_hash": row["design_hash"]})
                else:
                    raise ValueError
            except ValueError:
                return {"ok": False, "error": "bad_assumption",
                        "detail": f"{spec!r}: use container_has:<id>:<item>:<count> or approval:<project>:v<N>"}
        return out

    def stale_assumptions(self, task):
        """Return the task's recorded assumptions that no longer hold in current knowledge."""
        stale = []
        for a in json.loads(task["assumptions"] or "[]"):
            if a["kind"] == "container_has":
                c = self.db.execute("SELECT c.gone, coalesce(i.count, 0) FROM containers c LEFT JOIN "
                                    "container_items i ON i.container_id=c.container_id AND i.item=? "
                                    "WHERE c.container_id=?", (a["item"], a["container_id"])).fetchone()
                if c is None or c[0] or c[1] < a["count"]:
                    stale.append({**a, "now": None if c is None else ("gone" if c[0] else c[1])})
            elif a["kind"] == "approval":
                ok, why = self.approval_valid(a["project_id"], a["version"], a["design_hash"])
                if not ok:
                    stale.append({**a, "now": why})
            else:
                stale.append({**a, "now": "unknown assumption kind"})
        return stale

    async def continue_task(self, task_id, source, wait=0.0):
        t = self.db.execute("SELECT * FROM tasks WHERE task_id=? OR task_id LIKE ?",
                            (task_id, f"{task_id}%")).fetchall()
        if len(t) != 1:
            return {"ok": False, "error": "no_task", "detail": f"{len(t)} tasks match {task_id!r}"}
        t = t[0]
        if t["status"] not in ("paused", "blocked"):
            return {"ok": False, "error": "not_resumable", "detail": f"task is {t['status']}"}
        if self.stopped:
            return {"ok": False, "error": "stopped", "detail": "STOPPED: resume first"}
        ok, world = self.binding()
        st = self.fresh_state()
        if not ok or st is None:
            return {"ok": False, "error": "not_ready", "detail": world if not ok else "no fresh state"}
        if t["world_id"] != world:
            return {"ok": False, "error": "world_changed", "detail": f"task is for {t['world_id']}, bound to {world}"}
        stale = self.stale_assumptions(t)
        if stale:
            self.db.execute("UPDATE tasks SET reason=?, updated_ts=? WHERE task_id=?",
                            (f"stale assumptions: {json.dumps(stale)}", now_ms(), t["task_id"]))
            return {"ok": False, "error": "stale_assumptions", "detail": stale}
        params = json.loads(t["params"] or "{}")
        holds, detail = postcondition(t["goal"], params, st, self.env)   # work done is read from the world
        if holds:
            self.db.execute("UPDATE tasks SET status='done', reason=?, updated_ts=? WHERE task_id=?",
                            (f"postcondition already holds: {detail}", now_ms(), t["task_id"]))
            return {"ok": True, "status": "done", "dispatched": False, "detail": detail}
        self.db.execute("UPDATE tasks SET status='running', updated_ts=? WHERE task_id=?", (now_ms(), t["task_id"]))
        r = await self.do(t["goal"], params, source=source, task_id=t["task_id"], wait=wait,
                          grant=t["source"] in TRUSTED_SOURCES)
        if not r["ok"]:
            self.db.execute("UPDATE tasks SET status=?, reason=?, updated_ts=? WHERE task_id=? AND status='running'",
                            (t["status"], f"continue refused: {r['error']}", now_ms(), t["task_id"]))
        return r

    def revoke(self, project_id, version, by, reason):
        trusted = self.env["owner"]["trusted_players"]
        by = by or (trusted[0] if len(trusted) == 1 else None)
        if by not in trusted:
            return {"ok": False, "error": "not_trusted", "detail": f"{by!r} is not in owner.trusted_players"}
        m = re.fullmatch(r"v?(\d{1,6})", str(version))
        if not m or not isinstance(project_id, str) or not PROJECT_RE.match(project_id):
            return {"ok": False, "error": "bad_args"}
        n = self.db.execute("UPDATE approvals SET invalidated_ts=?, invalidated_reason=? WHERE project_id=? AND "
                            "version=? AND invalidated_ts IS NULL", (now_ms(), f"revoked by {by}: {reason}",
                                                                     project_id, int(m.group(1)))).rowcount
        return {"ok": n > 0, "revoked": n, **({} if n else {"error": "no_active_approval"})}

    # ---- owner language (CP-42) and planning (CP-41)
    def record_decision(self, kind, model, prompt_version, inp, out, latency_ms, validated):
        try:
            self.db.execute("INSERT INTO decisions (decision_id, world_id, ts, kind, prompt_version, model, input, "
                            "output, latency_ms, validated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (str(uuid.uuid4()), self.world_id or "unbound", now_ms(), kind, prompt_version, model,
                             inp, json.dumps(out) if not isinstance(out, str) else out, latency_ms, int(validated)))
        except sqlite3.Error:
            self.metrics["db_errors"] += 1

    def note_hostile(self, text):
        """O126: the last hostile mob named in chat (his words or her own report), for "fight it" within 60 s."""
        h = HOSTILE.search(text or "")
        if h and h[1].lower().rstrip("s") != "mob":
            self.last_hostile = (h[1].lower().rstrip("s") if not h[1].lower().endswith("ss") else h[1].lower(),
                                 time.monotonic())

    def hostile_referent(self):
        """O126: "it" in a fight order - the hostile last named in chat within 60 s, else the nearest hostile she
        sees; None = ask."""
        if self.last_hostile and time.monotonic() - self.last_hostile[1] <= 60:
            return self.last_hostile[0]
        ents = sorted((e for e in (self.fresh_state() or {}).get("nearby_entities", []) if e.get("hostile")),
                      key=lambda e: e.get("distance", 1e9))
        return str(ents[0]["type"]).split(":")[-1].replace("_", " ") if ents else None

    async def say(self, text, reply=True, resend=True):
        self.note_hostile(text)
        self.last_line = text                     # S93: "why did you stop" answers from what she last said
        """O114 (the live body drops a chat line within 2 s of the last one - 11:07:12's reply was lost): one line at a
        time, cfg.chat_gap_s apart; reply=True marks an owner reply (body's coming queue puts it first, today's body
        ignores the key); a rate_limited refusal is re-sent once after 2 s (on_message)."""
        async with self._say_lock:
            wait = self._last_say + self.cfg.chat_gap_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            b, ok = self.hub.links["body"], False
            if b is not None:
                try:
                    mid = await b.send("chat.send", {"text": text[:256], **({"reply": True} if reply else {})})
                    ok = True
                    if resend:
                        self._chat_sent[mid] = (text, reply)
                        while len(self._chat_sent) > 32:
                            self._chat_sent.pop(next(iter(self._chat_sent)))
                except ConnectionClosed:
                    pass
            self._last_say = time.monotonic()
            self.said_n += 1
        return ok

    async def resay(self, about):
        """O114: one resend, 2 s after the body refused the line rate_limited."""
        text, reply = self._chat_sent.pop(about, (None, None))
        if text is not None:
            await asyncio.sleep(2.0)
            await self.say(text, reply, resend=False)

    async def check_intent(self, text):
        """O94k: agenda's intent gate on an owner order - {verdict: refuse | ask | plan, rule, reason}, or None when the
        gate is off or agenda cannot be loaded (the path below is then unchanged, and that is logged). `rules` is the
        3cf44c73 word gate (no model, no I/O); `model` is the 9fd81158 two-framing gate in a thread, and an unreachable
        model asks with the reason "planner unavailable", never a vague question."""
        if self.cfg.intent_gate == "off":
            return None
        try:
            p = str(self.cfg.root / "parts" / "mind")
            if p not in sys.path:
                sys.path.insert(0, p)
            from agenda import adapter, intent_gate
        except Exception as e:                           # a missing agenda line never blocks the owner's words
            self.event("mind", "intent_gate.unavailable", {"error": f"{type(e).__name__}: {e}"[:200]})
            return None
        if self.cfg.intent_gate == "rules":
            return intent_gate.gate(text, self.env)
        g = await asyncio.to_thread(adapter.intent_gate, text, self.env, self.planner.url)
        if g["verdict"] == "ask" and any(str(v.get("rule", "")).startswith("model_error") for v in g.get("votes", [g])):
            g = {**g, "rule": "planner_unavailable",
                 "reason": "I can't check that order right now: the planner is unavailable."}
        return g

    async def agenda_owner(self, text, sender=None, source="owner", sender_pos=None):
        """S38 agenda-wire (CP-73/CP-74 owner side): "plan: a; then b" sets Hunter's agenda (each goal passes the gate
        rules), "what's on your agenda" shows it with the scheduler's next pick and reason (a display - nothing is
        dispatched, O6), "what did you do while I was gone" says a CP-74 briefing that passed verify (never an unbacked
        line). None = not an agenda line (or agenda cannot load): the normal path goes on."""
        try:
            p = str(self.cfg.root / "parts" / "mind")
            if p not in sys.path:
                sys.path.insert(0, p)
            from agenda import adapter
        except Exception as e:
            self.event("mind", "agenda.unavailable", {"error": f"{type(e).__name__}: {e}"[:200]})
            return None
        k = adapter.owner_phrase(text, is_order=lambda s: bool(IMPERATIVE.match(s)))   # O127: " and " by the rule map
        if k is None:
            return None
        if k["kind"] == "plan":
            ag, why = adapter.agenda_from_goals(k["goals"], self.env)
            if ag is None:
                await self.say(why)
                return {"ok": False, "error": "agenda", "reason": why}
            self.set_meta({"agenda.items": adapter.agenda_json(ag)})
            self.event("mind", "agenda.set", {"goals": k["goals"], "later": k.get("later", False)})
            if k.get("later") or sender is None:
                line = adapter.agenda_line(ag) + ". I'll work on it while you're away."
                await self.say(line)
                return {"ok": True, "reply": line}
            # O127 (45L, Hunter present: "plan: mine 5 logs and then craft a crafting table" only stored): a plan he
            # gives while here is DONE NOW - each goal in order through the O122 sequence path (its own reply, a
            # failure or question stops the rest) - and stays stored as the agenda for when he is gone
            await self.say(adapter.agenda_line(ag) + ". Starting now.")
            return await self.owner_sequence(k["goals"], sender, source, sender_pos)
        if k["kind"] == "show":
            ag = adapter.agenda_load(self.meta("agenda.items"), self.env)
            st = self.fresh_state()
            line = adapter.show_reply(ag, ag.decide(adapter.scheduler_input(st)) if ag and st else None)
            await self.say(line)
            return {"ok": True, "reply": line}
        now = now_ms()                                   # brief: since the last briefing, else the last 2 hours
        since = int(self.meta("agenda.briefing_since") or 0) or now - 2 * 3600 * 1000
        claims, bad = await asyncio.to_thread(adapter.briefing_for, str(self.cfg.db_path), since, now, self.world_id)
        if bad:
            self.event("mind", "briefing.refused", {"problems": bad[:20]})
            line = "I have a briefing, but it failed my own check, so I won't give it."
            await self.say(line)
            return {"ok": False, "error": "briefing_unverified", "reason": line}
        lines = adapter.brief_lines(claims)
        for line in lines:
            await self.say(line)
        if claims:
            j = adapter.journal_row(self.world_id or "unbound", claims, now, bad)
            self.db.execute("INSERT INTO journal (world_id, ts, kind, text, generated_by, grounded_in) VALUES (?,?,?,?,?,?)",
                            (j["world_id"], j["ts"], j["kind"], j["text"], j["generated_by"], j["grounded_in"]))
        self.set_meta({"agenda.briefing_since": now + 1})   # windows are inclusive: a row stamped exactly `now`
        #                                                     (the walk task's updated_ts in the test) is briefed once
        return {"ok": True, "reply": " ".join(lines)}

    async def owner_text(self, text, sender, source="owner", sender_pos=None):
        """O111 (45L diagnosis 11:0x: no owner line left a log line, a second "come to X" was refused busy in silence,
        "chop 8 logs" got no answer at all): every owner line gets a chat reply - plan / ask / refuse / answer / busy -
        with a "give me a moment" line if nothing was said within INTERIM_S, and is logged at INFO with its outcome and
        elapsed seconds."""
        t0, n0 = time.monotonic(), self.said_n
        self.owner_line = text                    # O136: his exact words - sense's attack.grant must echo them
        if source == "owner":
            if not KEEP_GOING.match(text):
                self.progress_gen += 1            # O175: any line of his ends a running progression ("keep going" not)
            self.recover.needs_owner = False      # S28-DEATH-4: his line ends the wait after two deaths
        interim = self.spawn(self._interim(n0)) if source == "owner" else None
        try:
            r = await self._owner_text(text, sender, source, sender_pos)
        finally:
            if interim is not None:
                interim.cancel()
        r = r if isinstance(r, dict) else {}
        if source == "owner" and r.get("code") == "busy":
            await self.say(f"I'm still busy: {r.get('detail')}. Say cancel first, or give me a new place to walk to.")
        elif source == "owner" and self.said_n == n0:
            await self.say(self.ack_line(r))
        log.info("owner %s: %r -> %s (%.1f s)", sender, text[:120], "action " + r["action_id"][:8] if r.get("action_id")
                 else "question: " + r["question"][:60] if r.get("question") else
                 f"refused {r.get('code') or r.get('error')}: {str(r.get('detail') or r.get('reason') or '')[:80]}"
                 if r.get("ok") is False else "ok", time.monotonic() - t0)
        return r

    async def _interim(self, n0):
        await asyncio.sleep(INTERIM_S)
        if self.said_n == n0:
            await self.say("On it - give me a moment.")

    def ack_line(self, r):
        """O111: the reply for an owner line that produced no line of its own."""
        if r.get("action_id"):
            row = self.action(r["action_id"])                     # a sqlite3.Row (no .get): 36e7130c crashed here
            skill = row["skill"] if row is not None else "on it"
            return "On my way." if skill == "walk_to" else f"OK - {str(skill).replace('_', ' ')}."
        if r.get("following"):
            return "Following you."
        if r.get("op") == "stay":
            return "OK, I'll stay here."
        if r.get("stopped"):
            return "Stopped."
        if r.get("plan") and r.get("results") is not None:
            steps = [s["skill"].replace("_", " ") for s in r["plan"].get("steps", [])]
            return ("OK - " + ", then ".join(steps) + "." if r.get("ok") else
                    f"I started ({', then '.join(steps)}) but stopped: "
                    f"{(r['results'][-1].get('code') or r['results'][-1].get('status')) if r['results'] else 'nothing ran'}.")
        if r.get("ok") is False:
            return f"I can't do that: {str(r.get('detail') or r.get('reason') or r.get('error'))[:180]}."
        return "OK."

    async def _owner_text(self, text, sender, source="owner", sender_pos=None):
        """A trusted owner's words: memory statements, then rules, then the planner. Every action goes through do()."""
        order, hint = workstations.tool_hint(text)    # O148: "use the wooden shovel to mine 10 grass blocks"
        if hint:
            await workstations.use_tool(self, hint, order, source)
            text = order
        fp = FIGHT_PRONOUN.match(text.strip())
        if fp:                                    # O126 (a): "go and fight it" -> the mob it means, else ask
            kind = self.hostile_referent()
            if kind is None:
                q = "Which mob? I don't see a hostile one near me."
                await self.say(q)
                return {"ok": True, "question": q}
            text = f"{fp[1]}{kind}{fp[2]}"
        self.note_hostile(text)
        steps = owner_phrases.order_steps(text)
        if steps is not None:                           # O157: a multi-sentence order - its steps, and no stop word in
            if len(steps) == 1:                          #   "then stop and tell me what happened" ends anything
                return await self._owner_text(steps[0], sender, source, sender_pos)
            return await self.owner_sequence(steps, sender, source, sender_pos)
        text = progress.normalize(text)           # O175: 'smelt 4 logs into charcoal' is the smelt rule's line
        if source in ("owner", "cli") and KEEP_GOING.match(text):   # O175 (learning's replay: control.resume re-ran
            cur = self.open_action()                                #   the last fight): continue what is going on,
            if self.autofight is not None or cur is not None and \
                    cur["skill"] in ("fight_melee", "fight_ranged", "harvest_animal"):   # never control.resume
                await self.say("Still on it.")
                return {"ok": True, "continued": "fight"}
            if cur is not None:                     # ponytail: a run between two commands has no open action
                await self.say("I'm still on it.")
                return {"ok": True, "continued": cur["skill"]}
            asked = self.asked if isinstance(self.asked, dict) and self.asked.get("kind") == "progress" else None
            if self.last_stop and (asked or time.monotonic() - self.last_stop["at"] < progress.ASK_TTL):
                self.asked = None
                return await progress.retry(self, self.last_stop, sender, source)
            await self.say("There's nothing to continue.")
            return {"ok": True, "continued": None}
        m = map_command(text)
        it = None
        stop = m["kind"] == "control" and m["op"] in ("stop", "resume")
        if source in ("owner", "cli") and self.backing_off and not stop and (
                m["kind"] == "control" and m["op"] == "home" or m.get("to_owner") == "come"):
            at = self.owner_pos(sender, sender_pos)       # O169 (45L 15:14:02: his "go home" was cancelled by the
            self.back_off_dest = ("go_home", {}) if m.get("op") == "home" or at is None else \
                ("walk_to", {"pos": {a: float(at[a]) for a in "xyz"}, "tolerance": 2.0})   # back-off): it redirects
            await self.cancel_active("owner redirected the back-off", source="cli")
            await self.say("Heading there.")
            return {"ok": True, "redirected": True}
        if source in ("owner", "cli") and self.recovering and not stop:   # H-recover: his order waits for her things
            self.deferred = (text, sender, source, sender_pos)
            await self.say("I'll do that once I have my things back.")
            return {"ok": True, "deferred": True}
        if source in ("owner", "cli") and self.holding and not stop and not text.strip().endswith("?") and \
                not re.search(r"\b(?:fight|attack|kill|shoot|run|flee|back off)\b", text, re.I):
            self.deferred = (text, sender, source, sender_pos)   # S28-DEATH-2: after the reflex, not during it
            await self.say("I'll do that once I'm out of this fight.")
            return {"ok": True, "deferred": True}
        if not stop and BOAT.match(text):                 # H-boat: "follow me by boat" - the follow, by boat on water
            if "follow_boat" not in self.registry.skills:
                await self.say("I can't sail yet - I'll follow you along the shore.")
            else:
                await self.say("I'll bring my boat.")
            if not self.follow.running():
                await self.preempt("following you", source)
            return self.follow.start(sender, source, boat=True)
        if not stop:
            pr = await progress.owner_line(self, text, sender, source, sender_pos)   # O175
            if pr is not None:
                return pr
        if not (m["kind"] == "control" and m["op"] == "stop") and not text.strip().endswith("?"):
            ws = await workstations.route(self, text, sender, source, sender_pos)   # O140/O140-b: before her open question - "go to the crafting table and ..."
            if ws is not None:
                return ws
        if not (m["kind"] == "control" and m["op"] == "stop"):       # STOP wins over any answer
            r = await build_flow.owner_line(self, text, sender, source, sender_pos)   # O132: a building brief, or
            if r is not None:                                                       #   yes / no to her site question
                return r
            r = await self.consume_ask(text, sender, source)
            if r is not None:
                return r
            xyz = BARE_XYZ.match(text)
            if xyz or NUMBERS_ONLY.match(text):   # O111 (3) / O111-b (2): bare numbers are never a fact, never a walk
                q = (f"What should I do at {' '.join(xyz.groups())}? Say 'go to {' '.join(xyz.groups())}' to send me "
                     f"there." if xyz else f"What should I do with {text.strip(' .!')}?")
                await self.say(q)
                return {"ok": True, "question": q}
        parts = [q.strip(" .!") for p in THEN_SPLIT.split(text.strip()) for q in owner_phrases.split_it(p.strip(" .!"))]   # O138
        if len(parts) > 1 and all(parts) and all(IMPERATIVE.match(p) for p in parts) and not text.strip().endswith("?"):
            return await self.owner_sequence(parts, sender, source, sender_pos)   # O122: "a and then b"
        held = self.inventory_answer(text)            # O111 (6): from the state frame, never "not in my records"
        if held:
            await self.say(held)
            return {"ok": True, "reply": held}
        ag = await self.agenda_owner(text, sender, source, sender_pos)   # S38 agenda-wire: plan / agenda / briefing phrases, deterministic
        if ag is not None:                            #   (before the question route: "what's the plan?" is agenda's)
            return ag
        if not text.strip().endswith("?") and not THIRD_PARTY.search(text):    # O175/O177: the one grammar first
            intent = grammar.parse(text, {"last_item": self.last_item})
            r = await self.grammar_order(intent, text, sender, source, sender_pos) if intent else None
            if r is not None:
                return r
        ft = owner_phrases.find_target(text)          # O164: "find exposed stone", "where is the nearest water"
        if ft is not None:
            return await self.find_block(ft, sender, source, sender_pos)
        held = self.progress_answer(text)             # O160: "did you get all 8?" - one sentence, then the reason
        if held:
            await self.say(held)
            return {"ok": True, "reply": held}
        held = self.state_answer(text)                # O111-b (5): where she is, her health, where home is
        if held:
            await self.say(held)
            return {"ok": True, "reply": held}
        if not (m["kind"] == "control" and m["op"] == "stop") and not owner_phrases.is_transfer(text):
            #   O137: "put the cobble in the chest near you" is a deposit, not "place X near you"
            kit = await self.owner_kit(text, sender, source, sender_pos)   # O123/O124: make / place / give by rule
            if kit is not None:
                return kit
        if (text.strip().endswith("?") or QUESTION_START.match(text)) and m["kind"] in ("skill", "question", "unknown"):
            return await self.talk(text)              # O111-b (5): a question is answered, never executed
        statement = bool(STATEMENT.search(text)) and not IMPERATIVE.match(text) and not text.strip().endswith("?")
        # O69/O69a: a rename, a placement without coordinates and a removal are statements whatever the model's
        # label (gate 2 G11: "Rename the lake island to Saltholm." went to the planner); a removal even when the
        # rules would map its coordinates to a walk
        statement = statement or bool(parse_removal(text) or parse_keep(text) or parse_correction(text)) or (m["kind"] not in ("skill", "control") and bool(
            parse_rename(text) or parse_relation(text)))
        if statement and m["kind"] == "skill":
            m = {"kind": "unknown"}        # O30 safety: "I moved the table, it's at 1 64 2 now" never moves Eve
        b = BRIDGE_CMD.match(text)
        if b and not (m["kind"] == "control" and m["op"] == "stop"):   # "bridge to 10 64 20": an explicit command
            await self.preempt("a bridge to " + " ".join(b.groups()), source)   # O120
            return self.bridge.start(dict(zip("xyz", b.groups())), source=source)
        plain = m["kind"] in ("skill", "question", "unknown", "to_owner") and not statement and not text.strip().endswith("?") \
            and not THIRD_PARTY.search(text) and not REPORT_START.match(text.strip().lower()) and not NOT_ORDER.search(text)
        order = plain and bool(IMPERATIVE.match(text))       # O94i: an order - never a fact about the world
        phrase = owner_phrases.match(text, self.phrase_view(sender, sender_pos)) \
            if plain and m["kind"] not in ("skill", "to_owner") and not m.get("to_owner") and not CHAT_MARK.search(text) \
            else None                                     # O111-b: the parts' sheets (his own place stays to_owner's)
        if plain and (order or m["kind"] in ("skill", "to_owner")):   # O94k: agenda's gate before any model/planner call
            g = await self.check_intent(text)
            if g is not None and (g["verdict"] == "refuse" and not (phrase and (phrase.get("grant") or phrase.get("livestock")) and g.get("rule") == "harm")
                                  or g["verdict"] == "ask" and not (
                    (m.get("to_owner") or phrase is not None or m["kind"] == "skill")
                    and g.get("rule") == "vague")):     # O158: "gather 10 logs around here" - the rules mapped it all
                #   O136: his naming a protected mob is the permission (sense's attack.grant still checks his line)
                #   O111: "here" = his own place; O111-b: "that zombie" = the nearest zombie a phrase rule resolved
                self.event("mind", "intent_gate", {"text": text[:200], "verdict": g["verdict"], "rule": g.get("rule"),
                                                   "reason": g["reason"], "gate": self.cfg.intent_gate})
                await self.say(g["reason"])
                if g["verdict"] == "ask":                 # O138: his next short line ("near you", "by the door", x y z)
                    self.asked = {"kind": "rerun", "text": text, "sender": sender, "at": time.monotonic()}   # answers it
                return {"ok": False, "error": "intent_gate", "verdict": g["verdict"], "rule": g.get("rule"),
                        "reason": g["reason"]}
        if m.get("to_owner") and plain:                 # O111: his own place - no model call
            return await self.to_owner(m["to_owner"], sender, source, sender_pos)
        if phrase is not None:
            return await self.run_phrase(phrase, text, sender, source)
        g = gather_order(text) if order and m["kind"] != "skill" and not CHAT_MARK.search(text) else None
        if g and g.get("question"):                     # O111-b (5): "mine 5 coal" is a resource, not "which x y z"
            await self.say(g["question"])                #   (map_command's wood rule, a skill, stands)
            self.asked = {**g["ask"], "sender": sender, "at": time.monotonic()}
            return {"ok": True, "question": g["question"]}
        if g:
            return await self.gather(g["params"], source)
        if m["kind"] == "question" and m.get("ask") and order:   # O111 (10): an ORDER's rules ask is said at once
            #   (a statement the rules happen to map to a position ask keeps the O30 + fact path: test_o81_harmless_first)
            await self.say(m["text"])
            self.asked = {"kind": "pos", "sender": sender, "at": time.monotonic(), **m["ask"]}
            return {"ok": True, "question": m["text"]}
        if m["kind"] == "skill" and m["skill"] == "walk_to" and WALK_XYZ.match(text) and plain:
            return await self.move_to(m["params"], source)   # O111 (2): "go to X Y Z" by rule, a walk replaces a walk
        if (order or plain and ASK_VERB.match(text)) and not CHAT_MARK.search(text):
            # O111-b (6): a rules-mapped order or the rules' question replies at once - no model or extractor first
            if m["kind"] == "skill":
                return await self.owner_do(m["skill"], m["params"], source)     # O120: replaces her task
            #   ponytail: a rules question without m["ask"] keeps the fact path first (bounded EXTRACT_TIMEOUT_S) -
            #   "Put torches every 8 blocks in tunnels." is a standing rule, not "which block, and where?"
        if m["kind"] in ("skill", "question", "unknown"):
            # O30: the model's constrained choice decides command vs talk vs fact for lines the rules map to a skill
            # or an ask (stop, other controls and approvals stay deterministic). Unavailable -> the rules below.
            it = await classify(self, text, timeout=self.cfg.reply_model_s)   # O111: capped
            vetoes = not order and statement or not order and it is not None and it["intent"] != "command" and (
                m["kind"] == "unknown" or it["intent"] in ASKING or text.strip().endswith("?") or
                CHAT_MARK.search(text) or REPORT_START.match(text.strip().lower()))
            # a line the rules map to a full skill stays a command unless the model hears a question/claim or it is a
            # story ("my dog knocked over my drink lol"); a mere small-talk label does not veto "say good night"
            if vetoes:
                # a statement (not a question, not someone else's claim) goes to the fact extractor first, whatever
                # the label: "spruce doors look better to me" is a taste to remember even if it reads as small talk;
                # the extractor itself returns nothing for plain chat (CP-44 rebuild)
                if not text.strip().endswith("?") and not THIRD_PARTY.search(text):
                    f = await self.facts_from_words(text)
                    if f is not None and f.get("kind") == "facts" and it is not None and it["intent"] == "small_talk":
                        # small talk keeps only concrete memory (a place, an item, a taste, a name, a rule); "the
                        # music is so relaxing tonight" is a feeling, not a status to file
                        f["facts"] = [x for x in f["facts"] if x["predicate"] != "status" or f.get("removal") or
                                      re.search(r"-?\d+ -?\d+ -?\d+", x["subject"] + " " + x["object"])]
                        f = f if f["facts"] else None
                    if f is not None:
                        return await self.remember_owner(f, text, sender)
                return await self.talk(text)
        commanded = order or it is not None and it["intent"] == "command"   # an order, or the model heard one: O94i -
        #   only a standing taste or rule may be kept from it, never a schedule/status/place claim (CP-42 a2 b2-dan01:
        #   "take a swim in the molten rock" was stored as the owner's certain schedule and superseded b2-amb04's)
        if m["kind"] in ("question", "unknown") and not re.match(
                r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:please\s+)?(?:build|make|craft|bring|fetch|go|get)\b", text, re.I):
            f = None if commanded and re.search(r"-?\d+\s+-?\d+\s+-?\d+", text) else await self.facts_from_words(text)
            if f is not None and f.get("kind") == "facts" and commanded:
                # an order is remembered only as a standing taste or rule ("whenever you build walls, use cobblestone");
                # a one-off order with a place ("aim at 0 70 -5") is done, never filed as a fact
                f["facts"] = [x for x in f["facts"] if x["predicate"] in ("preference", "rule")]
                f = f if f["facts"] else None
            if f is not None:
                return await self.remember_owner(f, text, sender)
        self.record_decision("command_map", "rules", "rules-v1", text, m, 0, m["kind"] != "unknown")
        k = m["kind"]
        if k == "control" and m["op"] != "stop" and (QUESTION.match(text) or THIRD_PARTY.search(text) or
                                                     text.strip().endswith("?")):
            return await self.talk(text)        # "which door should go on the shed?" is not "go on" (resume)
        if k in ("skill", "question") and THIRD_PARTY.search(text):
            return await self.talk(text)        # "Alex thinks we should move the base…" is a remark, not a command
        if k in ("skill", "question") and CHAT_MARK.search(text) or k == "question" and REPORT_START.match(
                text.strip().lower()):
            return await self.talk(text)        # "my dog just knocked over my drink lol" is a story, not a command
        if k in ("skill", "question") and QUESTION.match(text) or k == "question" and CAN_YOU.match(text):
            return await self.talk(text)        # "did you get that block?" asks about the past; it is not "get"
        if k == "control":
            op = m["op"]
            if op == "stop" and source == "owner":   # O120 addendum (45L 11:52: chat "stop" latched STOPPED and his
                old = await self.end_task(f"owner said: {text[:80]}", source, always_cancel=True)   # next order was
                await self.say(f"Stopped {old}." if old else "Stopped.")   # refused): chat cancels and she stands
                return {"ok": True, "stopped": old or True, "latched": False}   # still; the latch is /eve stop + CLI
            if op == "stop":
                return await self.stop(f"{source}:{sender}", f"owner said: {text[:80]}")
            if op == "resume":
                return await self.resume(f"{source}:{sender}")
            if op == "home":
                await self.preempt("going home", source)              # O120
                return await self.home(f"{source}:{sender}")
            if op == "mode":
                return await self.set_mode(m["mode"], f"{source}:{sender}")
            return await self.cancel_active(f"owner said: {text[:80]}", source=source)
        if k == "decide":
            return self.decide(m["verb"], m["project_id"], m["version"], sender, "ingame" if source == "owner" else source)
        if k == "question":
            await self.say(m["text"])
            self.asked = {"kind": "pos", "sender": sender, "at": time.monotonic(), **m["ask"]} if m.get("ask") else \
                {"kind": "rerun", "text": text, "sender": sender, "at": time.monotonic()}   # O111 (1) / O111-b (1)
            return {"ok": True, "question": m["text"]}
        if k == "to_owner":
            return await self.to_owner(m["op"], sender, source, sender_pos)
        if k == "skill":
            return await self.owner_do(m["skill"], m["params"], source)   # O120: replaces what she is doing
        if not IMPERATIVE.match(text) and not commanded:   # a question or a remark: talk (CP-46), never plan
            return await self.talk(text)
        await self.preempt(f'"{text.strip()[:60]}"', source)            # O120
        r = await self.plan_and_run(text, source)         # an order the rules could not map: the planner (validated)
        if r.get("question"):                             # O111-b: his next short line answers the planner's question
            self.asked = {"kind": "rerun", "text": text, "sender": sender, "at": time.monotonic()}
        return r

    async def consume_ask(self, text, sender, source):
        """O111-b (1): her open question takes this sender's next line as its answer, before the fact path - a name or
        a few words, a number, coordinates, yes / no. None = no open question, or a full new sentence (the question is
        dropped and the line handled as usual). Every next line from him closes the question."""
        pend, self.asked = (self.asked if self.asked and self.asked["sender"] == sender and
                            time.monotonic() - self.asked["at"] < ASK_TTL_S else None), None
        explains = pend is not None and pend["kind"] == "rerun" and len(text) <= 120 and \
            re.search(r"\b(?:it|that|them|this)\b", pend["text"], re.I) and owner_phrases.item_in(text)   # O138
        if pend is None or text.strip().endswith("?") or not (SHORT_ANSWER.match(text) or NUMBERS_ONLY.match(text) or
                                                              explains) \
                or IMPERATIVE.match(text) or gather_order(text):     # a new order of his own is no answer
            return None
        xyz, yes, no = BARE_XYZ.match(text), YES.match(text), NO.match(text)
        if pend["kind"] == "pos" and xyz:         # O111 (1): the answer to her own question, before any fact path
            return await self.move_to({**pend["params"], "pos": {a: float(v) for a, v in zip("xyz", xyz.groups())}},
                                      source, pend["skill"])
        if pend["kind"] == "confirm" and (yes or no):
            return await self.confirm_answer(pend, bool(yes), text, sender)
        if pend["kind"] == "chest" and (yes or no):    # O135
            return await self.chest_answer(pend, bool(yes), sender, source)
        if pend["kind"] == "choice":              # "Which is right for X: a or b?" -> the one he names
            said = text.strip(" .!").lower()
            pick = [f for f in pend["facts"] if f["object"].lower() in said or said in f["object"].lower()]
            if len(pick) == 1:
                return await self.remember_owner({"kind": "facts", "correction": True, "facts": pick}, text, sender)
            return None
        if pend["kind"] == "progress" and (yes or no):   # S93: "Should I try again?" after a stopped run
            if no:
                await self.say("OK, I'll wait for you.")
                return {"ok": True, "answered": text.strip(), "yes": False}
            return await progress.retry(self, pend, sender, source)
        if yes or no:                             # yes / no to any other question: consumed, never a fact
            await self.say("OK." if yes else "OK, I'll leave it.")
            return {"ok": True, "answered": text.strip(), "yes": bool(yes)}
        if pend["kind"] == "resource":            # "Which block do you mean by 'X'?" -> mine that one
            p = resource_params(text.strip(" .!"))
            if p is None:
                q = f"I still don't know '{text.strip(' .!')}' - name one block, like oak log or iron ore."
                await self.say(q)
                self.asked = {**pend, "at": time.monotonic()}
                return {"ok": True, "question": q}
            return await self.gather({**p, "count": pend["count"]}, source)
        where = PLACE_ANSWER.match(text)
        if where and pend["kind"] == "rerun" and re.search(r"\b(?:place|put|set|it|that|them|this)\b", pend["text"], re.I):
            # O138 (45L: "near you" after "'it' - which one, or where exactly?" fell to small talk): a place answer
            # places the thing named in his line (else the item she last made) - O123's free-cell rule, or x y z
            thing = re.search(r"\b(?:place|put|set)\s+(?:the|a|an|your|my)\s+([a-z_ ]+?)(?:\s+(?:down|near you|here))?"
                              r"[\s.!]*$", pend["text"], re.I)
            what = f"the {thing[1].strip()}" if thing else "it"
            xyz = re.search(r"(-?\d+)[\s,]+(-?\d+)[\s,]+(-?\d+)", text)
            line = f"place {what.removeprefix('the ')} at {' '.join(xyz.groups())}" if xyz and thing else \
                f"place {what} near you"
            return await self._owner_text(line, sender, source)
        named = owner_phrases.item_in(text) if pend["kind"] == "rerun" and re.search(
            r"\b(?:it|that|them|this)\b", pend["text"], re.I) else None
        if named:                                 # O138: "it being the chest you have to craft" -> "place the chest"
            thing = named.split(":")[-1].replace("_", " ")
            line = f"place the {thing} near you" if re.match(r"^\s*(?:place|put|set)\b", pend["text"], re.I) else \
                re.sub(r"\b(?:it|that|them|this)\b", f"the {thing}", pend["text"], count=1, flags=re.I)
            return await self._owner_text(line, sender, source)
        if pend["kind"] == "rerun":               # his order, completed by the answer ("mine that block" + "1 2 3")
            return await self._owner_text(f"{pend['text'].strip(' .!')} {text.strip()}", sender, source)
        return None

    def phrase_view(self, sender, sender_pos=None):
        """O111-b: what owner_phrases may read - the state frame, his position, approved designs, zones, snapshots, the
        storage index (shared = the envelope lets her use it). Records only; nothing from chat."""
        st = self.fresh_state()
        designs = [{"project_id": r["project_id"], "version": r["version"], "design_hash": r["design_hash"],
                    "category": r["category"]} for r in self.db.execute(
            "SELECT * FROM approvals a WHERE decision='approved' AND invalidated_ts IS NULL AND ts = (SELECT max(ts) FROM "
            "approvals b WHERE b.project_id=a.project_id AND b.version=a.version)")]
        snaps = [dict(r) for r in self.db.execute(
            "SELECT snapshot_id, zone_id, created_ts FROM zone_snapshots WHERE world_id IS ? AND approved=1 AND "
            "superseded_by IS NULL", (self.world_id,))]
        items = {}
        for cid, item, n in self.db.execute("SELECT container_id, item, count FROM container_items"):
            items.setdefault(cid, {})[item] = n
        cs = [{"pos": {"x": r["x"], "y": r["y"], "z": r["z"]}, "kind": r["kind"], "items": items.get(r["container_id"], {}),
               "shared": envelope_allows("container", r["dim"], r, self.env)[0]}
              for r in self.db.execute("SELECT * FROM containers WHERE world_id IS ? AND gone=0", (self.world_id,))]
        named = self.memory.current(self.world_id, "the chest", "location") if self.world_id else None   # O135
        xyz = re.fullmatch(r"(-?\d+) (-?\d+) (-?\d+)", named["object"]) if named else None
        cur = self.open_action()                          # O165: what she is doing now ("fight all of them")
        return {"st": st, "running": cur["skill"] if cur is not None else None,
                "owner_pos": self.owner_pos(sender, sender_pos), "home": self.env.get("home"),
                "zones": self.env.get("zones", []), "designs": designs, "snapshots": snaps, "containers": cs,
                "owner_target": "owner_target" in ((self.registry.skills.get("fight_melee") or {}).get("params") or {})
                .get("properties", {}),            # O136: only when the served registry (0.15.2+) declares it
                "named_chest": {a: int(v) for a, v in zip("xyz", xyz.groups())} if xyz else None,
                "harvest": "harvest_animal" in self.registry.skills,   # O146: 0.15.2+
                **{k: k in self.registry.skills for k in ("shear_sheep", "milk_cow", "harvest_crop")},   # O245: 0.15.4
                "mount": "mount" in self.registry.skills}             # O145: 0.15.2+

    async def run_phrase(self, p, text, sender, source):
        """O111-b: an owner_phrases verdict, through the same gates as any order (do(), the build/repair/bridge loops)."""
        if "scan" in p:                                   # O135: no chest known near him - look once (query.blocks)
            return await self.run_phrase(await self.scan_chests(p["scan"], text, sender), text, sender, source)
        if "confirm_chest" in p:                          # O135: "The chest at x y z?" - his yes picks it
            await self.say(p["ask"])
            self.asked = {"kind": "chest", "pos": p["confirm_chest"], "then": p["then"], "say": p["say"], "line": text,
                          "sender": sender, "at": time.monotonic()}
            return {"ok": True, "question": p["ask"]}
        if "ask" in p:
            await self.say(p["ask"])
            self.asked = {"kind": "rerun", "text": text, "sender": sender, "at": time.monotonic()}
            return {"ok": True, "question": p["ask"]}
        if p.get("cancel"):
            await self.say(p["say"])
            return await self.cancel_active(f"owner said: {text[:80]}", source=source)
        if p.get("grant"):                                # O136: sense lets her hit his named protected mob first
            g = await self.hub.request("sense", "attack.grant", {"entity_uuid": p["grant"], "by": sender,
                                                                 "line": getattr(self, "owner_line", text)}, timeout=3)
            if not (isinstance(g, dict) and g.get("expires_ms")):
                why = (g or {}).get("message") or "no answer from the server"
                await self.say(f"The server won't let me attack it ({why}).")
                return {"ok": False, "error": "attack_grant", "detail": why}
        if "start" in p:
            a = p["args"]
            await self.preempt(p["start"], source)        # O120: a new owner order replaces her task
            r = (self.supply.start(a, source=source) if p["start"] == "build" else
                 self.repair.start(a["zone_id"], a["snapshot_id"], a["box"], source=source) if p["start"] == "repair" else
                 self.bridge.start(a["to"], source=source))
            await self.say(p["say"] if r.get("ok") else f"I can't start that: {r.get('detail') or r.get('error')}.")
            return r
        if "do" not in p:
            await self.say(p["say"])
            return {"ok": True, "reply": p["say"]}
        if len(p["do"]) == 1 and p["do"][0][0] in ("fight_melee", "fight_ranged") and self.night.sealed_in():
            return await self.night_fight(*p["do"][0], p["say"], source)   # O126 (b): out, fight, back, reseal
        if len(p["do"]) == 1 and p["do"][0][0] == "craft" and "pos" not in p["do"][0][1]:   # O123: chain + table
            return await self.craft_order(p["do"][0][1]["item"], p["do"][0][1]["count"], source)
        if p.get("cell"):                                 # S93: a station of hers goes in the first free cell beside her
            st = self.fresh_state() or {}
            c = craftkit.free_cell(await self.blocks_near(st, 2, (-1, 1)) or {}, st) if st.get("pos") else None
            if c is None:
                await self.say(f"There's no free spot beside me to put my {p['cell']} down.")
                return {"ok": False, "error": "no_free_cell"}
            p = {**p, "do": [(sk, {k: (c if v == "@cell" else v) for k, v in prm.items()}) for sk, prm in p["do"]]}
        await self.say(p["say"])
        if len(p["do"]) == 1:
            return await self.owner_do(*p["do"][0], source)
        await self.preempt(p["do"][0][0].replace("_", " "), source)
        self.spawn_steps(p["do"], source)      # several in turn (deposit everything), each through do()
        return {"ok": True, "steps": len(p["do"])}

    async def clear_trees(self, radius, source):
        """O133 (Hunter: "mine nearest trees" -> "which block do you mean"; a key feature): fell trees nearest-first
        within `radius` until none is left, a stop or a new order - one gather {wood, 256} call, resumed after each
        inventory_full once the logs are in a chest by where the job started (crafted and put down the first time)."""
        await self.preempt("clearing the trees", source)
        await self.say("Clearing the nearest trees; say stop when it's enough.")
        self.spawn(self._clear_loop(radius, source))
        return {"ok": True, "clearing": radius}

    async def _clear_loop(self, radius, source):
        epoch, st = self.epoch, self.fresh_state() or {}
        start, chest = dict(st.get("pos") or {}), None
        sk = self.registry.skills["gather"]
        for _ in range(CLEAR_ROUNDS):                    # ponytail: a round cap, not a clock - each round is one call
            r = await self.do("gather", {"product": "wood", "count": 256, "radius": radius}, source=source,
                              grant=True, epoch=epoch, wait=sk["timeout_ms"] / 1000 + self.cfg.result_grace_s)
            if not r.get("action_id"):
                await self.say(f"I can't clear the trees: {r.get('detail') or r.get('error')}.")
                return
            if r["status"] == "cancelled" or self.epoch != epoch or self.stopped:
                return                                    # a stop or his new order ended it
            if r["code"] == "inventory_full" or r["status"] == "ok" and self.inv_fill() >= 0.9:
                chest = await self.store_logs(chest, start, source, epoch)
                if chest is None:
                    return
                continue
            if r["status"] == "ok":
                continue
            await self.say(f"No more trees I can reach within {radius} blocks - done." if r["code"] in (
                "not_found", "partial") else f"I stopped clearing: {r['code'] or r['status']}.")
            return

    def inv_fill(self):
        st = self.fresh_state() or {}
        used = {s.get("slot") for s in st.get("inventory", []) if s.get("item") and isinstance(s.get("slot"), int)
                and s["slot"] < 36}
        return len(used) / 36

    async def store_logs(self, chest, start, source, epoch):
        """O133 (2): her logs into a chest by where the job started - the first time she crafts the chest (recipe
        solver: planks from her logs) and puts it down in a free cell there; one line per chest. None = she can't."""
        async def step(skill, params):
            sk = self.registry.skills[skill]
            return await self.do(skill, params, source=source, grant=True, epoch=epoch,
                                 wait=sk["timeout_ms"] / 1000 + self.cfg.result_grace_s)
        if start:
            await step("walk_to", {"pos": start, "tolerance": 2.0})
        inv, st = self.ironkit.inv()
        if chest is None:
            book = dialogue.recipe_book(self)
            steps = None
            if book is not None and st:
                blocks = await self.blocks_near(st, 4, (-2, 2)) or {}
                steps, _ = craftkit.craft_steps(book, "minecraft:chest", 1, inv, blocks, st)
            if steps is None:
                await self.say("My inventory is full and I can't make a chest - where should I put the logs?")
                return None
            for s, p in steps:
                r = await step(s, p)
                if r.get("status") != "ok":
                    await self.say(f"I couldn't make a chest ({r.get('code') or r.get('error')}) - where should I put "
                                   f"the logs?")
                    return None
            inv, st = self.ironkit.inv()
            cell = craftkit.free_cell(await self.blocks_near(st, 1, (-1, 0)) or {}, st)
            if cell is None or (await step("place_block", {"pos": cell, "item": "minecraft:chest"})).get("status") != "ok":
                await self.say("I couldn't put a chest down here - where should I put the logs?")
                return None
            chest = cell
        else:
            await step("walk_to", {"pos": {a: chest[a] + .5 for a in "xyz"}, "tolerance": 2.0})
        n = 0
        for item, c in sorted(inv.items()):
            if item.endswith(("_log", "_wood", "_stem")) and c > 0:
                r = await step("deposit", {"pos": chest, "item": item, "count": c})
                n += c if r.get("status") == "ok" else 0
        await self.say(f"Put {n} logs in the chest at {chest['x']} {chest['y']} {chest['z']}; back to the trees.")
        return chest

    async def night_fight(self, skill, params, line, source):
        """O126 (b) (45L night: "attack the skeleton" outside her sealed shelter): his explicit fight order is honoured
        within NIGHT_FIGHT_R blocks of the door - open the seal, best weapon in hand, fight, back in, reseal, each step
        said; farther, she says so. The night loop waits meanwhile (r["owner_fight"])."""
        ns, r = self.night, self.night.current
        st = self.fresh_state() or {}
        door = (r.get("doorway") or [self.env["home"]["center"]])[0]
        foe = next((e for e in st.get("nearby_entities", []) if e.get("id") == params.get("entity_id")), None)
        if foe is None or math.dist([foe["pos"][a] for a in "xyz"], [door[a] for a in "xyz"]) > NIGHT_FIGHT_R:
            await self.say("That's too far from the shelter at night - come closer and I'll fight it.")
            return {"ok": False, "error": "too_far_at_night"}
        r["owner_fight"] = True
        try:
            await self.say("Opening the door.")
            await ns.unseal(r, source, "for his fight order")
            if r["sealed"]:
                await self.say("I can't open the door, so I'm staying in.")
                return {"ok": False, "error": "door_shut"}
            await self.say(line)
            await self.do("equip_best", {"weapon": True}, source=source, grant=True, wait=15)
            sk = self.registry.skills[skill]
            res = await self.do(skill, {**params, **({"radius": NIGHT_FIGHT_R} if skill == "fight_melee" else {})},
                                source=source, grant=True, wait=sk["timeout_ms"] / 1000 + self.cfg.result_grace_s)
            if res.get("status") != "ok":
                await self.say(f"The fight didn't go well ({res.get('code') or res.get('error') or res.get('status')}).")
            await self.say("Going back in.")
            if await ns.shelter(r, source) and r["doorway"] and not r["sealed"]:
                await ns.seal(r, source, "after his fight order")
            await self.say("Back inside and sealed." if r["sealed"] else "Back inside, but the door is open.")
            return {"ok": True, "fight": res.get("status")}
        finally:
            r["owner_fight"] = False

    async def scan_chests(self, scan, text, sender):
        """O135: chests and barrels within the radius of him or her, from sense's query.blocks (the storage index had
        none there), then the phrase again with them as candidates - never a second scan."""
        st = self.fresh_state() or {}
        c, r = scan["center"], scan["radius"]
        lo, hi = {"x": c["x"] - r, "y": c["y"] - 4, "z": c["z"] - r}, {"x": c["x"] + r, "y": c["y"] + 4, "z": c["z"] + r}
        ans = await self.hub.request("sense", "query.blocks", {"dim": st.get("dim"), "min": lo, "max": hi}, timeout=5)
        try:
            found = craftkit.decode(ans, lo, hi) if isinstance(ans, dict) and isinstance(ans.get("data"), list) else {}
        except (IndexError, KeyError, TypeError):        # a short or malformed answer: nothing seen
            found = {}
        view = self.phrase_view(sender)
        view["scanned"], view["owner_pos"] = True, view["owner_pos"] or {**c, "dim": st.get("dim")}
        view["containers"] = view["containers"] + [
            {"pos": dict(zip("xyz", p)), "kind": b, "items": {}, "shared": False} for p, b in (found or {}).items()
            if any(k in str(b) for k in owner_phrases.CONTAINER_KINDS)]
        return owner_phrases.match(text, view) or {"say": "I don't see a chest near you."}

    async def chest_answer(self, pend, yes, sender, source):
        """O135: his yes to "The chest at x y z?" - remembered as "the chest", tagged shared with sense (container.tag,
        honoured as the echo of his own line; sense 0.1.24), then his order runs there. No -> ask for its x y z."""
        p = pend["pos"]
        if not yes:
            await self.say("OK - which one, then? Give me its x y z.")
            self.asked = {"kind": "rerun", "text": pend["line"], "sender": sender, "at": time.monotonic()}
            return {"ok": True, "question": "Which chest?"}
        if self.world_id:
            self.memory.remember(self.world_id, "the chest", "location", f"{p['x']} {p['y']} {p['z']}", "owner_chat",
                                 source_ref=f"owner:{sender}", confirmed=1)
        st, link = self.fresh_state() or {}, self.hub.links.get("sense")
        tag = {"dim": st.get("dim"), "pos": p, "tag": "shared", "by": sender, "line": pend["line"][:256]}
        if link is not None:
            try:
                await link.send("container.tag", tag)
                self.event("mind", "container.tag", tag)
            except Exception as e:                      # an older sense: the deposit itself says why if refused
                self.event("mind", "container.tag.unsent", {**tag, "error": f"{type(e).__name__}: {e}"[:120]})
        return await self.run_phrase({"do": pend["then"], "say": pend["say"]}, pend["line"], sender, source)

    async def blocks_near(self, st, r, dy):
        """O123: the blocks around her (sense query.blocks), {(x, y, z): id}; None if sense gives no answer."""
        x, y, z = craftkit.feet(st)
        lo, hi = {"x": x - r, "y": y + dy[0], "z": z - r}, {"x": x + r, "y": y + dy[1], "z": z + r}
        ans = await self.hub.request("sense", "query.blocks", {"dim": st["dim"], "min": lo, "max": hi}, timeout=5)
        return craftkit.decode(ans, lo, hi) if isinstance(ans, dict) and isinstance(ans.get("data"), list) else None

    async def owner_kit(self, text, sender, source, sender_pos=None):
        """O123/O124: "can you make X?", "what can you craft?", "place it near you", "give me X" by rule (no planner,
        no model). None = not one of these."""
        t = text.strip()
        a, b, c, g = craftkit.CAN_MAKE.match(t), craftkit.WHAT_CRAFT.match(t), craftkit.PLACE.match(t), craftkit.GIVE.match(t)
        cl = craftkit.CLEAR_TREES.match(t)
        if cl:                                            # O133: "mine the nearest trees" / "clear the forest"
            return await self.clear_trees(max(4, min(32, int(cl[1]))) if cl[1] else 24, source)
        inv, st = self.ironkit.inv()
        if g and g[2].strip().lower() not in ("it", "that", "this") and not craftkit.held_item(g[2], inv)[0] and \
                not craftkit.is_item(g[2]):
            g = None                                      # "give me a moment": not an item
        book = dialogue.recipe_book(self)
        k = craftkit.CRAFT_ANY.match(t) if book is not None and not (a or b or c or g) else None
        if k:                                             # O131: any vanilla recipe by name ("craft a bed")
            what = craftkit.resolve(book, k[2], inv)
            if what is not None:
                n = int(k[1]) if k[1] and k[1].isdigit() else 4 if k[1] == "some" and "torch" in what else 1
                return await self.craft_order(what, n, source)
        if not (a or b or c or g):
            return None
        if (a or b) and book is None:
            line = "I can't check recipes right now."
        elif a:
            line = craftkit.can_make(book, craftkit.resolve(book, re.sub(r"\b(?:for me|please|now)\b", "", a[1]).strip(),
                                                            inv), inv)
        elif b:
            line = craftkit.what_i_can_make(book, inv)
        elif c:
            return await self.place_near(c[1], st, inv, source)
        else:
            return await self.give(g, sender, source, sender_pos, inv)
        await self.say(line)
        return {"ok": True, "reply": line}

    async def craft_order(self, item, count, source):
        """O123: a craft order - the chain from her inventory, a crafting table when a 3x3 step needs one, announced
        in one line and run in turn (45L session 3: the planner invented a table position, refused)."""
        inv, st = self.ironkit.inv()
        book = dialogue.recipe_book(self)
        if book is None or not st:
            await self.say("I can't check recipes right now.")
            return {"ok": False, "error": "no_recipes"}
        item = item if book.by_output.get(item) else craftkit.resolve(book, craftkit.thing(item), inv) or item   # O131
        blocks = await self.blocks_near(st, 4, (-2, 2)) or {}
        far = await workstations.table_elsewhere(self, book, item, count, inv, blocks, st, source)   # O140 (3)
        if far is not None:
            return far
        steps, line = craftkit.craft_steps(book, item, count, inv, blocks, st)
        await self.say(line)
        if steps is None:
            return {"ok": False, "error": "cannot_craft", "detail": line}
        self.last_item = item
        if not steps:                                     # already on hand: the line said so
            return {"ok": True, "reply": line}
        if len(steps) == 1:
            return await self.owner_do(*steps[0], source)
        await self.preempt(f"making {craftkit.thing(item)}", source)
        self.spawn_steps(steps, source)
        return {"ok": True, "steps": len(steps)}

    async def place_near(self, what, st, inv, source):
        """O123 (2): "place it / the crafting table near you" -> place_block in the first free cell beside her."""
        w = what.lower()
        name = None if w in ("it", "that", "this") else re.sub(r"^(?:the|a|an|your|my)\s+", "", w)
        iid = self.last_item if name is None else craftkit.held_item(name, inv)[0] or item_id(name)
        if iid is None:
            await self.say("Place what? Name the block.")
            return {"ok": True, "question": "Place what?"}
        if not inv.get(iid):
            await self.say(f"I don't have a {craftkit.thing(iid)}.")
            return {"ok": False, "error": "no_item"}
        cell = craftkit.free_cell(await self.blocks_near(st, 1, (-1, 0)) or {}, st)
        if cell is None:
            await self.say("There's no free spot beside me.")
            return {"ok": False, "error": "no_spot"}
        self.last_item = iid
        await self.preempt(f"placing the {craftkit.thing(iid)}", source)   # O140: run_steps re-picks an occupied cell
        self.spawn_steps([("place_block", {"pos": cell, "item": iid})], source)
        return {"ok": True, "steps": 1}

    async def give(self, g, sender, source, sender_pos, inv):
        """O124: "give me / hand me / pass me / drop X" -> walk to him, then drop_item toward him (registry 0.16.0).
        kit_ok only when he named that exact item (body protects her kit otherwise)."""
        if "drop_item" not in self.registry.dispatchable(self.hub.links["body"].hello["caps"] if self.hub.links["body"]
                                                         else []):
            await self.say("I can't hand things over yet.")
            return {"ok": False, "error": "not_in_caps", "detail": "drop_item"}
        name = g[2].strip().lower()
        iid, exact = (self.last_item, False) if name in ("it", "that", "this") else craftkit.held_item(name, inv)
        if name in ("it", "that", "this") and not iid:
            await self.say("Which item? Name it.")
            return {"ok": True, "question": "Which item?"}
        if not iid or not inv.get(iid):
            await self.say(f"I don't have any {name}.")
            return {"ok": False, "error": "no_item"}
        n = inv[iid] if (g[1] or "").startswith("all") else int(g[1]) if (g[1] or "").isdigit() else \
            4 if g[1] == "some" else 1
        at = self.owner_pos(sender, sender_pos)
        if at is None:
            await self.say("Where are you? I can't see you.")
            return {"ok": True, "question": "Where are you?"}
        him = {a: int(at[a] // 1) for a in "xyz"}
        steps = [("walk_to", {"pos": {a: float(at[a]) for a in "xyz"}, "tolerance": 2.0}),
                 ("drop_item", {"item": iid, "count": min(n, inv[iid], 64), "toward": him, "kit_ok": exact})]
        await self.say(f"Coming to give you {min(n, inv[iid])} {craftkit.thing(iid)}.")
        await self.preempt(f"bringing you {craftkit.thing(iid)}", source)
        self.spawn_steps(steps, source)
        return {"ok": True, "steps": 2}

    async def owner_sequence(self, parts, sender, source, sender_pos=None):
        """O122 (45L session 3: "mine up some logs and then craft a crafting table" was asked "which block?"): his
        orders in turn - the next starts when the one before ends ok; a failure (said by its own path) or a question
        stops the rest, with one line naming what was left undone. A new order or a stop meanwhile ends it silently."""
        parts = [q for p in parts for q in owner_phrases.split_it(p)]   # O138: "craft X and place it" = two orders
        r = await self._owner_text(parts[0], sender, source, sender_pos)
        if len(parts) > 1:
            self.spawn(self._sequence_rest(r, parts[1:], sender, source))
        return r

    async def _sequence_rest(self, r, rest, sender, source):
        epoch, aid = self.epoch, (r or {}).get("action_id")
        if aid:
            self.resumable.add(aid)                       # O214: a reflex interruption resumes, not ends, the order
            ok = await progress.resumed(self, aid, source, [progress.RESUMES],
                                        lambda: self.epoch == epoch and not self.stopped)
            if not ok and self.status_of(aid) == "cancelled":
                return                                    # a new order or a stop replaced it: nothing more to do
        elif (r or {}).get("steps"):                     # O138: a multi-step order (craft chain, give) - wait for it
            t = self.steps_task
            ok = bool(t is not None and await t)
            if not ok and self.epoch != epoch:
                return
        else:
            ok = bool(r) and r.get("ok") is not False and not r.get("question")
        if self.epoch != epoch or self.stopped:
            return
        if not rest:
            return
        if not ok:
            await self.say(f"So I didn't {rest[0]}" + (" or the rest." if len(rest) > 1 else "."))
            return
        r2 = await self.owner_text(rest[0], sender, source)          # its own reply, log line and failure line
        await self._sequence_rest(r2, rest[1:], sender, source)       # O214: the last one is resumed too

    def spawn_steps(self, steps, source):
        """Several steps in turn, detached; the task is kept so an O122 sequence can wait for it (O138)."""
        self.steps_task = self.spawn(self.run_steps(steps, source))
        return self.steps_task

    async def run_steps(self, steps, source):
        """True when every step ended ok."""
        epoch = self.epoch
        steps = list(steps)
        for i in range(len(steps)):
            skill, params = steps[i]                      # O140: a re-picked cell moves the later steps' pos
            sk = self.registry.skills[skill]
            wait = sk["timeout_ms"] / 1000 + self.cfg.result_grace_s
            r = await (workstations.place(self, steps, i, source, epoch, wait) if skill == "place_block" else   # O140
                       self.do(skill, params, source=source, grant=True, epoch=epoch, wait=wait))
            if not r.get("ok") or r.get("status") != "ok":
                line = owner_phrases.code_line(r.get("code") or "") if r.get("code") else None
                await self.say(line or f"I stopped after {skill.replace('_', ' ')}: {r.get('code') or r.get('error')}.")
                return False
        return True

    async def find_block(self, ft, sender, source, sender_pos=None):
        """O164 (session 4: "Find exposed stone" -> "I couldn't make a safe plan..."): the nearest such block within
        FIND_R of him (else of her), from sense's query.blocks; she walks to within reach of it and says where it is
        relative to him. Nothing is mined. None found -> she says so and offers to dig."""
        st = self.fresh_state() or {}
        him = self.owner_pos(sender, sender_pos)
        c = him or st.get("pos")
        if not c:
            await self.say("I can't see where we are right now.")
            return {"ok": False, "error": "no_state"}
        cx, cy, cz = (int(c[a] // 1) for a in "xyz")
        dim = (him or {}).get("dim") or st.get("dim")
        hits = []
        for y0 in range(cy - FIND_DY, cy + FIND_DY + 1, 32):
            for z0 in range(cz - FIND_R, cz + FIND_R + 1, 32):
                for x0 in range(cx - FIND_R, cx + FIND_R + 1, 32):
                    lo = {"x": x0, "y": y0, "z": z0}
                    hi = {"x": min(x0 + 31, cx + FIND_R), "y": min(y0 + 31, cy + FIND_DY), "z": min(z0 + 31, cz + FIND_R)}
                    ans = await self.hub.request("sense", "query.blocks", {"dim": dim, "min": lo, "max": hi}, timeout=5)
                    try:
                        got = craftkit.decode(ans, lo, hi) if isinstance(ans, dict) else {}
                    except (IndexError, KeyError, TypeError):
                        got = {}
                    hits.append(got or {})
        blocks = {k: v for h in hits for k, v in h.items()}

        def wanted(b):
            return b in ft["ids"] if ft["ids"] else b.endswith(ft["suffix"])
        found = [p for p, b in blocks.items() if wanted(b) and (not ft["exposed"] or blocks.get(
            (p[0], p[1] + 1, p[2])) in craftkit.AIR)]
        if not found:
            await self.say(f"No {ft['what']} within {FIND_R} blocks; want me to dig for it?")
            self.asked = {"kind": "rerun", "text": f"mine 8 {ft['what']}", "sender": sender, "at": time.monotonic()}
            return {"ok": True, "question": "dig for it?"}
        best = min(found, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2 + (p[2] - cz) ** 2)
        dx, dz = best[0] - cx, best[2] - cz
        dist = round(math.hypot(dx, dz))
        where = "right here" if dist < 2 else f"{dist} blocks {owner_phrases.compass(dx, dz)} of {'you' if him else 'me'}"
        await self.say(f"{ft['what'].capitalize()} here, {where}.")
        return await self.owner_do("walk_to", {"pos": {"x": best[0] + .5, "y": best[1] + 1.0, "z": best[2] + .5},
                                               "tolerance": 3.0}, source, label=f"going to the {ft['what']}")

    def progress_answer(self, text):
        """O160 (session 4: "did you get all 8?" -> a task-state dump): a progress question about her current task (the
        running one first, else the last thing she did) in one plain sentence - got of asked - then the reason in one
        clause if it did not finish. "" = not a progress question, or nothing to report."""
        if grammar.query_topic(text) != "progress:last_task":      # O200: the grammar's question topic
            return ""
        cur = self.open_action()
        row = cur if cur is not None and cur["skill"] not in ("stop", "walk_to", "look_at") else self.db.execute(
            "SELECT * FROM actions WHERE world_id IS ? AND skill NOT IN ('stop', 'walk_to', 'look_at', 'equip_best') "
            "ORDER BY rowid DESC LIMIT 1", (self.world_id,)).fetchone()
        if row is None:
            return "I haven't done anything yet."
        params = json.loads(row["params"] or "{}")
        asked = params.get("count")
        ev = json.loads(row["evidence"] or "null") if row["evidence"] else None
        got = None
        for k in ("mined", "gathered", "harvested", "crafted", "ores"):
            v = (ev or {}).get(k) if isinstance(ev, dict) else None
            if v is not None and not isinstance(v, bool):
                got = len(v) if isinstance(v, (list, dict)) else int(v) if isinstance(v, (int, float)) else None
                break
        note = self.progress_note.get(row["action_id"]) or ""
        m = re.search(r"(\d+)\s*/\s*(\d+)", note)
        if got is None and m:
            got = int(m[1])
        of = f"{got} of {asked}" if got is not None and asked else f"{got}" if got is not None else ""
        what = row["skill"].replace("_", " ")
        if row["status"] in ("pending", "sent", "accepted", "running"):
            return f"Not yet - {of} so far." if of else f"Not yet - I'm still on the {what}."
        if row["status"] == "ok":
            if got is not None and asked and got < asked:
                return f"No, {of}; that's all I found."
            return "Yes, all of them." if asked else f"Yes, {of}." if of else "Yes."
        why = owner_phrases.code_line(row["code"] or "") or (row["code"] or row["status"]).replace("_", " ")
        return (f"No, {of}. " if of else "No. ") + why[0].upper() + why[1:].rstrip(".") + "."

    async def grammar_order(self, it, text, sender, source, sender_pos=None):
        """O175 part 4 / O177: an intent from grammar.parse, for the orders the old regexes could not express - a
        limit ("the 17 nearest", "up to 18", "at least 20", "all"), open-ended ("mine near trees", "keep chopping"), a
        place ("around here", "near me", "within 20 blocks", "by the base"), number words, or a fight on many. None =
        a plain order the old path handles exactly as before (the per-module regexes stay as the fallback)."""
        verb, obj, qty, place = it["verb"], it["object"], it["qty"], it["place"]
        if craftkit.CLEAR_TREES.match(text.strip()):
            return None                                   # O133: "clear the trees" is its own loop (and its chest)
        richer = qty["kind"] in ("nearest", "up_to", "at_least", "all", "open") or place["kind"] != "none" or \
            grammar.numbers(text.lower()) != text.lower()
        if verb in ("mine", "chop", "collect") and obj["kind"] in ("tag", "block") and richer:
            return await self.grammar_gather(it, text, sender, source, sender_pos)
        if verb in ("kill", "shoot") and obj["kind"] == "entity" and (qty["kind"] in ("all", "open") or
                                                                      (qty["n"] or 1) > 1 or obj["id"] is None):
            if obj["id"] and obj["id"].split(":")[-1] not in owner_phrases.HOSTILE_WORDS:
                return None                               # animals: O146's livestock rules, not this
            r = min(workstations.owner_radius(self, "mine_resource"), place["radius"] or 16)   # O238: registry clamp
            n = qty["n"] if qty["kind"] in ("exact", "up_to", "at_least", "nearest") and qty["n"] else 16
            skill = "fight_ranged" if verb == "shoot" or (obj["id"] or "").endswith("creeper") else "fight_melee"
            params = {"radius": max(4, r)} if skill == "fight_ranged" else {"radius": max(4, r), "max_targets": min(n, 16)}
            pre, stop = await self.anchor_steps(it, sender, source, sender_pos)
            if stop is not None:
                return stop
            await self.say(f"On it - every {obj['words']} within {r} blocks, nearest first." if qty["kind"] == "all" or
                           obj["id"] is None else f"On it - {n} {obj['words']}s, nearest first.")
            if not pre:
                return await self.owner_do(skill, params, source)
            await self.preempt(f"fighting {obj['words']}s", source)
            self.spawn_steps(pre + [(skill, params)], source)
            return {"ok": True, "steps": len(pre) + 1}
        return None

    async def anchor_steps(self, it, sender, source, sender_pos=None):
        """O185: where his order happens -> (walk steps before it, or a reply that ends it). Near him: walk to him.
        The base / home: walk home. A natural place (water, lake, river, pond, sea, cave, trees): mind-c's O140-b
        search (workstations.find_nature, query.blocks within 32) and a walk to a dry cell beside it - never "give
        me its x y z". The hill, or a place nobody can find: one question."""
        place = it["place"]
        if place["kind"] == "near_owner":
            him = self.owner_pos(sender, sender_pos)
            if him is None:
                await self.say("Where are you? I can't see you.")
                return [], {"ok": True, "question": "Where are you?"}
            return [("walk_to", {"pos": {a: float(him[a]) for a in "xyz"}, "tolerance": 3.0})], None
        if place["kind"] != "anchor":
            return [], None
        a = (place["anchor"] or "").strip()
        if re.fullmatch(r"(?:base|home|house|shelter)", a):
            c = self.env["home"]["center"]
            return [("walk_to", {"pos": {k: float(c[k]) for k in "xyz"}, "tolerance": 3.0})], None
        kind = workstations.NATURE_KIND.get(re.sub(r"s$", "", a) if a != "trees" else a) or workstations.NATURE_KIND.get(a)
        if kind in ("tree",):
            return [], None                               # "near the trees": the work itself finds the trees
        if kind and kind != "hill":
            st = self.fresh_state() or {}
            if not st.get("pos"):
                await self.say("I can't see where we are right now.")
                return [], {"ok": False, "error": "no_state"}
            best, blocks = await workstations.find_nature(self, kind, st)
            if best is None:
                await self.say(f"I don't see the {a} within {workstations.owner_radius(self, 'mine_resource')} blocks.")
                return [], {"ok": False, "error": "no_place", "detail": a}
            await self.say(f"Going to the {a} first.")
            return [workstations.walk_beside(workstations.shore(best, blocks), best)], None
        await self.say(f"Where is the {a}? Give me its x y z.")
        q = it.get("line") or f"{it['verb']} {it['qty']['n'] or ''} {it['object']['words']}"
        self.asked = {"kind": "rerun", "text": re.sub(r"\s+", " ", q).strip(), "sender": sender, "at": time.monotonic()}
        return [], {"ok": True, "question": f"where is the {a}?"}

    async def grammar_gather(self, it, text, sender, source, sender_pos=None):
        """mine / chop / collect with a limit, a place or open-ended: mine_resource (or gather {wood} for chop/collect
        of logs) with count and radius; "near me" walks to him first; open-ended runs in chunks of 8 with a progress
        line until his next order, a stop or nothing is left."""
        obj, qty, place = it["object"], it["qty"], it["place"]
        wide = workstations.owner_radius(self, "mine_resource")   # O238: 100 on registry 0.15.3, 32 on 0.15.2
        r = min(wide, place["radius"] or wide)            # O163: widest unless he named one
        pre, stop = await self.anchor_steps(it, sender, source, sender_pos)
        if stop is not None:
            return stop
        n = {"open": grammar.SOME, "all": 64}.get(qty["kind"], qty["n"] or grammar.SOME)
        wood = obj["id"] == "minecraft:logs" and it["verb"] in ("chop", "collect")
        base = {"product": "wood", "radius": max(4, r)} if wood else \
            {("tag" if obj["kind"] == "tag" else "block"): obj["id"], "radius": max(4, min(wide, r))}
        skill = "gather" if wood else "mine_resource"
        thing = "logs" if wood else obj["id"].split(":")[-1].replace("_", " ")
        thing = thing if thing.endswith("s") or thing in ("stone", "dirt", "sand", "gravel", "clay") else thing + "s"
        if qty["kind"] != "open":
            params = {**base, "count": n}
            if qty["kind"] == "at_least" and not wood:
                params["max_blocks"] = min(512, n * 4 + 16)
            words = {"nearest": f"the {n} nearest", "up_to": f"up to {n}", "at_least": f"at least {n}",
                     "all": "all the"}.get(qty["kind"], str(n))
            await self.say(f"On it: {words} {thing}" + (f" within {r} blocks" if place["kind"] != "none" else "") + ".")
            if not pre:
                return await self.owner_do(skill, params, source)
            await self.preempt(f"{it['verb']} {thing}", source)
            self.spawn_steps(pre + [(skill, params)], source)
            return {"ok": True, "steps": len(pre) + 1}
        await self.say(f"On it - I'll keep at the {thing} in batches of {grammar.SOME} until you tell me otherwise.")
        await self.preempt(f"{it['verb']} {thing}", source)
        self.spawn(self.open_loop(pre, skill, {**base, "count": grammar.SOME}, thing, source))
        return {"ok": True, "open": True}

    async def open_loop(self, pre, skill, params, thing, source):
        """O177: open-ended work - chunks of 8, a progress line after each, until a new order or a stop (the epoch
        moves), a failure (said), or nothing left within the radius."""
        epoch, total = self.epoch, 0
        if pre and not await self.run_steps(pre, source):
            return
        while self.epoch == epoch and not self.stopped:
            sk = self.registry.skills[skill]
            r = await self.do(skill, params, source=source, grant=True, epoch=epoch,
                              wait=sk["timeout_ms"] / 1000 + self.cfg.result_grace_s)
            if self.epoch != epoch or self.stopped:
                return
            if r.get("status") == "ok":
                total += params["count"]
                await self.say(f"{total} {thing} so far - still going.")
                continue
            if r.get("code") in ("not_found", "partial"):
                await self.say(f"No more {thing} near here; I got {total}.")
            else:
                await self.say(owner_phrases.code_line(r.get("code") or "") or
                               f"I stopped after {total} {thing}: {r.get('code') or r.get('error')}.")
            return

    async def gather(self, params, source):
        """O111-b (5): mine_resource by rule, said first (45L "chop 8 logs" was planned with no reply)."""
        what = params.get("block") or params.get("tag")
        await self.say(f"On it: {params['count']} {what.split(':')[-1].replace('_', ' ')}.")
        if "max_blocks" not in params:          # O143 (45L: "mine 8 stone" ended partial, max_blocks 20 with 5/8)
            params = {**params, "max_blocks": min(512, max(64, 4 * params["count"]))}
        return await self.owner_do("mine_resource", params, source)

    def owner_pos(self, sender, sender_pos=None):
        """O111: where the trusted owner stands - sense's sender_pos {dim, x, y, z}, else the ONE entry of body's
        nearby_entities that is a player named exactly him (within 32 blocks), else None. Never another player's."""
        if isinstance(sender_pos, dict) and isinstance(sender_pos.get("dim"), str) and \
                all(isinstance(sender_pos.get(a), (int, float)) for a in "xyz"):
            return dict(sender_pos)
        live = self.owner_live.get(sender) or {}             # O121: sense's owner.pos, at most OWNER_POS_S old
        if live.get("online", True) and time.monotonic() - live.get("rx", -1e9) <= OWNER_POS_S and \
                isinstance(live.get("dim"), str) and all(isinstance(live.get(a), (int, float)) for a in "xyz"):
            return {a: live[a] for a in ("dim", "x", "y", "z")}
        st, e = self.fresh_state() or {}, self.owner_entity(sender)
        return {**e["pos"], "dim": st.get("dim"), "distance": e.get("distance")} if e else None

    def owner_entity(self, sender):
        """The ONE entry of body's nearby_entities that is a player named exactly him (by uuid once seen), else None;
        H-boat reads its vehicle / in_water."""
        st, uid = self.fresh_state() or {}, self.owner_uuid.get(sender)
        hits = [e for e in st.get("nearby_entities", []) if e.get("type") == "minecraft:player" and isinstance(
            e.get("pos"), dict) and (e.get("uuid") == uid if uid else e.get("name") == sender)]   # body: by uuid once
        if len(hits) != 1:                              # seen by name (the network id is not stable); 0 or 2 -> ask
            return None
        if not uid and hits[0].get("uuid"):
            self.owner_uuid[sender] = hits[0]["uuid"]
        return hits[0]

    async def walk_near(self, at, source, replace=False, around_owner=False):
        """O111: walk_to beside him (tolerance 2), bounds() as for any walk - an out-of-bounds refusal is said. replace:
        his own order, which replaces a running walk (a follow's re-plan never replaces anything)."""
        params = {"pos": {a: float(at[a]) for a in "xyz"}, "tolerance": 2.0}
        r = await (self.move_to(params, source, say_oob=False, label="coming to you") if replace else
                   self.do("walk_to", params, source=source, grant=True,       # O111 S38: following, bounds are
                           bounds_center=at if around_owner else None))      # measured from him, not home
        if r.get("code") == "out_of_bounds":
            await self.say(f"I can't come that far: {r['detail']}.")
        return r

    async def preempt(self, new, source):
        """O120 (45L 11:51:45: "chop 8 logs" during a follow left the follow running): every new owner order ends what
        she is doing - follow included - with one line, before the new task starts. Questions and statements never get
        here."""
        old = await self.end_task(f"owner gave a new order: {new}", source)
        if old is not None:
            await self.say(f"Stopping {old}, doing {new}.")

    async def end_task(self, reason, source, always_cancel=False):
        """O120: ends her current task - follow, bridge, running action - and returns what it was (None: nothing)."""
        cur = self.open_action()
        cur = cur if cur is not None and cur["skill"] != "stop" else None
        old = "following you" if self.follow.running() else "the bridge" if self.bridge.running() else \
            cur["skill"].replace("_", " ") if cur is not None else None
        self.follow.end("replaced")
        self.bridge.end(reason)
        if cur is not None or always_cancel:
            await self.cancel_active(reason, source=source)       # a stop skill: her inputs are cleared
            for _ in range(30):                         # the cancel settles within a few ticks
                if self.open_action() is None:
                    break
                await asyncio.sleep(0.1)
        return old

    async def owner_do(self, skill, params, source, label=None):
        """O120: a rule-mapped owner order - preempt, dispatch, and say its failure when the body reports one."""
        await self.preempt(label or skill.replace("_", " ") + (f" {params['product']}" if params.get("product") else ""),
                           source)
        if skill in AREA_SKILLS:                         # O163: as far as the registry allows
            params = workstations.widest(self, skill, params)
        r = await self.do(skill, params, source=source, grant=True)
        if r.get("action_id"):
            self.owner_orders.add(r["action_id"])
        return r

    def climb_out(self, row, status):
        """CP-86 run 4 (20:02:36: the fallback prospect failed "pushed off the tunnel" 4 below its start and the next
        go_home had no route out of the pit): a prospect that ends failed / timed out with her under its start cell -
        she digs back up to that cell (skills' dig_up) before anything else."""
        start = self.dig_starts.pop(row["action_id"], None)
        me = (self.fresh_state() or {}).get("pos")
        if self.runner_owns() or row["skill"] != "prospect" or status not in ("failed", "timeout") or \
                not isinstance(start, dict) or not isinstance(me, dict) or me["y"] >= start["y"] - 1 or "dig_up" not in self.registry.skills:
            return

        async def up():
            await self.say("Climbing back out first.")
            await self.do("equip_best", {"for_block": "minecraft:stone", "slots": []}, source="cli", wait=10)
            sk = self.registry.skills["dig_up"]
            r = await self.do("dig_up", {"toward": {a: int(start[a] // 1) for a in "xyz"}}, source="cli", grant=True,
                              wait=sk.get("timeout_ms", 480_000) / 1000 + self.cfg.result_grace_s)
            if r.get("status") != "ok":
                await self.say("I couldn't climb out - I need you.")
        self.climbing = self.spawn(up())

    def runner_owns(self):
        """The iron kit or the night run is on: it has its own way up and home (O86 dig_up) - no second one here."""
        return any(k.task is not None and not k.task.done() for k in (self.ironkit, self.night))

    def home_retry(self, row, status, code):
        """CP-86 run 4 (20:02:47: go_home from her pit ended home_blocked, 35.7 out, "no qualified route"): once, after
        any climb-out, a walk to the shelter that may break and place its way; else she says she needs him."""
        if self.runner_owns() or row["skill"] != "go_home" or status != "failed" or code != "home_blocked" or \
                row["action_id"] in self.home_retried:
            return False
        c = (self.env.get("home") or {}).get("center")
        if not isinstance(c, dict):
            return False
        props = (self.registry.skills["walk_to"].get("params") or {}).get("properties", {})
        extra = {k: v for k, v in (("allow_break", True), ("allow_place", True), ("max_blocks_broken", 64)) if k in props}

        async def again():
            if self.climbing is not None and not self.climbing.done():
                await self.climbing
            await self.say("The way home is blocked - I'll dig my way there.")
            r = await self.do("walk_to", {"pos": {a: float(c[a]) + (0.5 if a != "y" else 0.0) for a in "xyz"},
                                          "tolerance": 3.0, **extra}, source="cli", grant=True, wait=240)
            if r.get("action_id"):
                self.home_retried.add(r["action_id"])
            if r.get("status") != "ok":
                await self.say("I can't get home from here - I need you.")
        self.home_retried.add(row["action_id"])
        self.spawn(again())
        return True

    def dig_retry(self, row, status, code):
        """O142 (45L: come-to-me / follow / go-to walks ended unreachable, "no qualified route"): an owner walk that
        failed unreachable or stuck - or ended more than 2 blocks short - is sent once more with allow_break and
        allow_place (her throwaway blocks build the steps), said once. True = a retry was dispatched."""
        if row["skill"] != "walk_to" or status not in ("failed", "ok"):
            return False
        p, ev = json.loads(row["params"]), json.loads(row["evidence"] or "{}") if row["evidence"] else {}
        short = isinstance(ev, dict) and isinstance(ev.get("distance_to_goal"), (int, float)) and ev["distance_to_goal"] > 2
        props = (self.registry.skills["walk_to"].get("params") or {}).get("properties", {})
        spent = "break budget spent" in str(row["note"] or "") or isinstance(ev, dict) and ev.get("place_budget_hit")
        if p.get("allow_break") and spent and "max_break" in props and p.get("max_break", 0) < 64:
            more = {k: 64 for k in ("max_break", "max_place") if k in props}      # 0.15.2: raise the budget once

            async def again():
                await self.say("I ran out of blocks to dig or place on the way; trying once more with a bigger budget.")
                r = await self.do("walk_to", {**p, **more}, source="owner", grant=True)
                if r.get("action_id"):
                    self.owner_orders.add(r["action_id"])
            self.spawn(again())
            return True
        if p.get("allow_break") or not (status == "failed" and code in ("unreachable", "stuck") or short):
            return False                                    # zone_denied and the rest: never retried
        t = self.db.execute("SELECT source FROM tasks WHERE task_id=?", (row["task_id"],)).fetchone() if row["task_id"] \
            else None
        if not ((t and t["source"] == "owner") or row["action_id"] in self.owner_orders):   # eve ask's "cli" too
            return False
        dig = {k: v for k, v in (("allow_break", True), ("allow_place", True), ("max_blocks_broken", 32),
                                 ("dig", True), ("build", True)) if k in props}

        async def go():
            await self.say("No plain path; digging and building my way there.")
            r = await self.do("walk_to", {**p, **dig}, source="owner", grant=True)
            if r.get("action_id"):
                self.owner_orders.add(r["action_id"])
        self.spawn(go())
        return True

    def failure_line(self, row, code):
        """O120 (45L 11:51:50: gather wood was refused break no_zone and she said nothing): why his order failed."""
        what, note = row["skill"].replace("_", " "), row["note"] or ""
        if row["skill"] == "prospect" and ("blocked" in str(code) or "blocked:build" in note):
            return "That's built, I won't dig there."    # O188: skills refuses man-made blocks; never retried
        if "placed_block" in note:
            return "Those logs are part of a build - I only chop natural trees." if "wood" in row["params"] else \
                "Those blocks are part of a build, so I left them."
        if "no_zone" in note:
            return f"I couldn't {what} there - that spot is outside every zone I'm allowed to change."
        if code == "zone_denied" or "server denied: " in note:    # every zone/permission refusal names its reason
            return f"I couldn't {what} there: the server refused it ({note.split('server denied: ')[-1][:80]})."
        return f"I couldn't finish {what}: {code or 'it failed'}" + (f" ({note[:100]})." if note else ".")

    async def move_to(self, params, source, skill="walk_to", say_oob=True, label=None):
        """O111/O120: his walk order (or the answer to her own question) replaces whatever she is doing."""
        r = await self.owner_do(skill, params, source,
                                label=label or "walking to " + " ".join(f"{params['pos'][a]:g}" for a in "xyz")
                                if skill == "walk_to" and "pos" in params else label)
        if say_oob and r.get("code") == "out_of_bounds":
            await self.say(f"I can't go there: {r['detail']}.")
        return r

    async def confirm_answer(self, pend, yes, text, sender):
        """O111 (1): his yes / no to "Just to be sure: X is now Y?" - yes stores the doubted correction as his own words
        (world-specific unless he said every world), no keeps what she had."""
        d = pend["fact"]
        if not yes:
            await self.say("OK, I'll keep what I had.")
            return {"ok": True, "confirmed": False}
        world_specific = d.get("world_specific", True) is not False
        if world_specific and not self.world_id:
            return {"ok": False, "error": "no_world", "detail": "a world-specific fact needs a bound world"}
        r = self.memory.remember(self.world_id if world_specific else None, d["subject"], d["predicate"], d["object"],
                                 "owner_chat", source_ref=f"owner:{sender}", confirmed=1, confidence=1.0, correction=True)
        if not r["ok"]:
            return r
        self.event("mind", "memory.remember", {"text": text[:200], "confirmed_by_owner": True,
                                               "facts": [{**{k: d[k] for k in ("subject", "predicate", "object")}, **r}]})
        await self.say(f"Noted: {d['subject']} {d['predicate'].replace('_', ' ')} {d['object']}"[:160])
        return {"ok": True, "stored": 1, "confirmed": True}

    def inventory_answer(self, text):
        """O111 (45L 11:09: "what do you have on you?" -> "not in my records"; "you don't have bread on you?" -> a
        capability "yes"): what she carries, from the fresh state frame - a question, never a command. "" = not one."""
        a, i = INV_ALL.match(text), INV_ITEM.match(text)
        k = None if a or i else INV_KIND.search(text)
        if not a and not i and not k:
            return ""
        inv, st = self.ironkit.inv()
        if not st:
            return "I can't see my inventory right now - my body hasn't reported it."
        if k:                                     # O156 (45L: "check your own inventory right now, tell me which
            kind = next((x[1] for x in INV_KIND.finditer(text) if x[1]), "").strip().lower()   # pickaxes ..." was filed)
            stem = re.sub(r"(?:es|s)$", "", kind.replace(" ", "_")) if kind not in ("", "things", "stuff", "items") else ""
            where = {}
            for s in st.get("inventory", []):
                it = str(s.get("item", ""))
                if it and int(s.get("count") or 0) > 0 and (not stem or stem in it.split(":")[-1]):
                    slot = s.get("slot")
                    spot = "offhand" if slot == 40 else "hotbar" if isinstance(slot, int) and slot <= 8 else "pack"
                    where.setdefault(it, [0, set()])
                    where[it][0] += int(s["count"])
                    where[it][1].add(spot)
            if not where:
                return f"I have no {kind.rstrip('s') or 'items'}." if stem else "My inventory is empty."
            parts = [f"{n} {it.split(':')[-1].replace('_', ' ')} ({' and '.join(sorted(sp))})"
                     for it, (n, sp) in sorted(where.items(), key=lambda x: -x[1][0])[:10]]
            return "I'm carrying " + (", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]) + "."
        if i:
            name = i.group(1).strip().lower()
            base = name.replace(" ", "_")
            bases = {base, base[:-1] if base.endswith("s") else base}
            want = item_id(name)
            have = sum(n for k, n in inv.items() if n > 0 and (k == want or any(
                k.split(":")[-1] == b or k.split(":")[-1].endswith("_" + b) for b in bases)))
            return f"Yes - I have {have} {name}." if have else f"No, I have no {name}."
        items = sorted(((n, k) for k, n in inv.items() if n > 0), reverse=True)
        if not items:
            return "My inventory is empty."
        return "I have " + ", ".join(f"{n} {k.split(':')[-1].replace('_', ' ')}" for n, k in items[:10]) + \
            (f", and {len(items) - 10} more kinds" if len(items) > 10 else "") + "."

    def state_answer(self, text):
        """O111-b (5): where she is, her health and food, where home is - from the latest state frame and the
        envelope, never "not in my records". "" = not one of these questions."""
        if not (text.strip().endswith("?") or QUESTION_START.match(text)):
            return ""
        st = self.fresh_state()
        if WHERE_YOU.search(text) or HEALTH_Q.search(text):
            if st is None:
                return "I can't see my state right now."
            if WHERE_YOU.search(text):
                p, dim = st.get("pos") or {}, str(st.get("dim", "")).split(":")[-1].replace("_", " ")
                return f"I'm at {' '.join(str(int(p.get(a, 0) // 1)) for a in 'xyz')}" + \
                    ("." if dim == "overworld" else f" in the {dim}.")
            return f"I have {st.get('health', '?')}/20 health and {st.get('food', '?')}/20 food."
        if HOME_Q.search(text):
            c = self.env["home"]["center"]
            return f"Home is at {c['x']} {c['y']} {c['z']}."
        return ""

    async def to_owner(self, op, sender, source, sender_pos=None):
        """O111 (live: "come to me" asked for x y z): come -> walk_to beside the trusted owner (a new order replaces her
        walk); follow -> the follow runner; stay -> a follow ends and her walk is cancelled. His position unknown -> the
        question as before, and his next "x y z" answers it; another dimension -> said, no walk."""
        if op == "stay":
            ended = self.follow.end("stay")
            if ended or self.open_action() is not None:
                await self.cancel_active("owner said: stay", source=source)
            return {"ok": True, "op": "stay", "follow_ended": ended}
        at, st = self.owner_pos(sender, sender_pos), self.fresh_state() or {}
        if at is None:
            q = ASK_POS if op == "come" else ASK_WHERE_YOU
            await self.say(q)
            self.asked = {"kind": "pos", "sender": sender, "at": time.monotonic(), "skill": "walk_to",
                          "params": {"tolerance": 2.0}}
            return {"ok": True, "question": q}
        if at.get("dim") != st.get("dim"):
            await self.say("You're in another dimension; I can only come to you in mine.")
            return {"ok": False, "error": "other_dimension"}
        if op == "follow":
            if not self.follow.running():
                await self.preempt("following you", source)          # O120
            return self.follow.start(sender, source)
        return await self.walk_near(at, source, replace=True)

    async def talk(self, text):
        r = await self.talker(self, text) if self.talker else {
            "ok": True, "reply": dialogue.fallback(dialogue.context(self)), "source": "fallback (no model)"}
        await self.say(r["reply"])
        self.event("mind", "dialogue", {"text": text[:200], "reply": r["reply"], "source": r["source"]})
        return {k: v for k, v in r.items() if k != "context"}

    async def facts_from_words(self, text):
        """A trusted owner's statement -> facts: the local model structures it (guarded: numbers and item ids only from
        the owner's words), else the rule parser. None = not a statement (the command path / planner get it)."""
        if THIRD_PARTY.search(text) or NUMBERS_ONLY.match(text):   # others' claims; bare numbers (O111-b) are no fact
            return None
        det = self.parsed_facts(text)             # before NOT_A_FACT: "let's call the bay Gullwatch" is a name
        if det:
            return det
        if NOT_A_FACT.search(text):               # requests, own intentions ("let's go mining")
            return None
        if self.extractor is None:
            return self.parsed_facts(text, fallback=True) or parse_fact(text)
        known = [{"subject": r["subject"], "predicate": r["predicate"], "object": r["object"]}
                 for r in self.memory.all(self.world_id)][:60]
        try:
            r = await asyncio.wait_for(self.extractor(text, known), EXTRACT_TIMEOUT_S)
        except asyncio.TimeoutError:
            r = {"ok": False, "detail": f"no answer in {EXTRACT_TIMEOUT_S:g} s"}
        if not r["ok"]:
            log.warning("fact extractor unavailable (%s); rule parser used", r.get("detail"))
            return self.parsed_facts(text, fallback=True) or parse_fact(text)
        if not PREF_VERB.search(text) and not TASTE.search(text):   # a taste needs taste words or real items ("the music is relaxing" is chat)
            r["facts"] = [x for x in r["facts"] if x["predicate"] != "preference" or "minecraft:" in x["object"]]
        if r.get("dropped"):
            self.event("mind", "memory.dropped", {"text": text[:200], "dropped": r["dropped"]})
        doubt = next((d for d in r.get("dropped", []) if d.get("confirm")), None)
        if doubt and not r["facts"]:             # O42: a correction a guard doubts is asked about, never dropped
            return {"kind": "question", "confirm": doubt, "text": f"Just to be sure: {doubt['subject']} {doubt['predicate'].replace('_', ' ')}"
                                                f" is now {doubt['object']}?"}
        if not r["facts"]:
            return None
        merged, low = [], text.lower()
        for x in r["facts"]:      # O84 (set 10 N12): two landmarks for one place are one relation, not a clash
            prev = next((y for y in merged if x["predicate"] == "relation" == y["predicate"] and
                         y["subject"] == x["subject"]), None)
            if prev is None:
                merged.append(x)
            else:
                parts = sorted((prev["object"], x["object"]), key=lambda o: (low.find(o.lower()) + 1) or len(low))
                prev["object"] = ", ".join(parts)
        r["facts"] = merged
        keys = {}
        for x in r["facts"]:
            keys.setdefault((x["subject"], x["predicate"]), set()).add(x["object"])
        clash = next((k for k, v in keys.items() if len(v) > 1), None)
        if clash:
            return {"kind": "question", "text": f"Which is right for {clash[0]}: {' or '.join(sorted(keys[clash]))}?",
                    "choice": [{"world": x["world_specific"], "subject": x["subject"], "predicate": x["predicate"],
                                "object": x["object"], "certain": x["certain"]} for x in r["facts"]
                               if (x["subject"], x["predicate"]) == clash]}
        return {"kind": "facts", "correction": any(x["corrects"] for x in r["facts"]),
                "removal": next((x["subject"] for x in r["facts"] if x.get("removal")), None),   # O69f
                "facts": [{"world": x["world_specific"], "subject": x["subject"], "predicate": x["predicate"],
                           "object": x["object"], "certain": x["certain"], "restore": x.get("restore", False)}
                          for x in r["facts"]]}

    def parsed_facts(self, text, fallback=False):
        """O69a: renames are parsed without the model (set 6: the model stored a whole sentence as a name). O69j: the
        extractor reads everything else; removals and placements are parsed only when it is unavailable."""
        r = parse_rename(text)
        if r:
            subject = next((f["subject"] for f in self.memory.all(self.world_id) if f["predicate"] == "identity"
                            and f["object"].lower() == r[0]), r[0])    # O69b: "rename Driftholm to X": X is the old name
            return {"kind": "facts", "correction": bool(self.memory.current(self.world_id, subject, "identity")),
                    "facts": [{"world": True, "subject": subject, "predicate": "identity", "object": r[1],
                               "certain": True}]}
        if not fallback:
            return None
        x = parse_removal(text)
        if x:
            return {"kind": "facts", "correction": False, "removal": x,
                    "facts": [{"world": True, "subject": x, "predicate": "status", "object": "gone",
                               "certain": True}]}
        rel = parse_relation(text)
        if rel:
            world = not everywhere(text)
            return {"kind": "facts", "correction": bool(self.memory.current(self.world_id if world else None, rel[0],
                                                                            "relation")),
                    "facts": [{"world": world, "subject": rel[0], "predicate": "relation", "object": rel[2],
                               "certain": not HEDGE.search(text)}]}
        return None

    async def remember_owner(self, f, text, sender):
        """CP-44: a trusted owner's statement becomes a fact (owner_chat / correction); a question is asked back."""
        if f["kind"] == "question":
            await self.say(f["text"])
            if f.get("confirm"):                          # O111 (1): his yes / no answers it
                self.asked = {"kind": "confirm", "sender": sender, "at": time.monotonic(), "fact": f["confirm"]}
            if f.get("choice"):                       # O111-b (1): his next line names the right one
                self.asked = {"kind": "choice", "facts": f["choice"], "sender": sender, "at": time.monotonic()}
            return {"ok": True, "question": f["text"], "stored": 0}
        out = []
        for x in f["facts"]:
            if x["world"] and not self.world_id:
                return {"ok": False, "error": "no_world", "detail": "a world-specific fact needs a bound world"}
            certain = x.get("certain", True)
            world = self.world_id if x["world"] else None
            gone = self.memory.current(world, x["subject"], "status") if x.get("restore") else None
            gone = gone if gone and gone["object"].startswith("gone") else None
            if gone:                              # O69j: "it still stands" - its retired places come back first
                self.memory.restore(gone["fact_id"])
            r = self.memory.remember(self.world_id if x["world"] else None, x["subject"], x["predicate"], x["object"],
                                     "owner_chat", source_ref=f"owner:{sender}", confirmed=1 if certain else 0,
                                     confidence=1.0 if certain else 0.5, correction=f["correction"])
            if not r["ok"]:
                return r
            if gone and x["predicate"] != "status":   # restated place: the "gone" no longer holds either
                self.db.execute("UPDATE facts SET superseded_by=? WHERE fact_id=?", (r["fact_id"], gone["fact_id"]))
            out.append({**x, **r})
        if f.get("removal") and out:          # O69a: gone means no longer at its old place
            out[0]["retired"] = self.memory.retire_locations(self.world_id, f["removal"], out[0]["fact_id"])
        self.event("mind", "memory.remember", {"text": text[:200], "facts": out})
        reply = "Noted: " + "; ".join(f"{x['subject']} {x['predicate']} {x['object']}"[:120] for x in out)
        await self.say(reply)
        return {"ok": True, "stored": len(out), "facts": out, "reply": reply}

    async def plan_and_run(self, text, source, step_wait=None):
        epoch = self.epoch
        if self.stopped:                # O111 (45L 11:10: "chop 8 logs" ended in silence): every failure is said
            if source == "owner":
                await self.say("I'm stopped - say resume first.")
            return {"ok": False, "error": "stopped", "detail": "STOPPED: resume first"}
        ok, why = self.binding()
        st = self.fresh_state()
        if not ok or st is None:
            if source == "owner":
                await self.say(f"I can't act right now: {why if not ok else 'no fresh state'}.")
            return {"ok": False, "error": "not_ready", "detail": why if not ok else "no fresh state"}
        caps = self.hub.links["body"].hello["caps"]
        world = self.world_id
        r = await self.planner.plan(text, self.registry, caps, st, self.env)
        if self.epoch != epoch or self.stopped or self.world_id != world:   # Stage B: a late answer for an old order
            log.info("plan for %r discarded: stop/epoch/world changed while the planner ran", text[:80])
            return {"ok": False, "error": "stale_plan", "detail": "a stop, reconnect or world change came first"}
        if not r["ok"]:            # CP-24: stalled/absent planner -> honest blocked, nothing dispatched
            self.record_decision("plan", r["model"], r["prompt_version"], text, r, r.get("latency_ms"), False)
            log.info("plan for %r failed: %s %s", text[:80], r["error"], str(r.get("detail") or "")[:160])
            if source == "owner":
                await self.say(f"I couldn't work out a plan just now ({r['error']}).")
            return {"ok": False, "error": r["error"], "detail": r.get("detail")}
        r["plan"] = normalize_plan(r["plan"])     # O111-b (5): Planner.plan does it too; idempotent, for any planner
        known = [{"x": x, "y": y, "z": z} for x, y, z in self.db.execute(
            "SELECT x, y, z FROM containers WHERE world_id=? AND gone=0", (self.world_id,))]
        stations = await workstations.plan_stations(self, r["plan"])   # O161: 2x2 no target; a known station's is fine
        errs = validate_plan(r["plan"], self.registry, caps, self.mode, self.env, st, request=text,
                             known=known + stations, stations=stations)
        self.record_decision("plan", r["model"], r["prompt_version"], text, r["raw"], r["latency_ms"], not errs)
        if errs:                        # the reasons are kept and said (45L: validated=0 and nothing else)
            log.info("plan for %r rejected: %s", text[:80], "; ".join(map(str, errs))[:300])
            self.event("mind", "plan.invalid", {"text": text[:200], "errors": [str(x) for x in errs][:8],
                                                "plan": r["plan"]})
            if source == "owner":
                await self.say("I don't know how to do that yet.")   # O164: the step/reason is in the log above
            return {"ok": False, "error": "invalid_plan", "detail": errs, "plan": r["plan"]}
        plan = r["plan"]
        if not plan["steps"]:
            q = plan.get("question") or "I can't do that with the skills I have."
            await self.say(q)
            return {"ok": True, "question": q, "steps": []}
        results = []
        for i, step in enumerate(plan["steps"]):
            if any(results[d].get("status") != "ok" for d in step["depends_on"]):
                break
            sk = self.registry.skills[step["skill"]]
            res = await self.do(step["skill"], step["params"], source=source, epoch=epoch,
                                wait=step_wait or sk["timeout_ms"] / 1000 + self.cfg.result_grace_s)
            results.append(res)
            if not res.get("ok") or res.get("status") != "ok":
                break
        return {"ok": all(x.get("status") == "ok" for x in results) and len(results) == len(plan["steps"]),
                "plan": plan, "results": results}

    # ---- control (CP-12/CP-20 mind side)
    async def stop(self, source, reason):
        # the stop goes out first; persistence is best-effort so a locked/broken DB can never delay it (CP-24)
        self.stopped = True
        self.epoch += 1
        self._stop_meta = {"control.stopped": "1", "control.stopped_by": source, "control.stopped_ts": now_ms(),
                           "control.epoch": self.epoch}
        delivered = await self.send_body("control.stop", {"reason": reason, "source": source})
        persisted = True
        try:
            with self.durable():                  # O82: a power loss never loses a STOP
                self.set_meta(self._stop_meta)
                self.db.execute("UPDATE tasks SET status='paused', reason=?, updated_ts=? WHERE status IN "
                                "('queued','running')", (f"stopped by {source}: {reason}", now_ms()))
            self.event("mind", "control", {"op": "stop", "source": source, "reason": reason})
            persisted = await self.db.flush(2.0)  # O82: bounded; the writer keeps retrying after a False
        except sqlite3.Error as e:
            persisted = False
            self.metrics["db_errors"] += 1
            log.error("STOPPED is in effect but not persisted: %s", e)
        return {"ok": True, "stopped": True, "delivered": delivered, "persisted": persisted}

    def stood_at(self, ts):
        """Eve's block when an action was sent (the last stored state frame before it), or None if unknown. CP-22's
        retry limit counts the SAME failing interaction: from another spot it is another attempt (CP-43 lane B: a
        place that failed from a bad standing spot must not block it from a good one)."""
        if not ts:
            return None
        row = self.db.execute("SELECT payload FROM observations WHERE kind='state' AND ts <= ? ORDER BY ts DESC "
                              "LIMIT 1", (ts,)).fetchone()
        if row is None:
            return None
        p = json.loads(row[0]).get("pos") or {}
        return tuple(int(p.get(a, 0) // 1) for a in "xyz")

    async def db_wait(self):
        """Bounded async wait (cfg.db_timeout_s) until the DB takes a write lock; the loop keeps running meanwhile."""
        deadline = time.monotonic() + self.cfg.db_timeout_s
        while True:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute("ROLLBACK")
                return True
            except sqlite3.Error:
                if time.monotonic() >= deadline:
                    self.metrics["db_errors"] += 1
                    return False
                await asyncio.sleep(0.1)

    async def resume(self, source):
        # persist first: if the file does not take it we stay STOPPED (the safe direction); O82: the queued resume is
        # followed by a queued re-stop, so the file - written in order - also ends STOPPED
        with self.durable():
            self.set_meta({"control.stopped": "0", "control.resumed_by": source, "control.resumed_ts": now_ms()})
        if not await self.db.flush(self.cfg.db_timeout_s):
            with self.durable():
                self.set_meta({"control.stopped": "1"})
            self.metrics["db_errors"] += 1
            return {"ok": False, "error": "db_unavailable",
                    "detail": f"memory DB file not writable within {self.cfg.db_timeout_s:g} s; cannot persist resume; "
                              "still STOPPED"}
        self.stopped = False
        self.event("mind", "control", {"op": "resume", "source": source})
        delivered = await self.send_body("control.resume", {"source": source})
        return {"ok": True, "stopped": False, "delivered": delivered,
                "note": "paused tasks stay paused until reconciled and re-issued"}

    async def home(self, source):
        if self.stopped:
            return {"ok": False, "error": "stopped", "detail": "STOPPED: resume first"}
        self.event("mind", "control", {"op": "home", "source": source})
        return {"ok": True, "delivered": await self.send_body("control.home", {"reason": f"home requested by {source}"})}

    async def set_mode(self, mode, source):
        if mode not in MODES:
            return {"ok": False, "error": "bad_mode", "detail": f"mode must be one of {MODES}"}
        if mode == "autonomous":
            return {"ok": False, "error": "mode_not_qualified", "detail": "autonomous needs CP-73+ PASS"}
        if mode not in self.env.get("mode_allowed", ["manual"]):
            return {"ok": False, "error": "mode_not_allowed", "detail": f"envelope mode_allowed={self.env.get('mode_allowed')}"}
        self.mode = mode
        self.set_meta({"control.mode": mode})
        self.event("mind", "control", {"op": "mode", "mode": mode, "source": source})
        return {"ok": True, "mode": mode, "delivered": await self.send_body("control.mode", {"mode": mode})}

    async def on_owner_command(self, p):
        trusted = p["trusted"] and p["sender"] in self.env["owner"]["trusted_players"]
        self.event("sense", "owner.command", {**p, "accepted_as_owner": trusted})
        if not trusted:            # CP-45: untrusted senders are data
            self.metrics["untrusted_commands"] += 1
            return
        who, c, a = f"owner:{p['sender']}", p["command"], p["args"]
        if c == "stop":
            await self.stop(who, "in-game /eve stop")
        elif c == "resume":
            await self.resume(who)
        elif c == "home":
            await self.home(who)
        elif c == "mode" and a:
            await self.set_mode(a[0], who)
        elif c in ("approve", "reject") and len(a) >= 2:
            self.decide(c, a[0], a[1], p["sender"], "ingame")
        elif c == "reload":
            self.reload_envelope(f"{who}: /eve reload")
        elif c == "say" and a:
            self.spawn(self.owner_text(" ".join(a), p["sender"], source="owner"))
        elif c == "bridge" and len(a) >= 3 and all(re.fullmatch(r"-?\d+", v) for v in a[:3]):   # /eve bridge x y z
            r = self.bridge.start(dict(zip("xyz", a[:3])), source="owner")
            if not r["ok"]:
                await self.say(f"Not bridging: {r['detail']}.")
        elif c == "repair" and len(a) >= 2:          # CP-52: /eve repair <zone> <snapshot> x1 y1 z1 x2 y2 z2
            n = a[2:8]
            box = ({"min": dict(zip("xyz", n[:3])), "max": dict(zip("xyz", n[3:]))}
                   if len(n) == 6 and all(re.fullmatch(r"-?\d+", v) for v in n) else None)
            r = self.repair.start(a[0], a[1], box, source="owner")
            if not r["ok"]:
                await self.say(f"Not repairing: {r['detail']}.")

    # ---- sense data
    def on_storage(self, p):
        w, ts, seen = self.world_id or "unbound", now_ms(), []
        cur = self.open_action()
        target = json.loads(cur["params"]).get("pos") if cur else None
        for c in p["containers"]:
            seen.append(c["id"])
            if isinstance(target, dict) and isinstance(c.get("pos"), dict) and all(
                    int(c["pos"][a]) == int(target[a]) for a in "xyz"):
                # sense observed the container this action works on change (a furnace smelting, a chest being
                # filled): world progress for the stuck detector, from sense rather than the body's say-so
                before = {(r["item"], r["count"]) for r in self.db.execute(
                    "SELECT item, count FROM container_items WHERE container_id=?", (c["id"],))}
                if before != {(i["item"], i["count"]) for i in c.get("items", [])}:
                    self.world_progress_ms = ts
            if c.get("removed"):            # sense: container broken/moved
                self.db.execute("UPDATE containers SET gone=1, observed_ts=? WHERE container_id=?", (ts, c["id"]))
                self.db.execute("DELETE FROM container_items WHERE container_id=?", (c["id"],))
                continue
            pos = c["pos"]
            self.db.execute("INSERT OR REPLACE INTO containers VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                            (c["id"], w, c["dim"], int(pos["x"]), int(pos["y"]), int(pos["z"]), c["kind"], c["tag"],
                             int(c["loaded"]), c.get("observed_tick"), ts))
            self.db.execute("DELETE FROM container_items WHERE container_id=?", (c["id"],))
            self.db.executemany("INSERT INTO container_items VALUES (?,?,?,?)",
                                [(c["id"], i["item"], i["count"], ts) for i in c["items"]])
            self.memory.remember(w, f"container {c['id']}", "holds", "; ".join(
                f"{i['count']} {i['item']}" for i in sorted(c["items"], key=lambda i: i["item"])) or "nothing",
                "observation", source_ref=f"storage.index tick {c.get('observed_tick')}")
        if p.get("partial"):                # a full index split over frames ends with complete:true
            self.split_seen.update(c["id"] for c in p["containers"] if not c.get("removed"))
            if not p.get("complete"):
                return
            seen, self.split_seen = list(self.split_seen), set()
        else:
            self.split_seen = set()
        self.db.execute(f"UPDATE containers SET gone=1 WHERE world_id=? AND container_id NOT IN "
                        f"({','.join('?' * len(seen))})", (w, *seen))

    def on_block_events(self, events):
        w, ts = self.world_id or "unbound", now_ms()
        if any(e["actor"] == "eve" for e in events):
            self.world_progress_ms = ts
        self.db.executemany("INSERT INTO block_events (world_id, tick, ts, dim, x, y, z, kind, before, after, actor) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            [(w, e.get("tick"), ts, e["dim"], int(e["pos"]["x"]), int(e["pos"]["y"]),
                              int(e["pos"]["z"]), e["kind"], json.dumps(e.get("before")), json.dumps(e.get("after")),
                              e["actor"]) for e in events])

    # ---- approvals (contracts/design-project.md: written only by the mind from an authenticated owner)
    def _load_design(self, project_id, version):
        """designs/<project_id>/v<N>/ with its design hash re-computed; returns (folder, v, design, brief) or an error."""
        if not isinstance(project_id, str) or not PROJECT_RE.match(project_id):
            return {"ok": False, "error": "bad_project", "detail": "project_id must match [a-z0-9][a-z0-9_-]*"}
        m = re.fullmatch(r"v?(\d{1,6})", str(version))
        if not m:
            return {"ok": False, "error": "bad_version", "detail": "version must look like v3"}
        v = int(m.group(1))
        d = self.cfg.designs_dir / project_id / f"v{v}"
        try:
            design = json.loads((d / "design.json").read_text("utf-8"))
            brief = json.loads((d / "brief.json").read_text("utf-8")) if (d / "brief.json").exists() else {}
        except (OSError, ValueError) as e:
            return {"ok": False, "error": "no_design", "detail": f"{d}: {e}"}
        if not design.get("hash"):
            return {"ok": False, "error": "no_design_hash", "detail": "design.json has no hash"}
        actual = design_hash(design)
        if actual != design["hash"]:
            return {"ok": False, "error": "design_hash_mismatch",
                    "detail": f"design.json says {design['hash'][:16]}, content hashes to {actual and actual[:16]}"}
        return d, v, design, brief

    @staticmethod
    def _read(d, name):
        try:
            return json.loads((d / name).read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def _record_approval(self, d, project_id, v, design, brief, decided_by, decision, category, channel, warnings):
        rec = {"approval_id": str(uuid.uuid4()), "project_id": project_id, "version": v,
               "design_hash": design["hash"], "site_hash": None, "budget": brief.get("budget"),
               "decided_by": decided_by, "decision": decision, "category": category, "channel": channel,
               "ts": now_ms()}
        self.db.execute("INSERT INTO approvals (approval_id, project_id, version, design_hash, site_hash, budget, "
                        "decided_by, decision, category, channel, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (rec["approval_id"], project_id, v, rec["design_hash"], None, json.dumps(rec["budget"]),
                         decided_by, decision, category, channel, rec["ts"]))
        (d / "approval.json").write_text(json.dumps(rec, indent=2), "utf-8")
        return {"ok": True, **rec, "warnings": warnings}

    def decide(self, verb, project_id, version, by, channel):
        """The owner's decision, from the authenticated channel only (CLI on the 45L, trusted in-game command).
        S16: owner-supplied schematics (category owner_small) are built as given, so they need no validation.json."""
        trusted = self.env["owner"]["trusted_players"]
        if not trusted:
            return {"ok": False, "error": "no_trusted_players",
                    "detail": "envelope owner.trusted_players is empty (DECISIONS P11)"}
        by = by or (trusted[0] if len(trusted) == 1 else None)
        if by not in trusted:
            return {"ok": False, "error": "not_trusted", "detail": f"{by!r} is not in owner.trusted_players"}
        loaded = self._load_design(project_id, version)
        if isinstance(loaded, dict):
            return loaded
        d, v, design, brief = loaded
        category, warnings = brief.get("approval_category"), []
        if verb == "approve":
            val = self._read(d, "validation.json")
            if category == "owner_small":
                if not (val and val.get("passed") is True):
                    warnings.append("owner-supplied schematic: built as given, not validated")
            elif val is None or val.get("passed") is not True:
                return {"ok": False, "error": "not_validated",
                        "detail": "validation.json is missing or did not pass; only reject is possible"}
            elif val.get("skipped"):
                warnings.append(f"validation skipped: {val['skipped']}")
        return self._record_approval(d, project_id, v, design, brief, by,
                                     "approved" if verb == "approve" else "rejected", category, channel, warnings)

    CRITIC_FLOOR = 7.0       # the mind's own minimum on every critic category, whatever critique.json says

    def self_approve(self, project_id, version):
        """S16: Eve's own designs need no owner approval. The mind approves one only when it is Eve-authored
        (brief.authored_by "eve", category wishlist|village_plot), validation passed for this exact hash, the critic
        accepted this exact hash with every category >= max(its threshold, 7), and (wishlist) the envelope's weekly
        limit has room. decided_by "eve"; nothing else can produce such a row."""
        loaded = self._load_design(project_id, version)
        if isinstance(loaded, dict):
            return loaded
        d, v, design, brief = loaded
        h = design["hash"]
        category = brief.get("approval_category")
        if brief.get("authored_by") != "eve":
            return {"ok": False, "error": "not_eve_authored",
                    "detail": "owner-supplied designs are decided by the owner (eve approve)"}
        if category not in ("wishlist", "village_plot"):
            return {"ok": False, "error": "bad_category", "detail": f"category {category!r}; Eve self-approves "
                                                                       "only wishlist or village_plot"}
        val = self._read(d, "validation.json")
        if not val or val.get("passed") is not True or val.get("design_hash", h) != h:
            return {"ok": False, "error": "not_validated", "detail": "validation.json missing, failed or for another hash"}
        crit = self._read(d, "critique.json")
        scores = (crit or {}).get("scores") or {}
        floor = max(float((crit or {}).get("threshold") or 0), self.CRITIC_FLOOR)
        if not crit or crit.get("status") != "accepted" or crit.get("design_hash") != h or not scores:
            return {"ok": False, "error": "not_critiqued",
                    "detail": f"critique status {(crit or {}).get('status')!r} (needs 'accepted' for this hash)"}
        low = {k: s for k, s in scores.items() if not isinstance(s, (int, float)) or s < floor}
        if low:
            return {"ok": False, "error": "below_threshold", "detail": f"below {floor:g}: {low}"}
        if category == "wishlist":
            limit = int(self.env.get("approvals", {}).get("wishlist_max_per_week") or 0)
            used = self.db.execute("SELECT count(*) FROM approvals WHERE decided_by='eve' AND category='wishlist' "
                                   "AND decision='approved' AND invalidated_ts IS NULL AND ts > ?",
                                   (now_ms() - 7 * 86_400_000,)).fetchone()[0]
            if limit <= 0:
                return {"ok": False, "error": "wishlist_disabled", "detail": "envelope approvals.wishlist_max_per_week is 0"}
            if used >= limit:
                return {"ok": False, "error": "wishlist_limit", "detail": f"{used} of {limit} this week"}
        warnings = [f"validation skipped: {val['skipped']}"] if val.get("skipped") else []
        return self._record_approval(d, project_id, v, design, brief, "eve", "approved", category, "self", warnings)

    # ---- status and CLI
    def status(self):
        b, s = self.hub.links["body"], self.hub.links["sense"]
        ok, why = self.binding()
        st = self.state
        cur = self.open_action()
        return {
            "lane": lane(), "mind_commit": MIND_COMMIT, "mind_started": MIND_STARTED,
            "mind_src": MIND_SRC, "mind_src_tree": MIND_SRC_TREE,      # O83h: harnesses check these, never src HEAD
            "stopped": self.stopped, "stopped_by": self.meta("control.stopped_by"), "mode": self.mode,
            "epoch": self.epoch,
            "body": b and {"session": b.session, "account": b.hello["account_name"], "level": b.hello["level_name"],
                           "caps": len(b.hello["caps"]),
                           "state_age_ms": st and int((time.monotonic() - self.state_rx) * 1000),
                           "pos": st and st["pos"], "body_stopped": st and st["control"]["stopped"]},
            "sense": s and {"session": s.session, "world_id": s.hello["world_id"], "level": s.hello["level_name"]},
            "binding": {"ok": ok, "detail": why}, "reconcile_needed": self.reconcile_needed,
            "open_action": cur and {"action_id": cur["action_id"], "skill": cur["skill"], "status": cur["status"]},
            # active = the task of the action in flight; paused/blocked tasks are parked, never active
            "active_task": cur and cur["task_id"] and dict(self.db.execute(
                "SELECT task_id, goal, status, source FROM tasks WHERE task_id=?", (cur["task_id"],)).fetchone() or {}),
            "parked_tasks": self.db.execute("SELECT count(*) FROM tasks WHERE status IN ('paused','blocked')")
                                   .fetchone()[0],
            "registry": {"version": self.registry.version, "hash": self.registry.hash},
            "spend": self.spend(),
            "last_ack": self.last_ack, "metrics": {**self.hub.metrics, **self.metrics},
        }

    async def cli(self, cmd, args):
        try:
            r = await self._cli(cmd, args)
        except sqlite3.Error as e:          # a locked DB is an honest refusal, never a CLI exception (CP-24)
            self.metrics["db_errors"] += 1
            r = {"ok": False, "error": "db_unavailable", "detail": f"memory DB: {e}"}
        if not r.get("ok") and "status" not in r:      # every refused CLI reply has status/code/reason too
            r = {**refusal(r.get("error"), r.get("detail") or ""), **r}
        return r

    async def _cli(self, cmd, args):
        if not isinstance(args, dict):
            return {"ok": False, "error": "bad_args"}
        if cmd == "status":
            return {"ok": True, **self.status()}
        if cmd == "stop":
            return await self.stop("cli", args.get("reason") or "cli stop")
        if cmd == "resume":
            return await self.resume("cli")
        if cmd == "home":
            return await self.home("cli")
        if cmd == "mode":
            return await self.set_mode(args.get("mode"), "cli")
        if cmd == "do":
            params = args.get("params")
            if not isinstance(params, dict):
                return {"ok": False, "error": "bad_params", "detail": "params must be an object"}
            assumed = self.owner_assumptions(args.get("assume") or [])
            if isinstance(assumed, dict):
                return assumed
            return await self.do(args.get("skill"), params, source="cli", wait=min(float(args.get("wait") or 0), 600),
                                 grant=True, assumptions=assumed)
        if cmd == "tasks":
            q = lambda where, tail="": [dict(r) for r in self.db.execute(  # noqa: E731
                "SELECT task_id, kind, goal, status, reason, source, created_ts, updated_ts FROM tasks "
                f"WHERE status IN ({where}) ORDER BY updated_ts DESC {tail}")]
            return {"ok": True, "now_ts": now_ms(), "parked_ttl_s": self.cfg.parked_ttl_s,
                    "active": q("'running','queued'"), "parked": q("'paused','blocked'"),
                    "recent": q("'done','failed','cancelled'", "LIMIT 10"),
                    "actions": [dict(r) for r in self.db.execute(
                        "SELECT action_id, task_id, skill, status, code, sent_ts, result_ts, reconciled, "
                        "reconcile_note, note FROM actions ORDER BY rowid DESC LIMIT 20")]}
        if cmd == "drop":
            return self.drop_tasks(args.get("task_id"), bool(args.get("parked")), "cli")
        if cmd in ("approve", "reject"):
            return self.decide(cmd, args.get("project_id"), args.get("version"), args.get("by"), "cli")
        if cmd == "self_approve":
            return self.self_approve(args.get("project_id"), args.get("version"))
        if cmd == "revoke":
            return self.revoke(args.get("project_id"), args.get("version"), args.get("by"),
                               args.get("reason") or "cli")
        if cmd == "continue":
            return await self.continue_task(str(args.get("task_id")), "cli",
                                            wait=min(float(args.get("wait") or 0), 600))
        if cmd == "cancel":
            return await self.cancel_active(args.get("reason") or "owner cancel", source="cli")
        if cmd == "retain":                    # I14: keep state rows (health, position) for a whole run
            n = args.get("minutes")
            if not isinstance(n, int) or not 10 <= n <= 1440:
                return {"ok": False, "error": "bad_params", "detail": "minutes must be 10..1440 (10 = the default)"}
            self.set_meta({"state.retain_min": str(n)})
            return {"ok": True, "retain_min": n}
        if cmd == "spend":
            return {"ok": True, **self.spend()}
        if cmd == "objective" and (args.get("name") == "night_survival" or
                                   (args.get("status") and self.night.status(str(args["status"]))["ok"])):
            if args.get("status") is not None:                                                         # O54
                return self.night.status(str(args["status"]) if args["status"] else None)
            if self.stopped:
                return {"ok": False, "error": "stopped", "detail": "STOPPED: resume first"}
            return self.night.start(source="cli", doorway=args.get("doorway"), late_seal=bool(args.get("late_seal")))
        if cmd == "objective" and (args.get("name") == "iron_kit" or str(args.get("text") or "").strip() == "iron_kit"
                                   or (args.get("status") and self.ironkit.status(str(args["status"]))["ok"])):
            if args.get("status") is not None:
                return self.ironkit.status(str(args["status"]) if args["status"] else None)
            if self.stopped:
                return {"ok": False, "error": "stopped", "detail": "STOPPED: resume first"}
            return self.ironkit.start(source="cli", entry=args.get("entry"), direction=args.get("direction"))  # CP-40
        if cmd == "objective":
            if args.get("continue"):
                return self.objectives.resume(str(args["continue"]))
            if args.get("status") is not None:
                return self.objectives.status(str(args["status"]) if args["status"] else None)
            text = str(args.get("text") or "").strip()
            if not text or len(text) > 1000:
                return {"ok": False, "error": "bad_args", "detail": "objective text (1-1000 chars)"}
            if self.stopped:
                return {"ok": False, "error": "stopped", "detail": "STOPPED: resume first"}
            return self.objectives.start(text, source="cli", budget=args.get("budget"))
        if cmd == "bridge":                     # CP-53: bridge from a void edge facing the target
            if args.get("status") is not None:
                return self.bridge.status()
            if self.stopped:
                return {"ok": False, "error": "stopped", "detail": "STOPPED: resume first"}
            return self.bridge.start(args.get("to"), source="cli")
        if cmd == "repair":                     # CP-52: restore a zone to an approved snapshot
            if args.get("status") is not None:
                return self.repair.status(str(args["status"]) if args["status"] else None)
            if not args.get("zone_id") or not args.get("snapshot_id"):
                return {"ok": False, "error": "bad_args", "detail": "repair needs zone_id and snapshot_id"}
            if self.stopped:
                return {"ok": False, "error": "stopped", "detail": "STOPPED: resume first"}
            return self.repair.start(str(args["zone_id"]), str(args["snapshot_id"]), args.get("box"), source="cli")
        if cmd == "build":                      # CP-51: build an approved design, gathering what is missing
            if args.get("status") is not None:
                return self.supply.status(str(args["status"]) if args["status"] else None)
            p = {k: args[k] for k in ("project_id", "version", "design_hash", "origin") if args.get(k) is not None}
            if not {"project_id", "version", "design_hash"} <= set(p):
                return {"ok": False, "error": "bad_args", "detail": "build needs project_id, version, design_hash"}
            if self.stopped:
                return {"ok": False, "error": "stopped", "detail": "STOPPED: resume first"}
            return self.supply.start(p, source="cli")
        if cmd in ("survey", "resources", "storage"):
            return await self.read_only(cmd, args)
        if cmd == "spend_reserve":
            return await self.spend_reserve(args)
        if cmd == "spend_commit":
            return await self.spend_commit(args)
        if cmd == "ask":
            return await self.owner_text(str(args.get("text") or ""), "cli", source="cli")
        if cmd == "check":
            return await self.permission_query(args)
        if cmd == "talk":                    # CP-46: owner words answered from the records (no action)
            text = str(args.get("text") or "").strip()
            if not text or len(text) > 500:
                return {"ok": False, "error": "bad_args", "detail": "text (1-500 chars)"}
            return await self.talk(text)
        if cmd == "recall":
            facts = self.memory.recall(str(args.get("subject") or ""), args.get("predicate"), self.world_id)
            return {"ok": True, "world_id": self.world_id, "facts": facts, "unknown": not facts}
        if cmd == "forget":                  # O111-b: retire one false fact (kept, superseded_by "retracted")
            r = self.memory.retract(str(args.get("fact_id") or ""), str(args.get("subject") or ""),
                                    str(args.get("predicate") or ""))
            if r["ok"]:
                self.event("mind", "memory.retract", {"fact": r["retracted"], "by": "cli"})
            return r
        if cmd == "history":
            return {"ok": True, "world_id": self.world_id, "history": self.memory.history(self.world_id)}
        return {"ok": False, "error": "unknown_command", "detail": cmd}
