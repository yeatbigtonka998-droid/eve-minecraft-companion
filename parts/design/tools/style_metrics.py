"""Measure a style's reference builds and write styles/<style>.references.json (numbers only, no game files).

    python tools/style_metrics.py cozy_cabin taiga_small_house_1 taiga_small_house_2 ...

References are read from data/game/26.3/structure/village/**/<name>.nbt (Mojang's own builds in the pinned jar).
"""
import json
import sys
from pathlib import Path

from eve_design import gamedata, style

DESIGN = Path(__file__).resolve().parents[1]


def main(name: str, refs: list[str]) -> None:
    gd = gamedata.load()
    root = gd.root / "structure" / "village"
    paths = [next(root.rglob(f"{r}.nbt")) for r in refs]
    out = style.reference_set(paths, gd, gd.manifest["sources"]["jars"]["client"]["sha1_manifest"])
    out["style"] = name
    out["selection"] = "dwellings only (farms, pens, workshops and decorations left out): " + ", ".join(refs)
    (DESIGN / "styles" / f"{name}.references.json").write_text(json.dumps(out, indent=1) + "\n")
    card = DESIGN / "styles" / f"{name}.json"
    if not card.exists():                     # never overwrite a card someone has edited
        card.write_text(json.dumps(style.draft_card(name, out), indent=1) + "\n")
        print(f"drafted {card.name}")
    print(json.dumps(out["aggregate"], indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
