r"""Rule O39: every player teleport by a sense harness goes through the lane's safe_tp (eve-watch's ops/safe_tp.py: solid
floor, air at feet and head, inside an envelope zone; refuses otherwise and does not move the player). No safe_tp.py in the ops dir = no teleport (SystemExit);
there is no raw-tp fallback. A refusal is never retried as a raw tp.
    from lane_tp import lane_tp
    lane_tp(ROOT, OPS, "EveDev", x, y, z, yaw=0, pitch=0)      # x, y, z = feet; raises SystemExit (SafeTpRefused) if refused
"""
import importlib
import sys
from pathlib import Path


def lane_tp(root, ops, player, x, y, z, yaw=0.0, pitch=0.0, air_ok=False):
    ops = str(Path(ops))
    if not (Path(ops) / "safe_tp.py").exists():
        sys.exit(f"O39: no safe_tp.py in {ops}; not teleporting {player}")
    if ops not in sys.path:
        sys.path.insert(0, ops)
    return importlib.import_module("safe_tp").safe_tp(str(root), player, float(x), float(y), float(z), yaw, pitch, air_ok=air_ok)


def parse(spot):
    """'x y z' (as the harnesses' --park / --chest options give it) -> floats."""
    return [float(v) for v in str(spot).split()[:3]]
