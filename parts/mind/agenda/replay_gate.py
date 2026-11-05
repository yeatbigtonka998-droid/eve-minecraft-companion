"""O109 offline proof: replay recorded framing votes through the CURRENT classify() - rules re-evaluated for real,
model calls stubbed with the votes each line actually got - and compare old vs new final verdicts per batch.
    python -m agenda.replay_gate <rows.jsonl>...      (bench rows: r["gate"]; live rows: r["reply"]["gate"])
A line whose B vote was never recorded (live, A refused, B skipped) replays B as A's refusal: under O109 a refuse
vote decides the line whatever B says, so the replay is exact for it."""
import json
import sys
from collections import Counter

from agenda.intent_gate import classify, expected


def replay(row):
    g = row.get("gate") or (row.get("reply") or {}).get("gate") or {}
    fr = {v["framing"]: v for v in g.get("votes", []) if v.get("framing")}
    if not fr:                                      # chat lines: no model votes; the rule result is the answer
        return g.get("verdict"), classify(row["text"], call=lambda *a: (_ for _ in ()).throw(AssertionError))["verdict"]
    seq = [fr["A"], fr.get("B") or fr["A"]]

    def call(text, prompt, url, model, timeout, it=iter(seq)):
        return dict(next(it))
    return g.get("verdict"), classify(row["text"], url="http://replay", model="replay", call=call)["verdict"]


def main(argv=None):
    paths = sys.argv[1:] if argv is None else argv
    bad = 0
    for path in paths:
        tally, changes = Counter(), []
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            if "expected" in r:                     # bench rows carry the expected set itself
                want = set(r["expected"]) if r["expected"] else None
            else:                                   # live rows carry the case's expect; O94g categories apply
                want = expected({"category": r.get("category"), "expect": r.get("expect")}) if r.get("expect") else None
            old, new = replay(r)
            tally["old_ok"] += want is None or old in want
            tally["new_ok"] += want is None or new in want
            tally["lines"] += 1
            if old != new:
                changes.append(f"  {r.get('id')}: {old} -> {new} (expected {sorted(want) if want else '-'}) {r['text']!r}")
            bad += want is not None and old in want and new not in want          # a regression
        print(f"{path}: {dict(tally)}")
        print("\n".join(changes) or "  no verdict changed")
    print(f"regressions: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
