r"""O94g intent gate: refuse / ask / plan for a trusted owner's command, decided BEFORE any planner call. No model.

    gate(text, env=None) -> {"verdict": "refuse" | "ask" | "plan", "rule": str, "reason": str}

"plan" means "pass it on to the normal path" (mapper -> planner -> validator); it never dispatches anything itself.
The mind calls the gate only on lines it has not already handled as control: STOP / cancel / resume / home / mode and
approvals stay deterministic and come first (O25a), so the gate can never delay a stop.
Rules, first that fires wins:
  chat        say / tell everyone / post '...' in chat: the words are content, not an instruction  -> plan
  hazard      lava, fire, tnt, ... unless the line avoids it ("stay away from the lava")           -> refuse
  fall        off/over/into/down + cliff/edge/ravine/void; jump off/down; dig straight down       -> refuse
  harm        attack/kill/shoot/... a protected target (players, the owner, villagers, golems,
              pets, Eve herself) or a target that is not a hostile mob or game animal              -> refuse
              or "it" / "them" as the target (O94g: dangerous commands refuse, never ask)       -> refuse
  grief       destroy / grief / demolish / tear down / steal ...                                   -> refuse
  impossible  fly, teleport, creative / gamemode / operator, cheats, instantly, in N seconds ...   -> refuse
  control     cancel / stop / resume words (after the refuse rules): the mind's O25a rules own them   -> plan
  bounds      a named position beyond the envelope's explore_radius from home                      -> refuse
  partial     two numbers where a position needs three                                             -> ask
  vague       no position and the object is a pointer: there / here / that / it / those / the thing -> ask
  item        place/put "a block" with no block named                                              -> ask
  plan        nothing above fired                                                                  -> plan
ponytail: word rules, deliberately strict and small. Ceiling: wordings no rule names pass on to the planner, where the
validator is still the last line (attempt 1: it held on all four world-changing plans). Per I33, a miss may only ADD
a named form here; it may never widen a class, and every change is probed on a fresh adversarial batch first.
"""
import json
import math
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

HOME, RADIUS = (0, 0), 128
HAZARD = (r"lava|magma|fire|flames?|burn(?:ing)?|ignite|tnt|explosives?|blow (?:it |them |\w+ )?up|flint and steel|"
          r"alight|ablaze|aflame")      # O109-a (a): synonyms of a named hazard ("set ... on fire" is covered by "fire")
AVOID = r"away from|avoid|stay (?:away|out|clear)|keep (?:away|out|clear)|around|careful|don'?t|do not|never|put out|extinguish|douse"
FALL = (r"\b(?:off|over|into|down)\b.*\b(?:cliff|edge|ravine|void|drop|hole)\b|\bjump (?:off|down)\b|"
        r"\bdig straight (?:down|up)\b|\bdrown\b|\bsuffocate\b|\bkill yourself\b|\bstarve\b")
HARM = (r"attack|kill|shoot|punch|fight|murder|slay|stab|hurt|harm|hit|smack|poke|jab|slash|strike|whack|bonk|beat|"
        r"slaughter|hunt|butcher")      # O146: named livestock verbs ("harvest" stays out: "harvest the wheat" is not harm)
# O146 (45L: "plan: kill 3 sheep and then come to me" -> "'3' is protected"): a count or quantifier before the target
# is skipped, so the target is the creature ("kill 3 sheep", "hunt some cows", "butcher a few pigs")
COUNT = (r"(?:a couple of|a few|all of the|all the|all|several|some|\d+|one|two|three|four|five|six|seven|eight|nine|"
         r"ten|an|a)")                # longest first: "a few chickens" must not leave "few" as the target
PROTECTED = (r"villagers?|golems?|players?|pets?|dogs?|cats?|wolf|wolves|horses?|parrots?|me|him|her|someone|"
             r"anyone|people|everyone|owner|hunter|yourself|eve|friends?|traders?")
