"""O94ah-g: a lane run's own event sequence and Eve's real positions, out of the client log, for WalkTest's lane replay.

    py -3.12 parts/builder/tools/lane_replay_extract.py <client.log> <from HH:MM:SS> <to HH:MM:SS> <design> <ox,oy,oz> <out.json> [--cage x,y,z]

Takes the LAST block of the log whose times lie in [from, to) (the log is appended across days), keeps every
`eve-builder: place|break for X, Y, Z -> ok` as a world change and every `from (x, y, z)` as one of her positions (she is
standing still then: a click from where she is, or where a walk ended), all design-local. --cage adds the O94ah cage's
9 barriers around the stand at world x,y,z. Read-only: no lane, no RCON.
"""
import json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.argv, _args = sys.argv[:1] + ["build", "--lane", "C"], sys.argv[1:]  # laneb_build reads its own argv at import
import laneb_build as L  # noqa: E402  (design decoding exactly as the harness does)

log, t0, t1, name, origin, out = _args[:6]
cage = tuple(int(v) for v in _args[_args.index("--cage") + 1].split(",")) if "--cage" in _args else None
ox, oy, oz = (int(v) for v in origin.split(","))
lines = Path(log).read_text(encoding="utf-8", errors="replace").splitlines()
ts = lambda l: l[1:9] if len(l) > 9 and l[0] == "[" and l[3] == ":" and l[6] == ":" else None
end = max(i for i, l in enumerate(lines) if ts(l) and t0 <= ts(l) < t1)
start = end
while start > 0 and (ts(lines[start - 1]) is None or t0 <= ts(lines[start - 1]) < t1):
    start -= 1
d, _h, cells, _items, _steps = L.load(name)
sc = {c: st for c, st in L.scaffold_cells(name, d, (ox, oy, oz), states=True)}
ev = re.compile(r"eve-builder: (place|break) for (-?\d+), (-?\d+), (-?\d+) -> ok")
at = re.compile(r"from \((-?[\d.]+), (-?[\d.]+), (-?[\d.]+)\)")
seq, offmodel = [], 0
for l in lines[start:end + 1]:
    if "eve-builder" not in l:
        continue
    offmodel += "is off the walk model" in l
    m = ev.search(l)
    if m:
        w = tuple(int(m.group(i)) for i in (2, 3, 4))
        loc = [w[0] - ox, w[1] - oy, w[2] - oz]
        if m.group(1) == "break":
            seq.append(["b", *loc])
        else:
            want = cells.get(tuple(loc), "minecraft:air")  # design's block, else the scaffold's (a FIX place is design's too)
            seq.append(["p", *loc, want if want != "minecraft:air" else sc.get(w, "minecraft:dirt")])
        continue
    m = at.search(l)
    if m:
        seq.append(["s", float(m.group(1)) - ox, float(m.group(2)) - oy, float(m.group(3)) - oz])
extra = []
if cage:
    x, y, z = (cage[0] - ox, cage[1] - oy, cage[2] - oz)
    extra = [[x + dx, y + k, z + dz, "minecraft:barrier"] for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)) for k in (0, 1)] + [[x, y + 2, z, "minecraft:barrier"]]
Path(out).write_text(json.dumps({"design": name, "origin": [ox, oy, oz], "window": [lines[start][:10], lines[end][:10]],
                                 "logged_off_model": offmodel, "extra": extra, "seq": seq}, separators=(",", ":")), encoding="utf-8")
print(f"{out}: lines {start}..{end} {lines[start][:10]}..{lines[end][:10]}, {sum(e[0] == 'p' for e in seq)} places, "
      f"{sum(e[0] == 'b' for e in seq)} breaks, {sum(e[0] == 's' for e in seq)} positions, {offmodel} logged off-model reads")
