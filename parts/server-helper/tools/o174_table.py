"""O174: eve.hit / eve.use lines from a server log -> per-hit table, use-packet summary, trial match.
py -3.12 o174_table.py <lines.txt> [rows.jsonl]"""
import json
import re
import sys
from pathlib import Path

LINE = re.compile(r"^\[(\d\d:\d\d:\d\d)\].*\[eve-audit\] eve\.(hit|use) (\{.*\})\s*$")


def parse(path):
    out = []
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.match(raw.strip())
        if m:
            out.append((m.group(1), m.group(2), json.loads(m.group(3))))
    return out


def trial_of(t, trials):
    for tr in trials:
        if tr["from"] <= t <= tr["to"]:
            return tr.get("trial", tr.get("i", "?"))
    return "-"


def main():
    rows = parse(sys.argv[1])
    trials = []
    if len(sys.argv) > 2 and Path(sys.argv[2]).exists():
        for l in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines():
            r = json.loads(l)
            if "from" in r and "to" in r:
                trials.append(r)
    hits = [(t, o) for t, k, o in rows if k == "hit"]
    uses = [(t, o) for t, k, o in rows if k == "use"]
    print(f"{len(hits)} hits, {len(uses)} use lines, {len(trials)} trials")
    print("time     trial src            dir         loaded using tks block ang_h  ang_b  pierce blocked in    armor_out hurt hp")
    for t, o in hits:
        d = o.get("direct", {}).get("type", "-").replace("minecraft:", "")
        print(f"{t} {str(trial_of(t, trials)):>5} {o.get('source_type','-').replace('minecraft:',''):14} {d:11} "
              f"{str(o.get('client_loaded','?')):6} {str(o.get('is_using')):5} {o.get('ticks_using'):>3} {str(o.get('is_blocking')):5} "
              f"{o.get('angle_head_deg', float('nan')):6.1f} {o.get('angle_body_deg', float('nan')):6.1f} {str(o.get('arrow_pierce','-')):>6} "
              f"{o.get('blocked','-')!s:7} {o.get('amount_in')!s:5} {o.get('armor_out','-')!s:9} {o.get('hurt')!s:5} {o.get('health_after')}")
    kinds = {}
    for _, o in uses:
        kinds[o["what"]] = kinds.get(o["what"], 0) + 1
    print("use lines by kind:", kinds)
    # summary of the hit causes
    arrows = [o for _, o in hits if "arrow" in o.get("source_type", "")]
    def n(f):
        return sum(1 for o in arrows if f(o))
    print(f"arrow hits {len(arrows)}: using {n(lambda o: o.get('is_using'))}, blocking {n(lambda o: o.get('is_blocking'))}, "
          f"blocked>0 {n(lambda o: (o.get('blocked') or 0) > 0)}, head angle>90 {n(lambda o: o.get('angle_head_deg', 0) > 90)}, "
          f"body angle>90 {n(lambda o: o.get('angle_body_deg', 0) > 90)}, pierce>0 {n(lambda o: (o.get('arrow_pierce') or 0) > 0)}, "
          f"client_loaded false {n(lambda o: o.get('client_loaded') is False)}")


if __name__ == "__main__":
    main()