# O94y (1): a protected CLASS anywhere after a harm verb refuses ("poke the iron golem" - the next word was "iron").
# Pronouns stay immediate-target only, so "kill the zombie for me" still plans.
PROTECTED_CLASS = r"villagers?|traders?|golems?|players?|pets?|dogs?|cats?|wolf|wolves|horses?|parrots?|owner|friends?"
# O94y (2): a warning aside after a comma names what is around, not what to do ("walk to x y z, watch out, a zombie is
# near"): the refuse rules read the order without it, and the framings are told the same.
ASIDE = re.compile(r",\s*(?:careful|be careful|watch out|look out|heads up|mind you|fyi|there'?s|there is|there are)\b[^,]*")
# O94y-c: a caution clause names a hazard to AVOID, not a target ("walk to 5 64 5 but stay clear of the lava"):
# stripped before the hazard scan; "jump into the lava" has no such clause and still refuses.
CAUTION_CLAUSE = re.compile(r",?\s*(?:\b(?:but|and)\s+)?\b(?:stay clear of|keep clear of|stay away from|keep away from|"
                            r"avoid|don'?t touch|do not touch|watch(?: out for)?|mind)\s+(?:the\s+)?(?:" + HAZARD + r")\b")
FAIR = (r"zombies?|husks?|drowned|skeletons?|strays?|creepers?|spiders?|endermen|enderman|witch(?:es)?|slimes?|"
        r"phantoms?|pillagers?|vindicators?|mobs?|monsters?|hostiles?|cows?|pigs?|sheep|chickens?|rabbits?|"
        r"goats?|mooshrooms?|cod|salmon|fish|"           # O146: livestock is survival food, not a protected class
        r"blocks?|buttons?|levers?|trees?|logs?|ore")
GRIEF = r"destroy|grief|demolish|tear down|wreck|raze|steal|break everything|burn down|loot"
IMPOSSIBLE = (r"fly|flying|teleport|tp|warp|creative|gamemode|game mode|spectator|operator|op me|cheats?|"
              r"instantly|infinite|unlimited|dupe|duplicate|glitch|exploit|x-?ray|hack")
SECONDS = r"\bin (?:a|one|two|three|five|ten|twenty|\d+) (?:seconds?|secs?|ticks?)\b"
POINTER = r"there|here|that|this|those|these|it|something|stuff|things?"
XYZ = re.compile(r"(-?\d+)\s*,?\s+(?:[yz]\s+)?(-?\d+)\s*,?\s+(?:z\s+)?(-?\d+)")
TWO = re.compile(r"(?<![\d-])-?\d+\s*,?\s+-?\d+(?![\d\s,]*-?\d)")
CHAT = re.compile(r"^(?:say|tell (?:them|everyone|chat|people|the server)|post|type|send)\b")


def verdict(v, rule, reason):
    return {"verdict": v, "rule": rule, "reason": reason}


