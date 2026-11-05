"""eve-brief/1.1 dry run with cost: the program model reads each request text, the guards check it; no survey, no world.
Runs where the key and the mind's spend reservation are (the 45L): every call is reserved as job `brief` (the CP-54
US$1.00 cap) and its real cost printed.

    python tools/brief_read_probe.py [--texts phrasings.json] [--out DIR]

Without --texts it reads design's own dev phrasings (DEV below). Writes DIR/read_probe.json: per text the rule
reading, the model's raw reading, every guard decision, and the calls with their cost.
"""
import argparse
import json
import sys
from pathlib import Path

from eve_design import models
from eve_design.brief import _guard, _read, read_request

DEV = ["a cottage with a trio of bedrooms on north meadow", "a cottage with bedrooms for all the cousins on north meadow",
       "a cyclopean cottage on north meadow", "a cottage on north meadow, but don't make it gargantuan",
       "a cottage on north meadow next to the enormous lake", "a cottage on north meadow, I'm chill about the size",
       "a cottage on north meadow that mustn't be poky", "a farmhouse with half a dozen bedrooms and a pantry",
       "a hall with a kitchen on north meadow, roughly barn-ish", "an observatory with two reading rooms"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--texts", type=Path)
    ap.add_argument("--out", type=Path, default=Path("read_probe"))
    a = ap.parse_args(argv)
    texts = json.loads(a.texts.read_text()) if a.texts else DEV
    client = models.DeepSeekClient(models.configured_model("pro"))
    if (why := client.why_disabled()):
        print(f"refusing to start: {why}", file=sys.stderr)
        return 2
    rows = []
    for t in texts:
        rules = read_request(t)
        raw, err, calls = _read(t, client)
        got, items = _guard(raw, t, rules, rules.get("noun")) if raw else ({}, [])
        rows.append({"text": t, "rules": {k: rules[k] for k in ("rooms", "size", "size_free", "size_unclear",
                                                                 "rooms_unclear")},
                     "model": raw, "error": err, "guarded": got, "items": items, "calls": calls})
        print(f"{sum(c.get('usd') or 0 for c in calls):.5f}", err or "", [(i["field"], i["accepted"], i.get("why"))
                                                                          for i in items], t[:60], flush=True)
    a.out.mkdir(parents=True, exist_ok=True)
    total = sum(c.get("usd") or 0 for r in rows for c in r["calls"])
    (a.out / "read_probe.json").write_text(json.dumps({"total_usd": total, "rows": rows}, indent=1) + "\n")
    print(f"total US${total:.4f} for {len(texts)} reads")
    return 0


if __name__ == "__main__":
    sys.exit(main())
