"""O89m CP-75: builder's proposed registry delta on 0.15.0, build_schematic phased runs (additive: three optional params and
two failure codes; v1 dispatches without them behave exactly as before). Short names: params count against the planner
budget. Prints the canonical hash (mind config.canonical: sorted keys, no whitespace, UTF-8). Writes nothing into contracts/
unless --apply is given (orchestrator's call). If another part's delta lands first, rebase PREV/VER and re-run. Same layout as
combat-0.16.0.py.

    py -3.12 parts/builder/registry/builder-0.16.0.py [--apply]
"""
import hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
REG = REPO / "contracts" / "skills" / "registry.json"
PREV, VER = "0.15.0", "0.16.0"

PARAMS = {  # CP-75: run = the mind's run id (resume it exactly); pause at a phase boundary from pause_at (day time) or after phases
    "run": {"type": "string", "pattern": "^[a-z0-9_-]{8,64}$"},
    "pause_at": {"type": "integer", "minimum": 0, "maximum": 23999},
    "phases": {"type": "integer", "minimum": 1},
}
CODES = ["paused", "bad_run"]  # paused: resumable with the same run; bad_run: that run is for another design/site or unreadable
SUMMARY = " run: a phased build (CP-75), resumed exactly with the same run; pause_at/phases end it failed paused at a safe phase boundary."


def delta(skill):
    s = json.loads(json.dumps(skill))
    s["params"]["properties"].update(PARAMS)
    s["failure_codes"] += [c for c in CODES if c not in s["failure_codes"]]
    if SUMMARY.strip() not in s["summary"]:
        s["summary"] += SUMMARY
    return s


reg = json.loads(REG.read_text(encoding="utf-8"))
assert reg["registry_version"] == PREV, f"registry is {reg['registry_version']}, this script expects {PREV}"
i = next(k for k, s in enumerate(reg["skills"]) if s["name"] == "build_schematic")
new = delta(reg["skills"][i])
reg["skills"][i] = new
reg["registry_version"] = VER
canon = json.dumps(reg, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
added = len(json.dumps(PARAMS, separators=(",", ":"))) + len(json.dumps(CODES, separators=(",", ":"))) + len(SUMMARY)
print(f"registry {VER} hash", hashlib.sha256(canon).hexdigest(), f"({len(reg['skills'])} skills; build_schematic +{added} chars)")
if "--apply" in sys.argv:
    lines = REG.read_text(encoding="utf-8").split("\n")
    k = next(n for n, line in enumerate(lines) if line.lstrip().startswith('{"name": "build_schematic"'))
    end = "," if lines[k].rstrip().endswith(",") else ""
    lines[k] = lines[k][:len(lines[k]) - len(lines[k].lstrip())] + json.dumps(new, ensure_ascii=False) + end
    text = "\n".join(lines).replace(f'"registry_version": "{PREV}"', f'"registry_version": "{VER}"')
    REG.write_text(text, encoding="utf-8", newline="\n")
    check = json.loads(REG.read_text(encoding="utf-8"))
    assert hashlib.sha256(json.dumps(check, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest() \
        == hashlib.sha256(canon).hexdigest()
    print("applied to", REG)
