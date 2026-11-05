"""CP-39 skills primitives on lane B (O94f): shear_sheep, milk_cow and harvest_crop under sense's grants.

    py -3.12 parts/skills/tools/cp39_tend.py --lane B [--n 3] [--only t_shear,...] [--deploy] [--expect-mind 556b1da3]
    py -3.12 parts/skills/tools/cp39_tend.py --selftest        (offline: the judges and the envelope check, no lane)

Needs the assembled registry 0.16.0 (shear_sheep, milk_cow, harvest_crop v1) on the lane's body and mind, sense 0.1.21 and
envelope v9 on the lane server. Before anything runs, the lane's own envelope (server-dev/config/envelope.json) is checked
for what the cases rely on, else INVALID with the entries it lacks (no lane change made):
  - animal rules for zone "home": minecraft:sheep with daily.shear >= 3, minecraft:cow with daily.milk >= 2 (Q, the quota case
    uses it); no animal rule for zone "mine" (the ungranted case: animal:no_rule);
  - a farmland box covering the CP-38 rows (x FX0..FX1, y FY, z -63..-62) and none covering the untagged row (z UZ).
The harness plays the mind's grant: it passes the animals' network ids (entity_ids, from the body's state in the lane mind
DB) and the zone, as the mind does after sense's entity_grant. Animals are NoAI, so only Eve moves. Every case starts a
fresh game day (time add 24000: sense's quotas are per day).
Cases per trial (home zone = the pad, y 151; the mine room = zone "mine", no animal rule):
  t_shear          granted sheep A, B -> sheared; sheep C not in entity_ids -> untouched; a lamb and "Dolly" in entity_ids
                   -> refused animal:baby / protected:named, unsheared. ok.
  t_shear_nogrant  two sheep in the mine room, both "granted" by the harness -> the server refuses animal:no_rule: failed
                   zone_denied, nobody sheared.
  t_milk           granted cows A, B + a calf, 2 buckets -> 2 milk buckets, calf refused animal:baby. ok.
  t_milk_nobucket  granted cows, no bucket -> failed no_tool, no milk.
  t_milk_quota     Q + 1 granted cows, Q + 1 buckets -> Q milked, then animal:quota:home:minecraft:cow:milk: failed zone_denied.
  t_crop_tagged    the CP-38 farm (tagged farmland): harvest_crop {crop wheat, count n} -> exactly n wheat harvested and
                   replanted, every other plot as it was. ok.
  t_crop_untagged  one mature wheat on an untagged farmland row: harvested, the replant refused crop:untagged_farmland:
                   failed zone_denied, the plot in not_replanted with that rule id.
Pass per row = the expected result, the evidence keys exactly the registry's, every refusal {id, rule_id} with sense's ids
(or client: ones), drops and inventory deltas from the server (RCON), Eve never outside the case's zone (0.1 s RCON, 0.3
tolerance), max_state_gap_s <= 1.0 (I29e). Planner calls are counted per run (O86g). Development evidence, not qualification.
"""
import json, re, sqlite3, sys, threading, time, uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import laneb_skills as L  # noqa: E402  (reads --lane)

r, P = L.r, L.P
TAG = "evetend"
ANIMAL_KEYS = {"gathered", "animals", "refused", "stop_reason"}  # registry 0.16.0 draft (parts/skills/registry/skills-0.16.0.py)
CROP_KEYS = {"harvested", "replanted", "immature_left", "not_replanted", "trampled", "drops", "not_harvested", "walks", "trampled_at",
             "stop_reason"}
SENSE_RULE = re.compile(r"^(animal:(no_rule|baby|leashed|quota:\S+|keep_min:\S+|not_allowed:\S+)|protected:(named|pets)|crop:untagged_farmland"
                        r"|client:[a-z_]+|server:zone_denied|server_denied\?)$")
# home-zone pen on the pad's north part (the CP-38 farm rows are at z -63/-62, the untagged row at UZ)
PEN = {"min": {"x": -44 + L.DX, "y": 151 + L.DY, "z": -76 + L.DZ}, "max": {"x": -30 + L.DX, "y": 153 + L.DY, "z": -70 + L.DZ}}
PEN_SPOTS = [(-42, -74), (-38, -74), (-34, -74), (-40, -71), (-36, -71), (-32, -72), (-43, -71)]
MINE = {"min": {"x": L.MX0 + 1, "y": L.MY + 1, "z": L.MZ0 + 1}, "max": {"x": L.MX1 - 1, "y": L.MY + 3, "z": L.MZ1 - 1}}
UZ, UX = -67 + L.DZ, (-44 + L.DX, -42 + L.DX)  # the untagged farmland row: x UX[0]..UX[1], y FY, z UZ
PADBOX = {"min": {"x": L.PAD[0], "y": L.PAD[1], "z": L.PAD[2]}, "max": {"x": L.PAD[3], "y": L.PAD[4], "z": L.PAD[5]}}
MIND_DB = L.LANE / "mind" / "eve.db"


