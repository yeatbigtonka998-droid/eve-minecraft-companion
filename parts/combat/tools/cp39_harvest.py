"""CP-39 harvest_animal (O94b) on a Shark lane: Eve kills exactly the granted animals inside a zone box and picks up their drops.

    py -3.12 parts/combat/tools/cp39_harvest.py --lane e [--n 10] --expect-jar <sha256> --expect-body <sha256> [--stub] [--next <chat>]
    --stub (O102c b): drive body's stub_mind Hub (dev client built with the 0.16.0 draft; EVE_REGISTRY / EVE_HUB_PORT / EVE_LINK_TOKEN
    as body's rig sets them) instead of the lane mind, whose 0.15.0 registry has no harvest_animal.

Needs a registry with harvest_animal v1 on the lane's mind and body (combat-0.16.0.py delta, assembled by the orchestrator).
Each trial, inside a 15x15 zone in the middle of the arena (all animals NoAI, so only the scripted one moves):
  - granted: two adult cows                     -> must die (the drops are counted from her inventory)
  - breeding: an adult cow, in entity_ids and in exclude_ids -> refused client:excluded, never hit
  - baby: a baby cow in entity_ids              -> refused animal:baby, never hit
  - named: a sheep named "Dolly" in entity_ids  -> refused protected:named, never hit
  - fleer: a granted cow; when Eve comes within 5 of it the harness moves it 6 blocks outside the zone -> refused client:left_zone,
    alive, and Eve never follows it out
Pass = result ok, both granted cows dead, the three protected animals at full health, the fleer alive and refused left_zone,
Eve never outside the zone box (RCON every 0.1 s, 0.3 tolerance for her hitbox), drops > 0, no death, every row's
max_state_gap_s <= 1.0 (I29e).
"""
import json, sqlite3, sys, threading, time, uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import laneb_combat as L  # noqa: E402

r, P, X0, X1, Z0, Z1, Y, CX, CZ = L.r, L.P, L.X0, L.X1, L.Z0, L.Z1, L.Y, L.CX, L.CZ
TAG = "evecz"
ZONE = {"min": {"x": CX - 7, "y": Y + 1, "z": CZ - 7}, "max": {"x": CX + 7, "y": Y + 3, "z": CZ + 7}}  # block_pos (O94e)
MIND_DB = L.LANE / "mind" / "eve.db"
STUB = "--stub" in sys.argv  # O102c (b): body's dev client with the 0.16.0 draft, driven through body's stub_mind Hub
LINK = None


def pos_of(sel):
    out = r(f"data get entity {sel} Pos")
    nums = [float(v.rstrip("d")) for v in out.split("[", 1)[-1].rstrip("]").split(", ")] if "[" in out else None
    return nums


def health(sel):
    out = r(f"data get entity {sel} Health")
    try:
        return float(out.rsplit(" ", 1)[-1].rstrip("f"))
    except ValueError:
        return None  # gone (dead)


def summon(kind, name, x, z, extra=""):
    nbt = f'Tags:["{TAG}","{TAG}_{name}"],NoAI:1b,PersistenceRequired:1b' + (f",{extra}" if extra else "")
    r(f"summon minecraft:{kind} {x + 0.5} {Y + 1} {z + 0.5} {{{nbt}}}")


def uuid_of(name):
    """The animal's UUID from its int-array NBT, to find its network id in the body's state."""
    out = r(f"data get entity @e[tag={TAG}_{name},limit=1] UUID")
    ints = [int(v) for v in out.split("[I;", 1)[-1].rstrip("]").split(",")] if "[I;" in out else None
    return str(uuid.UUID(bytes=b"".join(i.to_bytes(4, "big", signed=True) for i in ints))) if ints else None


