"""O111-b: plain owner phrases -> builder, skills and combat dispatches, by rule (no model). Sources, one per part:
parts/builder/OWNER-PHRASES.md (23db191e), parts/skills/OWNER-PHRASES.md (86b928a0), parts/combat/OWNER-PHRASES.md
(272569b5..085f4011). Every param comes from the owner's words or the mind's own records (state frame, approvals, zone
snapshots, the storage index) - never guessed; a missing one is ONE question back, and a refusal is said in the owner's
words. A phrase never overrides the envelope, the gate or a stop: every dispatch still goes through do() / the loops.

match(text, view) -> None (not one of these phrases) or one of:
  {"do": [(skill, params), ...], "say": line}       one dispatch, or several in turn (deposit "everything")
  {"start": "build" | "repair" | "bridge", "args": {...}, "say": line}   the mind's own loops (CP-51 / CP-52 / CP-53)
  {"cancel": True, "say": line}
  {"ask": line}                                      his next short line completes the order (the mind's rerun ask)
  {"say": line}                                      a refusal or an answer, nothing dispatched
  {"confirm_chest": pos, "ask", "then": steps, "say"} O135: "The chest at x y z?" - his yes runs `then` there
  {"scan": {center, radius}}                          O135: no chest known near him - the mind looks (query.blocks) once
view: {"st": state frame or None, "owner_pos": {dim,x,y,z} or None, "home": envelope home, "zones": [zone specs],
       "designs": [{project_id, version, design_hash, category}], "snapshots": [{snapshot_id, zone_id, created_ts}],
       "containers": [{pos, kind, items: {id: n}, shared: bool, placed_ms?}], "named_chest": pos or None,
       "scanned": bool}
"""
import math
import re
import time

from .commands import item_id
from .planner import resource_params

LEAD = r"^\s*(?:hey\s+)?(?:eve[\s,:!]+)?(?:please\s+|can you\s+|could you\s+)?(?:go\s+(?:and\s+)?)?"
XYZ = r"(-?\d+)[\s,]+(-?\d+)[\s,]+(-?\d+)"
NUMWORD = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8, "ten": 10,
           "a couple of": 2, "a few": 4}
ORES = ("coal", "iron", "copper", "gold", "diamond", "redstone", "lapis", "emerald")
HOSTILE_WORDS = r"(?:zombie villager|zombie|husk|drowned|skeleton|stray|spider|cave spider|creeper|witch|slime|pillager|vindicator|" \
                r"phantom|enderman|silverfish|blaze|zombified piglin|piglin|hoglin|ghast|magma cube|warden|mob)s?"
TIERS = ("wooden", "stone", "iron", "golden", "diamond", "netherite")
ARCHERS = ("skeleton", "stray", "bogged", "pillager")   # O139: they shoot; with no bow she rushes them
TOOLS = ("pickaxe", "axe", "shovel", "hoe", "sword")
SMELT_IN = {"iron": "minecraft:raw_iron", "gold": "minecraft:raw_gold", "copper": "minecraft:raw_copper",
            "beef": "minecraft:beef", "steak": "minecraft:beef", "chicken": "minecraft:chicken",
            "pork": "minecraft:porkchop", "porkchop": "minecraft:porkchop", "mutton": "minecraft:mutton",
            "rabbit": "minecraft:rabbit", "cod": "minecraft:cod", "salmon": "minecraft:salmon",
            "potato": "minecraft:potato", "potatoes": "minecraft:potato", "sand": "minecraft:sand",
            "cobble": "minecraft:cobblestone", "cobblestone": "minecraft:cobblestone", "clay": "minecraft:clay_ball",
            "logs": "logs", "log": "logs"}
SMELT_OUT = {"glass": "minecraft:sand", "ingot": None, "ingots": None, "stone": "minecraft:cobblestone",
             "charcoal": "logs", "brick": "minecraft:clay_ball", "bricks": "minecraft:clay_ball"}
RESULT = {f"minecraft:{k}": f"minecraft:{v}" for k, v in (     # input she carries -> smelt's item (the output)
    ("raw_iron", "iron_ingot"), ("raw_gold", "gold_ingot"), ("raw_copper", "copper_ingot"), ("beef", "cooked_beef"),
    ("chicken", "cooked_chicken"), ("porkchop", "cooked_porkchop"), ("mutton", "cooked_mutton"),
    ("rabbit", "cooked_rabbit"), ("cod", "cooked_cod"), ("salmon", "cooked_salmon"), ("potato", "baked_potato"),
    ("sand", "glass"), ("cobblestone", "stone"), ("clay_ball", "brick"))}
ESSENTIAL = re.compile(r"_(?:sword|pickaxe|axe|shovel|hoe|helmet|chestplate|leggings|boots)$|:(?:bow|crossbow|arrow|"
                       r"shield|torch|bread|apple|golden_apple|cooked_\w+|baked_potato|carrot)$")
REACH = 6.0         # a workstation "in reach" (skills: furnace in reach, from sense's index)


def _n(word, default=None):
    if word is None:
        return default
    w = word.strip().lower()
    return int(w) if w.isdigit() else NUMWORD.get(w, default)


def _inv(st):
    out = {}
    for s in (st or {}).get("inventory", []):
        if s.get("item"):
            out[s["item"]] = out.get(s["item"], 0) + int(s.get("count") or 0)
    return out


def _dist(a, b):
    return math.dist([a[k] for k in "xyz"], [b[k] for k in "xyz"])


def _pos(m, i=1):
    return {"x": int(m[i]), "y": int(m[i + 1]), "z": int(m[i + 2])}


def _thing(item):
    return item.split(":")[-1].replace("_", " ")


def match(text, view):
    """O205: ride and livestock (their owners' rules) read the line; every other rule takes its params from the
    grammar's intent (grammar.intent) - no phrase regex of its own."""
    t = text.strip().lower().rstrip(".!")
    for rule in (ride, livestock):
        r = rule(t, view)
        if r is not None:
            return r
    from .grammar import intent, is_fight_all
    if is_fight_all(t):                                 # O165 (live, at night): "fight all of them" mid-fight
        if view.get("running") in ("fight_melee", "fight_ranged"):
            return {"say": "On it, all of them."}      # nearest-first already takes every hostile: no cancel
        return {"do": [("fight_melee", {})], "say": "On it, all of them - nearest first."}
    it = intent(t)
    if it is None:
        return None
    for rule in (combat, builder_bridge, builder_build, builder_repair, mine_block, prospect, dig_up, gather_animals,
                 equip, harvest, smelt, craft, containers):
        r = rule(it, view)
        if r is not None:
            return r
    return None


# ---- combat (parts/combat/OWNER-PHRASES.md)

