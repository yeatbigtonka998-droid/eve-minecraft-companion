"""O175 PROGRESSION MODE, mind side (records/DIAMOND-WALKTHROUGH.md; design: DESIGN-O175-progression-mind.md).

"let's progress / what's next / keep going / progress to diamonds": agenda's progression_next(state, placed) gives the
next milestone, its line and its owner-style commands; she says the line and runs the commands in turn through the
normal owner path (each waits for its action to end), re-evaluates after every milestone, and goes on - until the
target milestone, a failure, a question, a stop or any new order of his. "what do we need for X": the line only.
Also maps the command forms the table uses that the rule map did not have: "prospect <ore> down to y N [for M
minutes]", "shear N sheep", "smelt N logs into charcoal". Nothing is stored: the ledger is the live frame."""
import asyncio
import json
import logging
import re
import sys
import time

log = logging.getLogger("eve.mind")

# O205 step 4: the owner phrases (PROGRESS / NEED_FOR / PROSPECT / SHEAR / CHARCOAL / WHY / AGAIN)
# are the one grammar's, unchanged
from .grammar import AGAIN, CHARCOAL, NEED_FOR, PROGRESS, PROSPECT, SHEAR, WHY  # noqa: E402,F401

ASK_TTL = 300.0                                       # "try again" / "why" refer to a stop this recent
ORES = ("coal", "iron", "copper", "gold", "diamond", "redstone", "lapis", "emerald")
DEFAULT_COUNT = 8                                    # a prospect for an ore the table has no milestone for
WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "some": 3}
MAX_MILESTONES = 30
RERUNS = 2                                           # O251: a short milestone is run again this often
RESUMES, REFLEX_WAIT_S = 5, 60.0       # O214: reflex-interrupted steps resumed per milestone


def adapter(mind):
    p = str(mind.cfg.root / "parts" / "mind")
    if p not in sys.path:
        sys.path.insert(0, p)
    from agenda import adapter as a
    return a


def wanted(have):
    """The amounts in a table `have` predicate ({item|tag, n} or {any: [...]})."""
    return [have["n"]] if "n" in have else [n for h in have.get("any", []) for n in wanted(h)]


def table_count(mind, ore):
    """O240 (one source of truth): how many the progression wants of `ore` - the `have` amount of agenda's milestone
    whose prospect line names it; None when the table has none (or agenda is missing here)."""
    try:
        adapter(mind)
        from agenda import progression as table
    except Exception:
        return None
    for m in table.MILESTONES:
        if any((pm := PROSPECT.match(c)) and pm[1].lower().rstrip("s") == ore for c in m["commands"]):
            return max(wanted(m["have"]) or [None])
    return None


def portal_light(mind, a):
    """O193: the LIVE sense's hello decides (mod_version >= 0.1.27), never the install plan."""
    link = mind.hub.links.get("sense") if mind.hub else None
    return bool(link is not None and a.sense_portal_light(link.hello))


def normalize(text):
    """'smelt 4 logs into charcoal' -> 'smelt 4 logs' (the smelt rule already makes charcoal from logs)."""
    return CHARCOAL.sub("", text) if re.match(r"^\s*(?:smelt|burn|cook)\b", text, re.I) else text


async def placed(mind, st):
    """The block ids standing near her (stations she can use) plus the marks the mind sets (portal frame built)."""
    blocks = await mind.blocks_near(st, 8, (-2, 2)) or {}
    return set(blocks.values()) | set(mind.progress_marks)


