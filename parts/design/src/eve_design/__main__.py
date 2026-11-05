"""python -m eve_design compile PROGRAM.eve --out DIR [--project ID] [--version N] [--storage idx.json] [--brief brief.json]
python -m eve_design import FILE.litematic|FILE.schem --out DIR [--owner-supplied --origin X,Y,Z] [same options]
python -m eve_design generate BRIEF.json --out DIR --generator fake|deepseek [--critic none|fake|deepseek]
python -m eve_design gate DIR                      # (re)decide build | hold | reject for a design folder -> gate.json
python -m eve_design critic-check --critic fake|deepseek --out DIR   # critic self-test (planted visible faults)
python -m eve_design prompts BRIEF.json --out DIR [--design DIR]       # the exact DeepSeek requests, NOT sent
python -m eve_design paste DIR --out PACK [--origin X,Y,Z] | compare RENDER.png --shot SHOT.png --out DIR

Design folders follow contracts/design-project.md: design.json, design.litematic, scaffold.json (when needed), bom.json,
validation.json, previews/, build_order.json, gate.json (+ program.eve, brief.json, critique.json, candidates.json).
Exit codes: 0 ok / build, 2 program, schematic or model error, 3 validation failed or the gate did not say build.
The fake generator/critic are deterministic stand-ins; they must be chosen explicitly and can never gate a real build.
`--owner-supplied` (S16): Hunter's schematic at the position he stated is built as given — no critique, and only
stability, buildability, limits and affordability gate it."""
import argparse
import json
import shutil
import sys
from pathlib import Path

