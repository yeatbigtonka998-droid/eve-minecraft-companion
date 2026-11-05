"""Run one checkpoint's test suite as a counted qualification attempt and save evidence.

  python tools/run_cp.py CP-11 [--note "why this attempt"]

Writes evidence/CP-NN/<yyyy-mm-dd-HHMM>-attempt-N/{RESULT.md, junit.xml, pytest.log, manifest.json} and copies
RESULT.md to parts/mind/evidence-summaries/CP-NN-attempt-N.md. Every run is an attempt, pass or fail.
"""
import argparse
import datetime
import json
import platform
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from importlib.metadata import version
from pathlib import Path

MIND = Path(__file__).resolve().parents[1]
ROOT = MIND.parents[1]
sys.path.insert(0, str(MIND / "src"))
from eve_mind.config import lane, sha256, strict_loads  # noqa: E402
from eve_mind.registry import Registry  # noqa: E402

CPS = {
    "CP-11": ("tests/test_cp11.py",
              "Every malformed, oversized, duplicate, unauthorised, unknown-type, out-of-range, wrong-session or "
              "wrong-world message on the real /body and /sense links is refused before any row is stored or any "
              "action is sent; legitimate requests are correlated with their results; retries cannot multiply an "
              "action.",
              "All manifest cases pass (>=10 valid, >=10 invalid) against the real hub on loopback with the fakes."),
    "CP-19": ("tests/test_cp19.py",
              "The mind never records success for an action because a command was sent: ok needs the registry's "
              "evidence keys, else it is uncertain and settled only from observed state; failed preconditions stop "
              "without retry; a missing result becomes an explicit uncertain outcome; failure codes are a closed set.",
              "All cases pass against the real hub with the fakes; registry entries are complete contracts."),
    "CP-20": ("tests/test_cp20.py",
              "The mind is a single steering loop: one action in flight, stop always wins, a late result or "
              "pre-stop output cannot reactivate work, cancel is distinct from a hard stop, reconnect never resumes.",
              "All cases pass against the real hub with the fakes."),
    "CP-22": ("tests/test_cp22.py",
              "With no observed world progress (movement, inventory change, Eve block event) inside the window the "
              "mind cancels the action with a concrete reason and releases reservations; an ignored cancel escalates "
              "to a hard stop; the same failing request is refused after 3 failures in 10 minutes.",
              "All cases pass against the real hub with the fakes (window 0.5-0.8 s in the fixture; 30 s default)."),
    "CP-23": ("tests/test_cp23.py",
              "A paused task continues only if its recorded assumptions (container contents, approval + design hash, "
              "world) still hold; completed work is read from observed state, not replayed.",
              "All cases pass against the real hub with the fakes."),
    "CP-24": ("tests/test_cp24.py",
              "Link drops, a locked database and delayed responses degrade to refusal/bounded waiting with honest "
              "reasons; stop still goes out with the DB locked; nothing stale revives cancelled work.",
              "All cases pass against the real hub with the fakes."),
    "CP-35": ("tests/test_cp35.py",
              "Recipe trees solved over the pinned 26.3 recipe/tag/loot data match hand-counted expectations: batch "
              "yields and leftovers, mixed and avoided substitutions, storage-block cycles used only on hand, fuel, "
              "stations, and explicit impossible results.",
              "All 24 hand-counted cases and the accounting/bad-request checks pass."),
    "CP-41": ("tests/test_cp41.py",
              "Plans contain only registry ∩ caps skills with validated params, observed entities and grounded "
              "coordinates; invented skills, illegal coordinates/items, missing prerequisites and unapproved effects "
              "are rejected before dispatch; the Ollama format is generated from registry ∩ caps; a stalled, absent or "
              "garbled planner and a plan arriving after a stop dispatch nothing.",
              "All deterministic cases pass; live-model cases run only if the pinned model answers (else SKIP)."),
    "CP-42": ("tests/test_cp42.py",
              "Owner commands map to the right skill and arguments (>=19/20 on the held-out set), ambiguity yields one "
              "focused question, stop wins in conflicts, untrusted senders cannot command or approve.",
              "All cases pass. Held-out set authored by the mind chat (not Hunter): a self-check, not qualification."),
    "CP-44": ("tests/test_cp44.py",
              "Facts are remembered only from trusted owner words and observations, with source, time, confidence and "
              "world; corrections supersede; journal text counts as history only when grounded in ok actions.",
              "Every step of fixtures/CP-44.json recalls exactly as listed (incl. after a restart and in another world) "
              "and every boundary case behaves as listed. Fixture and expectations written by the mind chat."),
    "CP-21": ("tests/test_cp21.py",
              "After the mind service is killed mid-action (lost ack, lost result) and restarted on the same "
              "database, open actions are reconciled against observed body state and are never replayed.",
              "Each case: the body executed the action exactly once across both mind lifetimes; the action row is "
              "settled with a reconcile note grounded in an observed tick; the task status follows the observed "
              "postcondition; no auto-continue on a different world."),
}


