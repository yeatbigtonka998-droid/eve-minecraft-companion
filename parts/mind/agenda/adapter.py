"""The ONE file the mind would import (proposal; the mind chat owns every call site). Pure translation between the
contracts and this sub-line, no mind internals:
  scheduler_input(state, owner_request, unavailable)  link-protocol `state` payload -> Agenda.decide() input
  decision_row(world_id, entry)                       a scheduler log entry -> `decisions` row (kind need_pick)
  journal_row(world_id, claims, until)                verified briefing claims -> `journal` row (kind briefing,
                                                       grounded_in = every cited id; refuses unverified claims)
Proposed call sites (mind chat to place them, after its cancel-race gate):
  1 idle tick in supervised mode: d = agenda.decide(scheduler_input(fresh_state(), pending_owner_text, unavailable))
    -> record decision_row; dispatch d["task"] through the EXISTING runners only (night_survival -> night runner,
    retreat_home -> home(), eat/get_food -> the objective's food path, owner_request -> owner_text, agenda ->
    objective(goal), get_tool -> objective("make a <tool>"), idle_home -> home() + say(question)).
  2 the owner joins (sense owner presence) or `eve brief --since <ms>`: claims = briefing.build(db, since, now);
    send only if briefing.verify(...) == []; store journal_row; say the rendered lines.
  3 CP-42: nothing; the judge uses the CLI.
"""
import json


def scheduler_input(state, owner_request=None, unavailable=()):
    inv = {}
    for s in state.get("inventory") or []:
        inv[s["item"]] = inv.get(s["item"], 0) + int(s.get("count", 1))
    return {"time_of_day": state.get("time_of_day", 0), "health": state.get("health", 20),
            "food": state.get("food", 20), "inventory": inv, "threats": list(state.get("threats") or []),
            "owner_request": owner_request, "unavailable": list(unavailable),
            "now_ms": state.get("now_ms")}


def decision_row(world_id, entry):
    return {"world_id": world_id, "ts": entry["ts"], "kind": "need_pick", "prompt_version": "agenda-scheduler-v1",
            "model": "rules", "input": json.dumps({"inputs": entry["inputs"], "agenda": entry["agenda"]}),
            "output": json.dumps(entry["decision"]), "validated": 1}


def journal_row(world_id, claims, until, problems):
    if problems:
        raise ValueError(f"unverified briefing is never journalled: {problems[:3]}")
    return {"world_id": world_id, "ts": until, "kind": "briefing", "text": "\n".join(c["text"] for c in claims),
            "generated_by": "agenda.briefing-v1",
            "grounded_in": json.dumps(sorted({f"{t}:{k}" for c in claims for t, k in c["cites"]}))}


def intent_gate(text, envelope, url=None, model=None, timeout=60):
    """O94g/O94i: refuse / ask / plan before any planner call (two qwen3:8b framings ANDed with the rule vetoes; a
    model failure asks). Blocking (urllib, up to 2 x timeout): the mind runs it in asyncio.to_thread. Called on owner
    lines not handled as control; refuse -> say(reason), nothing planned; ask -> say(reason); plan -> normal path.
    url/model default to the mind's EVE_OLLAMA_URL / EVE_PLANNER_MODEL."""
    import os
    from agenda.intent_gate import classify
    return classify(text, envelope, url or os.environ.get("EVE_OLLAMA_URL"), model, timeout)


# ---- O-agenda-wire (kickoff, S38): owner phrases for the agenda and the offline briefing ------------------------------
# Deterministic phrase parsing + reply text; the mind calls one hook (Mind.agenda_owner) and owns sending and storage.
import re  # noqa: E402

BRIEF = re.compile(r"\bwhat (?:did|have) you (?:do|done|been doing|been up to|get up to)\b.*\b(?:while|when) i (?:was|were) "
                   r"(?:gone|away|out|offline|off)\b|\bwhat (?:did|have) you (?:do|done)\b.*\bsince i (?:left|logged off)\b|"
                   r"^\s*(?:eve[,:]?\s+)?(?:brief me|catch me up|give me (?:a|the) briefing)\b", re.I)
PLAN = re.compile(r"^\s*(?:eve[,:]?\s+)?(?:plan|agenda)(\s+for\s+later|\s+(?:when|while|after|once) i(?:'m| am)? "
                  r"(?:gone|away|out|offline|logged off))?\s*:\s*(.+?)\s*$", re.I)
