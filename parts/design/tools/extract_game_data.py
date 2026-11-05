"""Download the official Minecraft jars for one version and extract the game data Eve uses.

Source of truth: Mojang's public version manifest (piston-meta). Mojang publishes SHA-1 + size per
jar, so those are verified; SHA-256 is computed locally afterwards and recorded in MANIFEST.json.

Extracted into data/game/<version>/ (gitignored except MANIFEST.json):
  recipe/ tags/            data/minecraft/{recipe,tags}/ from the client jar (cross-checked against the server jar)
  blockstates/ models/block/ textures/block/   assets/minecraft/... from the client jar
  reports/                 blocks.json, registries.json from the server's own data generator (`--reports`),
                           plus items.json = the per-item component reports merged into {item_id: components};
                           the authority for legal block states, defaults and item stack sizes
  structure/village/       Mojang's village structure NBT files (private style references for CP-58; never redistributed)
  version.json             the jar's own version metadata (data version)

Usage:  python tools/extract_game_data.py [--version 26.3] [--out DIR] [--java PATH] [--no-reports]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
TOOL_VERSION = "1"
REPO = Path(__file__).resolve().parents[3]
CLIENT_PREFIXES = {
    "recipe": "data/minecraft/recipe/",
    "tags": "data/minecraft/tags/",
    "blockstates": "assets/minecraft/blockstates/",
    "models/block": "assets/minecraft/models/block/",
    "textures/block": "assets/minecraft/textures/block/",
    "structure/village": "data/minecraft/structure/village/",   # Mojang's village builds: style references (CP-58)
}
REPORTS = ("blocks.json", "registries.json")
MAX_JSON = 16 << 20


def fetch(url: str, limit: int = MAX_JSON) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise SystemExit(f"refusing {url}: larger than {limit} bytes")
    return data


def download(url: str, dest: Path, sha1: str, size: int) -> str:
    """Download (or reuse a cached copy) and verify size + SHA-1 from the manifest. Returns SHA-256."""
    if not (dest.exists() and dest.stat().st_size == size and _hash(dest, "sha1") == sha1):
        tmp = dest.with_suffix(".part")
        h, n = hashlib.sha1(), 0
        with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                n += len(chunk)
                if n > size:
                    raise SystemExit(f"{url}: more bytes than the manifest's size {size}")
                h.update(chunk)
                f.write(chunk)
        if n != size or h.hexdigest() != sha1:
            tmp.unlink()
            raise SystemExit(f"{url}: size/sha1 mismatch (got {n}, {h.hexdigest()}; manifest {size}, {sha1})")
        tmp.replace(dest)
    return _hash(dest, "sha256")


def _hash(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(root: Path) -> tuple[int, str]:
    """(file count, sha256 over sorted 'relpath NUL sha256(file) LF' lines). Lets a reader check an extract."""
    lines = sorted(
        f"{p.relative_to(root).as_posix()}\0{_hash(p, 'sha256')}\n" for p in root.rglob("*") if p.is_file()
    )
    return len(lines), hashlib.sha256("".join(lines).encode()).hexdigest()


def extract(jar: zipfile.ZipFile, prefix: str, dest: Path) -> None:
    for info in jar.infolist():
        name = info.filename
        if info.is_dir() or not name.startswith(prefix):
            continue
        rel = name[len(prefix):]
        target = (dest / rel).resolve()
        if ".." in Path(rel).parts or dest.resolve() not in target.parents:  # zip-slip guard
            raise SystemExit(f"unsafe path in jar: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(jar.read(info))


def prefix_digest(jar: zipfile.ZipFile, prefix: str) -> tuple[int, str]:
    entries = sorted(
        (i.filename[len(prefix):], hashlib.sha256(jar.read(i)).hexdigest())
        for i in jar.infolist() if i.filename.startswith(prefix) and not i.is_dir()
    )
    return len(entries), hashlib.sha256("".join(f"{a}\0{b}\n" for a, b in entries).encode()).hexdigest()


def find_java(major: int) -> str | None:
    cands = [os.environ.get("EVE_JAVA", "")]
    for base in (Path(r"C:\Program Files\Eclipse Adoptium"), Path(r"C:\Program Files\Java")):
        if base.exists():
            cands += [str(p / "bin" / "java.exe") for p in sorted(base.glob(f"jdk-{major}*"))]
    cands.append(shutil.which("java") or "")
    for c in cands:
        if c and Path(c).exists():
            out = subprocess.run([c, "-version"], capture_output=True, text=True).stderr
            if f'version "{major}' in out:
                return c
    return None


def run_reports(java: str, server_jar: Path, dest: Path) -> dict:
    """Run the server's built-in data generator in an empty temp dir; copy the reports we use."""
    with tempfile.TemporaryDirectory(prefix="eve-datagen-") as wd:
        cmd = [java, "-DbundlerMainClass=net.minecraft.data.Main", "-jar", str(server_jar),
               "--reports", "--output", "generated"]
        t0 = time.time()
        p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, timeout=900)
        if p.returncode != 0:
            raise SystemExit(f"data generator failed ({p.returncode}):\n{p.stderr[-2000:]}")
        dest.mkdir(parents=True, exist_ok=True)
        gen = Path(wd, "generated", "reports")
        for name in REPORTS:
            shutil.copyfile(gen / name, dest / name)
        items = {f"minecraft:{p.stem}": json.loads(p.read_text())["components"]
                 for p in sorted((gen / "minecraft" / "components" / "item").glob("*.json"))}
        (dest / "items.json").write_text(json.dumps(items, indent=0, sort_keys=True))
        ver = subprocess.run([java, "-version"], capture_output=True, text=True).stderr.splitlines()[0]
        return {"command": "java -DbundlerMainClass=net.minecraft.data.Main -jar server.jar --reports",
                "java": ver, "seconds": round(time.time() - t0, 1), "files": [*REPORTS, "items.json"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="26.3")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--java")
    ap.add_argument("--no-reports", action="store_true")
    a = ap.parse_args()
    out = (a.out or REPO / "data" / "game" / a.version).resolve()
    jars = out / "jars"
    jars.mkdir(parents=True, exist_ok=True)

    vm = json.loads(fetch(MANIFEST_URL))
    entry = next((v for v in vm["versions"] if v["id"] == a.version), None)
    if not entry:
        raise SystemExit(f"version {a.version} not in Mojang's manifest")
    vjson_bytes = fetch(entry["url"])
    if hashlib.sha1(vjson_bytes).hexdigest() != entry["sha1"]:
        raise SystemExit("version JSON sha1 mismatch")
    vjson = json.loads(vjson_bytes)

    jar_info = {}
    for side in ("client", "server"):
        d = vjson["downloads"][side]
        path = jars / f"{side}-{a.version}.jar"
        sha256 = download(d["url"], path, d["sha1"], d["size"])
        jar_info[side] = {"url": d["url"], "size": d["size"], "sha1_manifest": d["sha1"],
                          "sha1_verified": True, "sha256_local": sha256}
        print(f"{side}: verified size+sha1, sha256 {sha256}")

    for sub in list(CLIENT_PREFIXES) + ["reports"]:
        shutil.rmtree(out / sub, ignore_errors=True)
    with zipfile.ZipFile(jars / f"client-{a.version}.jar") as cj:
        for sub, prefix in CLIENT_PREFIXES.items():
            extract(cj, prefix, out / sub)
        jar_version = json.loads(cj.read("version.json"))
        (out / "version.json").write_text(json.dumps(jar_version, indent=1))
        client_data = {k: prefix_digest(cj, CLIENT_PREFIXES[k]) for k in ("recipe", "tags")}

    # The server jar is a bundler; the real server jar (with data/) sits inside it.
    with zipfile.ZipFile(jars / f"server-{a.version}.jar") as sj:
        inner = next(n for n in sj.namelist() if n.startswith("META-INF/versions/") and n.endswith(".jar"))
        with zipfile.ZipFile(io.BytesIO(sj.read(inner))) as ij:
            server_data = {k: prefix_digest(ij, CLIENT_PREFIXES[k]) for k in ("recipe", "tags")}
    if client_data != server_data:
        raise SystemExit(f"client/server data differ: {client_data} vs {server_data}")

    reports = None
    if not a.no_reports:
        java = a.java or find_java(vjson["javaVersion"]["majorVersion"])
        if not java:
            raise SystemExit(f"need Java {vjson['javaVersion']['majorVersion']} for --reports (or pass --no-reports)")
        reports = run_reports(java, jars / f"server-{a.version}.jar", out / "reports")

    extracted = {}
    for sub in list(CLIENT_PREFIXES) + (["reports"] if reports else []):
        n, h = tree_hash(out / sub)
        extracted[sub] = {"files": n, "tree_sha256": h}
    manifest = {
        "format": "eve-game-data/1",
        "version": a.version,
        "release_time": vjson["releaseTime"],
        "data_version": jar_version.get("world_version"),
        "java_major": vjson["javaVersion"]["majorVersion"],
        "sources": {"version_manifest": MANIFEST_URL, "version_json": entry["url"],
                    "version_json_sha1": entry["sha1"], "jars": jar_info},
        "client_server_data_identical": {k: {"files": v[0], "sha256": v[1]} for k, v in client_data.items()},
        "extracted": extracted,
        "reports": reports,
        "hash_note": "Mojang's manifest publishes SHA-1 + size only; both verified. sha256_local is computed after verification.",
        "tree_sha256_rule": "sha256 over sorted lines 'relpath NUL sha256(file) LF' (relpath posix, relative to the folder)",
        "tool": {"path": "parts/design/tools/extract_game_data.py", "version": TOOL_VERSION},
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(extracted, indent=1))


if __name__ == "__main__":
    sys.exit(main())
