"""Offline check of laneb.room_repair and room_cells on a fake world (no server): py -3.12 tools/test_room_repair.py"""
import re, sys
from pathlib import Path

sys.argv = [sys.argv[0], "--lane", "shark-d"]  # laneb reads its lane at import; nothing is sent anywhere
sys.path.insert(0, str(Path(__file__).parent))
import laneb  # noqa: E402

world = {}


def rcon(c):
    n = list(map(int, re.findall(r"-?\d+", c)))
    if c.startswith("execute if block"):
        return "Test passed" if world.get(tuple(n[:3]), "minecraft:air") == c.split()[6] else "Test failed"
    if c.startswith("setblock"):
        if world.get(tuple(n[:3]), "minecraft:air") != "minecraft:air":
            return "Could not set the block"
        world[tuple(n[:3])] = c.split()[4]
        return "Changed the block"
    if c.startswith("fill"):
        k = 0
        for x in range(n[0], n[3] + 1):
            for y in range(n[1], n[4] + 1):
                for z in range(n[2], n[5] + 1):
                    if world.get((x, y, z), "minecraft:air") == "minecraft:air":
                        world[(x, y, z)], k = c.split()[7], k + 1
        return f"Successfully filled {k} block(s)" if k else "No blocks were filled"
    raise AssertionError(c)


b, lantern, door = (-3, 199, -23, 3, 204, -17), (0, 204, -20), (0, -17)
shell = [(x, y, z) for x in range(-3, 4) for y in range(199, 205) for z in range(-23, -16)
         if x in (-3, 3) or y in (199, 204) or z in (-23, -17)]
for c in shell:
    world[c] = "minecraft:stone_bricks"
world[lantern] = "minecraft:sea_lantern"
for y in (200, 201):
    del world[(0, y, -17)]  # the doorway, open

assert laneb.room_cells(rcon, b, lantern, door) == [], "an intact room, doorway open"
assert laneb.room_repair(rcon, b, lantern, door) == 0, "an intact room re-lays nothing"
for y in (200, 201):
    del world[(0, y, -17)]
for c in ((0, 199, -20), (1, 199, -18), (3, 201, -19), lantern):  # the a14 hole under the stand, two more, the lantern
    del world[c]
world[(0, 200, -20)] = "minecraft:cobblestone"  # a block she placed inside stays
world[(0, 200, -17)] = "minecraft:cobblestone"  # a doorway seal: sealed_at_dawn's, not room_cells'
assert laneb.room_cells(rcon, b, lantern, door) == [
    [0, 199, -20, "air"], [0, 200, -20, "other"], [0, 204, -20, "air"], [1, 199, -18, "air"], [3, 201, -19, "air"]]
del world[(0, 200, -17)]
assert laneb.room_repair(rcon, b, lantern, door) == 4
assert all(world.get(c) for c in shell) and world[lantern] == "minecraft:sea_lantern"
assert world[(0, 200, -20)] == "minecraft:cobblestone"
print("room_repair, room_cells: ok")

# O98 doorway-skeleton judge (combat cp98_doorway's math), on the a16 site: door cell (0, -17), stand (0, -20), outside +z
fr = laneb.door_frame((0, -17), (0, -20))
assert fr == (0.5, -16.5, 0, 1), fr
assert laneb.door_frame((-17, 0), (-20, 0)) == (-16.5, 0.5, 1, 0)  # an east-facing door
skel = (fr[0] + fr[2] * 1.3, fr[1] + fr[3] * 1.3)
assert abs(skel[1] - -15.2) < 1e-9, skel  # combat's placement: summon 0.5 200 -15.2
assert laneb.past_door(fr, 1.126, -21.526) < 0 and abs(laneb.past_door(fr, 0.5, -16.0) - 0.5) < 1e-9
assert laneb.in_line_of_fire(fr, skel, (1.126, -21.526)), "a16: where she died"
assert not laneb.in_line_of_fire(fr, skel, (2.5, -17.5)) and not laneb.in_line_of_fire(fr, skel, (-1.5, -17.5)), "beside the door wall"
assert not laneb.in_line_of_fire(fr, (0.5, -16.8), (1.1, -21.5)), "the skeleton inside the door cell: not outside"
print("doorway judge: ok")

# O98o fire_s, raw and judged, on a22's shape: summon at 5.0, in line 5.5-8.1 at ~4.5 (shut from 8.6), and her dawn open at
# 501.5 with the skeleton 35 out: raw 4.0 (a22's), judged 1.5
a22 = ([[5.5 + 0.5 * i, -3.0, True, 1.3, False, 4.5, 13100] for i in range(6)]
       + [[8.6 + 0.5 * i, -3.0, False, 0.8, True, 4.5, 13200] for i in range(986)]
       + [[501.5 + 0.5 * i, -1.5, True, 33.7, False, 35.0, 23950] for i in range(5)])
assert laneb.fire_seconds(a22, 5.0) == (4.0, 1.5, 501.5), laneb.fire_seconds(a22, 5.0)
breach = a22[:6] + [[9.0, -3.0, False, 0.8, True, 4.5, 13200], [9.5, -3.0, True, 1.0, False, 4.6, 18000]]
assert laneb.fire_seconds(breach, 5.0) == (2.0, 2.0, None), "a night opening is no dawn open: it counts"
far = [[7.5, -3.0, True, 16.0, False, 18.0, 13100], [8.0, -3.0, True, 2.0, False, 5.0, 13100]]
assert laneb.fire_seconds(far, 5.0) == (1.0, 0.5, None), "beyond 15 the bow cannot reach her"
assert laneb.fire_seconds([r[:5] for r in a22], 5.0)[1] == 4.0, "old rows (no distance, tod): judged = raw"
print("fire_s raw/judged: ok")

# O101 far-at-dusk / far-at-dawn: the back corner of the a16 room, eyes ~5.4 from the doorway cells (past the 4.0 seal reach)
assert laneb.back_corner((0, -17), 0, -20) == (-2, -22)
assert laneb.back_corner((-17, 0), -20, 0) == (-22, -2)  # an east-facing door
print("back corner: ok")

# O94f: candidates_export through laneb (lane D; tonight's minds write no journal yet, so 0 rows is the expected report)
import tempfile  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
with tempfile.TemporaryDirectory() as tmp:
    assert laneb.LANE_LETTER == "D"
    line = laneb.export_candidates(tmp, datetime.now() - timedelta(minutes=5))
    assert "candidates" in line.lower() or "row" in line.lower(), line
    laneb.OBJECTIVE_IDS.append("selftest-0000")
    line2 = laneb.export_candidates(tmp, datetime.now())
    laneb.OBJECTIVE_IDS.clear()
    print("candidates export:", line, "|", line2)
