"""S16: validation + the critic decide Eve's own designs; owner-supplied schematics are built as given."""
import json
from pathlib import Path

import pytest

from eve_design import critic_check, gamedata, gate, taste
from eve_design.__main__ import main as cli
from eve_design.generate import generate
from eve_design.grid import Design
from eve_design.models import CATEGORIES, FakeCritic, FakeGenerator, write_requests

GD = gamedata.load()
HERE = Path(__file__).parent
BRIEFS = json.loads((HERE / "fixtures/briefs/manifest.json").read_text())["briefs"]
SITED = BRIEFS[1]                                         # has a site, so no brief check is skipped
EX = HERE.parent / "examples"


def qualify(tmp_path, name="fake", version=FakeCritic.version, passed=True, bar=None):
    d = tmp_path / "_critic"
    d.mkdir(exist_ok=True)
    critic_check.qualification_file(d, name, version).write_text(json.dumps({"passed": passed, "bar": bar}
                                                                            if bar is not None else {"passed": passed}))
    return {"critics": [name], "selftest_dir": str(d)}


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    out = tmp_path_factory.mktemp("g") / "v1"
    generate(SITED, GD, FakeGenerator(), FakeCritic(), out=out)
    return out


def test_fake_critic_never_gates_a_real_build(folder):
    d = gate.decide(folder)                               # default policy: only deepseek may gate
    assert d["decision"] == "hold" and not d["checks"]["critic_allowed"]["ok"]
    assert not d["checks"]["critic_qualified"]["ok"]


def test_qualified_critic_and_bound_artefacts_build(folder, tmp_path):
    d = gate.decide(folder, qualify(tmp_path))
    assert d["decision"] == "build" and all(c["ok"] for c in d["checks"].values()), d["reasons"]
    assert json.loads((folder / "gate.json").read_text())["decision"] == "build"


def test_the_bar_is_the_one_the_critics_self_test_set(folder, tmp_path):
    """Ruling O34: accept at the lowest score the critic gives the hand-built reference; shown in gate.json."""
    low = min(json.loads((folder / "critique.json").read_text())["scores"].values())
    d = gate.decide(folder, qualify(tmp_path, bar=low))
    assert d["decision"] == "build" and d["policy"]["bar"] == low
    d = gate.decide(folder, qualify(tmp_path, bar=low + 1))
    assert d["decision"] == "reject" and any(f"< bar {low + 1}" in r for r in d["reasons"])


def test_unqualified_critic_holds(folder, tmp_path):
    d = gate.decide(folder, qualify(tmp_path, passed=False))
    assert d["decision"] == "hold" and "critic_qualified" in d["reasons"][0]


def test_tampered_preview_is_rejected(folder, tmp_path):
    se = folder / "previews" / "SE.png"
    keep = se.read_bytes()
    try:
        se.write_bytes(keep + b"\0")
        d = gate.decide(folder, qualify(tmp_path))
        assert d["decision"] == "reject" and any("critic_saw_these_previews" in r for r in d["reasons"])
    finally:
        se.write_bytes(keep)


def test_edited_design_is_rejected(folder, tmp_path):
    p = folder / "design.json"
    keep = p.read_text()
    try:
        doc = json.loads(keep)
        doc["origin"]["x"] += 1                         # stored hash no longer matches
        p.write_text(json.dumps(doc))
        assert gate.decide(folder, qualify(tmp_path))["decision"] == "reject"
    finally:
        p.write_text(keep)


def test_needs_review_is_rejected(tmp_path):
    out = tmp_path / "v1"
    generate(SITED, GD, FakeGenerator(), FakeCritic(script=[{"all": 5, "fixes": ["x"]}] * 4), out=out)
    d = gate.decide(out, qualify(tmp_path))
    assert d["decision"] == "reject" and any("critique_accepted" in r for r in d["reasons"])


def test_without_a_site_the_design_holds(tmp_path):
    out = tmp_path / "v1"
    generate(BRIEFS[0], GD, FakeGenerator(), FakeCritic(), out=out)
    d = gate.decide(out, qualify(tmp_path))
    assert d["decision"] == "hold" and any("brief_checks_ran" in r for r in d["reasons"])


