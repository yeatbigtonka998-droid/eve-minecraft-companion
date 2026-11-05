"""O91: blast damage is attributed only within a creeper's reach. Data: CP-29 a48 k_fight #4 (lane E 2026-10-21), the blast
8.28 from Eve, hp 19.24 -> 18.62 in the next second, body damage events of 1.38 (a zombie through iron armour) at -1115,
-614 and +386 ms around the blast.  py -3.12 parts/combat/tools/test_blast_attr.py"""
import ast
from pathlib import Path

src = (Path(__file__).parent / "cp29_creeper.py").read_text(encoding="utf-8")
ns = {}
for node in ast.parse(src).body:  # only the pure pieces: the harness module itself connects to a lane on import
    if (isinstance(node, ast.FunctionDef) and node.name == "attribute") or \
            (isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "BLAST_REACH" for t in node.targets)):
        exec(compile(ast.Module([node], []), "cp29_creeper.py", "exec"), ns)
attribute = ns["attribute"]

assert attribute(8.28, round(19.24 - 18.62, 2)) == (0.0, 0.62), "a48 #4: a zombie hit, not the blast"
assert attribute(10.19, 1.38) == (0.0, 1.38), "45L a3 #18"
assert attribute(2.7, 15.2) == (15.2, 0.0), "a cornered blast at 2.7 is blast damage"
assert attribute(6.0, 1.0) == (1.0, 0.0), "the reach edge counts as blast"
assert attribute(None, 0.0) == (0.0, 0.0), "no blast"
print("blast attribution: a48 #4 -> other_damage 0.62, blast 0; reach 6.0 - ok")