# O127: "plan for later: ..." or "... when I'm gone" stores without running; any other plan runs now, in order
LATER = re.compile(r",?\s*\b(?:when|while|after|once) i(?:'m| am)? (?:gone|away|out|offline|logged off)\b,?", re.I)
# O127 (45L: "mine 5 logs and then craft a crafting table" -> "1) mine 5 logs and; 2) ..."): one clean split on
# "and then" / "then" / ";" / "," (a comma before a number stays: "walk to 10, 64, 10") / "1) 2)" numbering
SPLIT = re.compile(r"\s*;\s*(?:and\s+)?(?:then\s+)?|\s*,(?!\s*-?\d)\s*(?:and\s+)?(?:then\s+)?|"
                   r"\s+(?:and\s+)?then\s+|\s*\b\d+\)\s*", re.I)
SHOW = re.compile(r"\bwhat(?:'s| is) on your (?:agenda|plan|list)\b|\bwhat(?:'s| is) (?:your|the) (?:plan|agenda)\b|"
                  r"\bshow (?:me )?(?:your )?agenda\b", re.I)
STANDING = re.compile(r"^(?:keep|maintain|tend|look after|guard)\b", re.I)
BRIEF_LINES_MAX = 8


def split_and(goal, is_order):
    """O127 addendum (45L: "make a furnace and place the furnace" stayed one goal): split on " and " only where BOTH
    sides are orders by the mind's rule map (is_order = its IMPERATIVE test); "get logs and planks" stays one goal.
    Greedy left to right: "get logs and planks and make a table" -> ["get logs and planks", "make a table"]."""
    pieces = re.split(r"\s+and\s+", goal)
    out = [pieces[0]]
    for p in pieces[1:]:
        if is_order(p) and is_order(out[-1]):
            out.append(p)
        else:
            out[-1] += " and " + p
    return out


def owner_phrase(text, is_order=None):
    """{"kind": "brief"} | {"kind": "plan", "goals": [...]} | {"kind": "show"} | None (not an agenda line).
    is_order(text) -> bool: the mind's rule-map test for an order; without it, " and " never splits a goal."""
    t = " ".join(str(text).split())
    m = PLAN.match(t)
    if m:
        body, later = m[2], bool(m[1])
        if LATER.search(body):
            body, later = LATER.sub(" ", body), True
        goals = [re.sub(r"^(?:and\s+)?(?:then\s+)?", "", g.strip(" .,"), flags=re.I).strip(" .,") for g in SPLIT.split(body)]
        goals = [s for g in goals if g for s in (split_and(g, is_order) if is_order else [g])]
        return {"kind": "plan", "goals": goals, "later": later}
    if SHOW.search(t):
        return {"kind": "show"}
    if BRIEF.search(t):
        return {"kind": "brief"}
    return None


def agenda_from_goals(goals, envelope, budget_s=600):
    """Hunter's goals -> (Agenda, None) or (None, reason). Every goal passes the deterministic gate rules first, so a
    refused order can never sit on the agenda; the scheduler's own limits (<= 8 items, explore radius, positions within
    explore_radius) apply."""
    from agenda.intent_gate import gate
    from agenda.scheduler import Agenda
    home = (envelope or {}).get("home") or {}
    items = []
    for i, g in enumerate(goals, 1):
        v = gate(g, envelope)
        if v["verdict"] == "refuse":
            return None, f"I won't put #{i} on my agenda: {v['reason']}."
        kind = "explore" if re.search(r"\bexplore\b", g, re.I) else "standing" if STANDING.match(g) else "task"
        items.append({"id": f"a{i}", "goal": g, "kind": kind, "budget_s": budget_s,
                      **({"radius": min(64, int(home.get("explore_radius", 128)))} if kind == "explore" else {})})
    try:
        c = home.get("center") or {}
        return Agenda(items, explore_radius_max=int(home.get("explore_radius", 128)),
                      home=(c.get("x", 0), c.get("z", 0))), None
    except ValueError as e:
        return None, f"I can't take that agenda: {e}."


def agenda_json(agenda):
    return json.dumps([{"id": i.id, "goal": i.goal, "kind": i.kind, "budget_s": i.budget_s, "radius": i.radius,
                        "done": i.done} for i in agenda.items])


def agenda_load(stored, envelope):
    if not stored:
        return None

    from agenda.scheduler import Agenda
    home = (envelope or {}).get("home") or {}
    c = home.get("center") or {}
    return Agenda(json.loads(stored), explore_radius_max=int(home.get("explore_radius", 128)), home=(c.get("x", 0), c.get("z", 0)))


def agenda_line(agenda):
    return "Agenda: " + "; ".join(f"{n}) {i.goal}" + (" (ongoing)" if i.kind == "standing" else "") + (" (done)" if i.done else "")
                                  for n, i in enumerate(agenda.items, 1))[:240]