def _bow(st):
    inv = _inv(st)
    return any(k in inv for k in ("minecraft:bow", "minecraft:crossbow")), "minecraft:arrow" in inv or any(
        k.endswith("_arrow") for k in inv)


def _target(kind, st, owner_target=False):
    """The nearest entity of that type in her nearby_entities -> (entity, None) or (None, a say / ask verdict).
    owner_target (O136, the served registry has the param): a protected non-player mob he named is his to order."""
    kind = re.sub(r"s$", "", kind.strip()).replace(" ", "_")
    ents = [e for e in (st or {}).get("nearby_entities", []) if str(e.get("type", "")).split(":")[-1] == kind or
            (e.get("name") or "").lower() == kind.replace("_", " ")]
    if not ents:
        return None, {"say": f"I don't see a {kind.replace('_', ' ')} near me."}
    if any(e.get("type") == "minecraft:player" for e in ents):
        return None, {"say": "I don't fight players."}
    ents.sort(key=lambda e: e.get("distance", 1e9))
    if ents[0].get("protected") and not owner_target:
        return None, {"say": f"I won't attack {ents[0].get('name') or kind.replace('_', ' ')}: it's protected."}
    if len(ents) > 1 and abs(ents[1].get("distance", 1e9) - ents[0].get("distance", 0)) < 1.0:
        return None, {"ask": f"Two {kind.replace('_', ' ')}s are just as close - which one? Or say 'clear the mobs' "
                             f"and I'll take them one at a time."}
    return ents[0], None


RIDES = ("boat", "raft", "chest_boat", "chest_raft", "minecart", "horse", "donkey", "mule", "llama", "camel", "pig",
         "strider", "skeleton_horse", "zombie_horse")
NEEDS_SADDLE = ("horse", "donkey", "mule", "camel", "skeleton_horse", "zombie_horse", "pig", "strider")


def ride(t, view):
    """O145 (Hunter: "get in the wood boat" -> coordinates asked; riding with him is "extremely important"): "get in /
    hop in / ride (with me) / get in the boat|minecart|on the horse" -> mount the nearest rideable within 16 (the one
    nearest him first: he is likely in it); "get out / hop off / dismount" -> dismount. Registry 0.15.2+ only."""
    ridden = r"(?:the |that |this |your |my )?(?:\w+ )?(?:boat|raft|minecart|cart|horse|donkey|mule|llama|camel|pig|strider)"
    if re.match(LEAD + r"(?:get out(?: of " + ridden + r")?|hop (?:off|out)|get off(?: " + ridden + r")?|dismount|"
                       r"step out)$", t):           # "get out of the hole" stays dig_up's
        return {"do": [("dismount", {})], "say": "Getting out."} if view.get("mount") else \
            {"say": "I can't ride yet - that comes with the next update."}
    m = re.match(LEAD + r"(?:get in|hop in|jump in|climb in|get on|hop on|ride(?: with me)?|come ride with me|sit)"
                        r"(?: (?:the|that|this|a|my|your|our) )?(?: ?([a-z ]+?))?(?: with me)?$", t)
    if not m:
        return None
    want = (m[1] or "").replace("wood ", "").replace("wooden ", "").strip().replace(" ", "_")
    if want and not any(want == r or want.endswith(r) or r.endswith(want) for r in RIDES):
        return None                                   # "get in the house": not a ride
    if not view.get("mount"):
        return {"say": "I can't ride yet - that comes with the next update."}
    st, him = view.get("st") or {}, view.get("owner_pos")
    rides = [e for e in st.get("nearby_entities", []) if e.get("distance", 99) <= 16 and any(
        str(e.get("type", "")).split(":")[-1].endswith(r) for r in RIDES)
             and (not want or str(e.get("type", "")).split(":")[-1].endswith(want))]
    if not rides:
        return {"say": f"I don't see a {want.replace('_', ' ') or 'boat, minecart or horse'} near me."}
    near_him = (lambda e: _dist(e["pos"], him)) if him else (lambda e: e.get("distance", 99))
    e = min(rides, key=near_him)
    kind = str(e["type"]).split(":")[-1]
    if kind.endswith(NEEDS_SADDLE) and not (e.get("saddled") and (e.get("tamed") or kind in ("pig", "strider"))):
        return {"say": f"That {kind.replace('_', ' ')} isn't {'tamed and ' if kind not in ('pig', 'strider') else ''}"
                       f"saddled, so I can't ride it."}
    return {"do": [("mount", {"entity_id": e["id"]})], "say": f"Getting in the {kind.replace('_', ' ')}."}


LIVESTOCK = ("sheep", "cow", "pig", "chicken", "rabbit", "goat", "mooshroom", "cod", "salmon", "tropical_fish",
             "pufferfish")


