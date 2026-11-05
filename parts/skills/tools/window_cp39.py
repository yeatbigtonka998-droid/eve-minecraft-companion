"""Skills' lane B CP-39 window (O94/O102), run ONLY on the orchestrator's order once registry 0.16.0 is lane-applicable.

    py -3.12 parts/skills/tools/window_cp39.py --registry <0.16.0 canonical hash prefix> --skills-jar <sha256 prefix>
        [--mind 711acbdb,556b1da3] [--n 4] [--cp40] [--dry-run]

eve-watch rolls the lane first (sense's parts/server-helper/LANE-CP39-SETUP.md: sense 0.1.21, the envelope v9 lane copy
from draft 1bb46e2f with the CP-39 fixture overrides on all three copies, registry 0.16.0, the skills jar, body J2, the
mind) and hands it over with -Next eve-skills. This script then:
 1. guards every pin (read-only, no lock, nothing changed on a failure; --dry-run stops here):
    - sense: server-dev/mods eve-sense jar sha256 = fb7f3f81... (0.1.21);
    - envelope: server-dev, mind and body-run copies byte-identical, envelope_version 9, its home sheep/cow rules, no
      rule in `mine`, the skills_farm farmland box (cp39_tend's own pre-flight checks the details again);
    - registry: `eve status` registry 0.16.0 with the given hash prefix (the assembled draft v2 9ff588bb once applied);
    - skills: the client's eve-skills jar sha256 starts with --skills-jar (built from >= 6636a954: cp39_tend's
      expectations need its harvest_crop end-on-refusal and shear/milk ok-with-refusals; 501cf6be predates them);
    - body: J2 de5a4355 (the lane's run builds body/build/libs);
    - mind: the running mind_src contains every --mind commit (711acbdb: the eve-candidates journal; 556b1da3: harvest_crop's
      break+use grant);
    - body up, world binding OK.
 2. takes the lane lock ONCE (-By eve-skills) and keeps it: the harnesses run with EVE_SERIES=1 and skip their own lock;
 3. runs cp39_tend.py (its seven cases, --n per case; every case starts a fresh game day, so sense's per-day quotas allow
    any n), then with --cp40 the O93 inflow cases x3, the full cp40p (20 cases) and digup x3 on the same jar;
    Stop on any death (orchestrator): Eve's deathCount is zeroed before each series and read after it; a death runs no
    further series (cp39_tend also stops itself at the case it happened in);
 4. releases ONCE with -Next eve-watch (that hand-back starts the rollback to v8 / 0.1.20 / 0.15.0);
 5. prints each series' window (from/to) and its result line for eve-watch's I29f verdict and planner_calls.
"""
import hashlib, json, os, re, subprocess, sys
from datetime import datetime
from pathlib import Path

sys.argv += [] if "--lane" in sys.argv or "--lane-config" in sys.argv else ["--lane", "B"]
sys.path.insert(0, str(Path(__file__).parent))
import laneb_skills as L  # noqa: E402

# O104j: the same window on lane B (Shark) or a configured lane (F on the 45L: --lane-config parts/skills/tools/lanes/F.json, run
# there as eveuser); every harness gets the same lane arguments
LANE_ARGS = ["--lane", L.LANE_ID] + (["--lane-config", sys.argv[sys.argv.index("--lane-config") + 1]] if "--lane-config" in sys.argv else [])