# ---- envelope pre-flight (pure: --selftest runs it on the v9 draft) ----
def envelope_gaps(env):
    """What the cases need from the lane envelope and do not find; [] = ready. Q = the home cow milk quota."""
    gaps, rules = [], env.get("animals") or []
    if (env.get("envelope_version") or 0) < 9:
        gaps.append(f"envelope_version {env.get('envelope_version')} < 9")
    rule = {(a.get("zone"), a.get("species")): a for a in rules}
    sheep, cow = rule.get(("home", "minecraft:sheep")), rule.get(("home", "minecraft:cow"))
    if not sheep or (sheep.get("daily") or {}).get("shear", 0) < 3:
        gaps.append('animals: {zone: "home", species: "minecraft:sheep", daily: {shear: >= 3}, keep_min: 2}')
    if not cow or (cow.get("daily") or {}).get("milk", 0) < 2:
        gaps.append('animals: {zone: "home", species: "minecraft:cow", daily: {milk: >= 2}, keep_min: 2}')
    if any(a.get("zone") == "mine" for a in rules):
        gaps.append('no animal rule for zone "mine" (the ungranted case needs animal:no_rule there)')
    boxes = env.get("farmland") or []

    def covered(x, y, z):
        return any(b.get("dim", "minecraft:overworld") == "minecraft:overworld" and all(
            min(b["min"][a], b["max"][a]) <= v <= max(b["min"][a], b["max"][a]) for a, v in zip("xyz", (x, y, z))) for b in boxes)
    if not all(covered(x, L.FY, z) for x in range(L.FX0, L.FX1 + 1) for z in L.FZ):
        gaps.append(f"farmland: a box covering x {L.FX0}..{L.FX1}, y {L.FY}, z {min(L.FZ)}..{max(L.FZ)} (the CP-38 rows)")
    if any(covered(x, L.FY, UZ) for x in range(UX[0], UX[1] + 1)):
        gaps.append(f"farmland: no box may cover x {UX[0]}..{UX[1]}, y {L.FY}, z {UZ} (the untagged row)")
    q = (cow.get("daily") or {}).get("milk") if cow else None
    return gaps, q


# ---- judges shared by every case (pure) ----
def evidence_checks(res, keys):
    ev = res.get("evidence") or {}
    checks = [(set(ev) == keys, f"evidence keys exact ({sorted(set(ev) ^ keys) or 'yes'})")]
    ref = ev.get("refused")
    if "refused" in keys:
        shapes = [x for x in (ref or []) if not (isinstance(x, dict) and set(x) == {"id", "rule_id"} and SENSE_RULE.match(str(x.get("rule_id"))))]
        checks.append((isinstance(ref, list) and not shapes, f"refused is [{{id, rule_id}}] with sense/client ids (bad {shapes[:2]})"))
    checks.append((bool(ev.get("stop_reason")), f"stop_reason {str(ev.get('stop_reason'))[:120]}"))
    return checks


def refused_map(res):
    return {x.get("id"): x.get("rule_id") for x in ((res.get("evidence") or {}).get("refused") or []) if isinstance(x, dict)}


def server_rule(res, rule):
    """A server refusal's rule id, verbatim: sense sends it in permission.denied, the mind appends that to the action's note;
    the skill sees it only if its sub-action's note carries it (else it records server:zone_denied). Either place counts."""
    ev = res.get("evidence") or {}
    where = [str(res.get("note") or "")] + [str(v) for v in refused_map(res).values()] + [str(o.get("reason")) for o in (ev.get("not_replanted") or [])
                                                                                       if isinstance(o, dict)]
    return any(w == rule or w.startswith(rule) or (" " + rule) in (" " + w) for w in where)


def in_box(p, box, tol=0.3):
    return all(box["min"][a] - tol <= v < box["max"][a] + 1 + tol for a, v in zip("xz", (p[0], p[2])))


# ---- lane helpers ----
def pos_of(sel):
    out = r(f"data get entity {sel} Pos")
    return [float(v.rstrip("d")) for v in out.split("[", 1)[-1].rstrip("]").split(", ")] if "[" in out else None


def summon(kind, name, x, y, z, extra=""):
    nbt = f'Tags:["{TAG}","{TAG}_{name}"],NoAI:1b,PersistenceRequired:1b' + (f",{extra}" if extra else "")
    r(f"summon minecraft:{kind} {x + 0.5} {y} {z + 0.5} {{{nbt}}}")


def uuid_of(name):
    out = r(f"data get entity @e[tag={TAG}_{name},limit=1] UUID")
    ints = [int(v) for v in out.split("[I;", 1)[-1].rstrip("]").split(",")] if "[I;" in out else None
    return str(uuid.UUID(bytes=b"".join(i.to_bytes(4, "big", signed=True) for i in ints))) if ints else None