def livestock(t, view):
    """O146 (Hunter live: "kill the pig nearby" went to the planner and was refused; "kill 3 sheep" read '3' as the
    mob): killing livestock for materials is normal survival. "kill|slaughter|hunt|butcher|harvest (the|a|N) <animal>
    (nearby|here)" or "get me N <animal>" -> the nearest N adult, unnamed, untamed ones within 24: harvest_animal when
    the served registry has it (0.15.2), else fight_melee on each. "shear the sheep" is not this (no kill)."""
    m = re.match(LEAD + r"(?:go (?:and )?)?(?:kill|slaughter|hunt|butcher|harvest|get me)\s+(?:(\d+|a|an|one|two|three|"
                        r"four|five|six|eight|ten|some|all the|the)\s+)?([a-z ]+?)(?:\s+(?:nearby|near me|near you|here|"
                        r"around here|for me|please|now))*$", t)
    if not m:
        return None
    word = m[2].strip().replace(" ", "_")
    kind = next((k for k in (word, word[:-1] if word.endswith("s") else None, "cod" if word == "fish" else None)
                 if k in LIVESTOCK), None)
    if kind is None:
        return None
    n = 3 if m[1] == "some" else 8 if m[1] == "all the" else _n(m[1], 1) if m[1] not in (None, "the", "a", "an") else 1
    st = view.get("st") or {}
    ents = sorted((e for e in st.get("nearby_entities", []) if str(e.get("type", "")).split(":")[-1] == kind
                   and not e.get("name") and not e.get("baby") and not e.get("tamed") and e.get("distance", 99) <= 24),
                  key=lambda e: e.get("distance", 99))
    name = kind.replace("_", " ")
    if not ents:
        return {"say": f"I don't see a grown {name} near me.", "livestock": True}
    ids = [e["id"] for e in ents[:n]]
    many = f"{len(ids)} {name}" + ("" if name.endswith(("sheep", "fish", "cod", "salmon")) else "s")
    if view.get("harvest"):                       # registry 0.15.2: combat's harvest_animal (never run in game yet)
        p = st.get("pos") or {}
        zone = {"min": {a: int(p.get(a, 0) // 1) - (8 if a == "y" else 16) for a in "xyz"},
                "max": {a: int(p.get(a, 0) // 1) + (8 if a == "y" else 16) for a in "xyz"}}
        return {"do": [("harvest_animal", {"entity_ids": ids, "zone": zone, "species": [f"minecraft:{kind}"],
                                           "max_kills": len(ids), "collect_drops": True})],
                "say": f"Going for {many if len(ids) > 1 else 'the ' + name} - first time harvesting.", "livestock": True}
    return {"do": [("fight_melee", {"entity_id": i}) for i in ids], "livestock": True,
            "say": f"Going for the {name}." if len(ids) == 1 else f"Going for {many}."}


def _words(it):
    """The object words he said, without leading determiners."""
    return re.sub(r"^(?:(?:the|that|this|a|an|my|your|our|some)\s+)+", "", it["core"]).strip()


def combat(it, view):
    st, v, words = view.get("st"), it["verb"], _words(it)
    if v == "guard" and re.match(r"(?:door|doorway|entrance)\b", words):
        return {"say": "I can't hold a door yet."}
    if v == "flee":
        return {"cancel": True, "say": "Backing off."}
    bow, arrows = _bow(st)
    if v == "shoot":
        if not bow:
            return {"say": "I have no bow."}
        if not arrows:
            return {"say": "I have no arrows."}
        if not words or words in ("it", "them"):
            return {"do": [("fight_ranged", {})], "say": "Shooting the nearest one."}
        e, why = _target(words.split()[0] if words.split()[0] in ("it", "them") else words, st)
        return why if why else {"do": [("fight_ranged", {"entity_id": e["id"]})],
                                         "say": f"Shooting the {_thing(e['type'])}."}
    group = it["said_verb"] in ("fight with me", "help me fight", "help me", "get them") or (
        v == "kill" and it["said_verb"] == "fight" and not words)
    clear = v == "clear" and re.fullmatch(r"(?:all )?(?:the |out the )?(?:mobs|monsters|hostiles|everything|all the mobs)",
                                          words)
    if group or clear:
        far = it["extras"]["everything"] or it["place"]["kind"] == "anchor"
        return {"do": [("fight_melee", {"radius": 32} if far else {})], "say": "Fighting - nearest first."}
    if v == "collect" and re.fullmatch(HOSTILE_WORDS, words):
        v = "kill"                                      # "get the zombie"
    if v != "kill" or not words or it["qty"]["kind"] not in ("none", "exact") or (it["qty"]["n"] or 1) > 1:
        return None
    if words in ("one", "that one", "this one"):
        return {"ask": "Which one? Tell me what kind of mob."}
    kind = words
    known_mob = re.fullmatch(HOSTILE_WORDS, kind)
    seen = [e for e in (st or {}).get("nearby_entities", []) if (e.get("name") or "").lower() == kind or
            str(e.get("type", "")).split(":")[-1] == re.sub(r"s$", "", kind).replace(" ", "_")]   # "kill the villager"
    named = [e for e in seen if (e.get("name") or "").lower() == kind]
    if not known_mob and not seen:
        return None                                 # "get wood", "get Alex a sword": not a fight
    if named and named[0].get("type") == "minecraft:player":
        return {"say": "I don't fight players."}
    e, why = _target(kind, st, owner_target=bool(view.get("owner_target")))
    if why:
        return why
    if e.get("protected"):                          # O136: he named it - sense's attack.grant first (run_phrase)
        if e.get("type") == "minecraft:player" or not e.get("uuid"):
            return {"say": "I don't fight players."} if e.get("type") == "minecraft:player" else \
                {"say": f"I can't target that {_thing(e['type'])}."}
        return {"do": [("fight_melee", {"entity_id": e["id"], "owner_target": True})], "grant": e["uuid"],
                "say": f"OK, going for the {_thing(e['type'])} (you asked)."}
    creeper = e["type"].endswith("creeper")
    if creeper or it["said_verb"] == "deal with" and bow and arrows and e["type"].endswith(("skeleton", "stray")):
        if not (bow and arrows):
            return {"say": "I don't sword-fight creepers, and I have no " + ("arrows." if bow else "bow.")}
        return {"do": [("fight_ranged", {"entity_id": e["id"]})], "say": f"Shooting the {_thing(e['type'])}."}
    if e["type"].endswith(ARCHERS) and not (bow and arrows):   # O139: no bow - combat's rush rule closes in
        return {"do": [("fight_melee", {"entity_id": e["id"]})], "say": "It's an archer; I'll rush it."}
    return {"do": [("fight_melee", {"entity_id": e["id"]})], "say": f"Fighting the {_thing(e['type'])}."}


# ---- builder (parts/builder/OWNER-PHRASES.md)

def _owner_or_ask(view):
    p = view.get("owner_pos")
    return ({k: math.floor(p[k]) for k in "xyz"}, None) if p else (None, "Where are you? Give me x y z.")


def builder_bridge(it, view):
    if it["verb"] != "bridge":
        return None
    st, ex = view.get("st") or {}, it["extras"]
    if ex["xyz"]:
        to = dict(ex["xyz"])
    elif ex["to_me"]:
        to, ask = _owner_or_ask(view)
        if ask:
            return {"ask": ask}
        if st.get("pos"):
            to["y"] = math.floor(st["pos"]["y"]) - 1          # her floor's height
    else:
        return {"ask": "Where to? Give me the x y z of the far side."}
    me = st.get("pos")
    if me and math.floor(me["x"]) != to["x"] and math.floor(me["z"]) != to["z"]:
        return {"say": "That isn't in a straight line from me. Walk me to a start in line with it first."}
    if me and _dist({k: math.floor(me[k]) for k in "xyz"}, to) > 64:
        return {"say": "That's more than 64 blocks; walk me closer first."}
    return {"start": "bridge", "args": {"to": to}, "say": f"Bridging to {to['x']} {to['y']} {to['z']}."}


def builder_build(it, view):
    if it["verb"] not in ("build", "craft"):
        return None
    ex = it["extras"]
    raw = it["core"] if it["place"]["kind"] != "anchor" else f"{it['core']} of {it['place']['anchor']}".strip()
    raw = re.sub(r"^(?:version \d+ of |v\d+ of )|\s+v(?:ersion\s*)?\d+$|^(?:the|my|a|an)\s+", "", raw.strip()).strip()
    raw = re.sub(r"^(?:version \d+ of |v\d+ of )|^(?:the|my|a|an)\s+", "", raw).strip()
    name = re.sub(r"\b(?:schematic|design)\b.*", "", raw).strip()
    schematic = bool(re.search(r"\b(?:schematic|design)\b", raw))
    designs = view.get("designs", [])
    words = set(re.findall(r"[a-z0-9]+", name))
    hits = [d for d in designs if words and words <= set(re.findall(r"[a-z0-9]+", d["project_id"].lower().replace("_", " ")))] \
        if not schematic else [d for d in designs if d.get("category") in ("owner_supplied", "schematic")]
    if name in ("it", "that", "this") and designs:
        return {"ask": "Which design: " + " or ".join(sorted({d["project_id"] for d in designs})) + "?"}
    if not hits:
        if schematic:
            return {"say": "I have no approved schematic from you. Approve it with /eve approve <project> v<N>."}
        return None                                     # "build a wall": no design by that name - the planner's
    want = ex["version"]
    if want is not None:
        hits = [d for d in hits if d["version"] == want]
        if not hits:
            return {"say": f"Version {want} isn't approved. Approve it with /eve approve <project> v{want}."}
    if len({d["project_id"] for d in hits}) > 1:
        return {"ask": "Which design: " + " or ".join(sorted({d["project_id"] for d in hits})) + "?"}
    d = max(hits, key=lambda d: d["version"])
    args = {"project_id": d["project_id"], "version": d["version"], "design_hash": d["design_hash"]}
    if it["place"]["kind"] == "here":
        args["origin"], ask = _owner_or_ask(view)
        if ask:
            return {"ask": ask}
    elif ex["xyz"]:
        args["origin"] = dict(ex["xyz"])
    at = f" at {args['origin']['x']} {args['origin']['y']} {args['origin']['z']}" if "origin" in args else ""
    return {"start": "build", "args": args, "say": f"Building {d['project_id']} v{d['version']}{at}; I'll gather "
                                                   f"what I'm missing first."}


ZONE_WORDS = {"house": "home", "base": "home", "home": "home", "farm": "farm", "mine": "mine"}


def builder_repair(it, view):
    if it["verb"] != "repair":
        return None
    ex = it["extras"]
    word = (re.sub(r"^(?:the|my|our)\s+", "", it["raw"]).split() or [""])[0]
    zones = {z["id"] for z in view.get("zones", [])}
    zone = word if word in zones else ZONE_WORDS.get(word) if ZONE_WORDS.get(word) in zones else None
    if zone is None:
        if word in ZONE_WORDS:
            return {"say": f"I have no zone for the {word}; name one of: {', '.join(sorted(zones))}."}
        return None
    snaps = sorted((s for s in view.get("snapshots", []) if s["zone_id"] == zone), key=lambda s: s["created_ts"])
    if ex["yesterday"]:
        day = time.localtime(time.time() - 86400)[:3]
        snaps = [s for s in snaps if time.localtime(s["created_ts"] / 1000)[:3] == day]
    if not snaps:
        return {"say": f"I have no saved snapshot of {zone}" + (" from yesterday" if ex["yesterday"] else "") +
                       f". Save it first: eve save_zone {zone}."}
    if not (ex["xyz"] and ex["xyz2"]):
        return {"ask": "Which part should I repair? Give me its corners: x y z to x y z."}
    return {"start": "repair", "args": {"zone_id": zone, "snapshot_id": snaps[-1]["snapshot_id"],
                                        "box": {"min": dict(ex["xyz"]), "max": dict(ex["xyz2"])}},
            "say": f"Repairing {zone} from its snapshot. Your own changes since then stay, and I won't rebuild chests."}


# ---- skills (parts/skills/OWNER-PHRASES.md)

def mine_block(it, view):
    if it["verb"] not in ("mine", "clear"):
        return None
    w = _words(it)
    if it["extras"]["xyz"] and re.fullmatch(r"block(?: at)?|", re.sub(r"\s+at$", "", w)):
        return {"do": [("mine_block", {"pos": dict(it["extras"]["xyz"])})], "say": "Breaking that block."}
    if not it["extras"]["xyz"] and re.fullmatch(r"(?:that|this)(?: block)?(?: out)?|block(?: out)?", it["core"].strip()) \
            and it["said_verb"] in ("break", "remove", "mine", "dig", "dig out"):
        return {"ask": "Which block? Give me its x y z."}
    return None


def prospect(it, view):
    if it["verb"] != "prospect":
        return None
    word = re.sub(r"\s+ores?$", "", _words(it)).strip()
    ore = word.rstrip("s") if word.rstrip("s") in ORES else word if word in ORES else None
    if ore is None:
        return {"ask": "Which ore should I dig for?"} if word in ("ore", "ores", "") else None
    q, ex = it["qty"], it["extras"]
    p = {"tag": f"minecraft:{ore}_ores", "count": q["n"] if q["kind"] == "exact" and q["n"] else 8}
    if ex["direction"]:
        p["direction"] = ex["direction"]
    if ex["depth"] is not None:
        p["descend_to"] = ex["depth"]
    return {"do": [("prospect", p)], "say": f"Digging for {ore}: {p['count']} ore" + (", deep" if "descend_to" in p
                                                                                         else "") + "."}


def dig_up(it, view):
    if it["verb"] != "dig_up":
        return None
    if it["extras"]["to_me"]:
        to, ask = _owner_or_ask(view)
        if ask:
            return {"ask": ask}
    else:
        c = (view.get("home") or {}).get("center")
        if not c:
            return {"say": "I have no home to dig toward."}
        to = {k: int(c[k]) for k in "xyz"}
    return {"do": [("dig_up", {"toward": to})], "say": "Digging my way up."}


def _animals(view, kind, n):
    """O245: the nearest n grown, unnamed <kind> within 24 and a zone box around her (as harvest_animal's)."""
    st = view.get("st") or {}
    ents = sorted((e for e in st.get("nearby_entities", []) if str(e.get("type", "")).split(":")[-1] == kind
                   and not e.get("name") and not e.get("baby") and e.get("distance", 99) <= 24),
                  key=lambda e: e.get("distance", 99))[:n]
    p = st.get("pos") or {}
    zone = {"min": {a: int(p.get(a, 0) // 1) - (8 if a == "y" else 24) for a in "xyz"},
            "max": {a: int(p.get(a, 0) // 1) + (8 if a == "y" else 24) for a in "xyz"}}
    return [e["id"] for e in ents], zone, {i.get("item") for i in st.get("inventory", [])}


def gather_animals(it, view):
    v, w = it["verb"], _words(it)
    milk = v == "milk" or v == "collect" and re.fullmatch(r"(?:a bucket of )?milk", w)
    if milk and view.get("milk_cow"):                  # O245 (registry 0.15.4): skills' milk_cow on the nearest cow
        ids, zone, held = _animals(view, "cow", 1)
        if "minecraft:bucket" not in held:
            return {"say": "I need an empty bucket to milk a cow."}
        if not ids:
            return {"say": "I don't see a grown cow near me."}
        return {"do": [("milk_cow", {"entity_ids": ids, "zone": zone, "count": 1})], "say": "Milking the cow."}
    if milk:
        return {"do": [("gather", {"product": "milk", "count": 1})], "say": "Getting milk."}
    if not (v == "shear" and re.match(r"sheep\b", w) or v == "collect" and w == "wool"):
        return None
    n = it["qty"]["n"] if it["qty"]["kind"] == "exact" else None
    tail = re.search(r"(\d+)$", it["text"])
    n = n or (int(tail[1]) if tail else None)
    if v == "shear" and view.get("shear_sheep"):       # O245: skills' shear_sheep on the nearest sheep ("the sheep": 8)
        ids, zone, held = _animals(view, "sheep", n or 8)
        if "minecraft:shears" not in held:
            return {"say": "I need shears for that."}
        if not ids:
            return {"say": "I don't see a grown sheep near me."}
        return {"do": [("shear_sheep", {"entity_ids": ids, "zone": zone, "count": len(ids)})],
                "say": f"Shearing {len(ids)} sheep."}
    if n is None:
        return {"ask": "How much wool?"}
    return {"do": [("gather", {"product": "wool", "count": n})], "say": f"Getting {n} wool."}


def equip(it, view):
    if it["verb"] != "equip":
        return None
    sv, w = it["said_verb"], _words(it)
    if sv in ("gear up", "suit up") or "armo" in sv:
        return {"do": [("equip_best", {})], "say": "Gearing up."}
    if re.fullmatch(r"(?:sword|weapon)", w):
        return {"do": [("equip_best", {"weapon": True})], "say": "Sword out."}
    tool = w.split()[0] if w.split() and w.split()[0] in ("pickaxe", "axe", "shovel") else None
    if not tool:
        return None
    fb = it["extras"]["for_block"]
    block = (resource_params(fb) or {}).get("block") if fb else None
    block = block or {"pickaxe": "minecraft:stone", "axe": "minecraft:oak_log", "shovel": "minecraft:dirt"}[tool]
    return {"do": [("equip_best", {"for_block": block})], "say": f"Getting my {tool} out."}


def _zone_box(z):
    return {k: {a: int(z[k][a]) for a in "xyz"} for k in ("min", "max")}


CROPS = re.compile(r"(?:farm|crops|wheat|carrots|potatoes|beetroots?)$")


def harvest(it, view):
    w = re.sub(r"\s+from$", "", _words(it))
    if it["verb"] not in ("harvest", "collect") or not CROPS.match(w):
        return None
    ex = it["extras"]
    if ex["xyz"] and ex["xyz2"]:
        box = {"min": dict(ex["xyz"]), "max": dict(ex["xyz2"])}
    else:
        farms = [z for z in view.get("zones", []) if "farm" in z["id"]]
        if len(farms) != 1:
            return {"ask": "Where is the farm? Give me its corners: from x y z to x y z."}
        box = _zone_box(farms[0])
    size = [abs(box["max"][a] - box["min"][a]) + 1 for a in "xyz"]
    if size[0] > 32 or size[1] > 4 or size[2] > 32:
        return {"ask": "That farm is bigger than I can harvest at once (32 x 4 x 32). Give me a smaller part: from x y z "
                       "to x y z."}
    crop = {"wheat": "minecraft:wheat", "carrots": "minecraft:carrots", "potatoes": "minecraft:potatoes"}.get(w)
    if view.get("harvest_crop") and (crop or w in ("farm", "crops")):   # O245: skills' harvest_crop (0.15.4)
        return {"do": [("harvest_crop", {**box, "replant": ex["replant"], **({"crop": crop} if crop else {})})],
                "say": "Harvesting" + (f" the {w}" if crop else "") + (" and replanting." if ex["replant"] else
                                                                       ", no replanting.")}
    return {"do": [("harvest_replant", {**box, "replant": ex["replant"]})],
            "say": "Harvesting" + (" and replanting." if ex["replant"] else ", no replanting.")}


def _station(view, kind):
    me = (view.get("st") or {}).get("pos")
    near = [c for c in view.get("containers", []) if kind in str(c.get("kind", "")) and me and _dist(c["pos"], me) <= REACH]
    return min(near, key=lambda c: _dist(c["pos"], me)) if near else None


def smelt(it, view):
    sv = it["said_verb"]
    if not (it["verb"] == "smelt" or sv == "make"):
        return None
    what = re.sub(r"\b(?:in the furnace|for me|please|ores?|meat)\b", "", _words(it)).strip()
    inv = _inv(view.get("st"))
    if sv == "make":
        if what not in SMELT_OUT:
            return None
        src = SMELT_OUT[what]
    elif what in ("everything", "it", "stuff", "all", "") and it["qty"]["kind"] in ("none", "all"):
        return {"ask": "What should I cook?"}
    else:
        src = SMELT_IN.get(what) or SMELT_IN.get(what.split()[-1] if what else "") or SMELT_OUT.get(what)
        if src is None:
            if sv == "smelt":
                return {"ask": f"I don't know how to smelt '{what}'. Which item?"}
            return None                                   # "cook dinner": not a furnace order
    if src == "logs":
        src = next((k for k in inv if k.endswith("_log")), None)
        if src is None:
            return {"say": "I have no logs to burn into charcoal."}
    have = inv.get(src, 0)
    n = it["qty"]["n"] if it["qty"]["kind"] == "exact" and it["qty"]["n"] else have
    if not have:
        return {"say": f"I have no {_thing(src)} to {sv if sv != 'make' else 'smelt'}."}
    furnace = _station(view, "furnace") or _station(view, "smoker") or _station(view, "blast")
    out = "minecraft:charcoal" if src.endswith("_log") else RESULT[src]   # skills 86b928a0: item is the OUTPUT
    if furnace is None and inv.get("minecraft:furnace"):  # S93 (progression past the furnace): her own one, put down
        k = min(n, have)                                  #   beside her and picked back up after, as her table is
        return {"do": [("place_block", {"item": "minecraft:furnace", "pos": "@cell"}),
                       ("smelt", {"pos": "@cell", "item": out, "count": k}), ("mine_block", {"pos": "@cell"})],
                "cell": "furnace", "say": f"Putting my furnace down to smelt {k} {_thing(src)} into {_thing(out)}."}
    if furnace is None:
        return {"say": "I need a furnace nearby for that."}
    return {"do": [("smelt", {"pos": furnace["pos"], "item": out, "count": min(n, have)})],
            "say": f"Smelting {min(n, have)} {_thing(src)} into {_thing(out)}."}


def craft(it, view):
    inv = _inv(view.get("st"))
    if it["verb"] == "turn":
        w = re.fullmatch(r"(?:(?:the|my|all the)\s+)?(\w+ )?logs? into planks", it["core"].strip())
        if not w:
            return None
        logs = {k: n for k, n in inv.items() if k.endswith("_log")}
        kind = f"minecraft:{w[1].strip()}_log" if w[1] else (
            "minecraft:oak_log" if "minecraft:oak_log" in logs else next(iter(logs), None))
        if not kind or kind not in logs:
            return {"say": "I have no logs to turn into planks."}
        return {"do": [("craft", {"item": kind.replace("_log", "_planks"), "count": logs[kind] * 4})],
                "say": f"Making {logs[kind] * 4} {_thing(kind.replace('_log', '_planks'))}."}
    if it["verb"] not in ("craft", "build"):
        return None
    what = _words(it)
    for tool in TOOLS:
        tm = re.fullmatch(rf"(?:(\w+) )?{tool}s?", what)
        if tm:
            tier = {"wood": "wooden", "gold": "golden"}.get(tm[1], tm[1])
            if tier not in TIERS:
                return {"ask": f"Which {tool}: wooden, stone, iron or diamond?"}
            what = f"{tier} {tool}"
    what = re.sub(r"(?<=[cs]h)es$|s$", "", what) if not what.endswith("ss") else what   # torches -> torch
    word = what.replace(" ", "_")
    if word.rstrip("s") in ("torch", "crafting_table", "chest", "furnace", "stick", "bread", "ladder", "door",
                            "oak_door", "bucket", "shield", "bowl", "boat", "bed", "barrel") or "_" in word and \
            word.split("_")[-1].rstrip("s") in TOOLS + ("planks", "slab", "stairs", "helmet", "chestplate", "leggings",
                                                          "boots", "door", "fence"):
        q = it["qty"]
        n = q["n"] if q["kind"] == "exact" and q["n"] else 1
        if q["kind"] == "some":
            n = 4 if word.startswith("torch") else 1
        iid = item_id(what)
        return {"do": [("craft", {"item": iid, "count": n})], "say": f"Crafting {n} {_thing(iid)}."}
    return None                                           # "build a house", "make a path": not a recipe I map


def containers(it, view):
    cs = view.get("containers", [])
    w = _words(it)
    if it["verb"] == "open" and re.fullmatch(r"chest|barrel|box|shulker box|container", w):
        kind = {"box": "chest", "container": ""}.get(w, w)
        hits = [c for c in cs if kind in str(c.get("kind", ""))]
        if len(hits) != 1:
            return {"ask": f"Which {w}? Give me its x y z."} if hits else {"say": f"I don't know any {w} yet."}
        return {"do": [("open_container", {"pos": hits[0]["pos"]})], "say": f"Opening the {w}."}
    return transfer(it, view)


def _takes_intent(fn):
    """O205: a rule takes the grammar's intent; a caller that still has the line (build_flow, workstations) is read
    by the grammar first."""
    def rule(it, view):
        if isinstance(it, str):
            from .grammar import intent
            it = intent(it.strip().lower().rstrip(".!"))
            if it is None:
                return None
        return fn(it, view)
    rule.__name__, rule.__doc__ = fn.__name__, fn.__doc__
    return rule


for _rule in ("combat", "builder_bridge", "builder_build", "builder_repair", "mine_block", "prospect", "dig_up",
              "gather_animals", "equip", "harvest", "smelt", "craft", "containers"):
    globals()[_rule] = _takes_intent(globals()[_rule])

# ---- O137: item words <-> ids; O135: which chest "the chest" is

TIER = {"wood": "wooden", "wooden": "wooden", "stone": "stone", "cobblestone": "stone", "cobble": "stone", "iron": "iron",
        "gold": "golden", "golden": "golden", "diamond": "diamond", "netherite": "netherite", "chain": "chainmail",
        "chainmail": "chainmail", "leather": "leather"}
PIECE = {"pick": "pickaxe", "pickaxe": "pickaxe", "sword": "sword", "axe": "axe", "shovel": "shovel", "spade": "shovel",
         "hoe": "hoe", "helmet": "helmet", "helm": "helmet", "chestplate": "chestplate", "leggings": "leggings",
         "pants": "leggings", "boots": "boots"}
ITEM_WORDS = {"cobble": "cobblestone", "iron": "iron_ingot", "gold": "gold_ingot", "copper": "copper_ingot",
              "steak": "cooked_beef", "wood": "oak_log", "logs": "oak_log"}
LEAD_WORDS = re.compile(r"^(?:(?:all(?: of)?|the|a|an|some|your|my|our|any|of|\d+)\s+)+")
CONTAINER_KINDS = ("chest", "barrel", "trapped_chest", "shulker_box")
NEAR_R = 16


def _singulars(w):
    out = [w]
    for pat, rep in ((r"ies$", "y"), (r"([sxz]|ch|sh)es$", r"\1"), (r"s$", "")):
        s = re.sub(pat, rep, w)
        if s != w and s not in out:
            out.append(s)
    return out


def resolve_item(words, inv=None):
    """O137: his words for an item -> (item id or None, words). Order: an explicit id; tier + tool/armour ("cobblestone
    pickaxe" -> stone_pickaxe, "wood sword", "iron pick"); the game's English names (en_us.json item.* / block.*,
    case-insensitive, plural or not); the 26.3 item registry; a few owner words (cobble, iron = ingot); last, one item
    she carries whose id ends with the words. None = unknown (the caller asks)."""
    from .memory import item_names, known_items
    w = LEAD_WORDS.sub("", words.strip().lower()).strip(" .!")
    items, names = known_items(), item_names()
    ok = lambda i: not items or i in items     # noqa: E731 - without the game data, trust the form
    if w.startswith("minecraft:"):
        return (w if ok(w) else None), w
    parts = w.split()
    if len(parts) == 2 and parts[0] in TIER and _singulars(parts[1])[-1] in PIECE:
        iid = f"minecraft:{TIER[parts[0]]}_{PIECE[_singulars(parts[1])[-1]]}"
        if ok(iid):
            return iid, w
    for cand in _singulars(w):
        if cand in names:
            return names[cand], w
        under = cand.replace(" ", "_")
        if items and f"minecraft:{under}" in items:
            return f"minecraft:{under}", w
        if under in ITEM_WORDS:
            return f"minecraft:{ITEM_WORDS[under]}", w
    if inv:
        bases = {c.replace(" ", "_") for c in _singulars(w)}
        hits = [k for k in inv if any(k.split(":")[-1] == b or k.split(":")[-1].endswith("_" + b) for b in bases)]
        if len(hits) == 1:
            return hits[0], w
    if not items and re.fullmatch(r"[a-z_ ]+", w):
        return item_id(w), w                                # no game data here: the old guess, as before
    return None, w


SPLIT = re.compile(r"\s+(?:into|in|inside|to|from|out of)\s+")
REFERENT = re.compile(r"^(?:the|that|this|our|my|your|a)?\s*(?:one|chest|barrel|box|storage|container|trunk)\b(.*)$")


IT_AFTER = re.compile(r"^(?P<first>(?:.*?\b)?(?:craft|make|build|get|grab|fetch|mine|chop|take|bring)\s+(?:me\s+|us\s+)?"
                      r"(?:(?:\d+|a|an|one|some|the|a few)\s+)?(?P<thing>[a-z_ ]+?))\s*(?:,\s*)?\s+(?:and|,)\s+"
                      r"(?:then\s+)?(?P<verb>place|put|set|give|hand|drop|pass|use|equip)\s+(?:it|that|them|this|those)\b"
                      r"(?P<rest>.*)$", re.I)


def item_in(text):
    """O138: the item an explanatory answer names ("it being the chest you have to craft" -> minecraft:chest): the
    longest run of words that is a game name or a registry item; None when none is (never a guess)."""
    from .memory import item_names, known_items
    names, items = item_names(), known_items()
    words = re.findall(r"[a-z]+", text.lower())
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            for cand in _singulars(" ".join(words[i:i + n])):
                if cand in names:
                    return names[cand]
                if f"minecraft:{cand.replace(' ', '_')}" in items and cand not in ("it", "that", "one", "you"):
                    return f"minecraft:{cand.replace(' ', '_')}"
    return None


def split_it(text):
    """O138 (45L: "craft a crafting table and place it" -> "'it' - which one?"): within one order, it / that / them
    after "craft / make / get X and place / give / put it" is X from the same sentence -> [first, second]."""
    m = IT_AFTER.match(text.strip().rstrip(".!"))
    if not m:
        return [text]
    return [m["first"].strip(), f"{m['verb']} the {m['thing'].strip()}{m['rest']}".strip()]



FLUIDS = {"water": "minecraft:water", "lava": "minecraft:lava"}


def find_target(text):
    """O164 / O200: find / locate / look for / where is (the nearest) X -> {"what", "ids", "suffix", "exposed"} or
    None - read by the one owner grammar (grammar.find_order)."""
    from .grammar import find_order
    return find_order(text)

def compass(dx, dz):
    """The 8-way direction of (dx, dz) in Minecraft (+x east, +z south)."""
    import math as _m
    a = (_m.degrees(_m.atan2(dx, -dz)) + 360) % 360
    return ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")[int((a + 22.5) // 45) % 8]


def order_steps(text):
    """O157 / O200: a multi-sentence order -> its order sentences (grammar.order_clauses), None otherwise."""
    from .grammar import order_clauses
    return order_clauses(text)

DEP_VERBS, WD_VERBS = ("deposit", "place"), ("withdraw", "collect")


def is_transfer(text):
    """O137: a deposit / withdraw line naming a container ("... in the chest", "... from the barrel at 1 2 3")."""
    from .grammar import intent
    it = intent(text.strip().lower().rstrip(".!"))
    parts = SPLIT.split(it["raw"], maxsplit=1) if it and it["verb"] in DEP_VERBS + WD_VERBS else []
    return len(parts) == 2 and bool(REFERENT.match(parts[1]) or re.search(XYZ, parts[1]))


def _count(words):
    m = re.match(r"(\d+|all(?: of)?(?: the| your| my)?|the|some|a|an|one)\b", words.strip())
    return (m[1] if m else None), words


def _where(view, owner_first=True):
    me = (view.get("st") or {}).get("pos")
    him = view.get("owner_pos")
    return (him or me) if owner_first else (me or him)


def chest_for(cwords, view, kind_word="chest"):
    """O135: the container his words point at -> ({"pos": ...} container, None) or (None, a verdict). A position said is
    used as said; "the chest" he confirmed before (view["named_chest"]) next; one known shared container next; else the
    containers within 16 blocks of him (or her, for "near you"), newest placed first when sense gives placed_ms, else
    nearest - confirmed once with yes / no; none known -> a scan (sense query.blocks) once, then "I don't see one"."""
    cs = [c for c in view.get("containers", []) if any(k in str(c.get("kind", "")) for k in CONTAINER_KINDS)]
    if re.search(r"\b(?:chest|barrel)\b", cwords):        # "the chest" is a chest, "the barrel" a barrel
        cs = [c for c in cs if kind_word in str(c.get("kind", ""))]
    c = re.search(XYZ, cwords)
    if c:
        pos = _pos(c)
        hit = next((x for x in cs if x["pos"] == pos), {"pos": pos, "kind": "minecraft:chest", "items": {}, "shared": False})
        return (hit, None) if hit.get("shared") else (None, {"confirm_chest": pos})
    named = view.get("named_chest")
    if named and not re.search(r"\b(?:just placed|near you|by you|next to you|other)\b", cwords):
        return next((x for x in cs if x["pos"] == named), {"pos": named, "items": {}, "shared": True}), None
    shared = [x for x in cs if x.get("shared")]
    if len(shared) == 1 and not re.search(r"\b(?:my|just placed|near you|by you)\b", cwords):
        return shared[0], None
    if len(shared) > 1 and not re.search(r"\b(?:my|just placed|near you|by you)\b", cwords):
        return None, {"ask": f"Which {kind_word}? Give me its x y z."}
    at = _where(view, owner_first=not re.search(r"\b(?:near|by|next to) you\b", cwords))
    near = [x for x in cs if at and _dist(x["pos"], at) <= NEAR_R]
    if not near:
        if view.get("scanned") or not at:
            return None, {"say": f"I don't see a {kind_word} within {NEAR_R} blocks."}
        return None, {"scan": {"center": {k: math.floor(at[k]) for k in "xyz"}, "radius": NEAR_R}}
    if any(x.get("placed_ms") for x in near):
        near.sort(key=lambda x: -(x.get("placed_ms") or 0))
    else:
        near.sort(key=lambda x: _dist(x["pos"], at))
    return None, {"confirm_chest": near[0]["pos"]}


def transfer(it, view):
    """O137: deposit / withdraw - the item is split from the container on " into | in | to | from | out of " first."""
    inv, t = _inv(view.get("st")), it["text"]
    dep, wd = it["verb"] in DEP_VERBS, it["verb"] in WD_VERBS
    rest = it["raw"] if dep or wd else None
    if not rest:
        return None
    parts = SPLIT.split(rest, maxsplit=1)
    item_words, cwords = parts[0], (parts[1] if len(parts) > 1 else "")
    ref = REFERENT.match(cwords) or re.search(XYZ, cwords)
    if not ref and not (dep and not cwords and it["said_verb"] in ("store", "stash", "deposit", "empty")):
        return None                                     # "put torches every 8 blocks in tunnels", "get the wood"
    if wd and not cwords:
        return None
    kind_word = next((k for k in ("barrel", "chest") if k in cwords), "chest")
    count_word, item_words = _count(item_words)
    item_words = re.sub(r"\b(?:blocks?\s+of|pieces?\s+of)\b", "", item_words).strip()
    box, verdict = chest_for(cwords, view, kind_word)
    if dep:
        bare = LEAD_WORDS.sub("", item_words + " ").strip()
        everything = bare in ("", "everything", "bag", "inventory", "stuff", "things", "of it")
        tools_too = bool(re.search(r"\btools too\b", t))
        if everything:
            items, kit = [k for k in inv if tools_too or not ESSENTIAL.search(k)], tools_too
        elif re.fullmatch(r"ores?", bare):
            items, kit = [k for k in inv if k.startswith("minecraft:raw_") or k.endswith("_ore") or k in (
                "minecraft:coal", "minecraft:diamond", "minecraft:emerald", "minecraft:lapis_lazuli",
                "minecraft:redstone")], False
        else:
            iid, said = resolve_item(item_words, inv)
            if iid is None:
                return {"ask": f"Which item do you mean by '{said}'?"}
            items, kit = [iid], bool(ESSENTIAL.search(iid))   # he named this kit item: only it may go (O137)
        items = [k for k in items if inv.get(k, 0) > 0]
        if not items:
            return {"say": f"I have no {LEAD_WORDS.sub('', item_words) or 'such thing'} to put away."}
        n = int(count_word) if count_word and count_word.isdigit() else None

        def steps(pos):
            return [("deposit", {"pos": pos, "item": k, "count": min(n, inv[k]) if n else inv[k],
                                 **({"allow_essentials": True} if kit else {})}) for k in items]
        say = "Putting " + (f"{len(items)} kinds of things" if len(items) > 1 else
                            f"{min(n, inv[items[0]]) if n else inv[items[0]]} {_thing(items[0])}") + f" in the {kind_word}."
    else:
        iid, said = resolve_item(item_words, {k: 1 for c in view.get("containers", []) for k in c.get("items", {})})
        if iid is None:
            return {"ask": f"Which item do you mean by '{said}'?"}
        generic = not re.search(XYZ + r"|\b(?:my|just placed|near you|by you|that|this)\b", cwords)
        holders = [c for c in view.get("containers", []) if c.get("shared") and iid in c.get("items", {})]
        if generic and len(holders) == 1:
            box, verdict = holders[0], None                 # "take 10 iron from the chest": the one that has it
        elif generic and not holders and any(c.get("shared") and c.get("items") for c in view.get("containers", [])):
            return {"say": f"I don't know a {kind_word} with {_thing(iid)} in it."}
        have = (box or {}).get("items", {}).get(iid)
        if count_word and count_word.isdigit():
            n = int(count_word)
        elif count_word and count_word.startswith(("the", "all")) and have:
            n = have                                       # "the bread": all of it
        elif box is not None and have is None and box.get("items"):
            return {"say": f"There's no {_thing(iid)} in that {kind_word}."}
        else:
            return {"ask": f"How many {_thing(iid)}?"}

        def steps(pos):
            return [("withdraw", {"pos": pos, "item": iid, "count": n})]
        say = f"Taking {n} {_thing(iid)} from the {kind_word}."
    if verdict is None:
        return {"do": steps(box["pos"]), "say": say}
    if "confirm_chest" in verdict:
        p = verdict["confirm_chest"]
        return {"confirm_chest": p, "ask": f"The {kind_word} at {p['x']} {p['y']} {p['z']}?", "then": steps(p),
                "say": say}
    return verdict


# ---- result codes worded for the owner (skills' and combat's refusal tables)

CODE_LINES = {
    "not_found": "I couldn't find any {thing} within {radius} blocks.",
    "partial": "I got {got} of {wanted} {thing}; that's all I could reach.",
    "no_tool": "I don't have a tool that can do that.",
    "inventory_full": "My bag is full. Should I put things in a chest first?",
    "unreachable": "I can't get to it from here.",
    "zone_denied": "I'm not allowed to do that there.",
    "hazard": "I stopped: there's danger ahead.",
    "hurt": "I stopped because I got hurt.",
    "capped": "I've hit the hourly mining limit; I'll stop for now.",
    "no_recipe": "I don't know how to make that.",
    "missing_ingredients": "I don't have what I need to make that.",
    "no_workstation": "I need a crafting table or furnace nearby for that.",
    "no_fuel": "I have nothing to burn.",
    "workstation_busy": "That furnace is busy with something else.",
    "insufficient": "There aren't that many in there.",
    "container_full": "That chest is full.",
    "protected_item": "I keep my tools and food unless you say otherwise.",
    "private_container": "That's your private chest, so I won't take from it.",
    "not_reached": "I climbed but couldn't reach the surface.",
    "precondition_failed": "I couldn't start that right now.",
    "cancelled": "Stopped as you asked.",
    "timeout": "That took too long, so I stopped.",
    "interrupted": "I was interrupted.",
    "protected_entity": "I won't attack that: it's protected.",
    "health_floor": "I backed off, I'm hurt.",
    "no_target": "Nothing to fight here.",
    "lost_target": "It got away.",
    "too_close": "Too close to shoot and I have no sword.",
    "no_weapon": "I have no bow.",
    "no_ammo": "I'm out of arrows.",
    "out_of_arrows": "I'm out of arrows.",
    "no_item": "I don't have enough blocks for that.",
    "no_recovery": "I have nothing to save me if I fall, so I won't step out over that gap.",
    "bad_target": "That isn't a straight, level line from me.",
    "hostile_near": "There's a hostile mob near, so I won't start.",
    "gate_refused": "That design isn't cleared to build yet.",
}


def code_line(code, **kw):
    """A failure code as the owner hears it (never the raw code); None for a code with no line."""
    line = CODE_LINES.get(code)
    if line is None:
        return None
    try:
        return line.format(**kw)
    except (KeyError, IndexError):
        return re.sub(r"\s*\{[^}]*\}", "", line).replace(" ;", ";").replace(" .", ".")