def ids_by_uuid(want, timeout=5.0):
    """Network ids from the newest state frame's nearby_entities (read-only on the lane mind's DB)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        con = sqlite3.connect(f"file:{MIND_DB.as_posix()}?mode=ro", uri=True, timeout=5)
        row = con.execute("SELECT payload FROM observations WHERE kind='state' ORDER BY ts DESC LIMIT 1").fetchone()
        con.close()
        near = {e["uuid"]: e["id"] for e in (json.loads(row[0]).get("nearby_entities") or [])} if row else {}
        if all(u in near for u in want.values()):
            return {n: near[u] for n, u in want.items()}
        time.sleep(0.3)
    return None


def in_zone(p, tol=0.3):
    return (ZONE["min"]["x"] - tol <= p[0] < ZONE["max"]["x"] + 1 + tol and ZONE["min"]["z"] - tol <= p[2] < ZONE["max"]["z"] + 1 + tol)


class Watch(threading.Thread):
    """Eve's position every 0.1 s (the zone rule), and the fleer's scripted escape when she comes within 5 of it."""

    def __init__(self):
        super().__init__(daemon=True)
        self.stop, self.outside, self.fled = threading.Event(), [], False

    def run(self):
        while not self.stop.is_set():
            me = pos_of(P)
            if me and not in_zone(me):
                self.outside.append([round(v, 2) for v in me])
            f = pos_of(f"@e[tag={TAG}_fleer,limit=1]")
            if me and f and not self.fled and ((me[0] - f[0]) ** 2 + (me[2] - f[2]) ** 2) ** 0.5 < 5:
                r(f"tp @e[tag={TAG}_fleer,limit=1] {ZONE['max']['x'] + 7}.5 {Y + 1} {ZONE['max']['z'] + 1}.5")  # not a player: O39 passes
                self.fled = True
            time.sleep(0.1)


def trial(k):
    L.clear_arena()
    r(f"kill @e[tag={TAG}]")
    L.kit()
    L.stand(0)
    time.sleep(0.5)
    spots = {"cow_a": (CX + 4, CZ + 2), "cow_b": (CX - 4, CZ + 3), "breeding": (CX + 2, CZ - 4), "baby": (CX - 3, CZ - 3),
             "named": (CX + 5, CZ - 1), "fleer": (CX + 5, CZ + 6)}
    for name, (x, z) in spots.items():
        kind = "sheep" if name == "named" else "cow"
        extra = 'CustomName:"Dolly"' if name == "named" else "Age:-24000" if name == "baby" else ""
        summon(kind, name, x, z, extra)
    time.sleep(1.0)
    uuids = {n: uuid_of(n) for n in spots}
    ids = ({n: LINK.net_id(u) for n, u in uuids.items()} if STUB else ids_by_uuid(uuids))
    if ids and None in ids.values():
        ids = None
    if not ids:
        return {"case": "h_mixed", "trial": k + 1, "ok": False, "detail": "FAIL (fixture): animals not in the body's state", "valid": False}
    hp0 = {n: health(f"@e[tag={TAG}_{n},limit=1]") for n in ("breeding", "baby", "named")}
    inv0 = r(f"data get entity {P} Inventory")
    params = {"entity_ids": [ids[n] for n in ("cow_a", "cow_b", "breeding", "baby", "named", "fleer")],
              "exclude_ids": [ids["breeding"]], "zone": ZONE}
    w = Watch()
    w.start()
    t0_ms = int(time.time() * 1000)
    d0 = L.deaths()
    res = LINK.do("harvest_animal", params, 90) if STUB else L.eve("do", {"skill": "harvest_animal", "params": params, "wait": 60}, 90)
    w.stop.set(); w.join()
    died = L.deaths() - d0
    ev = res.get("evidence") or {}
    refused = {x.get("id"): x.get("rule_id") for x in (ev.get("refused") or [])}
    dead = {n: health(f"@e[tag={TAG}_{n},limit=1]") is None for n in ("cow_a", "cow_b", "fleer")}
    hp1 = {n: health(f"@e[tag={TAG}_{n},limit=1]") for n in hp0}
    drops = ev.get("drops") or {}
    checks = [(res.get("status") == "ok", f"result {res.get('status')}/{res.get('code') or res.get('error')}"),
              (dead["cow_a"] and dead["cow_b"], f"granted cows dead {dead['cow_a']}/{dead['cow_b']}"),
              (all(hp1[n] is not None and hp1[n] >= hp0[n] for n in hp0), f"protected health {hp0} -> {hp1}"),
              (refused.get(ids["breeding"]) == "client:excluded", f"breeding refused {refused.get(ids['breeding'])}"),
              (refused.get(ids["baby"]) == "animal:baby", f"baby refused {refused.get(ids['baby'])}"),
              (refused.get(ids["named"]) == "protected:named", f"named refused {refused.get(ids['named'])}"),
              (w.fled and not dead["fleer"] and refused.get(ids["fleer"]) == "client:left_zone",
               f"fleer moved {w.fled}, alive {not dead['fleer']}, refused {refused.get(ids['fleer'])}"),
              (not w.outside, f"Eve outside the zone {len(w.outside)} samples {w.outside[:3]}"),
              (sum(drops.values()) > 0 if isinstance(drops, dict) else False, f"drops {drops}"),
              (died == 0, f"deaths {died}")]
    gap = LINK.gap(t0_ms / 1000) if STUB else L.state_gap(t0_ms)
    checks.append((gap is not None and gap <= 1.0, f"max_state_gap_s {gap} (I29e <= 1.0)"))
    ok = all(c for c, _ in checks)
    detail = "; ".join(d for _, d in checks) if ok else "FAIL: " + "; ".join(d for c, d in checks if not c)
    r(f"kill @e[tag={TAG}]")
    return {"case": "h_mixed", "trial": k + 1, "t_end": round(time.time(), 1), "max_state_gap_s": gap, "ok": ok, "detail": detail,
            "ids": ids, "params": params, "result": res, "eve_outside": w.outside[:10], "fled": w.fled, "valid": True}


def main():
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 10
    global LINK
    st = {}
    if STUB:
        LINK = L.StubLink("harvest_animal")
    else:
        st = L.eve("status")
        if not st.get("body"):
            sys.exit(f"lane {L.LANE_ID} body is not up: {st}")
    L.lane_lock("acquire", 15 + 2 * n)
    try:
        L.check_jar()
    except SystemExit:
        L.lane_lock("release")
        raise
    root = L.EVIDENCE / "CP-39"
    root.mkdir(parents=True, exist_ok=True)
    num = 1 + sum(1 for d in root.iterdir() if d.is_dir() and f"-{L.LABEL}-attempt-" in d.name)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    out = root / f"{stamp}-{L.LABEL}-harvest-attempt-{num}"
    out.mkdir()
    rows, t0, aborted = [], time.time(), None
    L.build_arena()
    L.stash_save()
    try:
        for k in range(n):
            row = trial(k)
            rows.append(row)
            with open(out / "rows.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            print(f"{'PASS' if row['ok'] else 'FAIL'} h_mixed #{k + 1}: {row['detail']}", flush=True)
            if "deaths 1" in row["detail"]:
                raise RuntimeError(f"death in h_mixed #{k + 1}")
    except Exception as e:  # always summarise
        aborted = f"{type(e).__name__}: {e}"
    finally:
        r(f"kill @e[tag={TAG}]")
        teardown = L.safe_teardown(L.clear_arena)
        L.stash_restore()
        r(f"forceload remove {X0} {Z0} {X1} {Z1}")
        L.lane_lock("release")
    passed = sum(x["ok"] for x in rows)
    reg = st.get("registry") or ({"version": "0.16.0 draft via body's stub_mind", "hash": LINK.registry} if STUB else {})
    (out / "RESULT.md").write_text("\n".join([
        f"# CP-39 harvest_animal — lane {L.LANE_ID.upper()} {stamp} attempt {num}: {passed}/{len(rows)}", "",
        "Hypothesis: harvest_animal kills exactly the granted adult cows inside the zone, refuses breeding stock, a baby, a named "
        "sheep (refused named) and a cow that leaves the zone (never hit, never followed), stays inside the zone and picks up the drops.",
        f"Fixture: zone {ZONE}; animals NoAI; the fleer is moved 6 blocks outside the zone when Eve comes within 5. Kit iron sword "
        "+ iron armour. Judge: RCON 0.1 s positions, entity health, deathCount; skill evidence (refused, drops); I29e state gaps.",
        f"Versions: repo {L.git_head()}, lane src {L.lane_src()}, eve-combat jar {L.jar_sha()}, eve-body jar {L.body_sha()}, registry "
        f"{reg.get('version')} {str(reg.get('hash'))[:12]}. Elapsed {time.time() - t0:.0f} s. Teardown: {teardown}."
        + (f" ABORTED: {aborted}" if aborted else "") + " Development evidence, not qualification.", "",
        "| trial | ok | detail |", "|---|---|---|"] + [f"| {x['trial']} | {'PASS' if x['ok'] else 'FAIL'} | {x['detail']} |" for x in rows]) + "\n",
        encoding="utf-8")
    print(L.export_candidates(out, t0), flush=True)
    print(f"{passed}/{len(rows)} -> {out}")


if __name__ == "__main__":
    main()