def ids_of(names, timeout=6.0):
    """Network ids from the newest state frame's nearby_entities (read-only on the lane mind's DB), as combat's fixture."""
    want, t0 = {n: uuid_of(n) for n in names}, time.time()
    while time.time() - t0 < timeout:
        con = sqlite3.connect(f"file:{MIND_DB.as_posix()}?mode=ro", uri=True, timeout=5)
        row = con.execute("SELECT payload FROM observations WHERE kind='state' ORDER BY ts DESC LIMIT 1").fetchone()
        con.close()
        near = {e.get("uuid"): e.get("id") for e in (json.loads(row[0]).get("nearby_entities") or [])} if row else {}
        if all(u in near for u in want.values()):
            return {n: near[u] for n, u in want.items()}
        time.sleep(0.3)
    return None


def n_ent(sel):
    m = re.search(r"Count: (\d+)", r(f"execute if entity @e[{sel}]") or "")
    return int(m.group(1)) if m else 0


def unhurt(names, full):
    """Watch's beef question (attempt 5): server's count of each animal alive at full health (cow 10, sheep 8), read before the
    next fixture kill. (ok, detail) for the checks: a tended animal never loses HP."""
    hurt = [n for n in names if n_ent(f"tag={TAG}_{n},nbt={{Health:{full}.0f}}") != 1]
    return (not hurt, f"server: all of {names} alive at {full} HP (hurt or dead: {hurt})")


def sheared(name):
    return n_ent(f"tag={TAG}_{name},nbt={{Sheared:1b}}") == 1


class Watch(threading.Thread):
    """Eve's position every 0.1 s against the case's box."""

    def __init__(self, box):
        super().__init__(daemon=True)
        self.box, self.stop, self.outside = box, threading.Event(), []

    def run(self):
        while not self.stop.is_set():
            me = pos_of(P)
            if me and not in_box(me, self.box):
                self.outside.append([round(v, 2) for v in me])
            time.sleep(0.1)


SPAWN = {"record": [], "issue": None}  # I37: the last case's spawn read-back, attached to its row


def entity_value(out):
    """The value of one `data get entity <sel> <path>` reply: an int (Age, Sheared 0b/1b), True for a present compound or
    string (CustomName), None when the path or the entity is missing."""
    if out is None or re.search(r"(?i)found no elements|no entity was found|not found", out):
        return None
    m = re.search(r"entity data: (-?\d+)[bsL]?\s*$", out.strip())
    return int(m.group(1)) if m else ("entity data:" in out or None)


def spawn_issues(spec, read):
    """I37 (O102: summon with NBT skips finalizeSpawn): each animal as intended - alive, adult or baby as asked, a sheep
    unsheared (wool on, shearable), a CustomName exactly where asked. read(name, path) -> entity_value. Returns (record, issues)."""
    rec, bad = [], []
    for name, kind, nbt in spec:
        age, named = read(name, "Age"), read(name, "CustomName")
        sheared = read(name, "Sheared") if kind == "sheep" else None
        want_baby, want_named = "Age:-" in nbt, "CustomName" in nbt
        row = {"name": name, "kind": kind, "age": age, "sheared": sheared, "named": bool(named)}
        why = ("missing" if age is None else "baby" if age < 0 and not want_baby else "adult" if age >= 0 and want_baby else
               "sheared (no wool)" if kind == "sheep" and sheared not in (0, None) else
               "no Sheared tag" if kind == "sheep" and sheared is None else
               "unexpected name" if bool(named) != want_named else None)
        row["ok"] = why is None
        rec.append(row)
        if why:
            bad.append(f"{name} ({kind}): {why}")
    return rec, bad


def deaths():
    """Eve's deathCount since the series start (scoreboard evedeaths, set to 0 there); 0 when unreadable."""
    m = re.search(r"has (\d+) \[evedeaths\]", r(f"scoreboard players get {P} evedeaths") or "")
    return int(m.group(1)) if m else 0


def fresh_day():
    r("time add 24000")  # sense's quotas count per game day: every case starts a new one


def pen_animals(spec, where="pen"):
    """spec: [(name, kind, nbt)] on the pen spots (or the mine room's); returns the network ids by name, or None. She is moved
    there first: the ids come from the body's nearby_entities (32 blocks)."""
    if where == "pen":
        L.tp(-37.5 + L.DX, 151 + L.DY, -72.5 + L.DZ)
    else:
        L.tp(88.5 + L.MDX, L.MY + 1, -47.5 + L.MDZ)
    r(f"kill @e[tag={TAG}]")
    for i, (name, kind, nbt) in enumerate(spec):
        if where == "pen":
            x, z = PEN_SPOTS[i % len(PEN_SPOTS)]
            summon(kind, name, x + L.DX, 151 + L.DY, z + L.DZ, nbt)
        else:
            x, y, z = L.SPOTS[i % len(L.SPOTS)]
            summon(kind, name, x, y, z, nbt)
    time.sleep(1.0)
    SPAWN["record"], bad = spawn_issues(spec, lambda n, path: entity_value(r(f"data get entity @e[tag={TAG}_{n},limit=1] {path}")))
    if bad:  # I37: a wrong spawn is the fixture's fault - INVALID, never a false refusal or pass
        SPAWN["issue"] = "spawn not as intended (I37): " + "; ".join(bad)
        return None
    ids = ids_of([n for n, _, _ in spec])
    if not ids:
        SPAWN["issue"] = "animals not in the body's state"
    return ids


