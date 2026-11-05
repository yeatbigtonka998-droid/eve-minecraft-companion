"""Run a checkpoint's tests and record an evidence attempt (contracts/records.md).

    python tools/evidence.py CP-47

Writes evidence/CP-NN/<yyyy-mm-dd-HHMM>-attempt-N/{RESULT.md, pytest.txt, extra files} and copies RESULT.md to
parts/design/evidence-summaries/CP-NN-<stamp>-attempt-N.md. Every run is counted as an attempt.
"""
import json
import platform
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path

DESIGN = Path(__file__).resolve().parents[1]
REPO = DESIGN.parents[1]

CPS = {
    "CP-47": {
        "hypothesis": "eve_design reads declared .litematic (v5-7) and .schem (v2/v3) files with offsets, negative-size and "
                      "multiple regions, palettes, air, stairs, slabs, doors, logs and block entities; round-trips them through "
                      "its .litematic/.schem v3/.schem v2 writers without changing coordinates, block states or block-entity "
                      "data; rejects malformed/oversized files and lists every unsupported feature without substitution.",
        "tests": ["tests/test_schem_io.py"],
        "manifests": ["tests/fixtures/schematics/manifest.json"],
        "pass": "every manifest case matches its hand-written expectation (state probes, sizes, offsets, counts, listed "
                "issues or error text); every readable case round-trips through all three writers; litemapy (independent "
                "reader) agrees with the written .litematic; limits: 8 MiB file, 64 MiB NBT, 2,097,152 cells, 384/axis.",
        "limits": "fixtures are produced by litemapy/nbtlib from hand-written layouts, not by Litematica or WorldEdit "
                  "themselves; opening an export in Litematica is a separate manual check (Hunter).",
    },
    "CP-55": {
        "hypothesis": "Only the declared building vocabulary can execute: the hand-written parser accepts one statement "
                      "form with literal arguments, the compiler dispatches only names in its COMMANDS table, and time, "
                      "write count, coordinates, dimensions, statements and file size are bounded.",
        "tests": ["tests/test_lang.py"],
        "manifests": [],
        "pass": "cabin.eve compiles and exports; each of the hostile programs (invalid syntax, unknown commands, huge "
                "dimensions, loops, imports, file/network/Python escapes) raises LangError/CompileError with the "
                "expected reason; 2000 seeded mutations end only in a design or LangError/CompileError with no host "
                "call (os.system/popen, subprocess.Popen, socket patched to fail) and each under the 2 s budget.",
        "limits": "event-level tests in-process; the language has no loop construct, so 'endless computation' is "
                  "covered by syntax rejection plus the time/write budgets, not by a runaway program.",
    },
    "CP-56": {
        "hypothesis": "Compiling the same program twice gives the same design hash; roof/window/room edits change only "
                      "the cells of that feature; roles, rooms, seed, style-card hash and collision counts survive in "
                      "design.json (the sidecar the .litematic cannot hold).",
        "tests": ["tests/test_lang.py::test_deterministic_and_seed_recorded", "tests/test_lang.py::test_edits_are_local_and_roles_follow",
                  "tests/test_lang.py::test_collision_rule_recorded", "tests/test_lang.py::test_style_defaults_fill_omitted_blocks",
                  "tests/test_lang.py::test_cabin_compiles_and_exports"],
        "manifests": [],
        "pass": "identical hash and JSON across two compiles; every changed cell after an edit belongs to the edited "
                "feature (roof role / west wall / edited room box); overwrite counts match the collision rule; "
                "design.json round-trips with its hash.",
        "extra": ["examples/out/cabin"],
        "limits": "no palette randomness exists yet (seed recorded, unused until gradient detailing, CP-59).",
    },
    "CP-57": {
        "hypothesis": "The hand-authored cabin, two-storey house, L-shaped house with a wing, modern asymmetrical home and "
                      "six-storey setback tower all compile from the restricted language (no placement code), export, and "
                      "have usable interiors: every room reachable from outside with 2-block headroom, enclosed, lit, "
                      "furnished for its type, stable, fire-safe and buildable with the generated scaffold plan.",
        "tests": ["tests/test_examples.py"],
        "manifests": [],
        "pass": "all five compile, round-trip through .litematic and pass validate() with their scaffold; storeys, parts "
                "and sizes differ as described (K part). The J part (Hunter sees meaningful layout differences) is open.",
        "extra": ["examples/out"],
        "limits": "K part only; Hunter's judgement is required for the J part; previews not yet rendered (CP-62).",
    },
    "CP-60": {
        "hypothesis": "Grid-derived checks catch at least two planted faults in each of site fit, walkability, enclosure, "
                      "light, function, support, fire, survival buildability, affordability and size limits, with the "
                      "planted location, and accept valid counterexamples (open porch, open pavilion, supported sand, "
                      "stone hearth, a scaffolded tower).",
        "tests": ["tests/test_validate.py"],
        "manifests": ["tests/fixtures/validate/manifest.json"],
        "pass": "every manifest case fails exactly the expected check with the expected reason and location, the same base "
                "without the fault does not fail that check, and every valid case passes all checks.",
        "limits": "classifications (passable, opaque, luminance, flammable) come from 26.3 block tags plus short labelled "
                  "lists; reach uses final-building standing spots plus scaffold tops (build order not simulated).",
    },
    "CP-62": {
        "hypothesis": "The renderer draws the exact grid (every non-air cell) with geometry from the game's own blockstate/"
                      "model JSON and texture colours, makes missing support visible (magenta + listed), and binds every "
                      "preview set to the design hash.",
        "tests": ["tests/test_preview.py", "tests/test_world.py"],
        "manifests": [],
        "pass": "world paste round-trips to the exact grid with fills under the limit and cameras at the renderer angles; "
                "compare() scores self 1.0 and a scaled re-background copy > 0.9; slab half height, stair facing silhouette, unsupported chest drawn magenta and listed, views + render.json "
                "bound to the design hash. Full CP-62 also needs side-by-side comparison with test-world screenshots.",
        "extra": ["examples/out/cabin/previews", "examples/out/tower/previews"],
        "verdict_if_pass": "IMPLEMENTED (offline part); comparison with real screenshots NOT RUN",
        "limits": "no game world available here: fidelity against real screenshots (the CP-62 pass condition) is not tested.",
    },
    "CP-59": {
        "hypothesis": "Each detailing rule from the cozy_cabin card (pillars, base band, palette gradients, trapdoor shutters, "
                      "gable-end trim) applied alone and then together to the cabin, the two-storey house and the wing house "
                      "keeps every grid-derived check passing, leaves rooms and interiors untouched, does what the card says, "
                      "reproduces palette placement from the recorded seed and can be switched off.",
        "tests": ["tests/test_detail.py"],
        "manifests": [],
        "pass": "all 18 rule x base combinations validate (with scaffold); card effects present; same seed same hash, other "
                "seed different; wall mix within 55-85% primary for a 70/30 card; disabled rules leave no trace.",
        "extra": ["examples/out/cabin_detailed"],
        "verdict_if_pass": "PASS (K part); J part DEFERRED by owner choice (S16)",
        "limits": "window recess and grounds rules not implemented (grounds would change terrain; P9 allows 0 blocks); "
                  "the card's numbers are the Build Side example card, not reference-derived (CP-58 blocked).",
    },
    "CP-58": {
        "hypothesis": "Palette and dimension counts measured from the cozy_cabin (9 taiga) and medieval (11 plains) "
                      "village dwellings in the pinned 26.3 jar are exact against an independent raw-NBT count; inferred roof type and glass "
                      "share are labelled uncertain; provenance and terms are recorded.",
        "tests": ["tests/test_style.py"],
        "manifests": [],
        "pass": "every reference's palette counts equal the raw count; recomputation reproduces the committed file.",
        "verdict_if_pass": "IMPLEMENTED (K part, two styles: cozy_cabin, medieval); human labels and card choices need Hunter",
        "limits": "two styles, 9 + 11 vanilla references (plan: 10-20 per style, five styles); inferred properties unverified against "
                  "human labels; the card's palette is still the Build Side example (not replaced by measured numbers).",
    },
    "CP-61": {
        "hypothesis": "The brief -> 2-3 concepts -> program -> compile/validate pipeline produces, for each of 10 briefs, a "
                      "valid design that keeps every requested room and feature, repairs injected compile/validation "
                      "failures from exact error feedback within 2 repairs per concept, never bypasses validation, and "
                      "records every candidate with generator name and version.",
        "tests": ["tests/test_generate.py"],
        "manifests": ["tests/fixtures/briefs/manifest.json"],
        "pass": "all 10 briefs yield a validated design folder; the four injected fault kinds are repaired and the rejected "
                "candidate is recorded with the exact error; a generator that ignores feedback ends in no_valid_design "
                "with nothing written; model text only ever reaches the restricted parser.",
        "verdict_if_pass": "IMPLEMENTED (pipeline on a deterministic fake); real model off until CP-06 PASS (EVE_DESIGN_MODELS)",
        "limits": "the briefs were written by the design chat for the fake, not unseen briefs for a real model; the "
                  "DeepSeek adapter is tested only with an injected HTTP stub (no network, no spend).",
    },
    "CP-63": {
        "hypothesis": "The critic interface scores the six Build Side categories from the exact previews, the image critic "
                      "stays disabled until image input is verified (P10), and agreement with Hunter's keep/reject labels "
                      "can be computed with every disagreement listed.",
        "tests": ["tests/test_generate.py::test_image_critic_off_until_cp06",
                  "tests/test_gate.py::test_selftest_catches_a_useless_critic_and_passes_a_seeing_one",
                  "tests/test_generate.py::test_calibration_agreement_counts_disagreements",
                  "tests/test_generate.py::test_accepted_only_when_every_category_reaches_threshold"],
        "manifests": [],
        "pass": "interface + guards + calibration arithmetic only.",
        "verdict_if_pass": "IMPLEMENTED (interface + self-test harness); real critic BLOCKED until CP-06; Hunter calibration DEFERRED (S16)",
        "limits": "no real critic has scored anything; calibration needs Hunter's ratings on held-out designs.",
    },
    "CP-64": {
        "hypothesis": "After the initial candidate the loop runs at most 3 revision rounds, recompiles and revalidates every "
                      "revision, keeps the best valid candidate with its full scores, stops on oscillation or an API "
                      "failure, and reports anything below 7 as needs_review, never accepted by running out of rounds.",
        "tests": ["tests/test_generate.py::test_still_poor_after_three_rounds_needs_review",
                  "tests/test_generate.py::test_oscillation_stops_early", "tests/test_generate.py::test_api_failure_mid_loop_keeps_best",
                  "tests/test_generate.py::test_invalid_revision_is_recorded_and_loop_continues",
                  "tests/test_generate.py::test_accepted_only_when_every_category_reaches_threshold"],
        "manifests": [],
        "pass": "the five loop scenarios behave as stated on scripted fake critics/generators.",
        "verdict_if_pass": "PASS (K part, on deterministic fakes); real-critic run after CP-06; Hunter part DEFERRED (S16)",
        "limits": "scores come from scripted fakes; real-model behaviour (oscillating suggestions in prose) is untested.",
    },
    "CP-65": {
        "hypothesis": "Under S16 the gate decides Eve's own designs: build only when validation passed with the brief's site "
                      "and budget checks, the critique accepted the exact previews in the folder, the critic is allowed and "
                      "has a passing self-test, and every artefact carries the same design hash; owner-supplied schematics "
                      "at a stated position skip design and critique and are gated by stability/buildability/limits/cost only.",
        "tests": ["tests/test_gate.py"],
        "manifests": [],
        "pass": "fake critic never gates; qualified + bound -> build; unqualified/no site -> hold; tampered preview, edited "
                "design, needs_review -> reject; owner schematic -> build, floating sand -> hold, no position -> refused.",
        "verdict_if_pass": "IMPLEMENTED (design-side gate, S16); the mind's approval/task binding to gate.json is separate",
        "limits": "real critic not run (CP-06 pending); the gate reads files in one folder, the mind must re-check gate.json "
                  "against the design hash before building.",
    },
    "CP-67": {
        "hypothesis": "Eve's taste profile and design journal are fed only by her own critiques and build outcomes, labelled as "
                      "such, append-only and reversible; an owner entry would outrank them; no human-rating source exists.",
        "tests": ["tests/test_gate.py::test_taste_is_eves_own_labelled_and_reversible"],
        "manifests": [],
        "pass": "entries labelled eve_critique/build_outcome; preferences carry the not-Hunter label; revert works; owner "
                "entry outranks; an invented source is refused; the journal note cites the design hash.",
        "verdict_if_pass": "IMPLEMENTED (machinery); the J part (Hunter-rated taste) DEFERRED by owner choice (S16)",
        "limits": "no real critiques or builds recorded yet.",
    },
    "CP-48": {
        "hypothesis": "For the hand-built 5x5 hut, bom.json (final items, scaffold, recipe expansion with batch yields and "
                      "leftovers, deficit against a half-stocked storage index, carry slots, tool/fuel estimates) equals an "
                      "independent hand count, and design.json/scaffold.json/bom.json share one design hash.",
        "tests": ["tests/test_hut_bom.py", "tests/test_progress.py", "tests/test_buildorder.py"],
        "manifests": ["tests/fixtures/hut/expected_bom.json", "tests/fixtures/hut/storage_index.json",
                      "tests/fixtures/progress/hut5_half.json", "tests/fixtures/test3/expected_order.json"],
        "pass": "every number in expected_bom.json (hand count) equals the computed bom.json; estimates are labelled; "
                "the half-built hut's remaining items equal the hand count (shared progress vector); the 3x3x3 placement "
                "order equals the hand-written order and every example orders supports first.",
        "verdict_if_pass": "PASS (design side); construction-progress identity clause needs the mind",
        "extra": ["examples/out/hut5"],
        "limits": "offline count only; gathering times, tool wear and fuel are labelled estimates; the live build is CP-50/51.",
    },
}