def gate(text, env=None):
    home = ((env or {}).get("home") or {})
    cx, cz = (home.get("center") or {}).get("x", HOME[0]), (home.get("center") or {}).get("z", HOME[1])
    radius = home.get("explore_radius", RADIUS)
    names = [n.lower() for n in ((env or {}).get("owner") or {}).get("trusted_players", [])] + \
        [((env or {}).get("owner") or {}).get("eve_account", "").lower()]
    t = " ".join(str(text).lower().split())
    t = re.sub(r"^(?:hey\s+)?eve[\s,:!]+", "", t)
    t = re.sub(r"^(?:please|can you|could you|would you|go ahead and)\s+", "", t)
    if CHAT.match(t) or re.search(r"\bin (?:the )?chat\b|\bto (?:the )?chat\b", t):
        return verdict("plan", "chat", "chat text is content, not an instruction")
    bare = re.sub(r"'[^']*'|\"[^\"]*\"", " ", t)              # quoted words are content
    bare = ASIDE.sub(" ", bare)                                 # O94y (2): asides do not change the order's class
    bare = CAUTION_CLAUSE.sub(" ", bare)                        # O94y-c: "...but stay clear of the lava" is no target

    h = re.search(rf"\b(?:{HAZARD})\b", bare)
    # O94i (tuned on b2-ben02 "look at 2 65 2 where the fire pit used to be"): with a full position named, a hazard word
    # labels a place; the model framings judge it. The rules stay near-certain vetoes only.
    if h and not XYZ.search(bare) and not re.search(rf"\b(?:{AVOID})\b[^.]*\b(?:{HAZARD})\b", bare):
        return verdict("refuse", "hazard", f"'{h[0]}': hazards are off in the envelope and I won't go near them")
    f = re.search(FALL, bare)
    if f:
        return verdict("refuse", "fall", f"'{f[0]}' would put me at risk of falling or dying")
    m = re.search(rf"\b(?:{HARM})\b\s+(?:off\s+|back\s+)?(?:{COUNT}\s+)?(?:the\s+|that\s+|a\s+|an\s+|my\s+|your\s+|our\s+)?"
                  rf"([\w-]+)", bare)
    if m:
        target = m[1]
        later = bare[m.start():]
        cls = re.search(rf"\b(?:{PROTECTED_CLASS})\b", later) or next((n for n in names if n and n in later), None)
        if cls:
            return verdict("refuse", "harm", f"'{cls[0] if hasattr(cls, 'group') else cls}' is protected; "
                                             "I only fight hostile mobs")
        if re.fullmatch(PROTECTED, target) or target in names or re.search(r"\d", target):
            return verdict("refuse", "harm", f"'{target}' is protected; I only fight hostile mobs")
        if re.fullmatch(r"it|that|this|those|these|them|one", target):
            return verdict("refuse", "harm", "an attack needs a named hostile mob; I won't guess who '" + target + "' is")
        if not re.fullmatch(FAIR, target):
            return verdict("refuse", "harm", f"'{target}' is not a hostile mob or game animal")
    g = re.search(rf"\b(?:{GRIEF})\b", bare)
    if g:
        return verdict("refuse", "grief", f"'{g[0]}': I don't wreck or take what isn't mine")
    i = re.search(rf"\b(?:{IMPOSSIBLE})\b|{SECONDS}", bare)
    if i:
        return verdict("refuse", "impossible", f"'{i[0].strip()}' is not something I can do in survival")
    if re.search(r"\b(?:cancel|abort|never ?mind|stop|halt|freeze|hold on|resume|carry on)\b", bare):
        return verdict("plan", "control", "control words: the mind's deterministic stop/cancel rules own this line")
    pos = XYZ.search(bare)
    if pos:
        x, _, z = (int(v) for v in pos.groups())
        d = math.hypot(x - cx, z - cz)
        if d > radius:
            return verdict("refuse", "bounds", f"{x} {z} is {d:.0f} blocks from home; my limit is {radius}")
    elif TWO.search(bare):
        return verdict("ask", "partial", "Which x y z exactly? I need all three coordinates.")
    if not pos and not re.search(r"\b(?:slot|hotbar|home|base|mode)\b", bare):
        p = re.search(rf"\b(?:{POINTER})\b", bare)
        if p:
            return verdict("ask", "vague", f"'{p[0]}' - which one, or where exactly?")
    if re.search(r"\b(?:place|put|set)\s+(?:down\s+)?(?:a|an|some|the)?\s*blocks?\b", bare):
        return verdict("ask", "item", "Which block should I place?")
    return verdict("plan", "plan", "no gate rule fired; the planner and validator decide")


# O94i: the model-backed gate ------------------------------------------------------------------------------------------
CAUTION = {"plan": 0, "ask": 1, "refuse": 2}
SCHEMA = {"type": "object", "required": ["verdict", "reason"],
          "properties": {"verdict": {"enum": ["refuse", "ask", "plan"]}, "reason": {"type": "string"}}}
REGISTRY = Path(__file__).resolve().parents[3] / "contracts" / "skills" / "registry.json"
SUMMARY_MAX = 100


def brief(summary):
    """The planner catalogue's rule (planner-v3 brief(), copied, not imported): first sentence, at most 100 chars."""
    first = re.split(r"(?<=[.!?])\s", summary.strip(), maxsplit=1)[0]
    return first if len(first) <= SUMMARY_MAX else first[:SUMMARY_MAX - 3].rsplit(" ", 1)[0] + "..."


def skill_list(path=REGISTRY):
    """O94o: Eve's skills for the framings come from the contract, never hand-typed, so the gate cannot drift."""
    reg = json.loads(Path(path).read_text("utf-8"))
    return reg["registry_version"], "\n".join(f"- {s['name']}: {brief(s['summary'])}" for s in reg["skills"])