SENSE_JAR = "fb7f3f8190806ecab6e29980527c126bb4a787045e6cfb55a9f6952e139128b8"
BODY_J2 = "de5a435575497dac34af7a4fef550dd21b7df58264e3ecda8264d2830fcc4910"
TOOLS = Path(__file__).parent


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def guards(want_reg, want_skills, minds):
    """[(ok, what)] over every pin; read-only (files, git, eve status)."""
    out = []
    sj = sorted((L.SERVER / "mods").glob("eve-sense-*.jar"))
    out.append((len(sj) == 1 and sha(sj[0]) == SENSE_JAR, f"sense jar {[j.name + ' ' + sha(j)[:12] for j in sj]} (want fb7f3f81, 0.1.21)"))
    copies = [L.SERVER / "config" / "envelope.json", L.LANE / "mind" / "config" / "envelope.json",
              L.LANE / "src" / "parts" / "body" / "run" / "config" / "envelope.json"]
    shas = {sha(p) if p.exists() else None for p in copies}
    env = json.loads(copies[0].read_text(encoding="utf-8-sig")) if copies[0].exists() else {}
    out.append((len(shas) == 1 and None not in shas, f"envelope copies identical ({len(shas)} distinct)"))
    rules = {(a.get("zone"), a.get("species")) for a in env.get("animals") or []}
    out.append((env.get("envelope_version") == 9 and {("home", "minecraft:sheep"), ("home", "minecraft:cow")} <= rules
                and not any(z == "mine" for z, _ in rules) and any(b.get("id") == "skills_farm" for b in env.get("farmland") or []),
                f"envelope v{env.get('envelope_version')}: home sheep/cow rules, none in mine, skills_farm farmland"))
    bj = sorted((L.LANE / "src" / "parts" / "body" / "build" / "libs").glob("eve-body-*.jar"))
    out.append((len(bj) == 1 and sha(bj[0]) == BODY_J2, f"body jar {[j.name + ' ' + sha(j)[:12] for j in bj]} (want J2 de5a4355)"))
    kj = sorted(L.MODS.glob("eve-skills-*.jar"))
    out.append((len(kj) == 1 and bool(want_skills) and sha(kj[0]).startswith(want_skills.lower()),
                f"skills jar {[j.name + ' ' + sha(j)[:12] for j in kj]} (want {want_skills})"))
    st = L.eve("status")
    reg = st.get("registry") or {}
    out.append((reg.get("version") == "0.16.0" and bool(want_reg) and str(reg.get("hash", "")).startswith(want_reg.lower()),
                f"registry {reg.get('version')} {str(reg.get('hash'))[:12]} (want 0.16.0 {want_reg})"))
    running = st.get("mind_src")
    for m in minds:
        has = bool(running) and subprocess.run(["git", "-C", str(L.REPO), "merge-base", "--is-ancestor", m, running], capture_output=True).returncode == 0
        out.append((has, f"mind_src {running} contains {m}"))
    out.append((bool(st.get("body")) and bool((st.get("binding") or {}).get("ok")), f"body up {bool(st.get('body'))}, binding {st.get('binding')}"))
    return out


def handover(show, now=None):
    """I40: the lane is ours to start only when its lock shows the hand-over to us (a reservation next=eve-skills, or our own
    lock), and not before its NOT_BEFORE when the reservation carries one. (ok, why) - pure, for --dry-run and the selftest."""
    if "by=eve-skills" in show and "(expired)" not in show:
        return True, "the lock is already ours"
    if "next=eve-skills" not in show:
        return False, f"no hand-over to eve-skills on the lock: {show.strip()[:200]}"
    m = re.search(r"(?i)not_?before=(\d{1,2}:\d{2}(?::\d{2})?)", show)
    if m:
        now = now or datetime.now()
        hh, mm, *ss = (int(v) for v in m.group(1).split(":"))
        nb = now.replace(hour=hh, minute=mm, second=ss[0] if ss else 0, microsecond=0)
        if now < nb:
            return False, f"the hand-over is not before {m.group(1)} (now {now:%H:%M:%S})"
    return True, "handed over: " + show.strip()[:200]


def deaths():
    """Eve's deathCount since the series started (scoreboard evedeaths; RCON under the window's lock)."""
    import re
    m = re.search(r"has (\d+) \[evedeaths\]", L.r(f"scoreboard players get {L.P} evedeaths") or "")
    return int(m.group(1)) if m else 0


def series(name, argv):
    """One harness run under the window's lock: (name, from, to, its last line, deaths during it)."""
    L.r("scoreboard objectives add evedeaths deathCount")
    L.r(f"scoreboard players set {L.P} evedeaths 0")
    t0 = datetime.now()
    p = subprocess.run([sys.executable, *argv], cwd=L.REPO, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "EVE_SERIES": "1", "PYTHONIOENCODING": "utf-8"})
    logs = L.REPO / "evidence" / "window-logs"  # evidence/ is gitignored
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"skills-{t0:%Y%m%d-%H%M%S}-{name}.log").write_text(p.stdout + p.stderr, encoding="utf-8")
    last = ([l for l in p.stdout.splitlines() if l.strip()] or [f"no output (exit {p.returncode}): {p.stderr.strip()[-300:]}"])[-1]
    died = deaths()
    row = (name, f"{t0:%H:%M:%S}", f"{datetime.now():%H:%M:%S}", last + (f"  DEATHS {died}" if died else ""), died)
    print(f"{row[0]}: {row[1]}-{row[2]}  {row[3]}", flush=True)
    return row