def main(cp: str) -> int:
    spec = CPS[cp]
    root = REPO / "evidence" / cp
    root.mkdir(parents=True, exist_ok=True)
    n = len(list(root.glob("*-attempt-*"))) + 1
    stamp = time.strftime("%Y-%m-%d-%H%M")
    out = root / f"{stamp}-attempt-{n}"
    out.mkdir()
    t0 = time.time()
    p = subprocess.run([sys.executable, "-m", "pytest", "-v", *spec["tests"]], cwd=DESIGN, capture_output=True, text=True)
    elapsed = time.time() - t0
    (out / "pytest.txt").write_text(p.stdout + p.stderr, encoding="utf-8")
    summary = (p.stdout.strip().splitlines() or ["(no output)"])[-1]
    for extra in spec.get("extra", []):
        src = DESIGN / extra
        for f in src.rglob("*") if src.is_dir() else [src]:
            if f.is_file():
                dst = out / f.relative_to(DESIGN)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(f.read_bytes())
    counts = []
    for m in spec["manifests"]:
        d = json.loads((DESIGN / m).read_text())
        if "cases" in d:
            by = {}
            for c in d["cases"]:
                k = c.get("class") or c.get("category")
                by[k] = by.get(k, 0) + 1
            counts.append(f"- `{m}`: {len(d['cases'])} cases ({', '.join(f'{k} {v}' for k, v in sorted(by.items()))})")
        else:
            counts.append(f"- `{m}`")
    rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "parts/design"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    gm = json.loads((REPO / "data/game/26.3/MANIFEST.json").read_text())
    verdict = spec.get("verdict_if_pass", "PASS") if p.returncode == 0 else "FAIL"
    text = f"""# {cp} attempt {n} — {verdict}

- **Lane:** shark (O18; offline design checks, no 45L dependency)
- **When:** {time.strftime('%Y-%m-%d %H:%M %z')} · elapsed {elapsed:.1f} s · machine {platform.node()} (offline, no game running)
- **Hypothesis:** {spec['hypothesis']}
- **Pass condition (frozen before the run):** {spec['pass']}
- **Command:** `cd parts/design && python -m pytest -v {' '.join(spec['tests'])}` (via `python tools/evidence.py {cp}`)
- **Fixture manifests:**
{chr(10).join(counts)}
- **Versions:** code `{rev}`{' + uncommitted changes in parts/design' if dirty else ''}; Python {platform.python_version()}; litemapy {version('litemapy')}, nbtlib {version('nbtlib')}, numpy {version('numpy')}; game data {gm['version']} (data version {gm['data_version']}, client jar sha256 {gm['sources']['jars']['client']['sha256_local'][:16]}…)
- **Observed:** `{summary}` (exit {p.returncode}); full log `pytest.txt`
- **Attempts so far for {cp}:** {n} (this one #{n})
- **Deaths / interventions / violations:** n/a (offline file test)
- **Limits of this evidence:** {spec['limits']}
"""
    (out / "RESULT.md").write_text(text, encoding="utf-8")
    summ = DESIGN / "evidence-summaries"
    summ.mkdir(exist_ok=True)
    (summ / f"{cp}-{stamp}-attempt-{n}.md").write_text(text, encoding="utf-8")
    print(text)
    return p.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
