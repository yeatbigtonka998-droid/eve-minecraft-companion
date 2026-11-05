r"""I28a lane proof: the SERVER refuses a world-changing RCON command while the lane lock is absent (eve-sense >= 0.1.19 with
config/eve-sense.json {"rcon_write_lock": "<lane>\\LANE-IN-USE.txt"}). Uses a raw RCON socket on purpose: ops/rcon.py checks
lane_guard client side first, and this proves the server does it without that check.
    py -3.12 tools\i28a_proof.py --root C:\eve-shark-c\server-dev --by eve-sense
Steps: (1) lane free: a raw write (scoreboard objectives add) gets the I28a refusal and creates nothing; (2) reads pass;
(3) lane-lock acquire, the same write passes, then it is removed; release. Writes only a scratch scoreboard objective.
"""
import argparse
import socket
import struct
import subprocess
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--root", required=True)
ap.add_argument("--by", default="eve-sense")
a = ap.parse_args()
ROOT = Path(a.root)
LANE = ROOT.parent
LOCK = LANE / "LANE-IN-USE.txt"
props = dict(l.split("=", 1) for l in (ROOT / "server.properties").read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))


def raw(cmd):
    """One RCON command over a fresh socket, no client-side guard. The password is read, never printed."""
    with socket.create_connection(("127.0.0.1", int(props["rcon.port"])), timeout=10) as s:
        def send(i, t, body):
            b = body.encode("utf-8")
            s.sendall(struct.pack("<iii", len(b) + 10, i, t) + b + b"\0\0")

        def recv():
            n = struct.unpack("<i", s.recv(4))[0]
            d = b""
            while len(d) < n:
                d += s.recv(n - len(d))
            i, _ = struct.unpack("<ii", d[:8])
            return i, d[8:-2].decode("utf-8", "replace")
        send(1, 3, props["rcon.password"])
        if recv()[0] == -1:
            sys.exit("RCON auth failed")
        send(2, 2, cmd)
        return recv()[1]


def lock(op):
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LANE / "ops" / "lane-lock.ps1"), op, "-By", a.by,
                        "-Minutes", "5", "-What", "I28a proof"], capture_output=True, text=True)
    print(f"lane-lock {op}: {r.stdout.strip()}")
    return r.returncode == 0


results = []


def case(name, ok, got):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'} {name}: {got!r}")


if LOCK.exists():
    sys.exit(f"lane is held ({LOCK.read_text(encoding='utf-8').strip()}); run this when the lane is free")
W, OBJ = "scoreboard objectives add i28a dummy", "scoreboard objectives list"
r = raw(W)
case("1 lane free: raw write refused by the server", "I28a: refused" in r, r)
r = raw(OBJ)
case("1b ... and nothing was created", "i28a" not in r, r)
case("2 read passes: list", "players online" in raw("list"), "list")
r = raw("execute if entity @a")
case("2b read passes: execute if", "I28a" not in r and "Test" in r, r)
case("2c safety passes: forceload query", "I28a" not in raw("forceload query"), "forceload query")
if not lock("acquire"):
    sys.exit("could not take the lane lock")
try:
    r = raw(W)
    case("3 locked: the same raw write passes", "Created new objective" in r, r)
    r = raw("scoreboard objectives remove i28a")
    case("3b locked: cleanup write passes", "Removed objective" in r, r)
finally:
    lock("release")
r = raw(OBJ)
case("4 released, nothing left behind", "i28a" not in r, r)
print(f"I28a lane proof: {sum(results)}/{len(results)} {'PASS' if all(results) else 'FAIL'}")
sys.exit(0 if all(results) else 1)