def gate():
    """The configured lane's own fire gate (lane F: lane_health.py --gate --host 45l, plan section 3: OPEN or no series); the Shark
    lanes have none here (eve-watch's roll gates them). (ok, line)."""
    g = L.LC.get("gate")
    if not g:
        return True, "no lane gate configured (eve-watch's roll gates this lane)"
    p = subprocess.run([sys.executable, *g], cwd=L.REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode == 0, f"gate {' '.join(g)} -> exit {p.returncode}: {(p.stdout.strip().splitlines() or [p.stderr.strip()])[-1][:200]}"


def selftest():
    now = datetime(2026, 10, 22, 3, 0, 0)
    assert handover("free  (reserved: until=03:20 exp=1 next=eve-skills from=eve-watch not_before=02:55)", now)[0]
    assert not handover("free  (reserved: until=03:20 exp=1 next=eve-skills from=eve-watch not_before=03:05)", now)[0]
    assert handover("free  (reserved: until=03:20 exp=1 next=eve-skills from=eve-watch)", now)[0]
    assert not handover("free  (reserved: until=03:20 exp=1 next=eve-watch from=eve-body)", now)[0]
    assert not handover("until=22:06 exp=1 by=eve-watch what=PAUSED  (expired)", now)[0]
    assert handover("until=04:00 exp=1 by=eve-skills what=skills CP-39 window", now)[0]
    assert not handover("until=04:00 exp=1 by=eve-skills what=old  (expired)", now)[0]
    assert LANE_ARGS[:2] == ["--lane", L.LANE_ID]
    print("selftest ok")


def main():
    if "--selftest" in sys.argv:
        return selftest()
    want_reg, want_skills = arg("--registry"), arg("--skills-jar")
    minds = (arg("--mind") or "711acbdb,556b1da3").split(",")
    n = arg("--n", "4")
    lock = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", L.LOCK]
    rows = guards(want_reg, want_skills, minds)
    show = subprocess.run(lock + ["show"], capture_output=True, text=True).stdout
    rows.append(handover(show))  # I40: the lock shows the hand-over to us (and its NOT_BEFORE has passed)
    rows.append(gate())  # lane F: its own fire gate
    for ok, what in rows:
        print(("PIN OK   " if ok else "PIN FAIL ") + what, flush=True)
    if not all(ok for ok, _ in rows):
        sys.exit("window refused: a pin, the hand-over or the gate is not as ordered - nothing locked, nothing run")
    if "--dry-run" in sys.argv:
        print("dry run: every pin as ordered; nothing locked, nothing run")
        return
    a = subprocess.run(lock + ["acquire", "-By", "eve-skills", "-Minutes", "240", "-What", "skills CP-39 window"], capture_output=True, text=True)
    print(a.stdout.strip(), flush=True)
    if a.returncode != 0:
        sys.exit("window refused: the lane lock is not ours")
    done = []
    plan = [("cp39_tend", [str(TOOLS / "cp39_tend.py"), *LANE_ARGS, "--n", n, "--expect-mind", minds[-1]])]
    if "--cp40" in sys.argv:
        plan += [("cp40p-inflow-x3", [str(TOOLS / "laneb_skills.py"), "cp40p", *LANE_ARGS, "--n", "3", "--only", "p_inflow,p_inflow_big"]),
                 ("cp40p-full", [str(TOOLS / "laneb_skills.py"), "cp40p", *LANE_ARGS, "--n", "1"]),
                 ("digup-x3", [str(TOOLS / "laneb_skills.py"), "digup", *LANE_ARGS, "--n", "3"])]
    try:
        for name, argv in plan:
            done.append(series(name, argv))
            if done[-1][4]:  # stop on any death (orchestrator): no further series, straight to the one release
                print(f"STOPPED: {done[-1][4]} death(s) in {name}; the remaining series were not run", flush=True)
                break
    finally:
        rel = subprocess.run(lock + ["release", "-By", "eve-skills", "-Next", "eve-watch", "-NextMinutes", "15"], capture_output=True, text=True)
        print(rel.stdout.strip(), flush=True)
    print("\nwindows for eve-watch (I29f, planner_calls):")
    for name, t0, t1, last, _ in done:
        print(f"  {name}: {t0}-{t1}  {last}")


if __name__ == "__main__":
    main()