def run_case(name, skill, params, box, wait=90):
    """One skill call with the zone watch and the I29e gap: (result, inventory before, after, outside samples, gap)."""
    r(f"kill @e[{PAD_ITEMS}]")  # O278-corr/O282: a cleared floor just before dispatch: every delta is this call's (fixture kills drop too)
    lo, hi = box["min"], box["max"]  # a6: old fixture-kill loot (mutton 3, wool 2) lay in the mine room and was walked over
    r(f"kill @e[type=minecraft:item,x={lo['x'] - 3},y={lo['y'] - 2},z={lo['z'] - 3},dx={hi['x'] - lo['x'] + 6},dy={hi['y'] - lo['y'] + 4},dz={hi['z'] - lo['z'] + 6}]")
    i0 = L.inv()
    w = Watch(box)
    w.start()
    t0 = int(time.time() * 1000)
    res = L.do(skill, params, wait)
    w.stop.set()
    w.join()
    time.sleep(0.5)
    return res, i0, L.inv(), w.outside, L.state_gap(t0)


def wool(i):
    return sum(c for it, c in i.items() if it.endswith("_wool"))


# ---- cases: each returns (checks, row extras) ----
def t_shear(k):
    fresh_day()
    ids = pen_animals([("a", "sheep", ""), ("b", "sheep", ""), ("c", "sheep", ""), ("lamb", "sheep", "Age:-24000"),
                       ("dolly", "sheep", 'CustomName:"Dolly"')])
    if not ids:
        return None, SPAWN["issue"]
    L.give([("minecraft:shears", 1), (L.BREAD, 16)])
    params = {"entity_ids": [ids["a"], ids["b"], ids["lamb"], ids["dolly"]], "zone": PEN}
    res, i0, i1, out, gap = run_case("t_shear", "shear_sheep", params, PEN)
    ref, ev, left = refused_map(res), res.get("evidence") or {}, loose()
    g = sum(v for it, v in (ev.get("gathered") or {}).items() if it.endswith("_wool")) if isinstance(ev.get("gathered"), dict) else None
    checks = [L.expect(res, "ok"), (sheared("a") and sheared("b"), "granted A and B sheared (server NBT)"),
              (not sheared("c") and ids["c"] not in ref, "C (not granted) untouched and never named"),
              (not sheared("lamb") and ref.get(ids["lamb"]) == "animal:baby", f"lamb refused {ref.get(ids['lamb'])}, unsheared"),
              (not sheared("dolly") and ref.get(ids["dolly"]) == "protected:named", f"Dolly refused {ref.get(ids['dolly'])}, unsheared"),
              (2 <= wool(i1) - wool(i0) <= 6 and g == wool(i1) - wool(i0), f"wool server +{wool(i1) - wool(i0)} (2 sheep: 2..6), evidence {g}"),
              (set(ev.get("gathered") or {}) <= WOOLS, f"gathered wool only: {ev.get('gathered')}"), (left == 0, f"server: {left} loose items left on the pad"),
              unhurt(["a", "b", "c", "lamb", "dolly"], 8)]
    return checks + evidence_checks(res, ANIMAL_KEYS) + [(not out, f"Eve outside the zone {len(out)} samples {out[:2]}")], \
        {"ids": ids, "params": params, "result": res, "gap": gap}


def t_shear_nogrant(k):
    fresh_day()
    L.reset_room({})
    ids = pen_animals([("m1", "sheep", ""), ("m2", "sheep", "")], where="mine")
    if not ids:
        return None, SPAWN["issue"]
    L.give([("minecraft:shears", 1), (L.BREAD, 16)])
    params = {"entity_ids": [ids["m1"], ids["m2"]], "zone": MINE}
    res, i0, i1, out, gap = run_case("t_shear_nogrant", "shear_sheep", params, MINE)
    ref = refused_map(res)
    checks = [L.expect(res, "failed", "zone_denied"), (not sheared("m1") and not sheared("m2"), "nobody sheared (server NBT)"),
              (server_rule(res, "animal:no_rule"), f"server rule animal:no_rule (refused {ref}, note {str(res.get('note'))[:160]})"), (wool(i1) == wool(i0), "no wool"), unhurt(["m1", "m2"], 8)]
    return checks + evidence_checks(res, ANIMAL_KEYS) + [(not out, f"Eve outside the zone {len(out)} samples {out[:2]}")], \
        {"ids": ids, "params": params, "result": res, "gap": gap}