from . import bom, buildorder, gamedata, gate, preview, scaffold
from .validate import validate
from .compiler import CompileError, LangError, compile_file
from .schem_io import SchematicError, load, save_litematic


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="eve_design")
    ap.add_argument("cmd", choices=["compile", "import", "generate", "gate", "approval", "critic-check", "prompts", "paste",
                                    "compare"])
    ap.add_argument("src", type=Path, nargs="?")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--project")
    ap.add_argument("--version", type=int, default=1)
    ap.add_argument("--storage", type=Path)
    ap.add_argument("--brief", type=Path)
    ap.add_argument("--generator", choices=["fake", "deepseek"])
    ap.add_argument("--critic", choices=["none", "fake", "deepseek"], default="none")
    ap.add_argument("--origin", help="world x,y,z of the design's min corner (import --owner-supplied, paste)")
    ap.add_argument("--owner-supplied", action="store_true", help="import: Hunter's schematic, built as given at --origin")
    ap.add_argument("--design", type=Path, help="prompts: a design folder whose critic request to show")
    ap.add_argument("--shot", type=Path, help="compare: the real screenshot to set against the renderer image (src)")
    ap.add_argument("--interior", action="store_true", help="compile: scaffold may go inside rooms (O89: off by default)")
    ap.add_argument("--stand-policy", choices=["unbuilt", "never_design", "walk_surfaces"],
                    help="compile/import: where Eve may stand to place (O94ag; kept in provenance.stand_policy). Default: "
                         "walk_surfaces for eve-organic designs, unbuilt otherwise")
    ap.add_argument("--trusted", help="approval: the envelope's owner.trusted_players, comma-separated")
    a = ap.parse_args(argv)
    origin = tuple(int(v) for v in a.origin.split(",")) if a.origin else None
    if a.cmd in ("paste", "compare"):
        from . import world
        if a.cmd == "paste":
            m = world.paste(a.src, a.out, origin)
            print(f"data pack for {m['blocks']} blocks at {m['origin']} -> {a.out} ({', '.join(m['functions'])})")
        else:
            print(json.dumps(world.compare(a.src, a.shot, a.out), indent=1))
        return 0
    if a.cmd == "approval":                   # O94q: may this folder be built? (read-only; exit 0 = yes)
        from . import approval
        r = approval.check(a.src, trusted=a.trusted.split(",") if a.trusted else None)
        print(json.dumps(r, indent=1))
        return 0 if r["may_build"] else 3
    if a.cmd == "gate":
        r = gate.decide(a.src)
        print(f"{r['decision']} {r.get('design_hash', '')} " + "; ".join(r["reasons"]))
        return 0 if r["decision"] == "build" else 3
    gd = gamedata.load()
    if a.cmd == "critic-check":
        from . import critic_check, models
        critic = {"fake": models.FakeCritic, "deepseek": models.DeepSeekCritic}[a.critic]()
        r = critic_check.run(critic, gd, a.out or critic_check.SELFTEST_DIR)
        print(json.dumps({k: r.get(k) for k in ("critic", "version", "passed", "caught", "faults", "false_accepts",
                                                 "repeat_max_diff", "error")}, indent=1))
        return 0 if r.get("passed") else 3
    if a.cmd == "prompts":
        from . import models
        print(json.dumps(models.write_requests(json.loads(a.src.read_text()), gd, a.out, a.design), indent=1))
        return 0
    project = a.project or a.src.stem
    if a.cmd == "generate":
        return _generate(a, gd)
    try:
        if a.cmd == "compile":
            site = json.loads(a.brief.read_text()).get("site") if a.brief else None   # as generate compiles it
            at = {"origin": tuple(site.get("origin", {}).get(k, d) for k, d in (("x", 0), ("y", 64), ("z", 0))),
                  "plot": site.get("plot")} if site else {}
            g = compile_file(a.src, gd, project_id=project, version=a.version, **at)
        else:
            if a.owner_supplied and origin is None:
                print("error: --owner-supplied needs --origin X,Y,Z (the position Hunter stated)", file=sys.stderr)
                return 2
            g, issues = load(a.src, gd)
            if origin is not None:
                g.origin = origin
            g.meta.update({"project_id": project, "version": a.version,
                           "provenance": {"source": f"imported {a.src.name}", "import_issues": len(issues),
                                          "owner_supplied": bool(a.owner_supplied), "origin_stated": origin is not None}})
            for i in issues:
                print(f"UNSUPPORTED {i['feature']}: {i['detail']}", file=sys.stderr)
    except (LangError, CompileError, SchematicError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    a.out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "compile":
        shutil.copyfile(a.src, a.out / "program.eve")
    if a.stand_policy:                        # O94ag: kept with the design, so every replay and the validator agree
        g.meta.setdefault("provenance", {})["stand_policy"] = a.stand_policy
    h = g.save(a.out / "design.json")
    save_litematic(g, a.out / "design.litematic", gd, name=project, description=f"Eve {project} v{a.version} {h[:12]}")
    site = json.loads(a.brief.read_text()).get("site") if a.brief else None
    planning: dict = {}
    s, _ = scaffold.plan(g, gd, site, interior=True if a.interior else None, report=planning)
    if s:
        s.save(a.out / "scaffold.json")
    stock, ref = bom.stock_from_storage(json.loads(a.storage.read_text())) if a.storage else (None, None)
    b = bom.compute(g, gd, s, stock, ref)
    (a.out / "bom.json").write_text(json.dumps(b, indent=1) + "\n")
    brief = json.loads(a.brief.read_text()) if a.brief else None
    if brief:
        shutil.copyfile(a.brief, a.out / "brief.json")
    v = validate(g, gd, brief=brief, bom=b, scaffold=s)
    (a.out / "validation.json").write_text(json.dumps(v, indent=1) + "\n")
    preview.render(g, gd, a.out / "previews")
    (a.out / "build_order.json").write_text(json.dumps(buildorder.compact(h, buildorder.plan_order(g, gd, s, site=site)[0]), separators=(",", ":")) + "\n")
    d = gate.decide(a.out)
    bad = [k for k, c in v["checks"].items() if c["status"] == "fail"]
    print(f"{project} v{a.version} {h} size={g.size} blocks={sum(g.counts().values())} "
          f"scaffold={sum(s.counts().values()) if s else 0} valid={v['passed']}{' FAILED ' + ','.join(bad) if bad else ''} "
          f"gate={d['decision']} planning={planning['seconds']:g}/{planning['budget_s']:g} s "
          f"({planning['storeys']} storeys, O87i bar {'met' if planning['o87i_bar_met'] else 'missed'}) -> {a.out}")
    # S16: an owner schematic is built as given; its other checks (an O89i step-up door, say) are reported, not gating
    return 0 if v["passed"] or (a.owner_supplied and d["decision"] == "build") else 3


def _generate(a, gd) -> int:
    from . import models
    from .generate import generate
    if not a.generator:
        print("error: --generator fake|deepseek is required", file=sys.stderr)
        return 2
    gen = models.FakeGenerator() if a.generator == "fake" else models.DeepSeekGenerator()
    critic = {"none": None, "fake": models.FakeCritic, "deepseek": models.DeepSeekCritic}[a.critic]
    brief = json.loads(a.src.read_text())
    r = generate(brief, gd, gen, critic() if critic else None, out=a.out, project_id=a.project, version=a.version)
    d = gate.decide(a.out) if (a.out / "design.json").exists() else {"decision": "none"}
    print(f"{r.get('status')} {r.get('design_hash', '')} candidates={len(r.get('candidates', []))} gate={d['decision']} -> {a.out}"
          + (f" ({r['error']})" if r.get("error") else ""))
    return 0 if d["decision"] == "build" else 2 if r["status"] == "model_unavailable" else 3


if __name__ == "__main__":
    sys.exit(main())
