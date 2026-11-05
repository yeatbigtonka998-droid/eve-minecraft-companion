"""CP-54 on a lane: request text + a plot -> brief.json, through the mind's read-only /cli passthroughs (link-protocol
v1.9: survey, storage, resources). No model unless --word (then the program model via the mind's spend reservation).

    python tools/brief_site.py --envelope ENV.json --text "a small cabin with a bedroom on north meadow" --out DIR
        [--from owner|village|wishlist] [--answers '{"plot": "north_meadow"}'] [--twice] [--word]

--twice surveys the plot again and requires the site facts to be identical (O56a: the reader reproduces a site exactly).
Writes DIR/survey.json (raw results), DIR/resources.json (query.resources logs/ore/crops within 32 of the plot),
DIR/brief.json and DIR/result.json (reproduced, costs).
"""
import argparse
import json
import sys
import time
from pathlib import Path

from eve_design import gamedata, models
from eve_design.brief import BriefError, make_brief, pick_zone, read_site, style_cards, survey_box


def cli(cmd: str, args: dict, timeout: float = 10.0) -> dict:
    r = models.MindCliLedger(timeout=timeout)._call(cmd, args)
    if not r.get("ok", True):
        raise BriefError(f"/cli {cmd} refused: {r.get('error')} {r.get('detail', '')}".strip())
    return r


def survey(zone: dict) -> list[dict] | dict:
    """The zone's survey results, or {"error": why} (the brief then asks for another plot instead of guessing)."""
    try:   # ponytail: one call for the whole box; a 64x64 zone over y -64..319 is ~75 chunks, ~10 MB
        r = cli("survey", survey_box(zone), timeout=300)
    except (BriefError, models.ModelError) as e:
        return {"error": f"survey failed: {e}"}
    results = r.get("results") or r.get("chunks") or [r]
    for x in results:
        x.setdefault("tick", r.get("tick"))
    return results


def nearby(site: dict) -> dict:
    """query.resources (logs, ore, crops) within 32 of the plot's centre at its floor: budget.nearby's source."""
    p = site["plot"]
    c = {"x": (p["min"]["x"] + p["max"]["x"]) // 2, "y": site["origin"]["y"], "z": (p["min"]["z"] + p["max"]["z"]) // 2}
    out = {}
    for kind in ("logs", "ore", "crops"):
        try:
            out[kind] = cli("resources", {"dim": site["dim"], "kind": kind, "center": c, "radius": 32}, timeout=30)
        except (BriefError, models.ModelError) as e:   # a budget detail, never a reason to give up the brief
            out[kind] = {"error": str(e)}
    return out


def read(results, zone, gd):
    try:
        return read_site(results, zone, gd), None
    except BriefError as e:
        return None, str(e)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--envelope", type=Path, required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--from", dest="src", default="owner")
    ap.add_argument("--answers", default="{}")
    ap.add_argument("--twice", action="store_true")
    ap.add_argument("--word", action="store_true")
    a = ap.parse_args(argv)
    gd = gamedata.load()
    env = json.loads(a.envelope.read_text())
    answers = json.loads(a.answers)
    a.out.mkdir(parents=True, exist_ok=True)
    zone = pick_zone(a.text, env, answers)
    results = survey(zone) if zone else None
    s1, e1 = read(results, zone, gd) if zone else (None, None)
    res = nearby(s1) if s1 else None
    st, notes = None, {}
    try:
        st = cli("storage", {}) if zone else None
    except (BriefError, models.ModelError) as e:   # storage is a budget detail, never a reason to give up the brief
        notes["storage_error"] = str(e)
    notes |= {f"resources_error_{k}": v["error"] for k, v in (res or {}).items() if "error" in v}
    storage = st and (st.get("index") or st.get("storage") or st)
    req = {"text": a.text, "from": a.src, "sender": "cli", "ts": int(time.time() * 1000)}
    word = models.DeepSeekClient(models.configured_model("pro")) if a.word else None
    b = make_brief(req, results, env, gd, style_cards(), storage=storage, resources=res, answers=answers, wording=word)
    out = {"zone": zone and zone["id"], "status": b["clarification"]["status"],
           "wording_usd": sum(c.get("usd") or 0 for c in b["provenance"]["wording"]["calls"])} | notes
    if a.twice and zone:
        again = survey(zone)
        s2, e2 = read(again, zone, gd)
        if s1 and s2:
            out["reproduced"] = {k: s1[k] == s2[k] for k in s1 if k != "observed"} | {
                "site_hash": s1["observed"]["site_hash"] == s2["observed"]["site_hash"],
                "ticks": [s1["observed"]["tick"], s2["observed"]["tick"]]}
        else:   # the same reader answer twice (e.g. the plot is unloaded both times) is reproduced too
            out["reproduced"] = {"site": e1 is not None and e1 == e2, "error": e1 or e2, "errors": [e1, e2]}
        (a.out / "survey2.json").write_text(json.dumps(again) + "\n")
    (a.out / "survey.json").write_text(json.dumps(results) + "\n")
    if res:
        (a.out / "resources.json").write_text(json.dumps(res) + "\n")
    (a.out / "brief.json").write_text(json.dumps(b, indent=1) + "\n")
    (a.out / "result.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out))
    return 0 if all(v for k, v in out.get("reproduced", {}).items() if k not in ("ticks", "error", "errors")) else 1


if __name__ == "__main__":
    sys.exit(main())
