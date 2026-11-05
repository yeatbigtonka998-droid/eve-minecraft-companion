"""place_block v2 (target block state) on the dev server: each case sets up supports by RCON, gives the item in hotbar.8,
asks for a state, and judges the result by RCON `execute if block` with that exact state plus the item count.
Eve stands at (0.5, FLOOR, 0.5) facing south; targets are at z = 2 (two blocks south).
    py -3.12 tools/place_v2.py [--n 3]"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from arena import FLOOR, Run  # noqa: E402
from cp10 import T  # noqa: E402

Y = int(FLOOR)
arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d  # noqa: E731
P = lambda x, y, z: {"x": x, "y": y, "z": z}  # noqa: E731
WALL = f"setblock 0 {Y} 3 minecraft:stone"  # a support south of the target; the floor is the other
# (case, setup, item, state, expected block state at (0, Y, 2) or None, expected failure code or None)
CASES = [
    ("stairs_top_south", [WALL], "oak_stairs", {"half": "top", "facing": "south"}, "oak_stairs[half=top,facing=south]", None),
    ("stairs_bottom_south", [], "oak_stairs", {"half": "bottom", "facing": "south"}, "oak_stairs[half=bottom,facing=south]", None),
    ("slab_top", [WALL], "oak_slab", {"type": "top"}, "oak_slab[type=top]", None),
    ("slab_double", [f"setblock 0 {Y} 2 minecraft:oak_slab[type=bottom]"], "oak_slab", {"type": "double"}, "oak_slab[type=double]", None),
    ("log_z", [WALL], "oak_log", {"axis": "z"}, "oak_log[axis=z]", None),
    ("log_x", [f"setblock 1 {Y} 2 minecraft:stone"], "oak_log", {"axis": "x"}, "oak_log[axis=x]", None),
    ("log_z_no_support", [], "oak_log", {"axis": "z"}, None, "state_unreachable"),
    ("door_south_left", [], "oak_door", {"facing": "south", "hinge": "left"}, "oak_door[facing=south,hinge=left,half=lower]", None),
    ("door_south_right", [], "oak_door", {"facing": "south", "hinge": "right"}, "oak_door[facing=south,hinge=right,half=lower]", None),
    ("trapdoor_top", [WALL], "oak_trapdoor", {"half": "top"}, "oak_trapdoor[half=top]", None),
    ("bad_state", [], "oak_planks", {"axis": "x"}, None, "bad_state"),
]


def main():
    r = Run("PLACE-V2", "EveDev")
    t = T(r)
    for rep in range(int(arg("--n", "3"))):
        for name, cmds, item, state, expect, code in CASES:
            t.setup(*cmds, f"item replace entity {r.player} hotbar.8 with minecraft:{item} 8")
            params = {"pos": P(0, Y, 2), "item": f"minecraft:{item}", "state": state}
            before = t.count(item)
            _, _, res = r.act("place_block", params)
            time.sleep(0.4)
            after = t.count(item)
            st = res and res.get("status")
            if expect:
                ok = st == "ok" and t.block_is(0, Y, 2, expect) and after == before - 1
            else:
                ok = st == "failed" and res.get("code") == code and after == before
            r.record(f"{name}#{rep}", "place_block", params, ok,
                     f"status={st} code={res and res.get('code')} {item} {before}->{after} "
                     f"state_after={res and res['evidence'].get('state_after')} clicked={res and res['evidence'].get('clicked')} "
                     f"note={res and (res.get('note') or '')[:160]}")
    # v1 is unchanged: no state, face-centre click, and state_after is now reported
    for rep in range(int(arg("--n", "3"))):
        t.setup(f"setblock 0 {Y} 2 minecraft:stone")
        params = {"pos": P(0, Y + 1, 2), "item": "minecraft:dirt"}
        _, _, res = r.act("place_block", params)
        time.sleep(0.3)
        ok = bool(res) and res.get("status") == "ok" and t.block_is(0, Y + 1, 2, "dirt") and "state_after" in res["evidence"]
        r.record(f"v1_dirt#{rep}", "place_block", params, ok, f"status={res and res.get('status')}")
    r.finish("place_block v2 on the dev server: Eve stands still at (0.5, FLOOR, 0.5) facing south; each case asks for a target state "
             "at (0, FLOOR, 2) and is judged by RCON `execute if block` with that state and the item count; unreachable / bad states "
             "must fail with their code and spend nothing; v1 calls unchanged.", "")


if __name__ == "__main__":
    main()
