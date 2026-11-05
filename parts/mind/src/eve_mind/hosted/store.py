"""The token record: one protected JSON file per user, outside the repo, never logged.

Location: EVE_BRAIN_HOME, else %LOCALAPPDATA%\\eve\\hosted-brain (Windows) or ~/.local/share/eve/hosted-brain.
Files: host.json (the persistent ext_agent_host_id - not a credential), account.json (issued client id, identity,
tokens, scopes, expiry, chosen model), refresh.lock (serializes refreshes across processes). Writes are atomic
(temp file + os.replace) and the folder is owner-only (icacls on Windows, 0700 elsewhere).
"""
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

SECRET_KEYS = ("access_token", "refresh_token", "id_token")


def home():
    p = os.environ.get("EVE_BRAIN_HOME")
    if p:
        return Path(p)
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else None
    return Path(base) / "eve" / "hosted-brain" if base else Path.home() / ".local" / "share" / "eve" / "hosted-brain"


def redact(rec):
    """A record safe to print or log: token fields replaced by their presence."""
    return {k: ("<set>" if v else "<none>") if k in SECRET_KEYS else v for k, v in (rec or {}).items()}


class Store:
    def __init__(self, path=None):
        self.dir = Path(path) if path else home()

    def _ensure(self):
        if not self.dir.exists():
            self.dir.mkdir(parents=True)
            protect(self.dir)

    def _read(self, name):
        try:
            return json.loads((self.dir / name).read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def _write(self, name, obj):
        self._ensure()
        tmp = self.dir / f".{name}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.dir / name)

    def host_id(self):
        """ext_agent_host_id: stable, opaque, one per host, persisted before the first sign-in (urn:uuid form)."""
        h = self._read("host.json")
        if h and isinstance(h.get("ext_agent_host_id"), str):
            return h["ext_agent_host_id"]
        hid = f"urn:uuid:{uuid.uuid4()}"
        self._write("host.json", {"ext_agent_host_id": hid, "created": int(time.time())})
        return hid

    def account(self):
        return self._read("account.json")

    def save_account(self, rec):
        self._write("account.json", rec)

    def clear_tokens(self, why):
        """Signed out (or a refresh the server refused): tokens gone; the issued client id and identity stay, so the
        next sign-in reuses the registration."""
        rec = self.account()
        if rec is None:
            return
        for k in SECRET_KEYS + ("expires_at", "earliest_refresh_at", "scope"):
            rec.pop(k, None)
        rec["signed_out"] = {"at": int(time.time()), "why": why}
        self.save_account(rec)

    def lock(self, timeout_s=30.0, stale_s=120.0):
        return _Lock(self.dir / "refresh.lock", self._ensure, timeout_s, stale_s)


class _Lock:
    """A cross-process lock file (O_EXCL); a lock older than stale_s is taken over (a crashed holder)."""

    def __init__(self, path, ensure, timeout_s, stale_s):
        self.path, self.ensure, self.timeout_s, self.stale_s = path, ensure, timeout_s, stale_s

    def __enter__(self):
        self.ensure()
        t0 = time.monotonic()
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale_s:
                        self.path.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() - t0 > self.timeout_s:
                    raise TimeoutError(f"refresh lock {self.path} held for over {self.timeout_s:.0f} s")
                time.sleep(0.05)

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def protect(folder):
    """Owner-only: on Windows the current user alone, inheritance removed; elsewhere 0700."""
    if os.name == "nt":
        user = os.environ.get("USERNAME")
        if user:
            subprocess.run(["icacls", str(folder), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F"],
                           capture_output=True, check=False)
    else:
        os.chmod(folder, 0o700)