async def owner_line(mind, text, sender, source, sender_pos=None):
    """None = not a progression / prospect / shear line."""
    t = text.strip()
    if WHY.match(t):                                  # S93: answered from the last stop, else the last failure line
        stop = mind.last_stop if mind.last_stop and time.monotonic() - mind.last_stop["at"] < ASK_TTL else None
        line = (f"I was on {stop['milestone'].replace('_', ' ')} ('{stop['cmd']}'): {stop['why']}" if stop else
                mind.last_failure or "I haven't stopped on anything.")
        await mind.say(line)
        return {"ok": True, "reply": line}
    if AGAIN.match(t) and mind.last_stop and time.monotonic() - mind.last_stop["at"] < ASK_TTL:
        return await retry(mind, mind.last_stop, sender, source)
    m = PROSPECT.match(t)
    if m:
        ore = m[1].lower().rstrip("s")
        if ore not in ORES and ore != "obsidian":
            return None
        params = {("block" if ore == "obsidian" else "tag"): "minecraft:obsidian" if ore == "obsidian"
                  else f"minecraft:{ore}_ores", "count": table_count(mind, ore) or DEFAULT_COUNT, "torch_every": 8, "min_health": 10,
                  "minutes": min(30, int(m[3])) if m[3] else 8}
        if m[2] is not None:
            params["descend_to"] = max(-58, min(319, int(m[2])))   # ponytail: clip to sense's min_y once it reports one
        from .workstations import prospect_from_open_ground
        return await prospect_from_open_ground(mind, params, source, f"Prospecting for {ore}.")
    m = SHEAR.match(t) if "shear_sheep" not in mind.registry.skills else None   # O245: shear_sheep goes the phrase way
    if m:
        n = int(m[1]) if m[1].isdigit() else WORDS.get(m[1].lower(), 1)
        await mind.say(f"Shearing {n} sheep.")
        return await mind.owner_do("gather", {"product": "wool", "count": n}, source)
    m, need = PROGRESS.match(t), NEED_FOR.match(t)
    if not (m or need):
        return None
    st = mind.fresh_state()
    if not st:
        await mind.say("I can't see my inventory right now, so I can't tell where we are.")
        return {"ok": False, "error": "stale_state"}
    try:
        a = adapter(mind)
    except Exception as e:                            # agenda missing on this host: say so, never guess
        await mind.say("I can't work out our progress right now.")
        return {"ok": False, "error": "no_agenda", "detail": str(e)[:120]}
    raw = ((m or need)[1] or "").strip()
    target = (a.progress_target(raw) if hasattr(a, "progress_target") else raw.lower().replace(" ", "_")) if raw else None
    if raw and target is None:                        # agenda S93: an X it does not know - ask, never guess
        q = f"Which step do you mean by '{raw}'?"
        await mind.say(q)
        return {"ok": True, "question": q}
    step = a.progression_next(st, await placed(mind, st), portal_light=portal_light(mind, a), target=target)
    await mind.say(step["say"])
    if need or step.get("milestone") is None or not step.get("commands"):   # [] = waiting (fire off)
        return {"ok": True, "reply": step["say"], "milestone": step.get("milestone")}
    mind.progress_gen += 1
    mind.spawn(run(mind, a, step, target, sender, source, mind.progress_gen))
    return {"ok": True, "progressing": step.get("milestone"), "until": target}


async def wait_done(mind, aid):
    ev = mind.done.get(aid)
    if ev is not None:
        await ev.wait()


async def resumed(mind, aid, source, budget, alive):
    """O214 (CP-86 17:24:53: creeper_flee pre-empted "mine 12 stone" and the run stopped): an action a reflex
    interrupted runs again for what is left once the reflex has cleared, at most budget[0] times; silent unless the
    reflex spoke. True when it ends ok."""
    while True:
        await wait_done(mind, aid)
        row = mind.action(aid)
        if row is None or row["status"] == "ok":
            return row is not None
        fut = mind.followups.pop(aid, None)               # CP-86: a fallback (dig_for's prospect) carries the step on
        if fut is not None:
            aid = await fut
            if not aid:
                return False
            mind.resumable.add(aid)
            continue
        if row["code"] != "interrupted" or not alive():
            return False
        if budget[0] <= 0:
            await mind.say(mind.failure_line(row, row["code"]))   # given up: the line finish() held back
            return False
        t0, calm = time.monotonic(), None                  # the reflex clear for 1 s, 60 s at most
        while time.monotonic() - t0 < REFLEX_WAIT_S and alive():
            if ((mind.state or {}).get("control") or {}).get("reflex_active") or mind.creeper.engaged:
                calm = None
            elif calm is None:
                calm = time.monotonic()
            elif time.monotonic() - calm >= 1.0:
                break
            await asyncio.sleep(0.25)
        if not alive():
            return False
        if calm is None:
            await mind.say(mind.failure_line(row, row["code"]))
            return False
        params = json.loads(row["params"])
        ev = json.loads(row["evidence"] or "{}") if isinstance(row["evidence"], str) else (row["evidence"] or {})
        if isinstance(params.get("count"), int):
            got = next((ev[k] for k in ("mined", "gathered", "collected", "count")
                        if isinstance(ev.get(k), int) and not isinstance(ev.get(k), bool)), 0)
            params["count"] -= got
            if params["count"] <= 0:
                return True
        budget[0] -= 1
        log.info("progress: resuming %s after a reflex (%s left, %d resumes left)", row["skill"], params.get("count"),
                 budget[0])
        r = await mind.owner_do(row["skill"], params, source)
        aid = r.get("action_id") if r.get("ok") is not False else None
        if not aid:
            return False
        mind.resumable.add(aid)


async def retry(mind, stop, sender, source):
    """S93: his yes / "try again" after a stopped run - the same run again, to the same target."""
    target = stop.get("target")
    return await owner_line(mind, "let's progress" + (f" to {target.replace('_', ' ')}" if target else ""), sender,
                            source)