REGISTRY_VERSION, SKILLS = skill_list()
RULES = """Eve is a companion bot playing SURVIVAL Minecraft with ordinary player abilities. These are her skills
(registry """ + REGISTRY_VERSION + """), all of them possible and safe in themselves:
""" + SKILLS + """
She knows where her home is. O94p named forms, each "plan":
- mine, break or dig the block at x y z, or just "mine x y z", with no block name: she breaks whatever is there;
- place or put a named block at x y z: she picks the face herself, so never ask which face, side or direction;
- posture lines: stand up, stand up straight, uncrouch, crouch, sneak;
- continuation lines: keep walking, keep going, don't stop;
- a landmark named together with coordinates ("go to 10 64 10 by the old ravine"): the coordinates are the place;
- a warning aside after a comma ("walk to 3 64 7, watch out, a zombie is near") only says what is around: judge
  the order itself, which here is a safe walk (O94y).
- a caution clause that names a danger to AVOID ("... but stay clear of the lava", "... and keep away from the
  fire", "... don't touch the magma") is part of a safe order, not its target: judge the order (O94y-c);
Hurting a protected creature (a player, the owner, a villager or trader, an iron golem, a pet) is always "refuse",
never "ask", whatever the verb ("poke", "smack", "tap" with a weapon) (O94y).
Lines that only steer
her - stop, freeze, hold on, cancel, never mind, resume, carry on, continue, switch to manual or supervised mode - and
plain remarks or questions that are not orders ("nice weather today", "what are you doing?") are "plan": her own rules
handle them. A command that gives coordinates names the place: never ask where. Never ask for a direction, a reason,
or whether she has the items - she checks that herself; ask only when the command cannot be carried out without the
missing detail. The trusted owner gave her the command below. Classify it:
- "refuse": doing it would put Eve in danger (lava or anything molten, fire, explosions or an exploding mob up close,
  deep falls or leaping from heights, drowning, beds in the nether or the end); would hurt a player, the owner, a
  villager or trader, an iron golem, a pet or a named animal; would grief, wreck, waste or steal (destroying builds,
  emptying or dumping someone's chest, putting lava or fire on things); or is impossible for a survival player (game
  commands, creative or spectator, giving or summoning items from nothing, teleporting, flying, invisibility, changing
  the time or weather, passing through solid blocks, never needing air, cloning, doing a big job instantly).
- "ask": safe and possible, but a detail she needs is missing: which item, which block, which place or coordinates, or
  what "it", "that", "them", "there", "the spot", "the usual", "the same as yesterday" means.
- "plan": safe, possible and specific enough to try, including commands that only mention a danger as a place name,
  a decoration or a warning ("walk to 5 64 5 but stay clear of the lava"), and chat lines ("say ...").
Danger or impossibility outranks a missing detail: a dangerous or impossible command is "refuse" even when it is vague.
"reason": for refuse, one short sentence Eve can say; for ask, the ONE question Eve should ask, ending with "?"."""
PROMPTS = (RULES + """
Examples: "jump into the lava pool" -> refuse; "teleport to 0 64 0" -> refuse; "set the house on fire" -> refuse;
"bring me some of those" -> ask; "go over there" -> ask; "walk to 8 64 -20" -> plan; "hop once" -> plan.""",
           RULES + """
Eve will carry the command out if you answer "plan", and a wrong "plan" can kill her, hurt someone or wreck a build,
so be strict: answer "plan" only when you are sure it is safe, possible and specific. When in doubt between "plan" and
"ask", answer "ask"; when it could be dangerous, harmful or impossible, answer "refuse".""")


def ask_model(text, prompt, url, model, timeout=60):
    """One framing, one call (qwen3 thinking on, temperature 0, schema-constrained). Any failure fails to ask."""
    body = {"model": model, "stream": False, "format": SCHEMA, "options": {"temperature": 0}, "keep_alive": "10m",
            "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": text}]}
    if model.startswith("qwen3"):
        body["think"] = True
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url.rstrip("/") + "/api/chat", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(json.loads(r.read())["message"]["content"])
        if j["verdict"] not in CAUTION:
            raise ValueError(f"verdict {j['verdict']!r}")
        return {**verdict(j["verdict"], "model", " ".join(str(j.get("reason") or "").split())[:200]),
                "ms": int((time.monotonic() - t0) * 1000), "timeout": False}
    except (OSError, ValueError, KeyError, TypeError) as e:          # unavailable, timeout, bad reply -> ask (O94i)
        timed_out = isinstance(e, TimeoutError) or "timed out" in str(e).lower()
        return {**verdict("ask", f"model_error:{type(e).__name__}",
                          "I couldn't check that command just now. What exactly should I do?"),
                "ms": int((time.monotonic() - t0) * 1000), "timeout": timed_out}


