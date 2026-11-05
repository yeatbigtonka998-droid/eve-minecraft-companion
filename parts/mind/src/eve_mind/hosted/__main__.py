import sys

from .login import login, logout, status

cmd = sys.argv[1] if len(sys.argv) > 1 else ""
fn = {"login": login, "logout": logout, "status": status}.get(cmd)
if fn is None:
    print("usage: python -m eve_mind.hosted login|logout|status [options]")
    sys.exit(1)
sys.exit(fn(sys.argv[2:]))
