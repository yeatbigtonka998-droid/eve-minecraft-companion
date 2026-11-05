"""Approvals bind to the exact, verified design hash; only a trusted owner on an authenticated channel decides."""
import json

import pytest

from conftest import HUT5, count, design_folder, envelope, mind_env, run, until

pytestmark = pytest.mark.skipif(not HUT5.exists(), reason="BLOCKED: design test vector hut5 not present")


def test_approval_binds_verified_design_hash(tmp_path):
    designs = tmp_path / "designs"
    v, h = design_folder(designs, "cabin-north", 3)

    async def go():
        async with mind_env(tmp_path, env=envelope(), designs_dir=designs) as e:
            r = e.mind.decide("approve", "cabin-north", "v3", None, "cli")   # sole trusted player is the default
            assert r["ok"] and r["decided_by"] == "Hunter" and r["design_hash"] == h
            assert r["category"] == "owner_small" and r["decision"] == "approved"
            assert json.loads((v / "approval.json").read_text())["approval_id"] == r["approval_id"]
            assert count(e.db, "SELECT count(*) FROM approvals WHERE design_hash=?", h) == 1
            # in-game: a trusted sender can decide through owner.command; an untrusted one cannot
            await e.sense.owner_command("Mallory", True, "approve", ["cabin-north", "v3"])
            await e.sense.owner_command("Hunter", True, "reject", ["cabin-north", "v3"])
            await until(lambda: count(e.db, "SELECT count(*) FROM approvals") == 2)
            assert count(e.db, "SELECT count(*) FROM approvals WHERE decided_by='Mallory'") == 0
            assert e.mind.decide("approve", "cabin-north", "v4", None, "cli")["error"] == "no_design"
    run(go())


def test_tampered_or_unvalidated_design_cannot_be_approved(tmp_path):
    designs = tmp_path / "designs"
    design_folder(designs, "tampered", 1, mutate=lambda d: d["palette"].append("minecraft:tnt"))
    design_folder(designs, "failed", 1, validation=False, category="wishlist", authored_by="eve")
    design_folder(designs, "unchecked", 1, validation=None, category="village_plot", authored_by="eve")

    async def go():
        async with mind_env(tmp_path, designs_dir=designs) as e:
            assert e.mind.decide("approve", "tampered", "v1", None, "cli")["error"] == "design_hash_mismatch"
            assert e.mind.decide("approve", "failed", "v1", None, "cli")["error"] == "not_validated"
            assert e.mind.decide("approve", "unchecked", "v1", None, "cli")["error"] == "not_validated"
            assert e.mind.decide("reject", "failed", "v1", None, "cli")["ok"]      # rejecting is always possible
            assert count(e.db, "SELECT count(*) FROM approvals WHERE decision='approved'") == 0
    run(go())


def test_owner_supplied_schematic_is_built_as_given(tmp_path):
    """S16: an owner schematic with a stated position needs no design pipeline; validation is a warning only."""
    designs = tmp_path / "designs"
    design_folder(designs, "gift", 1, validation=None, category="owner_small")

    async def go():
        async with mind_env(tmp_path, designs_dir=designs) as e:
            r = e.mind.decide("approve", "gift", "v1", None, "cli")
            assert r["ok"] and r["category"] == "owner_small" and r["decided_by"] == "Hunter"
            assert r["warnings"] == ["owner-supplied schematic: built as given, not validated"]
    run(go())


def eve_design(designs, name, **kw):
    return design_folder(designs, name, 1, category=kw.pop("category", "wishlist"), authored_by="eve",
                         critique=kw.pop("critique", {}), **kw)


def test_eve_self_approves_only_through_every_gate(tmp_path):
    """S16: decided_by "eve" only when Eve-authored, validated and critic-accepted for this exact hash, scores >= 7."""
    designs = tmp_path / "designs"
    eve_design(designs, "good")
    eve_design(designs, "plot", category="village_plot")
    design_folder(designs, "owners", 1, category="wishlist", authored_by="Hunter", critique={})
    eve_design(designs, "small", category="owner_small")
    eve_design(designs, "review", critique={"status": "needs_review"})
    eve_design(designs, "stale", critique={"design_hash": "0" * 64})
    eve_design(designs, "low", critique={"threshold": 6.0, "scores": {"function": 9, "style": 6.5}})
    eve_design(designs, "nocrit", critique=None)
    eve_design(designs, "invalid", validation=False)
    eve_design(designs, "tamper", mutate=lambda d: d["palette"].append("minecraft:glass"))
    (designs / "tamper" / "v1" / "design.json").write_text(
        (designs / "tamper" / "v1" / "design.json").read_text().replace('"minecraft:glass"', '"minecraft:tnt"'))
    env = envelope()
    env["approvals"] = {"small_build_block_limit": 0, "wishlist_max_per_week": 1, "terrain_change_blocks": 0}

    async def go():
        async with mind_env(tmp_path, env=env, designs_dir=designs) as e:
            want = {"owners": "not_eve_authored", "small": "bad_category", "review": "not_critiqued",
                    "stale": "not_critiqued", "low": "below_threshold", "nocrit": "not_critiqued",
                    "invalid": "not_validated", "tamper": "design_hash_mismatch"}
            for name, code in want.items():
                r = await e.mind.cli("self_approve", {"project_id": name, "version": "v1"})
                assert r["status"] == "refused" and r["code"] == code, (name, r)
            r = await e.mind.cli("self_approve", {"project_id": "good", "version": "v1"})
            assert r["ok"] and r["decided_by"] == "eve" and r["channel"] == "self" and r["category"] == "wishlist"
            assert json.loads((designs / "good" / "v1" / "approval.json").read_text())["decided_by"] == "eve"
            eve_design(designs, "second")
            assert (await e.mind.cli("self_approve", {"project_id": "second", "version": "v1"}))["code"]                 == "wishlist_limit"                                   # 1 per week
            assert (await e.mind.cli("self_approve", {"project_id": "plot", "version": "v1"}))["ok"]   # no weekly cap
            assert e.mind.approval_valid("good", 1, r["design_hash"])[0]
            assert e.mind.decide("reject", "good", "v1", None, "cli")["ok"]                         # owner overrules
            assert not e.mind.approval_valid("good", 1, r["design_hash"])[0]
            assert e.mind.decide("approve", "good", "v1", "eve", "cli")["error"] == "not_trusted"   # no spoofing
            assert count(e.db, "SELECT count(*) FROM approvals WHERE decided_by='eve'") == 2
    run(go())


