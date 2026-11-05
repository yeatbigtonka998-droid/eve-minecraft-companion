"""One evidence summary per reflex (evidence-summaries/REFLEX-<name>.md) from the runs' attempts.jsonl, so CP-25/27/29/30/32 can
cite each reflex on its own. Counts every attempt of the listed runs; nothing is filtered.

    py -3.12 tools/reflex_summaries.py
"""
import json
from collections import defaultdict
from pathlib import Path

BODY = Path(__file__).resolve().parents[1]
EV = BODY.parents[1] / "evidence" / "CP-20"
RUNS = [  # (folder, lane, jar / code)
    ("2026-10-19-2103-attempt-13", "Shark local fixture (stub mind)", "smoke, 3 per fixture, jar 7520b732"),
    ("2026-10-19-2110-attempt-15", "Shark local fixture (stub mind)", "jar 7520b732"),
    ("2026-10-19-2131-attempt-17", "Shark local fixture (stub mind)", "jar 7520b732, edge/arbiter fixtures fixed"),
    ("2026-10-20-1401-attempt-18", "Shark local fixture (stub mind)", "smoke, body 674079a"),
    ("2026-10-20-1402-attempt-19", "Shark local fixture (stub mind)", "body 674079a"),
    ("2026-10-20-1420-attempt-20", "Shark local fixture (stub mind)", "jar 76429f04 (eve:rescue signal)"),
    ("2026-10-20-1424-attempt-21", "Shark local fixture (stub mind)", "jar 7ea4a1a8 (signal with integer pos)"),
    ("2026-10-20-1353-shark-attempt-1", "lane B `shark` (real mind + sense)", "lane-B client at main ac3b5c1 (body bcd2727+)"),
    ("2026-10-20-1358-shark-attempt-2", "lane B `shark` (real mind + sense)", "lane-B client at main ac3b5c1 (body bcd2727+)"),
    ("2026-10-20-1430-shark-attempt-3", "lane B `shark` (real mind + sense 0.1.8)", "lane-B client at main 0c636bc; death on trial 13"),
    ("2026-10-20-1434-shark-attempt-4", "lane B `shark` (real mind + sense 0.1.8)", "lane-B client at main 0c636bc; server restarted mid-run"),
    ("2026-10-20-1439-shark-attempt-6", "lane B `shark` (real mind + sense 2be469c)", "lane-B client at main 2be469c; death on trial 38"),
    ("../CP-32/2026-10-20-1511-shark-attempt-1", "lane B `shark` (real mind + sense 1fc670b3)", "--plan o20: 12x32 home, 4x32 no-zone, 4 arena"),
    ("../CP-25/2026-10-20-1508-shark-attempt-1", "lane B `shark`", "laneb --lane refactor smoke"),
    ("2026-10-20-1541-attempt-22", "Shark local fixture (stub mind)", "jar 02a63424, first creeper run (fixture: no pickaxe)"),
    ("2026-10-20-1549-attempt-23", "Shark local fixture (stub mind)", "jar 099d1851 (hissing cornered 0.5 s)"),
    ("2026-10-20-1555-attempt-24", "Shark local fixture (stub mind)", "jar 0f0acfd1 (alert within 5 unseen)"),
]
REFLEXES = {
    "eat": ("reflex_eat", "Eat when hungry and safe (hotbar food; golden apples and harmful foods excluded)", "CP-25"),
    "retreat": ("reflex_retreat", "Retreat at the health floor (<= 6) from a hostile within 10", "CP-25, CP-29"),
    "shield_arrow": ("reflex_shield_arrow", "Raise the off-hand shield against an incoming projectile", "CP-27"),
    "shield_melee": ("reflex_shield_melee", "Raise the off-hand shield against a hostile in melee reach", "CP-27"),
    "dodge": ("reflex_dodge", "Strafe off a projectile's line when no shield is usable", "CP-27, CP-30"),
    "edge": ("reflex_edge", "Hold sneak at a drop of 4+ blocks (vanilla back-off keeps her on the edge)", "CP-30, CP-32"),
    "fall": ("reflex_fall", "Detect a damaging fall with no rescue item (report only)", "CP-32"),
    "fall_bucket": ("reflex_fall_bucket", "Water-bucket fall rescue (O20: reflex-only, scooped back within 10 s, eve:rescue signal)", "CP-32"),
    "creeper": ("reflex_creeper", "Creeper flee (CP-29): run from a close or hissing creeper to the blast-safe distance over open ground", "CP-29"),
    "creeper_trapped": ("reflex_creeper_trapped", "Creeper, trapped: raise the shield toward it (least-damage response; never a CP-29 pass)", "CP-29"),
    "arbiter": ("arbiter", "Reflex pre-emption: a paused walk resumes; a hand skill ends interrupted", "CP-20"),
}

rows = defaultdict(list)
for folder, lane, code in RUNS:
    f = EV / folder / "attempts.jsonl"
    if not f.exists():
        continue
    for line in f.read_text(encoding="utf-8").splitlines():
        a = json.loads(line)
        rows[a["skill"]].append((folder, lane, code, a))

for name, (skill, title, cps) in REFLEXES.items():
    rs = rows.get(skill, [])
    by_run = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for folder, lane, code, a in rs:
        g = by_run[(folder, lane, code)][a["variant"].split("#")[0]]
        g[0] += a["pass"]
        g[1] += 1
    md = [f"# REFLEX {name}: {title}", "", f"Cited by: {cps}. Criteria: `tools/reflex.py` CRITERIA (frozen before each run); every attempt of every "
          "listed run is counted. Baselines (`*_baseline_*`) run the same threat with the reflex unable to act and must hurt her.", "",
          "| run | lane | code | group | pass/total |", "|---|---|---|---|---|"]
    for (folder, lane, code), groups in by_run.items():
        for g, (ok, n) in groups.items():
            md.append(f"| `evidence/CP-20/{folder}` | {lane} | {code} | {g} | {ok}/{n} |")
    fails = [(folder, a) for folder, lane, code, a in rs if not a["pass"]]
    md += ["", f"## Failures ({len(fails)})", ""] + [f"- `{folder}` #{a['n']} {a['variant']}: {a['why'][:300]}" for folder, a in fails]
    if not fails:
        md.append("- none")
    (BODY / "evidence-summaries" / f"REFLEX-{name}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(name, sum(a["pass"] for *_, a in rs), "/", len(rs))
