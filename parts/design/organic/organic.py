"""Organic path (CP-68..72, optional): mesh -> blocks for statues, trees and rock shapes, emitted as ordinary design
folders (design.json, design.litematic, scaffold.json, bom.json, validation.json, previews/, build_order.json, gate.json)
so the builder consumes them unchanged. Uses eve_design read-only: its Design, validators, BOM, scaffold planner, build
order, previews and gate are called exactly as `python -m eve_design import` calls them. Nothing here edits eve_design.

Run with the design venv (numpy + eve_design installed), from parts/design:
  py=.venv/Scripts/python
  $py organic/organic.py mesh --generate tree --seed 1 --out JOB            # or --src FILE.obj|.glb [--up z]
  $py organic/organic.py mesh --image IMG.png --provider fake|local-cmd --out JOB [--timeout 600 --cap-usd 0 --retries 1]
  $py organic/organic.py build JOB --height 10 --out DIR [--yaw 90] [--hollow 2] [--clean] [--palette tree] [--plinth]
                                   [--origin x,y,z] [--brief brief.json] [--project ID]
  $py organic/organic.py combine ARCH_DIR ORG_DIR --at dx,dy,dz [--yaw 90] --brief brief.json --out DIR
  $py organic/organic.py examples                                           # re-emit organic/examples/
Exit codes: 0 ok, 2 bad input / job refused or failed, 3 the design failed a check (floating, collision, validation)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from eve_design import bom, buildorder, gamedata, gate, preview, scaffold
from eve_design.grid import AIR, MAX_VOLUME, Design, rotated
from eve_design.schem_io import save_litematic
from eve_design.validate import validate

PIPELINE = "eve-organic/1"
HERE = Path(__file__).resolve().parent
MAX_FILE = 256 << 20          # bytes of mesh file read at all
MAX_FACES = 2_000_000
MAX_DIM = 64                  # blocks per axis for one organic piece (the programme's statue is ~40)
MAX_PAIRS = 40_000_000        # triangle x ray-column pairs, the voxelizer's work bound (checked before any work)
CHUNK = 1_000_000


class OrganicError(Exception):
    """Bad input or a refused/failed job: exit 2."""


# ---------------------------------------------------------------------------------------------- mesh I/O
def load_mesh(path: Path) -> tuple[np.ndarray, np.ndarray]:
    path = Path(path)
    if path.stat().st_size > MAX_FILE:
        raise OrganicError(f"{path.name}: {path.stat().st_size} bytes > {MAX_FILE} limit")
    ext = path.suffix.lower()
    try:
        if ext == ".obj":
            V, F = _obj(path.read_text(errors="replace"))
        elif ext == ".glb":
            V, F = _glb(path.read_bytes())
        else:
            raise OrganicError(f"{path.name}: unsupported mesh format (OBJ or GLB)")
    except (ValueError, KeyError, IndexError, struct.error, json.JSONDecodeError) as e:
        raise OrganicError(f"{path.name}: malformed mesh ({type(e).__name__}: {e})") from None
    return _sane(V, F)


def _sane(V, F):
    V, F = np.asarray(V, float).reshape(-1, 3), np.asarray(F, np.int64).reshape(-1, 3)
    if not len(F):
        raise OrganicError("mesh has no triangles")
    if len(F) > MAX_FACES:
        raise OrganicError(f"{len(F)} triangles > {MAX_FACES} limit")
    if not np.isfinite(V).all():
        raise OrganicError("mesh has non-finite vertex coordinates")
    if F.min() < 0 or F.max() >= len(V):
        raise OrganicError("mesh face refers to a vertex that does not exist")
    return V, F


def _obj(text):
    V, F = [], []
    for line in text.splitlines():
        t = line.split()
        if not t:
            continue
        if t[0] == "v":
            V.append([float(c) for c in t[1:4]])
        elif t[0] == "f":
            idx = [int(c.split("/")[0]) for c in t[1:]]
            if len(idx) < 3:
                raise ValueError("face with fewer than 3 vertices")
            idx = [i - 1 if i > 0 else len(V) + i for i in idx]
            F += [[idx[0], idx[k], idx[k + 1]] for k in range(1, len(idx) - 1)]
            if len(F) > MAX_FACES:
                raise OrganicError(f"more than {MAX_FACES} triangles")
    return V, F


def _glb(b: bytes):
    magic, ver, total = struct.unpack_from("<4sII", b, 0)
    if magic != b"glTF" or ver != 2:
        raise OrganicError("not a glTF 2.0 binary (.glb)")
    js = blob = None
    off = 12
    while off + 8 <= min(total, len(b)):
        ln, typ = struct.unpack_from("<I4s", b, off)
        chunk = b[off + 8: off + 8 + ln]
        off += 8 + ln
        if typ == b"JSON":
            js = json.loads(chunk)
        elif typ == b"BIN\0":
            blob = chunk
    if js is None:
        raise OrganicError("glb has no JSON chunk")
    if js.get("extensionsRequired"):   # Draco / meshopt compression etc.
        raise OrganicError(f"glb needs extensions {js['extensionsRequired']}: not supported (export uncompressed)")

    def acc(i, comps):
        a = js["accessors"][i]
        if "sparse" in a or "bufferView" not in a:
            raise OrganicError("sparse or empty glTF accessors are not supported")
        bv = js["bufferViews"][a["bufferView"]]
        if bv.get("buffer", 0) != 0 or blob is None or "uri" in js["buffers"][0]:
            raise OrganicError("external glTF buffers are not supported")
        dt = np.dtype({5121: "u1", 5123: "<u2", 5125: "<u4", 5126: "<f4"}[a["componentType"]])
        n, item = a["count"], dt.itemsize * comps
        stride = bv.get("byteStride") or item
        start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        if n and start + stride * (n - 1) + item > len(blob):
            raise OrganicError("glTF accessor runs past the binary chunk")
        return np.ndarray((n, comps), dt, blob, start, (stride, dt.itemsize)).astype(np.float64 if comps == 3 else np.int64)

    def local(n):
        if "matrix" in n:
            return np.array(n["matrix"], float).reshape(4, 4).T
        x, y, z, w = n.get("rotation", [0, 0, 0, 1])
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        M = np.eye(4)
        M[:3, :3] = R * np.array(n.get("scale", [1, 1, 1]), float)
        M[:3, 3] = n.get("translation", [0, 0, 0])
        return M

    nodes = js.get("nodes", [])
    kids = {c for n in nodes for c in n.get("children", [])}
    scenes = js.get("scenes")
    roots = scenes[js.get("scene", 0)]["nodes"] if scenes else [i for i in range(len(nodes)) if i not in kids]
    Vs, Fs, nv, stack, seen = [], [], 0, [(i, np.eye(4)) for i in roots], 0
    while stack:
        i, parent = stack.pop()
        seen += 1
        if seen > 100_000:
            raise OrganicError("glTF node graph too large or cyclic")
        n = nodes[i]
        M = parent @ local(n)
        stack += [(c, M) for c in n.get("children", [])]
        if "mesh" not in n:
            continue
        for prim in js["meshes"][n["mesh"]]["primitives"]:
            if prim.get("mode", 4) != 4:
                continue                      # ponytail: points/lines/strips skipped; image-to-3D tools emit triangles
            P = acc(prim["attributes"]["POSITION"], 3)
            I = acc(prim["indices"], 1).ravel() if "indices" in prim else np.arange(len(P))
            Vs.append(P @ M[:3, :3].T + M[:3, 3])
            Fs.append(I.reshape(-1, 3) + nv)
            nv += len(P)
    if not Vs:
        raise OrganicError("glb contains no triangle meshes")
    return np.concatenate(Vs), np.concatenate(Fs)


def write_obj(V, F, path: Path) -> str:
    lines = ["# eve-organic normalized mesh: y up, min y = 0, centred in x/z, height 1"]
    lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in V]
    lines += [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in F]
    path.write_text("\n".join(lines) + "\n", newline="\n")
    return sha256(path)


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------------------------- mesh checks
def weld(V, F):
    """Merge coincident vertices (UV seams split them) and drop degenerate triangles."""
    span = float(np.ptp(V, axis=0).max()) or 1.0
    q = np.round(V / (span * 1e-6)).astype(np.int64)
    _, first, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
    V, F = V[first], inv.reshape(-1)[F]
    area2 = np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1)
    keep = (F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2]) & (area2 > span * span * 1e-12)
    return V, F[keep]


def components(F, nv) -> np.ndarray:
    """Per-face id of the connected shell it belongs to (shells sharing a vertex are one component)."""
    lab = np.arange(nv)
    while True:
        m = lab[F].min(axis=1)
        new = lab.copy()
        for k in range(3):
            np.minimum.at(new, F[:, k], m)
        new = new[new]
        if (new == lab).all():
            break
        lab = new
    return np.unique(lab[F[:, 0]], return_inverse=True)[1].ravel()


def edge_report(F) -> dict:
    e = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    cnt = np.unique(np.sort(e, axis=1), axis=0, return_counts=True)[1]
    dcnt = np.unique(e, axis=0, return_counts=True)[1]
    r = {"boundary_edges": int((cnt == 1).sum()), "nonmanifold_edges": int((cnt > 2).sum()),
         "misoriented_edges": int((dcnt > 1).sum())}
    r["watertight"] = not any(r.values())
    return r


def normalize(V, F, up="y"):
    """y up, min y = 0, centred in x/z, height 1. Returns (V, F, comp, report)."""
    if up == "z":
        V = np.column_stack([V[:, 0], V[:, 2], -V[:, 1]])
    V, F = weld(V, F)
    if not len(F):
        raise OrganicError("mesh has no non-degenerate triangles")
    lo, hi = V.min(0), V.max(0)
    h = hi[1] - lo[1]
    if h <= 1e-9 * max(1.0, float(np.ptp(V, axis=0).max())):
        raise OrganicError("mesh is flat (zero height): ambiguous scale")
    V = (V - [(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2]) / h
    comp = components(F, len(V))
    shells = [edge_report(F[comp == c]) for c in range(comp.max() + 1)]
    return V, F, comp, {"source_size": [float(v) for v in hi - lo], "up": up, "vertices": len(V), "faces": len(F),
                        "shells": len(shells), "watertight": all(s["watertight"] for s in shells),
                        "open_shells": sum(not s["watertight"] for s in shells), "edges": edge_report(F)}


# ---------------------------------------------------------------------------------------------- generated meshes
def box(lo=(0, 0, 0), hi=(1, 1, 1)):
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    V = [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]
    F = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [3, 6, 2], [3, 7, 6], [0, 4, 7], [0, 7, 3],
         [1, 2, 6], [1, 6, 5]]
    return np.array(V, float), np.array(F)


def sphere(c=(0, 0, 0), r=1.0, sub=3, squash=(1, 1, 1)):
    t = (1 + 5 ** 0.5) / 2
    V = [[-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0], [0, -1, t], [0, 1, t], [0, -1, -t], [0, 1, -t],
         [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1]]
    F = [[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6],
         [7, 1, 8], [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7],
         [9, 8, 1]]
    V = [list(np.array(v) / np.linalg.norm(v)) for v in V]
    for _ in range(sub):
        mid, nf = {}, []

        def m(a, b):
            k = (min(a, b), max(a, b))
            if k not in mid:
                p = (np.array(V[a]) + V[b]) / 2
                V.append(list(p / np.linalg.norm(p)))
                mid[k] = len(V) - 1
            return mid[k]
        for a, b, c_ in F:
            ab, bc, ca = m(a, b), m(b, c_), m(c_, a)
            nf += [[a, ab, ca], [b, bc, ab], [c_, ca, bc], [ab, bc, ca]]
        F = nf
    return np.array(V) * r * np.array(squash) + c, np.array(F)


def cylinder(p0, p1, r, n=16):
    p0, p1 = np.array(p0, float), np.array(p1, float)
    ax = p1 - p0
    ax /= np.linalg.norm(ax)
    u = np.cross(ax, [1, 0, 0] if abs(ax[0]) < 0.9 else [0, 1, 0])
    u /= np.linalg.norm(u)
    w = np.cross(ax, u)
    ring = [r * (np.cos(a) * u + np.sin(a) * w) for a in np.linspace(0, 2 * np.pi, n, endpoint=False)]
    V = [p0 + q for q in ring] + [p1 + q for q in ring] + [p0, p1]
    F = []
    for i in range(n):
        j = (i + 1) % n
        F += [[i, j, n + j], [i, n + j, n + i], [2 * n, j, i], [2 * n + 1, n + i, n + j]]
    return np.array(V), np.array(F)


def torus(R=1.0, r=0.35, n=32, m=16):
    V, F = [], []
    for i in range(n):
        a = 2 * np.pi * i / n
        for j in range(m):
            b = 2 * np.pi * j / m
            V.append([(R + r * np.cos(b)) * np.cos(a), r * np.sin(b), (R + r * np.cos(b)) * np.sin(a)])
    for i in range(n):
        for j in range(m):
            a, b = i * m + j, ((i + 1) % n) * m + j
            c, d = ((i + 1) % n) * m + (j + 1) % m, i * m + (j + 1) % m
            F += [[a, d, c], [a, c, b]]
    return np.array(V, float), np.array(F)


def merge(*parts):
    Vs, Fs, n = [], [], 0
    for V, F in parts:
        Vs.append(V)
        Fs.append(F + n)
        n += len(V)
    return np.concatenate(Vs), np.concatenate(Fs)


def tree(seed=1, branch_r=0.62):
    """A broadleaf tree: trunk, three branches, five canopy blobs. Overlapping closed shells (voxelized per shell)."""
    rng = np.random.default_rng(seed)
    parts = [cylinder((0, 0, 0), (0, 6.6, 0), 0.75, 12)]           # runs up into the crown blob
    tips = [(0, 7.2, 0)]
    for k in range(3):
        a = 2 * np.pi * (k / 3 + rng.uniform(-0.08, 0.08))
        tip = (2.6 * np.cos(a), 6.0 + rng.uniform(-0.3, 0.3), 2.6 * np.sin(a))
        parts.append(cylinder((0, 3.8 + 0.4 * k, 0), tip, branch_r, 8))
        tips.append(tip)
    for i, t in enumerate(tips):
        parts.append(sphere(t, 2.0 if i == 0 else 1.7 + rng.uniform(0, 0.3), 2, (1, 0.8, 1)))
    parts.append(sphere((rng.uniform(-1, 1), 8.4, rng.uniform(-1, 1)), 1.5, 2, (1, 0.8, 1)))
    return merge(*parts)


def statue(seed=1):
    """A standing guardian on a low round base: legs, cloaked body, shoulders, arms resting on a sword, head."""
    rng = np.random.default_rng(seed)
    lean = rng.uniform(-0.15, 0.15)
    return merge(cylinder((0, 0, 0), (0, 0.8, 0), 2.4, 20),                         # base
                 cylinder((-0.7, 0.5, 0), (-0.6 + lean, 4.6, 0), 0.75, 10),         # legs
                 cylinder((0.7, 0.5, 0), (0.6 + lean, 4.6, 0), 0.75, 10),
                 sphere((lean, 6.2, 0), 1.9, 2, (1, 1.25, 0.7)),                    # body / cloak
                 sphere((lean, 7.9, 0), 1.8, 2, (1.35, 0.6, 0.8)),                  # shoulders
                 cylinder((-2.2 + lean, 7.9, 0), (-1.2 + lean, 5.0, 0.9), 0.65, 8),  # arms to the hilt
                 cylinder((2.2 + lean, 7.9, 0), (1.2 + lean, 5.0, 0.9), 0.65, 8),
                 cylinder((lean, 0.6, 1.5), (lean, 5.6, 1.2), 0.5, 8),              # sword, point on the base
                 sphere((lean, 9.6, 0), 1.15, 2))                                   # head


GENERATORS = {"tree": lambda s: tree(s), "statue": lambda s: statue(s), "box": lambda s: box((0, 0, 0), (2, 3, 1)),
              "sphere": lambda s: sphere(), "torus": lambda s: torus()}


# ---------------------------------------------------------------------------------------------- controlled mesh job (CP-68)
class ProviderError(Exception):
    pass


class FakeImageToMesh:
    """Deterministic stand-in, labelled fake: NOT a reconstruction. Exercises the job controls (timeout, failure,
    duplicates, price limit) without a real provider; its meshes can never gate a real build (gate = hold)."""
    name, version = "fake", "1"

    def __init__(self, delay_s=0.0, fail=False, usd_worst=0.0):
        self.delay_s, self.fail, self.usd_worst, self.calls = delay_s, fail, usd_worst, 0

    def run(self, image: bytes, timeout_s: float):
        self.calls += 1
        if self.fail:
            raise ProviderError("provider reported job failure")
        if self.delay_s > timeout_s:
            raise TimeoutError(f"no result within {timeout_s} s")
        h = hashlib.sha256(image).digest()
        return sphere(r=0.6 + h[0] / 640, sub=2, squash=(1, 1.3 + h[1] / 512, 1))


class LocalCmd:
    """A local image-to-3D tool (e.g. TripoSR, STATUS.md) run as `EVE_ORGANIC_IMG2MESH_CMD` with {image} and {out}
    (an .obj or .glb path). Free; killed at the timeout."""
    name, version, usd_worst = "local-cmd", "1", 0.0   # TripoSR output is Z-up: submit with up="z" (CLI --up z)

    def __init__(self):
        self.cmd = os.environ.get("EVE_ORGANIC_IMG2MESH_CMD")
        self.calls = 0

    def run(self, image: bytes, timeout_s: float):
        if not self.cmd:
            raise ProviderError("no local image-to-3D tool configured (EVE_ORGANIC_IMG2MESH_CMD; none installed on Shark)")
        self.calls += 1
        tmp = Path(os.environ.get("TEMP", ".")) / f"eve-organic-{hashlib.sha256(image).hexdigest()[:12]}"
        tmp.mkdir(exist_ok=True)
        (tmp / "in.png").write_bytes(image)
        out = tmp / "out.obj"
        out.unlink(missing_ok=True)                     # never read a previous run's mesh
        Path(str(out) + ".json").unlink(missing_ok=True)
        try:
            r = subprocess.run(self.cmd.format(image=tmp / "in.png", out=out), shell=True, timeout=timeout_s,
                               capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"no result within {timeout_s} s") from None
        if r.returncode or not out.exists():
            raise ProviderError(f"tool exit {r.returncode}: {r.stderr[-300:]}")
        side = Path(str(out) + ".json")                 # the tool's own provenance (triposr_run.py writes it)
        self.meta = json.loads(side.read_text()) if side.exists() else None
        return load_mesh(out)


PROVIDERS = {"fake": FakeImageToMesh, "local-cmd": LocalCmd}


def mesh_job(out: Path, *, src: Path | None = None, generate: str | None = None, seed: int = 1,
             image: Path | None = None, provider=None, up="y", timeout_s=600.0, cap_usd=0.0, retries=1) -> dict:
    """One controlled mesh job -> out/mesh.obj + out/job.json. Same inputs = same job id: a finished job is returned
    without running again; a running one (lock file) refuses a second submission. Each attempt reserves the provider's
    worst-case price first and stops when the next reservation would pass cap_usd (a timed-out paid attempt stays
    counted as spent). Paid providers need the orchestrator's OK first: none is wired here."""
    out = Path(out)
    if sum(x is not None for x in (src, generate, image)) != 1:
        raise OrganicError("give exactly one of --src, --generate, --image")
    if src is not None:
        source = {"kind": "file", "name": Path(src).name, "sha256": sha256(src), "bytes": Path(src).stat().st_size}
        tool = {"name": "file", "version": None}
    elif generate is not None:
        if generate not in GENERATORS:
            raise OrganicError(f"unknown generator {generate!r} ({', '.join(GENERATORS)})")
        source = {"kind": "generated", "generator": generate, "seed": seed}
        tool = {"name": f"organic.{generate}", "version": PIPELINE}
    else:
        data = Path(image).read_bytes()
        source = {"kind": "image", "name": Path(image).name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        tool = {"name": provider.name, "version": provider.version, "fake": provider.name == "fake"}
    params = {"up": up, "timeout_s": timeout_s, "cap_usd": cap_usd, "retries": retries}
    job_id = hashlib.sha256(json.dumps([source, tool, params], sort_keys=True).encode()).hexdigest()[:16]
    jf = out / "job.json"
    if jf.exists():
        old = json.loads(jf.read_text())
        if old.get("job_id") == job_id and old.get("status") == "ok" and (out / "mesh.obj").exists():
            return {**old, "duplicate": True}
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".lock"
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        raise OrganicError(f"job {job_id} already running in {out} (duplicate submission refused)") from None
    job = {"pipeline": PIPELINE, "job_id": job_id, "source": source, "tool": tool, "params": params, "attempts": [],
           "usd": {"cap": cap_usd, "reserved": 0.0}, "status": None}
    try:
        V = F = None
        if src is not None:
            V, F = load_mesh(src)
        elif generate is not None:
            V, F = GENERATORS[generate](seed)
        else:
            for n in range(1 + retries):
                worst = float(provider.usd_worst)
                if job["usd"]["reserved"] + worst > cap_usd:
                    job["attempts"].append({"n": n + 1, "outcome": "refused_budget",
                                            "why": f"worst case US${worst} would pass the cap US${cap_usd}"})
                    break
                job["usd"]["reserved"] += worst
                t0 = time.monotonic()
                try:
                    V, F = provider.run(data, timeout_s)
                    job["attempts"].append({"n": n + 1, "outcome": "ok", "usd_worst": worst, "s": round(time.monotonic() - t0, 3)})
                    job["tool"]["run"] = getattr(provider, "meta", None)
                    break
                except TimeoutError as e:
                    job["attempts"].append({"n": n + 1, "outcome": "timeout", "usd_worst": worst, "why": str(e)})
                except (ProviderError, OrganicError) as e:
                    job["attempts"].append({"n": n + 1, "outcome": "failed", "usd_worst": worst, "why": str(e)})
            if V is None:
                job["status"] = job["attempts"][-1]["outcome"] if job["attempts"] else "failed"
                jf.write_text(json.dumps(job, indent=1) + "\n")
                raise OrganicError(f"mesh job {job_id}: {job['status']} ({job['attempts'][-1].get('why', '')})")
        V, F = _sane(V, F)
        V, F, _, rep = normalize(V, F, up)
        job["output"] = {"mesh": "mesh.obj", "sha256": write_obj(V, F, out / "mesh.obj"), **rep}
        job["status"] = "ok"
        jf.write_text(json.dumps(job, indent=1) + "\n")
        return job
    finally:
        lock.unlink(missing_ok=True)


def load_job(job_dir: Path):
    job = json.loads((Path(job_dir) / "job.json").read_text())
    if job.get("status") != "ok":
        raise OrganicError(f"mesh job {job.get('job_id')} status {job.get('status')}")
    if sha256(Path(job_dir) / "mesh.obj") != job["output"]["sha256"]:
        raise OrganicError("mesh.obj does not match the hash in job.json")
    V, F = load_mesh(Path(job_dir) / "mesh.obj")
    return job, V, F


# ---------------------------------------------------------------------------------------------- voxelize (CP-69)
def voxelize(V, F, *, height=None, width=None, depth=None, yaw=0, max_dim=MAX_DIM, max_pairs=MAX_PAIRS):
    """Occupancy [y, z, x]: a cell is solid when its centre is inside the mesh (ray parity along x, y and z, majority
    vote; each shell separately, then OR, so overlapping closed shells and small holes are handled). Exactly one target
    size in blocks; every bound is checked before any array is made. Returns (occ, report)."""
    target = {k: v for k, v in (("width", width), ("height", height), ("depth", depth)) if v}
    if len(target) != 1:
        raise OrganicError("ambiguous scale: give exactly one of height/width/depth in blocks")
    (k, t), = target.items()
    if yaw % 90:
        raise OrganicError("yaw must be a multiple of 90")
    for _ in range(yaw // 90 % 4):                      # clockwise from above, like grid.rotated
        V = np.column_stack([-V[:, 2], V[:, 1], V[:, 0]])
    V, F = weld(V, F)
    ext = V.max(0) - V.min(0)
    ax = {"width": 0, "height": 1, "depth": 2}[k]
    if ext[ax] <= 0:
        raise OrganicError(f"mesh has zero {k}: ambiguous scale")
    s = t / ext[ax]
    dims = np.maximum(1, np.ceil(ext * s - 1e-6)).astype(int)          # x, y, z
    if dims.max() > max_dim or int(np.prod(dims)) > MAX_VOLUME:
        raise OrganicError(f"{dims.tolist()} blocks exceeds the limit ({max_dim} per axis, {MAX_VOLUME} cells)")
    P = (V - V.min(0)) * s + (dims - ext * s) / 2
    comp = components(F, len(V))
    pairs = sum(_pairs(P, F, dims, a)[2].sum() for a in range(3))
    if pairs > max_pairs:
        raise OrganicError(f"voxelizer work {pairs} triangle-ray pairs > limit {max_pairs} (simplify the mesh)")
    occ = np.zeros(dims, bool)
    disagree = 0
    for c in range(comp.max() + 1):
        Fc = F[comp == c]
        votes = sum(_parity(P, Fc, dims, a).astype(np.uint8) for a in range(3))
        occ |= votes >= 2
        disagree += int(((votes == 1) | (votes == 2)).sum())
    occ = occ.transpose(1, 2, 0).copy()                 # [x, y, z] -> [y, z, x]
    return occ, {"target": {k: t}, "scale_blocks_per_unit": float(s), "yaw": yaw, "size": dims.tolist(),
                 "solid": int(occ.sum()), "axis_disagreements": disagree, "shells": int(comp.max() + 1)}


J = (1.3e-4, 0.7e-4, 1.1e-4)     # ray offsets: no ray passes exactly through a vertex or edge of an aligned mesh


def _pairs(P, F, dims, a):
    u, v = [i for i in range(3) if i != a]
    T = P[F]
    lo = [np.clip(np.ceil(T[:, :, i].min(1) - 0.5 - J[i]), 0, dims[i]).astype(np.int64) for i in (u, v)]
    hi = [np.clip(np.floor(T[:, :, i].max(1) - 0.5 - J[i]) + 1, 0, dims[i]).astype(np.int64) for i in (u, v)]
    nu, nv = np.maximum(hi[0] - lo[0], 0), np.maximum(hi[1] - lo[1], 0)
    return lo, (nu, nv), nu * nv


def _parity(P, F, dims, a):
    u, v = [i for i in range(3) if i != a]
    (lu, lv), (nu, nv), cnt = _pairs(P, F, dims, a)
    hits = np.zeros((dims[u], dims[v], dims[a] + 1), np.int32)
    ends = np.cumsum(cnt)
    t0 = 0
    while t0 < len(F):                                  # chunks of about CHUNK pairs
        t1 = max(t0 + 1, int(np.searchsorted(ends, (ends[t0 - 1] if t0 else 0) + CHUNK)))
        c = cnt[t0:t1]
        t = np.repeat(np.arange(t0, t1), c)
        k = np.arange(c.sum()) - np.repeat(np.cumsum(c) - c, c)
        iu, iv = lu[t] + k // nv[t], lv[t] + k % nv[t]
        pu, pv = iu + 0.5 + J[u], iv + 0.5 + J[v]
        A, B, C = P[F[t, 0]], P[F[t, 1]], P[F[t, 2]]
        bu, bv, cu, cv = B[:, u] - A[:, u], B[:, v] - A[:, v], C[:, u] - A[:, u], C[:, v] - A[:, v]
        d = bu * cv - cu * bv
        ok = np.abs(d) > 1e-12
        d = np.where(ok, d, 1.0)
        w1 = ((pu - A[:, u]) * cv - cu * (pv - A[:, v])) / d
        w2 = (bu * (pv - A[:, v]) - (pu - A[:, u]) * bv) / d
        ok &= (w1 >= 0) & (w2 >= 0) & (w1 + w2 <= 1)
        wa = A[:, a] + w1 * (B[:, a] - A[:, a]) + w2 * (C[:, a] - A[:, a]) - J[a]
        s = np.clip(np.floor(wa - 0.5) + 1, 0, dims[a]).astype(np.int64)
        np.add.at(hits, (iu[ok], iv[ok], s[ok]), 1)
        t0 = t1
    inside = (np.cumsum(hits, axis=2)[:, :, :dims[a]] & 1).astype(bool)
    order = [u, v, a]
    return inside.transpose(np.argsort(order))         # -> [x, y, z]


# ---------------------------------------------------------------------------------------------- grid analysis
N6 = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
N26 = tuple((a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1) if a or b or c)


def shift(a, d, fill=False):
    """b[p] = a[p + d] ([y, z, x] offsets); cells beyond the grid read `fill`."""
    out = np.full_like(a, fill)
    src = tuple(slice(max(k, 0), n + min(k, 0)) for k, n in zip(d, a.shape))
    dst = tuple(slice(max(-k, 0), n + min(-k, 0)) for k, n in zip(d, a.shape))
    out[dst] = a[src]
    return out


def flood(seed, allowed, nbrs=N6):
    r = seed & allowed
    while True:
        n = r.copy()
        for d in nbrs:
            n |= shift(r, d) & allowed
        if (n == r).all():
            return r
        r = n


def label(occ):
    """6-connected components: (labels with -1 for air, count)."""
    big = occ.size
    lab = np.where(occ, np.arange(occ.size).reshape(occ.shape), big)
    while True:
        n = lab.copy()
        for d in N6:
            n = np.minimum(n, np.where(occ, shift(lab, d, big), big))
        n = np.where(occ, n, big)
        if (n == lab).all():
            break
        lab = n
    ids, inv = np.unique(lab[occ], return_inverse=True)
    out = np.full(occ.shape, -1)
    out[occ] = inv
    return out, len(ids)


def grounded(occ):
    base = np.zeros_like(occ)
    base[0] = occ[0]
    return flood(base, occ)


def exterior_air(occ):
    """Air reachable from outside the grid (the ground below y=0 counts as solid)."""
    air = ~occ
    edge = np.zeros_like(occ)
    edge[-1], edge[:, 0], edge[:, -1], edge[:, :, 0], edge[:, :, -1] = True, True, True, True, True
    return flood(edge & air, air)


def floating(occ) -> list[dict]:
    lab, n = label(occ & ~grounded(occ))
    out = []
    for i in range(n):
        ys, zs, xs = np.nonzero(lab == i)
        out.append({"blocks": int(len(ys)), "at": [int(xs[0]), int(ys[0]), int(zs[0])]})
    return sorted(out, key=lambda c: -c["blocks"])


def bridge(occ, rounds=64):
    """Thin diagonal features voxelize into cells that touch only along an edge: the builder needs a face. For each
    ungrounded cell edge-adjacent to a grounded one, add the lower of the two in-between cells. Returns (occ, added)."""
    added = 0
    for _ in range(rounds):
        g = grounded(occ)
        f = occ & ~g
        if not f.any():
            break
        new = np.zeros_like(occ)
        for d in N26:
            if sum(map(abs, d)) != 2:
                continue
            touch = f & shift(g, d)                      # cell p floating, p + d grounded, only an edge shared
            steps = sorted((s for s in ((d[0], 0, 0), (0, d[1], 0), (0, 0, d[2])) if any(s)), key=lambda s: s[0])
            for s in steps[:1]:                          # dy = -1 first: the cell below p (it rests on more)
                new |= shift(touch, tuple(-k for k in s)) & ~occ
        if not new.any():
            break
        added += int(new.sum())
        occ = occ | new
    return occ, added


def cleanup(occ, max_fragment=4):
    """Fill enclosed voids and pinholes (air with >= 5 solid faces), bridge edge-only joints, then drop ungrounded
    fragments of at most max_fragment blocks. Larger ungrounded parts are kept for the floating check to fail."""
    before = int(occ.sum())
    voids = ~occ & ~exterior_air(occ)
    occ = occ | voids
    solid_faces = sum(shift(occ, d, d == (-1, 0, 0)).astype(int) for d in N6)  # ground below y=0 is solid
    pin = ~occ & (solid_faces >= 5)
    occ = occ | pin
    occ, bridged = bridge(occ)
    lab, n = label(occ & ~grounded(occ))
    dropped = 0
    for i in range(n):
        m = lab == i
        if m.sum() <= max_fragment:
            occ = occ & ~m
            dropped += int(m.sum())
    return occ, {"before": before, "voids_filled": int(voids.sum()), "pinholes_filled": int(pin.sum()),
                 "bridge_blocks_added": bridged, "fragment_blocks_removed": dropped, "after": int(occ.sum())}


def thicken(occ, legs_per_round=4, spacing=3, rounds=8):
    """Leg-thickening (S30): while a body mass floats, give its largest floating part up to `legs_per_round` 2x2
    pillars from the ground (y=0) up to its underside. Pillars go where the gap is smallest, preferring columns that
    already have a grounded stub below (a thin leg), spaced >= `spacing` apart. Deterministic: candidates are sorted by
    (stub first, gap, x, z). Fills air only. Returns (occ, report); floating parts may remain (the check then fails)."""
    occ = occ.copy()
    H, D, W = occ.shape
    legs, before = [], int(occ.sum())
    for rnd in range(rounds):
        lab, n = label(occ & ~grounded(occ))
        if not n:
            break
        sizes = np.bincount(lab[lab >= 0])
        body = lab == int(np.argmax(sizes))
        g = grounded(occ)
        cand = []
        for z, x in zip(*np.nonzero(body.any(axis=0))):
            yb = int(np.argmax(body[:, z, x]))           # lowest body cell in this column
            below = np.nonzero(g[:yb, z, x])[0]
            top = int(below.max()) if len(below) else -1  # highest grounded cell under it, -1 = bare ground
            cand.append((0 if top >= 0 else 1, yb - top - 1, int(x), int(z), yb))
        cand.sort()
        picked = []
        for stub, gap, x, z, yb in cand:
            if len(picked) >= legs_per_round:
                break
            if any(max(abs(x - px), abs(z - pz)) < spacing for px, pz, _ in picked):
                continue
            picked.append((x, z, yb))
        added = 0
        for x, z, yb in picked:
            squares = [(x0, z0) for x0 in (x - 1, x) for z0 in (z - 1, z) if 0 <= x0 < W - 1 and 0 <= z0 < D - 1]
            if not squares:                              # grid narrower than 2: no 2x2 leg fits (reported floating)
                continue
            x0, z0 = max(squares, key=lambda q: (int(body[:, q[1]:q[1] + 2, q[0]:q[0] + 2].sum()), -q[0], -q[1]))
            cols = body[:, z0:z0 + 2, x0:x0 + 2]
            ytop = min(int(np.argmax(cols[:, j, i])) for j in range(2) for i in range(2) if cols[:, j, i].any())
            box = (slice(0, ytop), slice(z0, z0 + 2), slice(x0, x0 + 2))
            new = ~occ[box]
            occ[box] = True
            added += int(new.sum())
            legs.append({"round": rnd + 1, "x": x0, "z": z0, "size": [2, 2], "from_y": 0, "to_y": ytop - 1,
                         "added": int(new.sum())})
        if not added:
            break
    return occ, {"before": before, "after": int(occ.sum()), "added": int(occ.sum()) - before, "legs": legs,
                 "floating_after": floating(occ)}


def hollow(occ, t):
    """Keep a shell t blocks thick (26-neighbour distance from outside air, so no diagonal thin spots); the bottom
    layer and anything closer than t to it stays solid. Returns (shell, cavity)."""
    if t <= 0:
        return occ, np.zeros_like(occ)
    inner = occ.copy()
    for _ in range(t):
        e = inner.copy()
        for d in N26:
            e &= shift(inner, d, False)
        inner = e
    inner[:t] = False
    return occ & ~inner, inner


def cap_cavities(shell, cavity, max_side=3):
    """O-decision 2026-10-21: an enclosed cavity that does not fit in a max_side^3 box is filled back (a dark hollow
    inside a sculpture is a mob spawner; no lanterns in sculptures). Returns (shell, cavity, cells refilled)."""
    lab, n = label(cavity)
    refill = np.zeros_like(cavity)
    for i in range(n):
        m = lab == i
        if max(int(np.ptp(a)) + 1 for a in np.nonzero(m)) > max_side:
            refill |= m
    return shell | refill, cavity & ~refill, int(refill.sum())


def wall_ok(shell, cavity, t) -> bool:
    """Every cavity cell is more than t blocks (26-neighbour steps) from air outside the piece."""
    if not cavity.any():
        return True
    near = ~(shell | cavity)
    for _ in range(t):                                 # beyond the grid is outside air, except the ground below
        near = near | np.logical_or.reduce([shift(near, d, d[0] != -1) for d in N26])
    return not (near & cavity).any()


def support_report(occ) -> dict:
    below = shift(occ, (-1, 0, 0), True)               # ground below y=0
    over = occ & ~below
    per = [int(over[y].sum()) for y in range(occ.shape[0])]
    return {"overhang_blocks": int(over.sum()), "overhang_per_layer": per,
            "ground_contact_blocks": int(occ[0].sum()),
            "rule": "every block connects to the ground through faces (no floating parts); the design's own buildable "
                    "check then replays the build order with eve-builder's reach/support rules (the decider)"}


# ---------------------------------------------------------------------------------------------- palettes (CP-70)
# Bands by height fraction (0 = bottom, 1 = top); faces by the smoothed surface normal. "fill" = hidden blocks.
# Every block here has a gather route in eve_design.bom and none falls (no sand, gravel, concrete powder).
PALETTES = {
    "stone": {"fill": "cobblestone", "plinth": "stone_bricks", "bands": [
        {"upto": 0.2, "top": "mossy_cobblestone", "side": "cobblestone", "under": "cobblestone"},
        {"upto": 0.7, "top": "polished_andesite", "side": "stone_bricks", "under": "stone"},
        {"upto": 1.0, "top": "polished_andesite", "side": "andesite", "under": "stone"}]},
    "wood": {"fill": "oak_planks", "plinth": "cobblestone", "bands": [
        {"upto": 0.5, "top": "oak_planks", "side": "oak_log", "under": "oak_planks"},
        {"upto": 1.0, "top": "spruce_planks", "side": "spruce_log", "under": "spruce_planks"}]},
    "terracotta": {"fill": "terracotta", "plinth": "bricks", "bands": [
        {"upto": 0.3, "top": "terracotta", "side": "red_terracotta", "under": "terracotta"},
        {"upto": 1.0, "top": "yellow_terracotta", "side": "orange_terracotta", "under": "terracotta"}]},
    "concrete": {"fill": "stone", "plinth": "stone_bricks", "bands": [
        {"upto": 0.3, "top": "gray_concrete", "side": "gray_concrete", "under": "gray_concrete"},
        {"upto": 1.0, "top": "white_concrete", "side": "light_gray_concrete", "under": "light_gray_concrete"}]},
    "tree": {"fill": "oak_planks", "plinth": "cobblestone", "bands": [
        {"upto": 0.45, "top": "oak_log", "side": "oak_log", "under": "oak_log"},
        {"upto": 1.0, "top": "moss_block", "side": "moss_block", "under": "oak_planks"}]},
}
GRAVITY = ("sand", "gravel", "concrete_powder", "anvil", "dragon_egg", "scaffolding", "pointed_dripstone")


def assign(shell, full, palette: str):
    """-> object array [y, z, x] of block ids (None = air), one smoothing pass. Logs get their axis later."""
    pal = PALETTES[palette]
    H = shell.shape[0]
    f = full.astype(float)
    blur = sum(shift(f, d, 0.0) for d in N26) + f
    gy, gz, gx = (g[1:-1, 1:-1, 1:-1] for g in np.gradient(np.pad(blur, 1)))   # pad: a 1-thick piece has a gradient too
    ny = -gy / (np.sqrt(gx * gx + gy * gy + gz * gz) + 1e-9)
    visible = shell & (sum((~shift(shell, d, False)).astype(int) for d in N6) > 0)
    out = np.full(shell.shape, None, object)
    for y, z, x in zip(*np.nonzero(shell)):
        if not visible[y, z, x]:
            out[y, z, x] = pal["fill"]
            continue
        frac = (y + 0.5) / H
        band = next(b for b in pal["bands"] if frac <= b["upto"])
        n = ny[y, z, x]
        out[y, z, x] = band["top"] if n > 0.55 else band["under"] if n < -0.55 else band["side"]
    sm = out.copy()                                    # one majority pass among visible face neighbours
    for y, z, x in zip(*np.nonzero(visible)):
        votes = [out[y, z, x]]
        for dy, dz, dx in N6:
            q = (y + dy, z + dz, x + dx)
            if all(0 <= q[i] < shell.shape[i] for i in range(3)) and visible[q]:
                votes.append(out[q])
        best = max(set(votes), key=votes.count)
        if votes.count(best) > votes.count(out[y, z, x]):
            sm[y, z, x] = best
    return sm


def axis_for(occ, y, z, x):
    """Log axis the builder can place: y if a block is above or below, else along a horizontal neighbour."""
    def solid(dy, dz, dx):
        q = (y + dy, z + dz, x + dx)
        return (q[0] < 0) or (all(0 <= q[i] < occ.shape[i] for i in range(3)) and occ[q])
    if solid(-1, 0, 0) or solid(1, 0, 0):
        return "y"
    return "x" if solid(0, 0, 1) or solid(0, 0, -1) else "z"


def to_design(blocks, roles, gd, origin, meta) -> Design:
    sy, sz, sx = blocks.shape
    g = Design((sx, sy, sz), origin)
    occ = blocks != None  # noqa: E711 (object array)
    for y, z, x in zip(*np.nonzero(occ)):
        b = blocks[y, z, x]
        if b.endswith("_log"):
            b += f"[axis={axis_for(occ, y, z, x)}]"
        g.set(int(x), int(y), int(z), gd.canonical("minecraft:" + b), roles[y, z, x])
    bad = g.canonicalize(gd)
    if bad:
        raise OrganicError(f"illegal block states: {bad}")
    g.meta.update(meta)
    return g


# ---------------------------------------------------------------------------------------------- design folder
def code_rev() -> dict:
    """The eve_design code this folder was made with: the checkout that eve_design was imported from (a worktree may
    differ from the one organic.py runs in), its HEAD + branch + files edited but not committed."""
    src = Path(sys.modules["eve_design"].__file__).resolve().parent
    git = lambda *a: subprocess.run(["git", *a], cwd=src, capture_output=True, text=True).stdout.strip()
    return {"commit": git("rev-parse", "--short", "HEAD") or None, "branch": git("branch", "--show-current"),
            "uncommitted": git("status", "--porcelain", "--", ".").splitlines()}


def write_folder(g: Design, gd, out: Path, brief: dict | None, extra: dict) -> dict:
    """Exactly the files `python -m eve_design import` writes, from the same calls, plus organic.json."""
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    pid = g.meta.get("project_id") or out.name
    h = g.save(out / "design.json")
    save_litematic(g, out / "design.litematic", gd, name=pid, description=f"Eve {pid} v{g.meta.get('version')} {h[:12]}")
    site = (brief or {}).get("site")
    s, _ = scaffold.plan(g, gd, site, interior=None)
    if s:
        s.save(out / "scaffold.json")
    b = bom.compute(g, gd, s, None, None)
    (out / "bom.json").write_text(json.dumps(b, indent=1) + "\n")
    if brief:
        (out / "brief.json").write_text(json.dumps(brief, indent=1) + "\n")
    v = validate(g, gd, brief=brief, bom=b, scaffold=s)
    (out / "validation.json").write_text(json.dumps(v, indent=1) + "\n")
    preview.render(g, gd, out / "previews")
    (out / "build_order.json").write_text(json.dumps(buildorder.compact(h, buildorder.plan_order(g, gd, s, site=site)[0]),
                                                     separators=(",", ":")) + "\n")
    d = gate.decide(out)
    rep = {"pipeline": PIPELINE, "design_hash": h, "eve_design_code": code_rev(),
           "approval_policy": "gate hold + owner approval per piece via design's CP-65 path; no critic; these structural "
                              "checks are the precondition (orchestrator decision 2026-10-21)", **extra, "blocks": sum(g.counts().values()),
           "scaffold_blocks": sum(s.counts().values()) if s else 0,
           "validation": {"passed": v["passed"], "failed": [k for k, c in v["checks"].items() if c["status"] == "fail"],
                          "skipped": v["skipped"]},
           "gate": d["decision"], "files": {}}
    rep["files"] = {p.relative_to(out).as_posix(): sha256(p) for p in sorted(out.rglob("*")) if p.is_file()}
    (out / "organic.json").write_text(json.dumps(rep, indent=1) + "\n")
    package(out)                                       # approval.png + PACKAGE.md for every piece (APPROVAL-PACKAGE.md)
    return rep


def build(job_dir: Path, out: Path, *, height=None, width=None, depth=None, yaw=0, hollow_t=2, clean=False,
          palette="stone", plinth=False, origin=(0, 64, 0), brief=None, project=None, max_fragment=4,
          thick=False) -> dict:
    """mesh job -> voxels -> [cleanup] -> hollow -> palette -> design folder. Fails (OrganicError / exit 3 via
    'passed': False) on floating parts, a thin wall, a falling block or a failed design validation."""
    gd = gamedata.load()
    job, V, F = load_job(job_dir)
    occ, vrep = voxelize(V, F, height=height, width=width, depth=depth, yaw=yaw)
    rep = {"mesh_job": {"job_id": job["job_id"], "mesh_sha256": job["output"]["sha256"], "source": job["source"],
                        "tool": job["tool"], "watertight": job["output"]["watertight"]},
           "voxelize": vrep, "params": {"hollow": hollow_t, "clean": clean, "palette": palette, "plinth": plinth,
                                         "max_fragment": max_fragment, "thicken": thick}}
    if clean:
        occ, rep["cleanup"] = cleanup(occ, max_fragment)
    if thick:
        occ, rep["thicken"] = thicken(occ)
    full = occ
    roles = np.full(occ.shape, "trim", object)
    if plinth:
        foot = occ[0]
        zs, xs = np.nonzero(foot)
        pad = np.zeros((occ.shape[0] + 1, occ.shape[1] + 2, occ.shape[2] + 2), bool)
        pad[1:, 1:-1, 1:-1] = occ
        pad[0, zs.min():zs.max() + 3, xs.min():xs.max() + 3] = True
        full = occ = pad
        roles = np.full(occ.shape, "trim", object)
        roles[0] = "foundation"
    fl = floating(occ)
    rep["floating"] = fl
    rep["support"] = support_report(occ)
    shell, cavity = hollow(occ, hollow_t)
    shell, cavity, refilled = cap_cavities(shell, cavity)
    rep["hollow"] = {"solid": int(occ.sum()), "shell": int(shell.sum()), "removed": int(cavity.sum()),
                     "cavity_refilled_over_3x3x3": refilled,
                     "min_wall": hollow_t, "wall_ok": wall_ok(shell, cavity, hollow_t)}
    if fl:
        raise OrganicError(f"floating parts not connected to the ground: {fl[:5]}")
    if not rep["hollow"]["wall_ok"]:
        raise OrganicError("hollowing left a wall thinner than the minimum")
    blocks = assign(shell, full, palette)
    if plinth:
        blocks[0][shell[0]] = PALETTES[palette]["plinth"]
    used = sorted({b for b in blocks.ravel() if b})
    if any(w in b for b in used for w in GRAVITY):
        raise OrganicError(f"palette has falling blocks: {used}")
    rep["palette_used"] = used
    meta = {"project_id": project or Path(out).name, "version": 1, "style": f"organic:{palette}",
            "provenance": {"source": "organic", "pipeline": PIPELINE, "mesh_job": job["job_id"],
                           "mesh_sha256": job["output"]["sha256"], "target": vrep["target"], "yaw": yaw}}
    g = to_design(blocks, roles, gd, tuple(origin), meta)
    return write_folder(g, gd, out, brief, rep)


def approval_png(folder: Path, side: str = "auto") -> Path:
    """One image for the owner's yes/no (O94l): a straight side view + the SE 3/4 view from the design's own renders
    (previews/), on a plain white background, captioned with the design hash. The side is the elevation facing the long
    axis unless given."""
    from PIL import Image, ImageDraw
    folder = Path(folder)
    d = json.loads((folder / "design.json").read_text())
    if side == "auto":
        side = "E" if d["size"]["z"] >= d["size"]["x"] else "S"
    ims = [Image.open(folder / "previews" / f).convert("RGBA") for f in (f"elev-{side}.png", "SE.png")]
    pad, cap = 24, 28
    w = sum(i.width for i in ims) + pad * (len(ims) + 1)
    h = max(i.height for i in ims) + pad * 2 + cap
    out = Image.new("RGB", (w, h), "white")
    x = pad
    for i in ims:
        bg = Image.new("RGBA", i.size, "white")
        im = Image.alpha_composite(bg, i).convert("RGB")
        a = np.array(im)
        a[(a == a[0, 0]).all(axis=2)] = 255            # the renderer's background (elevation pink, iso grey-blue) -> white
        out.paste(Image.fromarray(a), (x, pad))
        x += i.width + pad
    ImageDraw.Draw(out).text((pad, h - cap + 6), f"{d['project_id']}  {d['hash'][:12]}  side ({side}) | 3/4 (SE)", fill="black")
    p = folder / "approval.png"
    out.save(p)
    return p


EXTERIOR = {"roof", "wall", "trim", "support", "window", "furniture", "light", "door"}


def stand_audit(folder: Path) -> dict:
    """C2 (CP-49 a16): where does build_order.json stand Eve? Live, stands resting on the design's exterior (roof, wall
    tops, sculpture, supports...) were reached 91/272 times vs 367/378 on ground/scaffold/floor. `passed` is False if any
    final step stands on such a surface. A proxy calibrated on one run, not Baritone itself."""
    folder = Path(folder)
    g = Design.load(folder / "design.json")
    s = Design.load(folder / "scaffold.json") if (folder / "scaffold.json").exists() else None
    bo = json.loads((folder / "build_order.json").read_text())
    cols = bo["columns"]
    count, bad = {}, []
    for row in bo["rows"]:
        r = dict(zip(cols, row))
        if r["layer"] != "final" or not r["stand"]:
            continue
        x, y, z = r["stand"]
        if y == 0:
            k = "ground"
        else:
            q = (x + g.origin[0] - s.origin[0], y - 1 + g.origin[1] - s.origin[1], z + g.origin[2] - s.origin[2]) if s else None
            if s and s.in_bounds(*q) and s.get(*q) != AIR:
                k = "scaffold"
            elif g.in_bounds(x, y - 1, z) and g.get(x, y - 1, z) != AIR:
                k = g.role_at(x, y - 1, z)
            else:
                k = "other"
        count[k] = count.get(k, 0) + 1
        if k in EXTERIOR:
            bad.append({"cell": [r["x"], r["y"], r["z"]], "stand": [x, y, z], "on": k})
    return {"steps": sum(count.values()), "stands_on": count, "on_exterior": len(bad), "passed": not bad,
            "first": bad[:5], "rule": "no planned stand may rest on the design's roof/wall/trim/support/window/furniture"}


def package(folder: Path, caveats=()) -> Path:
    """The fixed per-piece approval package (APPROVAL-PACKAGE.md): PACKAGE.md next to approval.png, from the folder's
    own files only. Automatic caveats come from organic.json; `caveats` adds the ones a person noticed."""
    folder = Path(folder)
    if not (folder / "approval.png").exists():
        approval_png(folder)
    rep = json.loads((folder / "organic.json").read_text())
    d = json.loads((folder / "design.json").read_text())
    b = json.loads((folder / "bom.json").read_text())
    v = json.loads((folder / "validation.json").read_text())
    g = json.loads((folder / "gate.json").read_text())
    auto = []
    if rep.get("scaffold_blocks", 0) > rep.get("blocks", 0):
        auto.append(f"scaffold ({rep['scaffold_blocks']} dirt, placed and removed) outweighs the piece ({rep['blocks']} blocks)")
    for lg in (rep.get("thicken") or {}).get("legs", []):
        auto.append(f"leg added by thickening: 2x2 at x {lg['x']}, z {lg['z']}, y 0..{lg['to_y']} (+{lg['added']})")
    c = rep.get("cleanup") or {}
    if c.get("bridge_blocks_added") or c.get("fragment_blocks_removed"):
        auto.append(f"cleanup: +{c.get('bridge_blocks_added', 0)} bridge blocks, -{c.get('fragment_blocks_removed', 0)} fragment blocks")
    mj = rep.get("mesh_job") or {}
    if mj and not mj.get("watertight", True):
        auto.append("source mesh not watertight (voxelized by 3-axis vote)")
    if (mj.get("tool") or {}).get("fake"):
        auto.append("mesh came from the labelled FAKE provider: not a real reconstruction")
    sa = stand_audit(folder)
    if not sa["passed"]:
        auto.append(f"stand audit FAILS: {sa['on_exterior']} of {sa['steps']} steps stand Eve on the design's own "
                    f"roof/walls/sculpture (C2: such stands were reached 33 % of the time live)")
    if not v["passed"]:
        auto.append("VALIDATION FAILED: " + ", ".join(k for k, x in v["checks"].items() if x["status"] == "fail"))
    plinth = (rep.get("params") or {}).get("plinth")
    parts = rep.get("combined")
    rows = [("design_hash", f"`{d['hash']}`"), ("project / version", f"{d['project_id']} v{d['version']}"),
            ("height", f"{d['size']['y']} blocks incl. any plinth (footprint {d['size']['x']} x {d['size']['z']})"),
            ("blocks", f"{b['totals']['final_blocks']} (+ {b['totals']['scaffold_blocks']} scaffold dirt, removed after)"),
            ("plinth", "n/a (combined)" if parts else ("yes" if plinth else "no")),
            ("materials", ", ".join(f"{i['count']} {i['item'].removeprefix('minecraft:')}" for i in b["final"])),
            ("cost", f"about {b['totals']['estimated_gather_minutes']} min of gathering (uncalibrated estimate); "
                     f"model spend US$0"),
            ("validation", f"{'passed' if v['passed'] else 'FAILED'}; skipped: {', '.join(v['skipped']) or 'none'}"),
            ("gate", f"{g['decision']}: builds only after Hunter's yes for this exact hash"),
            ("origin (world)", f"{d['origin']['x']}, {d['origin']['y']}, {d['origin']['z']}")]
    if parts:
        rows.append(("made of", "; ".join(f"{q['part']} {q['folder']} ({q['design_hash'][:12]})" for q in parts)))
    lines = [f"# {d['project_id']}: approval package", "", "![side and 3/4](approval.png)", "",
             "| | |", "|---|---|"] + [f"| {k} | {x} |" for k, x in rows] + ["", "## Known caveats"]
    lines += [f"- {x}" for x in list(caveats) + auto] or ["- none found"]
    lines += ["", "Decision: Hunter answers yes or no for this design_hash. A yes is recorded by the mind (CP-65)."]
    out = folder / "PACKAGE.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return out


CANDIDATES = [("cp69-horse-triposr", "APPROVED by Hunter (H-horse) for a lane-plot test build; re-planned under walk_surfaces, C3 waits for the orchestrator's ruling",
               ["the body rests on ONE 1-block leg column; the other legs are stubs on the plinth",
                "planned on design/o94ag-c (not yet on main): main's placecheck lacks the fixes it was planned under"]),
              ("cp70-horse16-thick", "waiting for Hunter's yes/no", []),
              ("cp70-horse20-thick", "NOT READY: plan predates walk_surfaces (re-plan killed at the 10-min limit)", []),
              ("cp72-tree", "waiting for Hunter's yes/no (the CP-72 piece, O285; replaces the cabin-and-tree)", [])]


def candidates(root: Path | None = None) -> Path:
    """CANDIDATES.md (next to examples/): one page of the pieces ready for Hunter's yes/no, each with its package."""
    root = Path(root or EX)
    lines = ["# Organic pieces for Hunter's yes/no", "",
             "Each row links its package (PACKAGE.md: picture, hash, counts, cost, caveats). Nothing is built in a "
             "world until Hunter says yes to that exact design hash.", "",
             "| piece | status | height (incl. plinth) | blocks + scaffold | design_hash | package |", "|---|---|---|---|---|---|"]
    for name, status, cav in CANDIDATES:
        f = root / name
        package(f, cav)
        d = json.loads((f / "design.json").read_text())
        t = json.loads((f / "bom.json").read_text())["totals"]
        lines.append(f"| {name} | {status} | {d['size']['y']} | {t['final_blocks']} + {t['scaffold_blocks']} | "
                     f"`{d['hash'][:12]}` | [{name}/PACKAGE.md]({root.name}/{name}/PACKAGE.md) |")
    out = root.parent / "CANDIDATES.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return out


# ---------------------------------------------------------------------------------------------- combine (CP-71)
def merge_designs(arch_dir: Path, org_dir: Path, at, gd, *, yaw=0, project=None) -> tuple[Design, list]:
    """Place the organic piece (min corner at arch origin + at, after a clockwise yaw) beside or on a compiled
    design. Collisions fail; rooms, roles and both hashes are kept. Returns (merged design, parts). No planning."""
    arch, org = Design.load(Path(arch_dir) / "design.json"), Design.load(Path(org_dir) / "design.json")
    if yaw % 90:
        raise OrganicError("yaw must be a multiple of 90")
    if yaw:
        org = rotated(org, yaw // 90, gd)
    org.origin = tuple(a + d for a, d in zip(arch.origin, at))
    lo = [min(a, b) for a, b in zip(arch.origin, org.origin)]
    hi = [max(a + s, b + t) for a, s, b, t in zip(arch.origin, arch.size, org.origin, org.size)]
    m = Design(tuple(h - l for h, l in zip(hi, lo)), tuple(lo))
    hits = []
    for part in (arch, org):
        ox, oy, oz = (p - l for p, l in zip(part.origin, lo))
        sx, sy, sz = part.size
        sl = (slice(oy, oy + sy), slice(oz, oz + sz), slice(ox, ox + sx))
        lut = np.array([m.idx(s) for s in part.palette], np.uint16)
        mask = part.blocks != 0
        clash = mask & (m.blocks[sl] != 0)
        hits += [[int(x) + ox, int(y) + oy, int(z) + oz] for y, z, x in zip(*np.nonzero(clash))]
        m.blocks[sl][mask] = lut[part.blocks[mask]]
        m.roles[sl][mask] = part.roles[mask]
        for r in part.rooms:
            sh = lambda p: {"x": p["x"] + ox, "y": p["y"] + oy, "z": p["z"] + oz}
            m.rooms.append({**r, "min": sh(r["min"]), "max": sh(r["max"]),
                            **({"door_pos": sh(r["door_pos"])} if r.get("door_pos") else {})})
        for be in part.block_entities:
            m.block_entities.append({**be, "pos": {"x": be["pos"]["x"] + ox, "y": be["pos"]["y"] + oy, "z": be["pos"]["z"] + oz}})
    if hits:
        raise OrganicError(f"organic piece collides with the design at {len(hits)} cells, e.g. {hits[:5]}")
    m.canonicalize(gd)
    parts = [{"part": "architecture", "folder": Path(arch_dir).name, "design_hash": arch.hash(),
              "origin": list(arch.origin)},
             {"part": "organic", "folder": Path(org_dir).name, "design_hash": Design.load(Path(org_dir) / "design.json").hash(),
              "origin": list(org.origin), "yaw": yaw, "at": list(at)}]
    m.meta.update({"project_id": project, "version": 1, "style": arch.meta.get("style"),
                   "provenance": {"source": "combined", "pipeline": PIPELINE, "parts": parts}})
    return m, parts


def combine(arch_dir: Path, org_dir: Path, at, out: Path, *, yaw=0, brief=None, project=None) -> dict:
    """merge_designs, then the folder: validated in-process (authoritative) and again through the design CLI
    (`python -m eve_design import` of the merged .litematic; roles lost, so a cross-check)."""
    gd = gamedata.load()
    m, parts = merge_designs(arch_dir, org_dir, at, gd, yaw=yaw, project=project or Path(out).name)
    rep = write_folder(m, gd, out, brief, {"combined": parts})
    rep["cli_validation"] = cli_validate(Path(out), brief)
    (Path(out) / "organic.json").write_text(json.dumps(rep, indent=1) + "\n")
    (Path(out) / "PACKAGE.md").unlink(missing_ok=True)
    package(Path(out))
    return rep


def cli_validate(folder: Path, brief: dict | None) -> dict:
    """Re-run the design's validators through its own CLI on the merged blocks (the .litematic carries no rooms or
    roles, so room checks are the in-process run's; block checks must agree). Writes to a temp dir, not the folder."""
    tmp = Path(os.environ.get("TEMP", ".")) / f"eve-organic-cli-{folder.name}"
    shutil.rmtree(tmp, ignore_errors=True)
    args = [sys.executable, "-m", "eve_design", "import", str(folder / "design.litematic"), "--out", str(tmp / "v")]
    d = json.loads((folder / "design.json").read_text())["origin"]
    args += [f"--origin={d['x']},{d['y']},{d['z']}"]   # "=": a negative x would read as an option
    args += ["--stand-policy=unbuilt"]                  # a .litematic has no roles, so under walk_surfaces every floor is
                                                        # unlabelled and the planner toils in vain: this run cross-checks the
                                                        # block-level checks and the grid; stands are the in-process run's
    if brief:
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / "brief.json").write_text(json.dumps(brief))
        args += ["--brief", str(tmp / "brief.json")]
    r = subprocess.run(args, capture_output=True, text=True)
    v = json.loads((tmp / "v" / "validation.json").read_text())
    same_blocks = Design.load(tmp / "v" / "design.json").same_blocks(Design.load(folder / "design.json"))
    return {"command": "python -m eve_design import design.litematic --origin ... --brief brief.json", "exit": r.returncode,
            "passed": v["passed"], "checks": {k: c["status"] for k, c in v["checks"].items()},
            "same_blocks": same_blocks}


# ---------------------------------------------------------------------------------------------- examples + CLI
EX = HERE / "examples"
HUT = HERE.parent / "examples" / "out" / "hut5_sim"
CABIN = HERE.parent / "examples" / "out" / "cabin"     # compiled examples/cabin.eve: 13x11x13, door (6,1,9) + doorstep
PLOT_BRIEF = {"site": {"dim": "minecraft:overworld", "origin": {"x": 0, "y": 64, "z": 0},
                       "plot": {"min": {"x": -4, "z": -6}, "max": {"x": 28, "z": 18}}},
              "budget": {"max_blocks": 900}}
TREE_BRIEF = {**PLOT_BRIEF, "site": {**PLOT_BRIEF["site"],        # 3 cells round the tree's 9 x 9 footprint at 10,64,2
                                     "plot": {"min": {"x": 7, "z": -1}, "max": {"x": 21, "z": 13}}}}
TOWER = HERE / "examples" / "watchtower_small"         # organic/examples/watchtower_small.eve, compiled by the design CLI
TOWER_AT = (-10, 0, 0)                                 # the guardian stands west of the tower, clear of its south door
TOWER_BRIEF = {"site": {"dim": "minecraft:overworld", "origin": {"x": 0, "y": 64, "z": 0},
                        "plot": {"min": {"x": -18, "z": -8}, "max": {"x": 16, "z": 18}}},
               "budget": {"max_blocks": 2000}}


def examples(root: Path = EX) -> dict:
    """Re-emit every committed example (deterministic; check_organic.py re-emits to a temp dir and compares)."""
    root = Path(root)
    r = {}
    r["cp68-tree-mesh"] = mesh_job(root / "cp68-tree-mesh", generate="tree", seed=1)
    r["cp69-statue-raw"] = (mesh_job(root / "cp68-statue-mesh", generate="statue", seed=1), build(
        root / "cp68-statue-mesh", root / "cp69-statue-raw", height=16, hollow_t=2, palette="stone", origin=(10, 64, 2),
        brief=PLOT_BRIEF))[1]
    r["cp70-tree"] = build(root / "cp68-tree-mesh", root / "cp70-tree", height=10, hollow_t=2, clean=True,
                           palette="tree", origin=(10, 64, 2), brief=PLOT_BRIEF)
    r["cp71-tower-statue"] = combine(TOWER, root / "cp69-statue-raw", TOWER_AT, root / "cp71-tower-statue", yaw=90,
                                     brief=TOWER_BRIEF)
    r["cp72-tree"] = build(root / "cp68-tree-mesh", root / "cp72-tree", height=10, hollow_t=2, clean=True, palette="tree",
                           origin=(10, 64, 2), brief=TREE_BRIEF)
    return {k: {kk: v.get(kk) for kk in ("job_id", "design_hash", "blocks", "gate", "validation")} for k, v in r.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="organic")
    ap.add_argument("cmd", choices=["mesh", "build", "combine", "examples", "package", "candidates", "audit"])
    ap.add_argument("src", nargs="*", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--src", dest="mesh_src", type=Path)
    ap.add_argument("--generate")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--image", type=Path)
    ap.add_argument("--provider", choices=list(PROVIDERS))
    ap.add_argument("--up", choices=["y", "z"], default="y")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--cap-usd", type=float, default=0.0)
    ap.add_argument("--retries", type=int, default=1)
    for k in ("height", "width", "depth"):
        ap.add_argument(f"--{k}", type=int)
    ap.add_argument("--yaw", type=int, default=0)
    ap.add_argument("--hollow", type=int, default=2)
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--palette", choices=list(PALETTES), default="stone")
    ap.add_argument("--plinth", action="store_true")
    ap.add_argument("--thicken", action="store_true", help="build: 2x2 legs under floating body masses (S30)")
    ap.add_argument("--origin", default="0,64,0")
    ap.add_argument("--at")
    ap.add_argument("--brief", type=Path)
    ap.add_argument("--project")
    a = ap.parse_args(argv)
    brief = json.loads(a.brief.read_text()) if a.brief else None
    try:
        if a.cmd == "mesh":
            r = mesh_job(a.out, src=a.mesh_src, generate=a.generate, seed=a.seed, image=a.image,
                         provider=PROVIDERS[a.provider]() if a.provider else None, up=a.up, timeout_s=a.timeout,
                         cap_usd=a.cap_usd, retries=a.retries)
        elif a.cmd == "build":
            r = build(a.src[0], a.out, height=a.height, width=a.width, depth=a.depth, yaw=a.yaw, hollow_t=a.hollow,
                      clean=a.clean, palette=a.palette, plinth=a.plinth, thick=a.thicken,
                      origin=tuple(int(v) for v in a.origin.split(",")), brief=brief, project=a.project)
        elif a.cmd == "combine":
            r = combine(a.src[0], a.src[1], tuple(int(v) for v in a.at.split(",")), a.out, yaw=a.yaw, brief=brief,
                        project=a.project)
        elif a.cmd == "package":
            print(package(a.src[0]))
            return 0
        elif a.cmd == "audit":
            r = stand_audit(a.src[0])
            print(json.dumps(r, indent=1))
            return 0 if r["passed"] else 3
        elif a.cmd == "candidates":
            print(candidates())
            return 0
        else:
            r = examples()
    except OrganicError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2 if "floating" not in str(e) and "collides" not in str(e) and "thinner" not in str(e) else 3
    print(json.dumps({k: v for k, v in r.items() if k != "files"}, indent=1, default=str)[:4000])
    if a.cmd in ("build", "combine") and not (r["validation"]["passed"] and r.get("cli_validation", {"passed": True})["passed"]):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