# --- owner-supplied (S16 exception) ---------------------------------------------------------------------
def test_owner_schematic_at_stated_position_is_built_as_given(tmp_path):
    src = EX / "out" / "hut5" / "design.litematic"
    assert cli(["import", str(src), "--out", str(tmp_path / "own"), "--owner-supplied", "--origin", "120,70,-35"]) == 0
    d = json.loads((tmp_path / "own" / "gate.json").read_text())
    assert d["category"] == "owner_supplied" and d["decision"] == "build", d["reasons"]
    g = Design.load(tmp_path / "own" / "design.json")
    assert g.origin == (120, 70, -35) and not (tmp_path / "own" / "critique.json").exists()
    assert cli(["import", str(src), "--out", str(tmp_path / "x"), "--owner-supplied"]) == 2   # position is required


@pytest.mark.parametrize("name", ["hut5", "hut5_sim", "test3"])
def test_schematic_checkpoints_are_outside_the_gate_by_category(name, tmp_path):
    # O61: CP-50's hut and test3 are owner-provided schematics: category "schematic", decision n/a, checks reported
    import shutil
    f = shutil.copytree(EX / "out" / name, tmp_path / name)
    d = gate.decide(f)
    assert d["checks"]["plan_fresh"] == {"ok": True}              # O94ag-b; the checkpoints' gate.json re-decided (S93-ci-1)
    assert (d["category"], d["decision"], d["reasons"]) == ("schematic", "n/a", []) and d == json.loads(
        (EX / "out" / name / "gate.json").read_text()) | {"policy": d["policy"]}
    shutil.rmtree(f / "previews")
    d = gate.decide(f)
    assert d["decision"] == "n/a" and d["reasons"] == ["previews_bound: previews missing or for another design"]


def test_owner_schematic_that_cannot_stand_holds(tmp_path):
    from eve_design.schem_io import save_litematic
    g = Design.load(EX / "out" / "hut5" / "design.json")
    g.set(2, 3, 2, GD.canonical("minecraft:sand"), "furniture")         # floating sand
    g.canonicalize(GD)
    save_litematic(g, tmp_path / "bad.litematic", GD)
    cli(["import", str(tmp_path / "bad.litematic"), "--out", str(tmp_path / "own"), "--owner-supplied", "--origin", "0,64,0"])
    d = json.loads((tmp_path / "own" / "gate.json").read_text())
    assert d["decision"] == "hold" and any("validation.stable" in r and "falls" in r for r in d["reasons"])


# --- critic self-test -----------------------------------------------------------------------------------
class OracleCritic:
    """Scores the known-good base high and anything else low: what a critic that sees faults would do."""
    name, version = "oracle", "test-1"

    def __init__(self):
        from eve_design.compiler import compile_file
        self.good = compile_file(EX / "cabin_detailed.eve", GD).hash()

    def critique(self, brief, card, previews, references=(), design=None):
        v = 8.0 if design.hash() == self.good else 3.0
        return {"scores": {c: v for c in CATEGORIES}, "fixes": [] if v > 5 else ["repair it"]}


def test_selftest_catches_a_useless_critic_and_passes_a_seeing_one(tmp_path):
    bad = critic_check.run(FakeCritic(), GD, tmp_path)
    assert not bad["passed"] and set(bad["false_accepts"]) >= {"no_roof", "wall_hole", "griefed", "plain_box"}
    good = critic_check.run(OracleCritic(), GD, tmp_path)
    assert good["passed"] and good["caught"] == good["faults"] == 6 and good["repeat_max_diff"] == 0
    assert json.loads(critic_check.qualification_file(tmp_path, "oracle", "test-1").read_text())["passed"]


# --- taste / journal (Eve's own, labelled) ---------------------------------------------------------------
def test_taste_is_eves_own_labelled_and_reversible(folder, tmp_path):
    prof = tmp_path / "profile.json"
    e = taste.record_critique(folder, prof)
    assert e["source"] == "eve_critique" and e["scores"]
    taste.record_outcome(e["design_hash"], {"built": True, "mismatches": 0}, e["features"], prof)
    p = taste.preferences(prof)
    assert "not Hunter's ratings" in p["label"] and p["features"]["porch"]["critiques"] == 1
    assert p["features"]["porch"]["clean_build_rate"] == 1.0 and "porch" in p["avoid_repeating"]
    taste.revert(e["id"], "critic later found unreliable", prof)
    assert taste.preferences(prof)["features"]["porch"]["critiques"] == 0
    taste.record_owner("porch", "avoid", "Hunter said: no porches", prof)
    assert taste.preferences(prof)["features"]["porch"]["owner"] == "avoid"
    with pytest.raises(ValueError):
        taste._append(prof, {"source": "hunter_rating", "features": []})     # no invented ratings source
    note = taste.journal_note(folder)
    assert note["kind"] == "design_note" and note["grounded_in"][0] == Design.load(folder / "design.json").hash()