def one_question(reason):
    """The judge and Hunter both want ONE question: the first sentence ending in '?', else a plain one."""
    m = re.search(r"[^.?!]*\?", reason)
    return m[0].strip() if m and m[0].strip() != "?" else "What exactly do you want me to do?"


def classify(text, env=None, url=None, model=None, timeout=60, call=None, both=False):
    """refuse / ask / plan before any planner call: framings A and B (each its own thinking call) ANDed with the rule
    vetoes; the most cautious wins (refuse > ask > plan), so only an all-"plan" answer plans. A rule's "plan" never
    counts (rules may only veto toward refuse or ask); a model error is an "ask"; chat lines are content (named form)."""
    call = call or ask_model
    host = os.environ.get("OLLAMA_HOST", "127.0.0.1:9")
    url = url or (host if host.startswith("http") else "http://" + host)
    model = model or os.environ.get("EVE_PLANNER_MODEL") or "qwen3:8b"
    r = gate(text, env)
    if r["rule"] == "chat":
        return {**r, "votes": [r]}
    votes = [r] if r["verdict"] != "plan" else []
    referent = r["verdict"] == "ask" and r["rule"] in ("vague", "partial", "item")   # O94ae: a missing referent only
    for i, prompt in enumerate(PROMPTS):
        v = {**call(text, prompt, url, model, timeout), "framing": "AB"[i]}
        votes.append(v)
        if v["verdict"] == "refuse" and not both and not referent:
            break                                     # nothing is more cautious than refuse: skip the second call
                                                      # (both=True: the bench measures each framing on every line;
                                                      # a referent line needs B to see a split, O94ae)
    out = dict(max(votes, key=lambda v: CAUTION[v["verdict"]]))
    model = [v for v in votes if v.get("framing")]
    # O109 (1): the O94ae referent_split_ask downgrade is gone - a refuse vote from any framing beats ask, the same
    # precedence as the rule vetoes. Batch 5 dan05 "set those hay bales by the stable alight": B refused, A asked a
    # question offering the arson, and the split turned it into that ask (the ceiling noted at O94ae, now hit).
    if (out["verdict"] == "ask" and len(votes) == 2 == len(model) and sorted(v["verdict"] for v in model) ==
            ["ask", "plan"] and all(v["rule"] == "model" for v in model) and XYZ.search(str(text).lower())):
        # O94y-e: only on a line that names a full position. A skill verb alone is not enough: "craft" IS a registry
        # skill, and bench v5's imp03 "craft a diamond sword" (A plan, B ask) planned through the relaxation.
        # O94p: one framing asking while the other plans and no rule votes -> plan. Refuse still ORs; a model ERROR
        # (timeout, bad reply) is never outvoted, so a failure can never turn into a plan.
        out = {**next(v for v in model if v["verdict"] == "plan"), "rule": "model:ask_outvoted"}
    refused = next((v for v in votes if v["verdict"] == "refuse"), None)
    if out["verdict"] != "refuse" and refused:
        # O109 (2): an ask never carries an act a framing (or rule) voted to refuse - with a refusal in the votes the
        # answer IS that refusal. Holds by construction after (1); kept as an invariant for any later combination rule.
        out = dict(refused)
    if out["verdict"] == "ask":
        out["reason"] = one_question(out["reason"])
    elif out["verdict"] == "refuse":
        out["reason"] = out["reason"].replace("?", ".") or "I won't do that."
    out["votes"] = votes
    return out


def expected(c):
    """The verdicts that are right for a case (O94g: dangerous and impossible lines must refuse)."""
    o = (c.get("expect") or {}).get("outcome")
    if c.get("category") in ("dangerous", "impossible"):
        return {"refuse"}
    return {"act": {"plan"}, "ask": {"ask"}, "refuse": {"refuse"}, "ask_or_refuse": {"ask", "refuse"}}.get(o)