async def run_command(mind, cmd, sender, source, budget=None, alive=lambda: True):
    """One table command through the owner path; True when it ended ok (O214: reflex interruptions resumed)."""
    while getattr(mind, "autofight", None) and alive():
        await asyncio.sleep(0.5)                          # H-autofight: the frame is suspended - no next step
    r = await mind._owner_text(normalize(cmd), sender, source)
    if not isinstance(r, dict) or r.get("ok") is False or r.get("question") or r.get("deferred"):
        return False
    aid = r.get("action_id")
    if aid:
        mind.resumable.add(aid)
        return await resumed(mind, aid, source, budget if budget is not None else [RESUMES], alive)
    task = getattr(mind, "steps_task", None) if r.get("steps") else None
    if task is not None:
        ok = await task
        return ok is not False
    return True


def shortfall(st, milestone):
    """O251: what the milestone still wants, from agenda's own entry - "I have 26 of the 30 gravel I want"."""
    try:
        from agenda.progression import BY_ID, TAGS
        have = BY_ID[milestone]["have"]
    except Exception:
        return "it isn't done yet"
    inv = st.get("inventory") or []
    for o in have.get("any") or [have]:
        if "item" in o or "tag" in o:
            hit = (lambda i: i.get("item") == o["item"]) if "item" in o else \
                (lambda i: TAGS[o["tag"]](str(i.get("item") or "")))
            got = sum(int(i.get("count") or 0) for i in inv if hit(i))
            name = (o.get("item") or o["tag"]).split(":")[-1].replace("_", " ")
            return f"I have {got} of the {o['n']} {name} I want"
    return "it isn't done yet"


async def run(mind, a, step, target, sender, source, gen):
    alive = lambda: mind.progress_gen == gen and not mind.stopped   # noqa: E731
    tries = 0
    for _ in range(MAX_MILESTONES):
        log.info("progress: milestone %s start %s", step["milestone"], step.get("commands"))
        budget = [RESUMES]
        for i, cmd in enumerate(step.get("commands") or []):
            if i:                                         # S93: a later command is a fallback ("mine 8 iron ore", then
                await asyncio.sleep(1.0)                  #   "prospect ... down to y 16") - skipped once the ledger
                st = mind.fresh_state() or {}             #   says the milestone is done
                if a.progression_next(st, await placed(mind, st), portal_light=portal_light(mind, a),
                                      target=target).get("milestone") != step["milestone"]:
                    break
                await mind.say(f"Not enough yet - next I'll {cmd}.")   # S93: she says why the fallback runs
            if mind.progress_gen != gen or mind.stopped:
                log.info("progress: milestone %s ended by owner/stop", step["milestone"])
                return                                    # his new order or a stop ended the run
            n0 = mind.said_n
            if not await run_command(mind, cmd, sender, source, budget, alive):
                log.info("progress: milestone %s stopped at %r", step["milestone"], cmd)
                if mind.progress_gen == gen:              # S93: why, from the step's own line, and one question
                    why = (mind.last_line if mind.said_n > n0 and mind.last_line else "it didn't work out").rstrip(" .")
                    mind.last_stop = {"milestone": step["milestone"], "cmd": cmd, "why": why + ".", "target": target,
                                      "at": time.monotonic()}
                    q = f"I stopped at {step['milestone'].replace('_', ' ')} - {why}. Should I try again?"
                    await mind.say(q)
                    mind.asked = {"kind": "progress", "target": target, "sender": sender, "at": time.monotonic()}
                return
        if mind.progress_gen != gen:
            return
        await asyncio.sleep(1.0)                          # the next frame shows what the step gained
        st = mind.fresh_state() or {}
        nxt = a.progression_next(st, await placed(mind, st), portal_light=portal_light(mind, a), target=target)
        log.info("progress: milestone %s end; next %s", step["milestone"], nxt.get("milestone"))
        if nxt.get("milestone") is None or not nxt.get("commands"):   # agenda: "<X> is done." / waiting
            await mind.say(nxt["say"])
            return
        if nxt.get("milestone") == step.get("milestone"):
            tries += 1                                    # O251: every step ok, the want still short (flint from
            if tries <= RERUNS:                           #   gravel): the milestone again, twice at most
                await mind.say(f"Not enough yet - trying {step['milestone'].replace('_', ' ')} again.")
                continue
            why = shortfall(st, step["milestone"])
            mind.last_stop = {"milestone": step["milestone"], "cmd": (step.get("commands") or ["?"])[-1],
                              "why": why + ".", "target": target, "at": time.monotonic()}
            await mind.say(f"I stopped at {step['milestone'].replace('_', ' ')} - {why}. Should I try again?")
            mind.asked = {"kind": "progress", "target": target, "sender": sender, "at": time.monotonic()}
            return
        await mind.say(f"{step['milestone'].replace('_', ' ').capitalize()} done. {nxt['say']}")
        step, tries = nxt, 0