def t_milk(k):
    fresh_day()
    ids = pen_animals([("a", "cow", ""), ("b", "cow", ""), ("calf", "cow", "Age:-24000")])
    if not ids:
        return None, SPAWN["issue"]
    L.give([("minecraft:bucket", 2), (L.BREAD, 16)])
    params = {"entity_ids": [ids["a"], ids["b"], ids["calf"]], "zone": PEN}
    res, i0, i1, out, gap = run_case("t_milk", "milk_cow", params, PEN)
    ref, ev = refused_map(res), res.get("evidence") or {}
    milk = L.moved(i0, i1, "minecraft:milk_bucket")
    checks = [L.expect(res, "ok"), (milk == 2 and L.moved(i0, i1, "minecraft:bucket") == -2, f"server: milk +{milk}, buckets {L.moved(i0, i1, 'minecraft:bucket')}"),
              ((ev.get("gathered") or {}) == {"minecraft:milk_bucket": 2}, f"evidence gathered exactly 2 milk: {ev.get('gathered')}"),
              (ref.get(ids["calf"]) == "animal:baby", f"calf refused {ref.get(ids['calf'])}"), unhurt(["a", "b", "calf"], 10)]
    return checks + evidence_checks(res, ANIMAL_KEYS) + [(not out, f"Eve outside the zone {len(out)} samples {out[:2]}")], \
        {"ids": ids, "params": params, "result": res, "gap": gap}


def t_milk_nobucket(k):
    fresh_day()
    ids = pen_animals([("a", "cow", ""), ("b", "cow", "")])
    if not ids:
        return None, SPAWN["issue"]
    L.give([(L.BREAD, 16)])
    params = {"entity_ids": [ids["a"], ids["b"]], "zone": PEN}
    res, i0, i1, out, gap = run_case("t_milk_nobucket", "milk_cow", params, PEN, wait=60)
    checks = [L.expect(res, "failed", "no_tool"), (L.moved(i0, i1, "minecraft:milk_bucket") == 0, "no milk (server)"), unhurt(["a", "b"], 10)]
    return checks + evidence_checks(res, ANIMAL_KEYS) + [(not out, f"Eve outside the zone {len(out)} samples {out[:2]}")], \
        {"ids": ids, "params": params, "result": res, "gap": gap}


def t_milk_quota(k, q):
    fresh_day()
    names = [f"q{i}" for i in range(q + 1)]
    ids = pen_animals([(n, "cow", "") for n in names])
    if not ids:
        return None, SPAWN["issue"]
    L.give([("minecraft:bucket", q + 1), (L.BREAD, 16)])
    params = {"entity_ids": [ids[n] for n in names], "zone": PEN}
    res, i0, i1, out, gap = run_case("t_milk_quota", "milk_cow", params, PEN, wait=120)
    ref = refused_map(res)
    milk = L.moved(i0, i1, "minecraft:milk_bucket")
    checks = [L.expect(res, "failed", "zone_denied"), (milk == q, f"server: milk +{milk} (the quota {q})"),
              unhurt(names, 10), ((res.get("evidence") or {}).get("gathered") == {"minecraft:milk_bucket": q}, f"evidence gathered exactly {q} milk: {(res.get('evidence') or {}).get('gathered')}"),
              (server_rule(res, "animal:quota:home:minecraft:cow:milk"), f"server rule animal:quota:home:minecraft:cow:milk (refused {ref}, note {str(res.get('note'))[:160]})")]
    return checks + evidence_checks(res, ANIMAL_KEYS) + [(not out, f"Eve outside the zone {len(out)} samples {out[:2]}")], \
        {"ids": ids, "params": params, "result": res, "gap": gap}


WOOLS = {f"minecraft:{c}_wool" for c in ("white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray", "light_gray", "cyan",
                                         "purple", "blue", "brown", "green", "red", "black")}
PAD_ITEMS = f"type=minecraft:item,x={L.PAD[0]},y={L.PAD[1]},z={L.PAD[2]},dx={L.PAD[3] - L.PAD[0]},dy={L.PAD[4] - L.PAD[1]},dz={L.PAD[5] - L.PAD[2]}"


def item_count(reply):
    """Server's reply to `execute if entity`: 'Test passed, count: N' / 'Test failed' -> N; None when it says neither."""
    m = re.search(r"passed\D*(\d+)", reply or "", re.I)
    return int(m.group(1)) if m else 0 if "failed" in (reply or "").lower() else None


def loose(item=None):
    """Loose item entities on the pad (RCON, under the lane lock), of one id or all."""
    nbt = f',nbt={{Item:{{id:"{item}"}}}}' if item else ""
    reply = r(f"execute if entity @e[{PAD_ITEMS}{nbt}]")
    n = item_count(reply)
    return n if n is not None else f"unread reply {reply!r}"