def git(*args):
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        return "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cp")
    ap.add_argument("--note", default="")
    ap.add_argument("--repeat", type=int, default=1, help="run each case N times (S-class attempt counts)")
    a = ap.parse_args()
    test, hypothesis, pass_cond = CPS[a.cp]
    base = ROOT / "evidence" / a.cp
    base.mkdir(parents=True, exist_ok=True)
    n = 1 + sum(1 for d in base.iterdir() if re.search(r"-attempt-\d+$", d.name) and (d / "RESULT.md").exists()
                and "mind" in (d / "RESULT.md").read_text("utf-8", "replace")[:400])
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
    out = base / f"{stamp}-{lane()}-attempt-{n}"
    out.mkdir()
    cmd = [sys.executable, "-m", "pytest", test, "-v", "-p", "no:cacheprovider", f"--junitxml={out / 'junit.xml'}"]
    t0 = time.monotonic()
    env = {**__import__("os").environ, "EVE_REPEAT": str(a.repeat)}
    proc = subprocess.run(cmd, cwd=MIND, capture_output=True, text=True, env=env)
    elapsed = time.monotonic() - t0
    (out / "pytest.log").write_text(proc.stdout + proc.stderr, "utf-8")

    cases = []
    for tc in ET.parse(out / "junit.xml").getroot().iter("testcase"):
        bad = tc.find("failure") if tc.find("failure") is not None else tc.find("error")
        skip = tc.find("skipped")
        cases.append({"case": tc.get("name"), "outcome": "FAIL" if bad is not None else "SKIP" if skip is not None
                      else "PASS", "time_s": float(tc.get("time", 0)),
                      "detail": (bad.get("message") or "")[:200] if bad is not None else ""})
    (out / "manifest.json").write_text(json.dumps(cases, indent=2), "utf-8")
    failed = [c for c in cases if c["outcome"] == "FAIL"]
    outcome = "PASS" if proc.returncode == 0 and cases and not failed else "FAIL"
    reg = Registry(ROOT / "contracts" / "skills" / "registry.json")
    env_path = ROOT / "config" / ("envelope.json" if (ROOT / "config" / "envelope.json").exists() else "envelope.example.json")
    prior = sorted(d.name for d in base.iterdir() if d != out and re.search(r"-attempt-\d+$", d.name))
    rows = "\n".join(f"| {c['case']} | {c['outcome']} | {c['time_s']:.2f} | {c['detail']} |" for c in cases)
    result = f"""# {a.cp} — mind — attempt {n} — {outcome}

- **Lane:** `{lane()}` (O18)
- **Part:** mind (`parts/mind`), run {datetime.datetime.now().isoformat(timespec='seconds')} on {platform.node()} ({platform.system()} {platform.release()})
- **Hypothesis:** {hypothesis}
- **Pass condition (frozen before the run):** {pass_cond}
- **Fixture:** `{test}` (case table = fixture manifest; per-case outcome in `manifest.json`)
- **Command:** `{' '.join(['python', '-m', 'pytest', test, '-v'])}` (cwd `parts/mind`)
- **Versions:** code `{git('rev-parse', '--short', 'HEAD')}`{' + uncommitted changes' if git('status', '--porcelain', 'parts/mind') else ''}; Python {platform.python_version()}; websockets {version('websockets')}; jsonschema {version('jsonschema')}; pytest {version('pytest')}; registry {reg.version} `{reg.hash[:16]}`; envelope `{env_path.name}` hash `{sha256(strict_loads(env_path.read_text('utf-8')))[:16]}`
- **Conditions:** fakes (`tools/fake_body.py`, `tools/fake_sense.py`), not the real mods; loopback; no game running. This is mind-side evidence only.
- **Note:** {a.note or '-'}{f" (each case repeated {a.repeat}x)" if a.repeat > 1 else ""}
- **Elapsed:** {elapsed:.1f} s; deaths 0; human interventions 0; permission violations 0 (no world)
- **Observed:** {sum(c['outcome'] == 'PASS' for c in cases)} passed, {sum(c['outcome'] == 'SKIP' for c in cases)} skipped, {len(failed)} failed of {len(cases)} cases; exit code {proc.returncode}
- **Earlier attempts in this folder:** {', '.join(prior) or 'none'}

| case | outcome | s | detail |
|---|---|---|---|
{rows}
"""
    (out / "RESULT.md").write_text(result, "utf-8")
    summaries = MIND / "evidence-summaries"
    summaries.mkdir(exist_ok=True)
    shutil.copy(out / "RESULT.md", summaries / f"{a.cp}-attempt-{n}.md")
    skipped = sum(c["outcome"] == "SKIP" for c in cases)
    print(f"{a.cp} attempt {n}: {outcome} ({len(cases) - len(failed) - skipped} passed, {skipped} skipped, "
          f"{len(failed)} failed) -> {out.relative_to(ROOT)}")
    return 0 if outcome == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
