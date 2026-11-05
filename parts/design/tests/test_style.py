"""CP-58 (K part): reference metrics are exact; inferred properties are labelled uncertain; provenance recorded."""
import json
from collections import Counter
from pathlib import Path

import nbtlib
import pytest

from eve_design import gamedata, style

GD = gamedata.load()
STYLES = Path(__file__).parents[1] / "styles"
ALL = {n: json.loads((STYLES / f"{n}.references.json").read_text()) for n in ("cozy_cabin", "medieval")}
CASES = [(n, r) for n, refs in ALL.items() for r in refs["references"]]
VILLAGE = GD.root / "structure" / "village"


def raw_count(path):
    """Independent count straight from the NBT: jigsaw -> final_state id, air/structure_void/ground left out."""
    f = nbtlib.load(path)
    ids = [str(p.get("id", p.get("Name"))) for p in f["palette"]]
    c = Counter()
    for b in f["blocks"]:
        i = ids[int(b["state"])]
        if i == "minecraft:jigsaw":
            i = str(b["nbt"]["final_state"]).split("[")[0]
        if i not in ("minecraft:air", "minecraft:structure_void") and i not in style.GROUND:
            c[i] += 1
    return c


@pytest.mark.parametrize("style_name,ref", CASES, ids=[f"{n}:{r['file']}" for n, r in CASES])
def test_reference_counts_exact(style_name, ref):
    path = next(VILLAGE.rglob(ref["file"]))
    assert dict(raw_count(path)) == ref["exact"]["palette"]
    assert ref["exact"]["blocks"] == sum(ref["exact"]["palette"].values())
    assert all(v["uncertain"] for v in ref["inferred"].values())


@pytest.mark.parametrize("style_name", ALL)
def test_recomputed_metrics_match_committed_file(style_name):
    refs = ALL[style_name]
    paths = [next(VILLAGE.rglob(r["file"])) for r in refs["references"]]
    again = style.reference_set(paths, GD, refs["provenance"]["jar_sha1"])
    assert again["references"] == refs["references"] and again["aggregate"] == refs["aggregate"]
    assert "EULA" in refs["provenance"]["terms"] and refs["provenance"]["data_version"] == GD.data_version


def test_medieval_card_follows_its_references():
    card = json.loads((STYLES / "medieval.json").read_text())
    if card["status"] != "draft from references":
        pytest.skip("card edited by a person; no longer a pure draft")
    assert card["palette"] == style.draft_card("medieval", ALL["medieval"])["palette"]
    assert card["proportions"]["roof"]["uncertain"] is True and card["provenance"]["role_rule"] == style.ROLE_RULE