def show_reply(agenda, decision):
    """What's on her agenda and what the scheduler would pick now, with its reason. A display: nothing is dispatched."""
    if agenda is None:
        return "My agenda is empty. Tell me with 'plan: first thing; then second thing'."
    tail = f" Next for me: {decision['task'].replace('_', ' ')} - {decision['reason']}." if decision else ""
    return (agenda_line(agenda) + "." + tail)[:256]


def briefing_for(db_path, since, until, world=None, agenda_log=None):
    """CP-74 for the owner: (claims, problems) from the mind's DB, read-only. Send nothing when problems is non-empty."""
    from agenda import briefing
    claims = briefing.build(db_path, since, until, world, agenda_log)
    return claims, briefing.verify(claims, db_path, since, until, world, agenda_log)


def brief_lines(claims, max_lines=BRIEF_LINES_MAX):
    """Chat lines, most important first: deaths, failures, questions, then what she did, resources, memory."""
    order = ["deaths", "failed", "questions", "did", "resources", "memory"]
    xs = sorted(claims, key=lambda c: order.index(c["section"]) if c["section"] in order else 9)
    if not xs:
        return ["Nothing happened that I logged while you were away."]
    lines = [c["text"][:250] for c in xs[:max_lines]]
    if len(xs) > max_lines:
        lines.append(f"...and {len(xs) - max_lines} more in my records.")
    return lines


PROGRESS_ALIASES = {"diamonds": "diamond", "iron": "iron_ingots", "ingots": "iron_ingots", "raw iron": "raw_iron",
                    "portal": "nether_portal", "the nether": "nether_portal", "nether": "nether_portal",
                    "lit portal": "nether_portal", "frame": "portal_frame", "coal": "fuel", "charcoal": "fuel",
                    "logs": "wood", "table": "crafting_table", "workbench": "crafting_table", "stone": "cobblestone",
                    "cobble": "cobblestone", "steak": "food", "dirt": "scaffold_dirt", "torch": "torches",
                    "spare": "spare_shield", "second shield": "spare_shield", "flint": "flint_and_steel"}


def progress_target(text):
    """S93 "progress to X": the spoken X ("diamonds", "the nether portal", "an iron pickaxe") -> a milestone id, or None
    (mind asks; never a guess). Pass it as progression_next(..., target=id) and stop the run when it says milestone None."""
    from agenda.progression import BY_ID
    t = re.sub(r"^(?:the|a|an|some|my|our)\s+", "", " ".join(str(text or "").lower().replace("-", " ").split()))
    for c in (t, t.rstrip("s") if not t.endswith("ss") else t):
        if c in PROGRESS_ALIASES:
            return PROGRESS_ALIASES[c]
        for k in (c.replace(" ", "_"), c.replace(" ", "_") + "s"):
            if k in BY_ID:
                return k
    return None


def progression_next(state, placed=(), portal_light=False, target=None):
    """O175 PROGRESSION MODE, one call for mind: the live state frame (inventory + equipment) and the block ids Eve
    knows are placed (mind's block memory) -> {"milestone", "say", "commands", "done", "blocked"} from progression.py."""
    from agenda.progression import next_step
    inv = list(state.get("inventory") or [])
    listed = {s.get("item") for s in inv if s}
    has_extra_slots = any(int(s.get("slot", 0)) >= 36 for s in inv if s)   # link-protocol lists slots 0-35 only
    for where, item in (state.get("equipment") or {}).items():   # mainhand is a hotbar slot, already listed;
        if not (isinstance(item, str) and item):                  # offhand/armor are separate slots (O220e: a shield
            continue                                              # in the offhand + one in the bag = 2 shields)
        if (item not in listed) if where == "mainhand" else not has_extra_slots:
            inv.append({"item": item, "count": 1})
    return next_step(inv, placed, portal_light, target)   # O193: portal_light only while the live sense advertises it


PORTAL_LIGHT_SENSE = (0, 1, 27)   # O193: sense 0.1.27 carries the scoped allow:portal_light exception (no capability key)


def sense_portal_light(hello):
    """portal_light for progression_next, from the LIVE sense hello (never from the install plan): mod_version >=
    0.1.27 compared as dotted integers (0.1.27 > 0.1.9). The per-frame authority stays sense's permission.check
    {effect: use_hazard, hazard: fire, dim, pos, face} -> rule_id "portal_light" (else "hazard:fire")."""
    try:
        v = tuple(int(x) for x in str((hello or {}).get("mod_version", "")).split("-")[0].split("."))
    except ValueError:
        return False
    return v >= PORTAL_LIGHT_SENSE