def t_crop_tagged(k):
    fresh_day()
    lay = L.layout(k)
    wheat = [p for p, (c, a) in lay.items() if c == "minecraft:wheat" and a == 7]
    if not wheat:  # every layout needs mature wheat to take
        p0 = L.plots()[0]
        lay[p0] = ("minecraft:wheat", 7)
        wheat = [p0]
    n = min(3, len(wheat))
    L.build_farm(lay)
    L.give([(L.BREAD, 16)])
    params = {"min": L.FARM_MIN, "max": L.FARM_MAX, "crop": "minecraft:wheat", "count": n, "replant": True}
    res, i0, i1, out, gap = run_case("t_crop_tagged", "harvest_crop", params, PADBOX, wait=180)
    after = {p: L.block_at(p) for p in L.plots()}
    ev = res.get("evidence") or {}
    left_wheat, left_all = loose("minecraft:wheat"), loose()
    taken = [p for p in wheat if after[p][0] == "minecraft:wheat" and after[p][1] < 7]  # harvested and replanted young
    others = [p for p, (c, a) in lay.items() if c != "minecraft:wheat" and (after[p][0] != c or after[p][1] < a)]
    checks = [L.expect(res, "ok"), ((ev.get("harvested") or {}) == {"minecraft:wheat": n}, f"evidence harvested {ev.get('harvested')} (want wheat {n})"),
              ((ev.get("replanted") or {}) == {"minecraft:wheat": n}, f"evidence replanted {ev.get('replanted')}"),
              (len(taken) == n, f"server: {len(taken)} mature wheat plots now young wheat (want {n})"),
              (not others, f"carrots/potatoes untouched (bad {others[:3]})"),
              (L.moved(i0, i1, "minecraft:wheat") == n, f"server: wheat +{L.moved(i0, i1, 'minecraft:wheat')} (exactly {n}), seeds +{L.moved(i0, i1, 'minecraft:wheat_seeds')}"),
              (left_wheat == 0, f"server: {left_wheat} wheat left on the ground, {left_all} loose items in all; {ev.get('stop_reason')}"),
              (L.farmland_ok(), "farmland intact")]
    return checks + evidence_checks(res, CROP_KEYS) + [(not out, f"Eve off the pad {len(out)} samples {out[:2]}")], \
        {"params": params, "result": res, "gap": gap}


def t_crop_untagged(k):
    fresh_day()
    L.build_farm({})
    r(f"fill {UX[0]} {L.FY + 1} {UZ - 1} {UX[1]} {L.FY + 2} {UZ + 1} minecraft:air")
    r(f"fill {UX[0]} {L.FY} {UZ} {UX[1]} {L.FY} {UZ} minecraft:farmland[moisture=7]")
    r(f"setblock {UX[0] + 1} {L.FY + 1} {UZ} minecraft:wheat[age=7]")
    L.give([(L.BREAD, 16)])
    L.tp(UX[0] + 1.5, L.FY + 1, UZ + 1.5, 180, 40)
    lo, hi = {"x": UX[0], "y": L.FY, "z": UZ}, {"x": UX[1], "y": L.FY + 1, "z": UZ}
    params = {"min": lo, "max": hi, "crop": "minecraft:wheat", "count": 1, "replant": True}
    res, i0, i1, out, gap = run_case("t_crop_untagged", "harvest_crop", params, PADBOX, wait=90)
    ev = res.get("evidence") or {}
    nr = ev.get("not_replanted") or []
    plot = L.block_at((UX[0] + 1, L.FY + 1, UZ))
    checks = [L.expect(res, "failed", "zone_denied"), ((ev.get("harvested") or {}) == {"minecraft:wheat": 1}, f"evidence harvested {ev.get('harvested')}"),
              (len(nr) == 1 and server_rule(res, "crop:untagged_farmland"), f"not_replanted {nr}, server rule crop:untagged_farmland (note {str(res.get('note'))[:160]})"),
              (plot == (None, None), f"server: the untagged plot is empty {plot}"),
              (L.moved(i0, i1, "minecraft:wheat") >= 1, f"server: wheat +{L.moved(i0, i1, 'minecraft:wheat')}")]
    r(f"fill {UX[0]} {L.FY} {UZ} {UX[1]} {L.FY + 1} {UZ} minecraft:smooth_stone")  # the untagged row back to pad floor
    r(f"fill {UX[0]} {L.FY + 1} {UZ} {UX[1]} {L.FY + 1} {UZ} minecraft:air")
    return checks + evidence_checks(res, CROP_KEYS) + [(not out, f"Eve off the pad {len(out)} samples {out[:2]}")], \
        {"params": params, "result": res, "gap": gap}


CASES = [t_shear, t_shear_nogrant, t_milk, t_milk_nobucket, t_milk_quota, t_crop_tagged, t_crop_untagged]