def test_wishlist_disabled_by_envelope(tmp_path):
    designs = tmp_path / "designs"
    eve_design(designs, "good")

    async def go():
        async with mind_env(tmp_path, designs_dir=designs) as e:       # example envelope: wishlist_max_per_week 0
            assert (await e.mind.cli("self_approve", {"project_id": "good", "version": "v1"}))["code"]                 == "wishlist_disabled"
    run(go())


def test_build_schematic_only_for_the_approved_design_hash(tmp_path):
    """registry 0.4.0: `build_schematic` is dispatched only for a design with a current owner approval and exactly the
    approved hash; a revoked approval or another hash is refused before anything is sent."""
    import pytest
    from conftest import mind_env, run, until
    designs = tmp_path / "designs"
    v, h = design_folder(designs, "hut", 1)

    async def go():
        async with mind_env(tmp_path, designs_dir=designs) as e:
            if "build_schematic" not in e.mind.registry.skills:
                pytest.skip("registry without build_schematic (0.4.0)")
            p = {"project_id": "hut", "version": 1, "design_hash": h}
            assert (await e.mind.do("build_schematic", p))["code"] == "not_approved"
            assert e.mind.decide("approve", "hut", "v1", None, "cli")["ok"]
            assert (await e.mind.do("build_schematic", {**p, "design_hash": "0" * 64}))["code"] == "design_hash_mismatch"
            await e.sense.send_storage([])                  # gives the mind a server tick (grants need one)
            await until(lambda: e.mind.sense_tick is not None)
            r = await e.mind.do("build_schematic", p, grant=True)
            assert r.get("action_id") and r["ok"], r
            await until(lambda: len([g for g in e.sense.regions if g["action_id"] == r["action_id"]]) >= 2, timeout=3)
            regions = [g for g in e.sense.regions if g["action_id"] == r["action_id"]]
            await until(lambda: e.mind.status_of(r["action_id"]) == "ok", timeout=5)
            assert regions and {g["effect"] for g in regions} == {"place", "break"}
            box = regions[0]
            vol = lambda g: (g["max"]["x"] - g["min"]["x"] + 1) * (g["max"]["y"] - g["min"]["y"] + 1) * (g["max"]["z"] - g["min"]["z"] + 1)  # noqa: E731
            assert all(vol(g) <= 32768 for g in regions) and box["min"]["x"] <= box["max"]["x"]
            await until(lambda: ("revoke", r["action_id"]) in e.sense.order, timeout=5)   # ends with the action
            assert not [g for g in e.sense.regions if g["action_id"] == r["action_id"]]
            assert e.mind.decide("revoke", "hut", "v1", None, "cli")["ok"]
            assert (await e.mind.do("build_schematic", p))["code"] == "not_approved"
    run(go())


def test_build_regions_shift_the_scaffold_and_split_large_boxes(tmp_path):
    import json
    from conftest import mind_env, run
    d = tmp_path / "f"
    d.mkdir()
    (d / "scaffold.json").write_text(json.dumps({"origin": {"x": -1, "y": 64, "z": -1}, "size": {"x": 7, "y": 2, "z": 7}}))
    design = {"hash": "a" * 64, "origin": {"x": 0, "y": 64, "z": 0}, "size": {"x": 40, "y": 30, "z": 40}}

    async def go():
        async with mind_env(tmp_path) as e:
            e.mind._load_design = lambda *a: (d, 1, design, {})
            boxes = e.mind.build_regions({"project_id": "x", "version": 1, "design_hash": "a" * 64,
                                          "origin": {"x": 100, "y": 70, "z": 200}})
            vol = lambda b: (b[1]["x"] - b[0]["x"] + 1) * (b[1]["y"] - b[0]["y"] + 1) * (b[1]["z"] - b[0]["z"] + 1)  # noqa: E731
            assert all(vol(b) <= 32768 for b in boxes)
            assert sum(vol(b) for b in boxes) == 40 * 30 * 40 + 7 * 2 * 7          # every block, no overlap within a box
            assert ({"x": 99, "y": 70, "z": 199}, {"x": 105, "y": 71, "z": 205}) in boxes   # scaffold moved with O
            assert e.mind.build_regions({"project_id": "x", "version": 1, "design_hash": "b" * 64}) == []
    run(go())