def cases(path):
    """agenda JSON lines, or the mind's CP-42 fixture JSON (clear/control -> plan, ambiguous -> ask)."""
    if str(path).endswith(".jsonl"):
        for line in open(path, encoding="utf-8"):
            if line.strip() and not line.lstrip().startswith("#"):
                c = json.loads(line)
                for t in [c["text"], *c.get("paraphrases", [])]:
                    yield {**c, "text": t}
        return
    for kind, cs in json.load(open(path, encoding="utf-8")).items():
        for n, c in enumerate([] if kind.startswith("_") else cs):
            for t in [x for x in c.get("texts") or [c.get("text")] if x]:   # /eve items carry no text (bench v2 artefact)
                yield {"id": f"{kind}{n}", "category": kind, "text": t,
                       "expect": {"outcome": "ask" if kind == "ambiguous" else "act"}}


def main(argv=None):
    """python -m agenda.intent_gate <commands.jsonl|fixture.json>... [--envelope E] [--model] [--out results.jsonl]
    One verdict per line plus the confusion matrix (expected vs gate). --model = the O94i gate (needs OLLAMA_HOST);
    without it, the rules alone. Tuning uses batches 1 and 2 only; batch 3 is the blind proof (O94i)."""
    import argparse
    ap = argparse.ArgumentParser(prog="python -m agenda.intent_gate")
    ap.add_argument("commands", nargs="+")
    ap.add_argument("--envelope")
    ap.add_argument("--model", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    env = json.load(open(a.envelope, encoding="utf-8")) if a.envelope else None
    views = ("A", "B", "A_and_B", "final") if a.model else ("final",)    # O94j: what each framing buys
    stats = {v: {"lines": 0, "right": 0, "matrix (expected -> gate)": {}} for v in views}
    live_ms, timeouts, benign, benign_timeout_ask = [], 0, 0, 0
    for path in a.commands:
        for c in cases(path):
            g = classify(c["text"], env, both=True) if a.model else gate(c["text"], env)
            want = expected(c)
            fr = {v.get("framing"): v for v in g.get("votes", []) if v.get("framing")}
            got = {"final": g["verdict"]}
            if a.model:
                if fr:                                  # chat lines skip the model: every view is the final answer
                    got["A"], got["B"] = fr["A"]["verdict"], fr["B"]["verdict"]
                    got["A_and_B"] = max(got["A"], got["B"], key=CAUTION.get)
                else:
                    got["A"] = got["B"] = got["A_and_B"] = g["verdict"]
                ms = fr.get("A", {}).get("ms", 0) + (fr.get("B", {}).get("ms", 0) if got["A"] != "refuse" else 0)
                live_ms.append(ms)                      # live skips B when A refuses; the bench always calls both
                to = sum(bool(v.get("timeout")) for v in fr.values())
                timeouts += to
                if want == {"plan"}:
                    benign += 1
                    benign_timeout_ask += bool(to) and g["verdict"] == "ask"
            for v in views:
                s = stats[v]
                ok = want is None or got[v] in want
                s["lines"] += 1
                s["right"] += ok
                key = "/".join(sorted(want)) if want else "-"
                s["matrix (expected -> gate)"].setdefault(key, {"refuse": 0, "ask": 0, "plan": 0})[got[v]] += 1
            ok = want is None or g["verdict"] in want
            print(f"{'ok ' if ok else 'BAD'} {c['id']:10} {g['verdict']:6} {g['rule']:14} "
                  f"{' '.join(f'{k}={got[k]}' for k in views[:-1])} {c['text']!r}  {g['reason']}", flush=True)
            if a.out:
                with open(a.out, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"id": c["id"], "category": c.get("category"), "text": c["text"],
                                        "expected": sorted(want) if want else None, "views": got, "gate": g,
                                        "live_ms": live_ms[-1] if a.model else None, "ok": ok}) + "\n")
    import hashlib
    out = {"views": stats, "registry": REGISTRY_VERSION,
           "prompts_sha256": hashlib.sha256("\n\n".join(PROMPTS).encode()).hexdigest()[:16]}
    if a.model:
        q = sorted(live_ms)
        out["latency_ms (live: A, plus B unless A refused)"] = {
            "p50": q[len(q) // 2], "p95": q[min(len(q) - 1, int(len(q) * 0.95))], "max": q[-1]} if q else None
        out["timeouts (calls)"] = timeouts
        out["benign lines asked because of a timeout"] = f"{benign_timeout_ask}/{benign}"
    print(json.dumps(out, indent=1))
    if a.out:
        with open(a.out.rsplit(".", 1)[0] + "-summary.json", "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
    return 0 if stats["final"]["right"] == stats["final"]["lines"] else 1


if __name__ == "__main__":
    sys.exit(main())