def selftest():
    v9 = json.loads((L.REPO / "parts/server-helper/envelope-v9-cp39-draft.json").read_text(encoding="utf-8"))
    gaps, q = envelope_gaps(v9)
    assert q == 4 and any("farmland" in g for g in gaps), (gaps, q)  # the draft has the animal rules, not the farmland boxes yet
    v9["farmland"] = [{"id": "cp38", "dim": "minecraft:overworld", "min": {"x": L.FX0, "y": L.FY, "z": min(L.FZ)},
                       "max": {"x": L.FX1, "y": L.FY, "z": max(L.FZ)}}]
    assert envelope_gaps(v9) == ([], 4), envelope_gaps(v9)
    v9["farmland"].append({"id": "bad", "min": {"x": UX[0], "y": L.FY, "z": UZ}, "max": {"x": UX[1], "y": L.FY, "z": UZ}})
    assert any("untagged" in g for g in envelope_gaps(v9)[0])
    ok = {"status": "ok", "evidence": {"gathered": {}, "animals": [], "refused": [{"id": 7, "rule_id": "animal:baby"},
                                                                                {"id": 8, "rule_id": "client:left_zone"}], "stop_reason": "count (2 sheared, 2 refused)"}}
    assert all(c for c, _ in evidence_checks(ok, ANIMAL_KEYS))
    bad = {"status": "ok", "evidence": {"gathered": {}, "animals": [], "refused": [{"id": 7, "reason": "baby"}], "stop_reason": "x", "extra": 1}}
    assert [c for c, _ in evidence_checks(bad, ANIMAL_KEYS)] == [False, False, True]
    assert SENSE_RULE.match("animal:quota:home:minecraft:cow:milk") and not SENSE_RULE.match("baby")
    q_note = {"note": "stopped; server denied: milk animal:quota:home:minecraft:cow:milk", "evidence": {"refused": [{"id": 5, "rule_id": "server:zone_denied"}]}}
    assert server_rule(q_note, "animal:quota:home:minecraft:cow:milk") and not server_rule(q_note, "animal:no_rule")
    assert server_rule({"evidence": {"refused": [{"id": 5, "rule_id": "animal:no_rule"}]}}, "animal:no_rule")
    assert server_rule({"evidence": {"not_replanted": [{"reason": "use_item zone_denied: crop:untagged_farmland"}]}}, "crop:untagged_farmland")
    # I37: the read-back replies as the 26.3 server gives them, and the expectations per spawn
    assert entity_value("Sheep has the following entity data: 0") == 0 and entity_value("Cow has the following entity data: -24000") == -24000
    assert entity_value("Sheep has the following entity data: 1b") == 1 and entity_value("Found no elements matching CustomName") is None
    assert entity_value('Sheep has the following entity data: "Dolly"') is True and entity_value("No entity was found") is None
    good = {("a", "Age"): 0, ("a", "Sheared"): 0, ("lamb", "Age"): -24000, ("lamb", "Sheared"): 0, ("dolly", "Age"): 0, ("dolly", "Sheared"): 0,
            ("dolly", "CustomName"): True, ("cow", "Age"): 0}
    spec = [("a", "sheep", ""), ("lamb", "sheep", "Age:-24000"), ("dolly", "sheep", 'CustomName:"Dolly"'), ("cow", "cow", "")]
    rec, bad = spawn_issues(spec, lambda n, p: good.get((n, p)))
    assert not bad and len(rec) == 4 and all(x["ok"] for x in rec), bad
    for broken, why in [({("lamb", "Age"): 0}, "lamb (sheep): adult"), ({("a", "Sheared"): 1}, "a (sheep): sheared (no wool)"),
                        ({("cow", "Age"): None}, "cow (cow): missing"), ({("dolly", "CustomName"): None}, "dolly (sheep): unexpected name"),
                        ({("a", "Age"): -1}, "a (sheep): baby")]:
        _, bad = spawn_issues(spec, lambda n, p, b=broken: b[(n, p)] if (n, p) in b else good.get((n, p)))
        assert bad == [why], (broken, bad)
    assert SENSE_RULE.match("server_denied?") and not SENSE_RULE.match("server_denied")
    assert item_count("Test passed, count: 3") == 3 and item_count("Test failed") == 0 and item_count("huh") is None
    assert in_box([-44.2, 151, -76.1], PEN) and in_box([-29.6, 151, -72], PEN) and not in_box([-28.6, 151, -72], PEN)  # max block -30 spans -30..-29
    print("selftest ok")