# --- the real DeepSeek requests, written but not sent ------------------------------------------------------
def test_requests_written_not_sent(folder, tmp_path):
    from eve_design.compiler import COMMANDS
    summary = write_requests(SITED, GD, tmp_path / "req", folder)
    doc = json.loads((tmp_path / "req" / "requests.json").read_text())
    assert doc["note"].startswith("NOT SENT")
    system = doc["requests"]["program (example concept)"][0]["content"]
    assert all(f"b.{c}(" in system for c in COMMANDS)              # vocabulary generated from the compiler
    imgs = [p for p in doc["requests"]["critique"][1]["content"] if p["type"] == "image_url"]
    assert len(imgs) >= 5 and all("sha256" in p["image_url"] and "url" not in p["image_url"] for p in imgs)
    assert summary["critique"]["images"] == len(imgs)


def test_the_critic_is_read_from_any_critiqued_round(folder, tmp_path):
    """45L attempt 5: h02's written design was not the best-scored round, the gate fell back to bar 7.0."""
    crit = json.loads((folder / "critique.json").read_text())
    keep = (folder / "critique.json").read_text()
    try:
        for r in crit["history"]:
            if r.get("design_hash") == crit["design_hash"]:
                r.pop("critic", None)
        (folder / "critique.json").write_text(json.dumps(crit))
        d = gate.decide(folder, qualify(tmp_path, bar=1.0))
        assert d["policy"]["bar"] == 1.0 and d["checks"]["critic_qualified"]["ok"]
    finally:
        (folder / "critique.json").write_text(keep)


def test_o94q_an_owner_approval_is_the_only_way_a_held_design_builds(tmp_path):
    """O94q (CP-65): approval.json, written by the mind on Hunter's word and bound to this project, version and design
    hash, lets a hold-gated design build. One field off, an untrusted name, a reject, or no file: it does not."""
    import shutil

    from eve_design import approval
    f = shutil.copytree(EX / "out" / "cabin", tmp_path / "cabin")
    g = Design.load(f / "design.json")
    assert json.loads((f / "gate.json").read_text())["decision"] == "hold"
    r = approval.check(f)
    assert not r["may_build"] and r["reasons"] == ["gate says hold and there is no approval.json"]
    ok = {"format": "eve-approval/1", "approval_id": "a-1", "project_id": g.meta["project_id"], "version": g.meta["version"],
          "design_hash": g.hash(), "site_hash": None, "budget": None, "decided_by": "Hunter", "decision": "approved",
          "category": "owner_small", "channel": "mind_cli", "ts": 1792650000000, "text": "yes, build the cabin"}

    def write(d):
        (f / "approval.json").write_text(json.dumps(d))
    write(ok)
    assert approval.check(f, trusted=["Hunter"])["may_build"] and cli(["approval", str(f), "--trusted", "Hunter"]) == 0
    for k, v in (("design_hash", "0" * 64), ("project_id", "other"), ("version", 2), ("decision", "revise"),
                 ("decided_by", ""), ("channel", "discord_webhook"), ("ts", None), ("text", "  "), ("approval_id", None),
                 ("site_hash", "x"), ("format", None)):
        write({**ok, k: v})
        r = approval.check(f)
        assert not r["may_build"] and len(r["approval"]["reasons"]) == 1, (k, r["approval"]["reasons"])
    write(ok)
    assert not approval.check(f, trusted=["SomeoneElse"])["may_build"]
    gate_json = json.loads((f / "gate.json").read_text())
    (f / "gate.json").write_text(json.dumps({**gate_json, "decision": "reject"}))
    assert not approval.check(f)["may_build"] and cli(["approval", str(f)]) == 3    # a reject never builds