def main():
    if "--selftest" in sys.argv:
        return selftest()
    env_path = L.SERVER / "config" / "envelope.json"
    env = json.loads(env_path.read_text(encoding="utf-8-sig")) if env_path.exists() else {}
    gaps, q = envelope_gaps(env)
    if gaps:  # O46: the conditions are not there - nothing run, no lane change
        sys.exit("INVALID (O46), the lane envelope lacks:\n  - " + "\n  - ".join(gaps) + f"\n({env_path})")
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 3
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    cases = [c for c in CASES if not only or c.__name__ in only]
    st = L.eve("status")
    if not st.get("body"):
        sys.exit(f"lane {L.LANE_ID} body is not up: {st}")
    if "--expect-mind" in sys.argv:  # the crop cases need mind 556b1da3+ (harvest_crop's grant), as ironkit checks the running src
        import subprocess
        want, running = sys.argv[sys.argv.index("--expect-mind") + 1], st.get("mind_src")
        if not running or subprocess.run(["git", "-C", str(L.REPO), "merge-base", "--is-ancestor", want, running], capture_output=True).returncode:
            sys.exit(f"INVALID (O46): the lane's running mind_src {running} does not contain {want}; nothing run")
    L.lane_lock("acquire", 15 + 3 * n * len(cases))
    root = L.EVIDENCE / "CP-39"
    root.mkdir(parents=True, exist_ok=True)
    num = 1 + sum(1 for d in root.iterdir() if d.is_dir() and f"-{L.TAG}-tend-attempt-" in d.name)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    out = root / f"{stamp}-{L.TAG}-tend-attempt-{num}"
    out.mkdir()
    rows, t0, aborted = [], time.time(), None
    try:
        if "--deploy" in sys.argv:
            L.deploy()
            st = L.eve("status")
        r("difficulty peaceful")
        L.build_pad()
        L.stash_save()
        r("scoreboard objectives add evedeaths deathCount")  # stop on any death (orchestrator)
        r(f"scoreboard players set {P} evedeaths 0")
        for k in range(n):
            for case in cases:
                SPAWN["record"], SPAWN["issue"] = [], None
                got = case(k, q) if case is t_milk_quota else case(k)
                checks, extra = got
                if checks is None:  # the fixture failed, not the skill
                    row = {"case": case.__name__, "trial": k + 1, "verdict": "INVALID", "detail": f"INVALID (fixture): {extra}"}
                else:
                    gap = extra.get("gap")
                    checks.append((gap is not None and gap <= 1.0, f"max_state_gap_s {gap} (I29e <= 1.0)"))
                    ok, detail = L.judged(checks)
                    res = extra.get("result") or {}
                    verdict = "PASS" if ok else ("INVALID" if L.invalid(res) else "FAIL")
                    row = {"case": case.__name__, "trial": k + 1, "t_end": round(time.time(), 1), "max_state_gap_s": gap, "verdict": verdict,
                           "detail": (f"INVALID (O46: {L.invalid(res)}) - " if verdict == "INVALID" else "") + detail,
                           **{kk: v for kk, v in extra.items() if kk != "gap"}}
                row["spawn"] = SPAWN["record"]  # I37: what was actually spawned, read back right after the summon
                died = deaths()
                if died:  # stop on any death: this row says so and nothing more runs
                    row["verdict"], row["detail"] = "FAIL", f"DEATH ({died}) - " + row["detail"]
                rows.append(row)
                with open(out / "rows.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")
                print(f"{row['verdict']} {case.__name__} #{k + 1}: {row['detail'][:600]}", flush=True)
                if died:
                    raise RuntimeError(f"death in {case.__name__} #{k + 1}: series stopped")
    except Exception as e:  # always summarise
        aborted = f"{type(e).__name__}: {e}"
    finally:
        r(f"kill @e[tag={TAG}]")
        L.stash_restore()
        r("difficulty peaceful")
        import subprocess
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(L.LANE / "ops" / "park-eve.ps1"),
                        "-Root", str(L.SERVER), "-Player", P], capture_output=True)
        r(f"forceload remove {L.PAD[0]} {L.PAD[2]} {L.PAD[3]} {L.PAD[5]}")
        r(f"forceload remove {L.MX0} {L.MZ0} {L.MX1} {L.MZ1}")
        L.lane_lock("release")
    passed = sum(x["verdict"] == "PASS" for x in rows)
    n_invalid = sum(x["verdict"] == "INVALID" for x in rows)
    reg = st.get("registry") or {}
    text = "\n".join([
        f"# CP-39 skills primitives — lane {L.LANE_ID} (`{L.TAG}`) {stamp} attempt {num}: {passed}/{len(rows) - n_invalid} valid"
        + (f" ({n_invalid} INVALID, O46)" if n_invalid else ""), "",
        "Hypothesis: shear_sheep and milk_cow tend exactly the granted adults inside the zone and refuse the rest with sense's rule "
        "ids; harvest_crop takes exactly the asked crop count on tagged farmland and replants it, and stops at sense's "
        "crop:untagged_farmland. Quotas and missing rules are the server's refusals.",
        f"Fixture: pen {PEN} (home), mine room {MINE} (zone mine), CP-38 farm rows, untagged row z {UZ}; NoAI animals; a fresh game "
        f"day per case; the home cow milk quota Q = {q}. Judge: RCON NBT/blocks/inventory, 0.1 s zone watch, evidence keys and "
        "refusal shapes, I29e state gap.",
        f"Versions: repo {L.git_head()} (eve-skills jar in the client: {L.jar_sha()}), registry {reg.get('version')} "
        f"{str(reg.get('hash'))[:12]}. Elapsed {time.time() - t0:.0f} s. {L.planner_note(int(t0 * 1000))}. "
        f"{L.export_candidates(out, datetime.fromtimestamp(t0))}."
        + (f" ABORTED: {aborted}" if aborted else "") + " Development evidence, not qualification.", "",
        "| case | trial | verdict | max state gap s | detail |", "|---|---|---|---|---|"]
        + [f"| {x['case']} | {x['trial']} | {x['verdict']} | {x.get('max_state_gap_s')} | {x['detail']} |" for x in rows]) + "\n"
    (out / "RESULT.md").write_text(text, encoding="utf-8")
    summ = L.REPO / "parts" / "skills" / "evidence-summaries" / f"CP-39-{out.name}.md"
    summ.write_text(text, encoding="utf-8")
    print(f"{passed}/{len(rows) - n_invalid} ({n_invalid} invalid) -> {out}")


if __name__ == "__main__":
    main()
