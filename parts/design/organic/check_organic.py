"""Offline, fake-free checks for the organic pipeline, one group per stage (CP-68..72). No game, no network, no paid
call; the only stand-in is FakeImageToMesh, used solely to drive the job controls and labelled as such.
  .venv/Scripts/python organic/check_organic.py [--stage 1 2 3 4 5]      (from parts/design; exit 0 = all pass)
Each case is counted; normal and malformed/planted cases are listed separately in the output."""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import organic as o  # noqa: E402
from eve_design import bom, gamedata  # noqa: E402
from eve_design.grid import Design  # noqa: E402
from eve_design.validate import validate  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="eve-organic-check-"))
GD = None
CASES: dict[int, list] = {i: [] for i in range(1, 6)}


def case(stage, kind):
    def deco(fn):
        CASES[stage].append((kind, fn.__name__, fn))
        return fn
    return deco


def raises(fn, *words):
    try:
        fn()
    except o.OrganicError as e:
        assert all(w in str(e) for w in words), f"wrong error: {e}"
        return str(e)
    raise AssertionError("no error raised")


def write(name, text):
    p = TMP / name
    p.write_text(text) if isinstance(text, str) else p.write_bytes(text)
    return p


def obj_of(V, F):
    return "".join(f"v {x} {y} {z}\n" for x, y, z in V) + "".join(f"f {a + 1} {b + 1} {c + 1}\n" for a, b, c in F)


def glb_of(V, F, node):
    """A minimal glTF 2.0 binary: one mesh, one node with `node` transform (independent writer for the loader test)."""
    pos = np.asarray(V, "<f4").tobytes()
    idx = np.asarray(F, "<u4").ravel().tobytes()
    blob = pos + idx
    js = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, **node}],
          "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
          "buffers": [{"byteLength": len(blob)}],
          "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos)},
                          {"buffer": 0, "byteOffset": len(pos), "byteLength": len(idx)}],
          "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(V), "type": "VEC3"},
                        {"bufferView": 1, "componentType": 5125, "count": len(F) * 3, "type": "SCALAR"}]}
    j = json.dumps(js).encode()
    j += b" " * (-len(j) % 4)
    blob += b"\0" * (-len(blob) % 4)
    body = struct.pack("<I4s", len(j), b"JSON") + j + struct.pack("<I4s", len(blob), b"BIN\0") + blob
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def job_from(name, V, F, **kw):
    p = write(f"{name}.obj", obj_of(V, F))
    return o.mesh_job(TMP / f"job-{name}", src=p, **kw)


# ============================================================================================ stage 1: CP-68 mesh job
@case(1, "normal")
def generators_are_watertight():
    for k, f in o.GENERATORS.items():
        V, F = f(1)
        r = o.normalize(V, F)[3]
        assert r["watertight"] and r["open_shells"] == 0, (k, r)


@case(1, "normal")
def obj_roundtrip_and_normalized():
    j = o.mesh_job(TMP / "job-box", src=write("box.obj", obj_of(*o.box((0, 0, 0), (2, 3, 1)))))
    _, V, F = o.load_job(TMP / "job-box")
    assert abs(V[:, 1].min()) < 1e-9 and abs(V[:, 1].max() - 1) < 1e-6, "height 1, min y 0"
    assert np.allclose(V[:, [0, 2]].min(0), -V[:, [0, 2]].max(0), atol=1e-6), "centred in x/z"
    assert j["output"]["source_size"] == [2.0, 3.0, 1.0] and j["output"]["watertight"]
    assert j["source"]["sha256"] and j["output"]["sha256"] == o.sha256(TMP / "job-box" / "mesh.obj")


@case(1, "normal")
def obj_polygons_negative_indices_uv_normals():
    V, F = o._obj("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nvt 0 0\nf 1/1/1 2/1/1 3/1/1 4/1/1\nf -4 -2 -1\n")
    assert len(V) == 4 and F == [[0, 1, 2], [0, 2, 3], [0, 2, 3]]


@case(1, "normal")
def glb_node_transform_applied():
    V, F = o.box((0, 0, 0), (1, 1, 1))
    p = write("t.glb", glb_of(V, F, {"translation": [5, 0, 0], "scale": [2, 3, 2]}))
    V2, F2 = o.load_mesh(p)
    assert np.allclose(V2.min(0), [5, 0, 0]) and np.allclose(V2.max(0), [7, 3, 2]) and len(F2) == 12


@case(1, "normal")
def glb_matrix_rotation():
    V, F = o.box((0, 0, 0), (1, 2, 1))
    m = [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1]   # column-major: +90 deg about x (y -> z)
    V2, _ = o.load_mesh(write("m.glb", glb_of(V, F, {"matrix": m})))
    assert np.allclose(V2.max(0) - V2.min(0), [1, 1, 2])


@case(1, "normal")
def z_up_input():
    V, F = o.box((0, 0, 0), (1, 1, 4))                      # tall along z
    r = o.normalize(V, F, up="z")[3]
    assert r["up"] == "z" and abs(r["source_size"][1] - 4) < 1e-9


@case(1, "normal")
def duplicate_submission_returns_finished_job_without_running():
    img = write("img.png", b"\x89PNG fake image bytes 1")
    p = o.FakeImageToMesh()
    a = o.mesh_job(TMP / "job-img", image=img, provider=p)
    b = o.mesh_job(TMP / "job-img", image=img, provider=p)
    assert a["status"] == "ok" and b.get("duplicate") and b["job_id"] == a["job_id"] and p.calls == 1
    assert a["tool"]["fake"] is True and a["usd"]["reserved"] == 0


@case(1, "normal")
def retry_after_timeout_within_cap():
    class Flaky(o.FakeImageToMesh):
        def run(self, image, timeout_s):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("first attempt timed out")
            return o.sphere()
    p = Flaky(usd_worst=0.25)
    j = o.mesh_job(TMP / "job-flaky", image=write("img2.png", b"img2"), provider=p, cap_usd=0.5, retries=2)
    assert j["status"] == "ok" and [a["outcome"] for a in j["attempts"]] == ["timeout", "ok"]
    assert j["usd"]["reserved"] == 0.5, "the timed-out attempt stays counted"


@case(1, "malformed")
def timeout_ends_job_after_retries():
    p = o.FakeImageToMesh(delay_s=999)
    e = raises(lambda: o.mesh_job(TMP / "job-to", image=write("i3.png", b"i3"), provider=p, timeout_s=5, retries=1),
               "timeout")
    j = json.loads((TMP / "job-to" / "job.json").read_text())
    assert p.calls == 2 and j["status"] == "timeout" and not (TMP / "job-to" / "mesh.obj").exists(), e


@case(1, "malformed")
def provider_failure_recorded():
    raises(lambda: o.mesh_job(TMP / "job-f", image=write("i4.png", b"i4"), provider=o.FakeImageToMesh(fail=True),
                              retries=0), "failed")
    assert json.loads((TMP / "job-f" / "job.json").read_text())["status"] == "failed"


@case(1, "malformed")
def price_over_cap_never_calls_provider():
    p = o.FakeImageToMesh(usd_worst=0.5)
    raises(lambda: o.mesh_job(TMP / "job-p", image=write("i5.png", b"i5"), provider=p, cap_usd=0.0), "refused_budget")
    assert p.calls == 0


@case(1, "malformed")
def retries_stop_at_cap():
    p = o.FakeImageToMesh(delay_s=999, usd_worst=0.5)
    raises(lambda: o.mesh_job(TMP / "job-c", image=write("i6.png", b"i6"), provider=p, cap_usd=1.0, retries=5,
                              timeout_s=1), "refused_budget")
    j = json.loads((TMP / "job-c" / "job.json").read_text())
    assert p.calls == 2 and j["usd"]["reserved"] == 1.0 and len(j["attempts"]) == 3


@case(1, "malformed")
def concurrent_duplicate_refused():
    d = TMP / "job-lock"
    d.mkdir()
    (d / ".lock").write_text("")
    raises(lambda: o.mesh_job(d, generate="box"), "already running")


@case(1, "malformed")
def local_tool_not_configured():
    os.environ.pop("EVE_ORGANIC_IMG2MESH_CMD", None)
    raises(lambda: o.mesh_job(TMP / "job-l", image=write("i7.png", b"i7"), provider=o.LocalCmd(), retries=0),
           "no local image-to-3D tool")


@case(1, "malformed")
def bad_meshes_fail_explicitly():
    raises(lambda: o.load_mesh(write("a.obj", "v 0 0 0\nv 1 0 0\nf 1 2 3\n")), "does not exist")
    raises(lambda: o.load_mesh(write("b.obj", "v 0 0 nan\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")), "non-finite")
    raises(lambda: o.load_mesh(write("c.obj", "v 0 0 0\n")), "no triangles")
    raises(lambda: o.load_mesh(write("d.obj", "v 0 0 zero\n")), "malformed")
    raises(lambda: o.load_mesh(write("e.stl", "solid x")), "unsupported")
    raises(lambda: o.load_mesh(write("f.glb", b"glTF\x01\0\0\0\x0c\0\0\0")), "not a glTF 2.0")
    raises(lambda: o.mesh_job(TMP / "job-flat", src=write("g.obj", "v 0 0 0\nv 1 0 0\nv 0 0 1\nf 1 2 3\n")), "flat")


@case(1, "malformed")
def glb_compressed_or_truncated_refused():
    V, F = o.box()
    g = bytearray(glb_of(V, F, {}))
    j0 = struct.unpack_from("<I", g, 12)[0]
    js = json.loads(g[20:20 + j0])
    js["extensionsRequired"] = ["KHR_draco_mesh_compression"]
    raises(lambda: o.load_mesh(write("draco.glb", glb_of(V, F, {})[:0] + _reglb(js, g, j0))), "extensions")
    raises(lambda: o.load_mesh(write("trunc.glb", bytes(g[:-40]))), "")


def _reglb(js, g, j0):
    j = json.dumps(js).encode()
    j += b" " * (-len(j) % 4)
    rest = bytes(g[20 + j0:])
    body = struct.pack("<I4s", len(j), b"JSON") + j + rest
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


@case(1, "malformed")
def open_mesh_reported():
    V, F = o.box()
    r = o.normalize(V, F[:-1])[3]
    assert not r["watertight"] and r["edges"]["boundary_edges"] == 3
    V, F = o.box()
    F2 = F.copy()
    F2[0] = F2[0][::-1]
    assert o.normalize(V, F2)[3]["edges"]["misoriented_edges"] > 0


@case(1, "normal")
def committed_mesh_example_reproduces():
    j = json.loads((o.EX / "cp68-tree-mesh" / "job.json").read_text())
    k = o.mesh_job(TMP / "job-tree-again", generate="tree", seed=1)
    assert j["status"] == "ok" and k["output"]["sha256"] == j["output"]["sha256"] == o.sha256(o.EX / "cp68-tree-mesh" / "mesh.obj")


# ============================================================================================ stage 2: CP-69 voxelize
@case(2, "normal")
def box_exact():
    V, F = o.box((0, 0, 0), (4, 6, 2))
    occ, r = o.voxelize(V, F, height=6)
    assert r["size"] == [4, 6, 2] and occ.all() and occ.sum() == 48


@case(2, "normal")
def box_yaw90_swaps_axes():
    occ, r = o.voxelize(*o.box((0, 0, 0), (4, 6, 2)), height=6, yaw=90)
    assert r["size"] == [2, 6, 4] and occ.shape == (6, 4, 2)


@case(2, "normal")
def box_width_target_and_half_scale():
    occ, r = o.voxelize(*o.box((0, 0, 0), (4, 6, 2)), width=2)
    assert r["size"] == [2, 3, 1] and occ.sum() == 6


def _vs_analytic(occ, inside, band):
    sy, sz, sx = occ.shape
    y, z, x = np.meshgrid(np.arange(sy) + .5, np.arange(sz) + .5, np.arange(sx) + .5, indexing="ij")
    d = inside(x, y, z)                                       # signed: < 0 inside
    wrong = (occ & (d > band)) | (~occ & (d < -band))
    return int(wrong.sum()), int((np.abs(d) <= band).sum())


@case(2, "normal")
def sphere_matches_analytic():
    occ, r = o.voxelize(*o.sphere(r=1.0, sub=3), height=12)
    R = 6.0
    wrong, ambiguous = _vs_analytic(occ, lambda x, y, z: np.sqrt((x - 6) ** 2 + (y - 6) ** 2 + (z - 6) ** 2) - R, 0.12)
    band_cells = 4 * np.pi * R * R * 2 * 0.12                 # cells whose centre lies within the band (~109)
    assert r["size"] == [12, 12, 12] and wrong == 0 and ambiguous < 1.5 * band_cells, (wrong, ambiguous)


@case(2, "normal")
def torus_keeps_its_hole():
    occ, r = o.voxelize(*o.torus(R=1.0, r=0.35, n=64, m=32), width=27)
    c = 13.5
    s = 27 / 2.7

    def d(x, y, z):
        rr = np.sqrt((x - c) ** 2 + (z - c) ** 2)
        return np.sqrt((rr - s) ** 2 + (y - occ.shape[0] / 2) ** 2) - 0.35 * s
    wrong, amb = _vs_analytic(occ, d, 0.25)
    assert not occ[:, 13, 13].any() and wrong == 0, (wrong, amb)


@case(2, "normal")
def overlapping_shells_union():
    V, F = o.merge(o.box((0, 0, 0), (4, 4, 4)), o.box((2, 0, 0), (6, 4, 4)))
    occ, _ = o.voxelize(V, F, height=4)
    assert occ.sum() == 6 * 4 * 4, "overlap region must stay solid (per-shell parity, then OR)"


@case(2, "normal")
def hollow_cube_shell_thickness():
    occ = np.ones((9, 9, 9), bool)
    shell, cav = o.hollow(occ, 2)
    assert cav.sum() == 5 ** 3 and shell.sum() == 729 - 125 and o.wall_ok(shell, cav, 2) and not o.wall_ok(shell, cav, 3)


@case(2, "normal")
def big_cavity_refilled_small_kept():
    big = np.ones((11, 11, 11), bool)
    sh, cav = o.hollow(big, 2)
    sh2, cav2, n = o.cap_cavities(sh, cav)
    assert cav.sum() == 7 ** 3 and n == 343 and not cav2.any() and (sh2 == big).all()
    small = np.ones((7, 7, 7), bool)
    sh, cav = o.hollow(small, 2)
    sh2, cav2, n = o.cap_cavities(sh, cav)
    assert n == 0 and cav2.sum() == 27


@case(2, "normal")
def thin_parts_not_hollowed():
    occ = np.zeros((10, 3, 3), bool)
    occ[:, 1, 1] = True
    shell, cav = o.hollow(occ, 2)
    assert (shell == occ).all() and not cav.any()


@case(2, "normal")
def statue_folder_is_a_design_folder():
    d = o.EX / "cp69-statue-raw"
    names = {p.name for p in d.iterdir()}
    need = {"design.json", "design.litematic", "bom.json", "validation.json", "build_order.json", "gate.json",
            "previews", "organic.json", "brief.json"}
    assert need <= names, need - names
    g = Design.load(d / "design.json")                        # re-verifies the hash
    v = json.loads((d / "validation.json").read_text())
    rep = json.loads((d / "organic.json").read_text())
    assert v["passed"] and not v["skipped"] and v["design_hash"] == g.hash() == rep["design_hash"]
    assert json.loads((d / "build_order.json").read_text())["design_hash"] == g.hash()
    if (d / "scaffold.json").exists():
        assert json.loads((d / "scaffold.json").read_text())["provenance"]["scaffold_for"] == g.hash()
    assert all(o.sha256(d / f) == h for f, h in rep["files"].items() if f != "organic.json")
    assert rep["floating"] == [] and rep["voxelize"]["target"] == {"height": 16} and g.size[1] == 16


@case(2, "malformed")
def ambiguous_or_excessive_scale_fails_before_work():
    V, F = o.box()
    raises(lambda: o.voxelize(V, F), "ambiguous")
    raises(lambda: o.voxelize(V, F, height=4, width=4), "ambiguous")
    raises(lambda: o.voxelize(V, F, height=200), "exceeds")
    raises(lambda: o.voxelize(V, F, height=4, yaw=45), "multiple of 90")
    raises(lambda: o.voxelize(*o.sphere(sub=4), height=60, max_pairs=1000), "triangle-ray pairs")
    raises(lambda: o.voxelize(*o.box((0, 0, 0), (100, 1, 1)), height=1), "exceeds")


@case(2, "malformed")
def floating_part_fails_the_job():
    V, F = o.merge(o.box((0, 0, 0), (3, 3, 3)), o.box((0, 5, 0), (2, 7, 2)))
    job_from("float", V, F)
    raises(lambda: o.build(TMP / "job-float", TMP / "f-float", height=7), "floating")


@case(2, "malformed")
def thin_branch_tree_canopy_floats_without_cleanup():
    V, F = o.tree(1, branch_r=0.2)
    V, F = o.merge((V, F))
    occ, _ = o.voxelize(*o.normalize(V, F)[:2], height=10)
    # trunk reaches into the crown, so this tree stands; detach the crown to plant the fault
    occ[5] = False
    assert o.floating(occ), "crown cut from the trunk must be reported floating"


@case(2, "malformed")
def corner_only_contact_is_floating():
    occ = np.zeros((3, 3, 3), bool)
    occ[0, 0, 0] = occ[1, 1, 1] = True                        # touch at a corner only: no face to place against
    assert o.floating(occ) == [{"blocks": 1, "at": [1, 1, 1]}]


# ============================================================================================ stage 3: CP-70 palette + cleanup
@case(3, "normal")
def every_palette_block_is_legal_obtainable_and_does_not_fall():
    for name, p in o.PALETTES.items():
        ids = {p["fill"], p["plinth"]} | {b[k] for b in p["bands"] for k in ("top", "side", "under")}
        for b in ids:
            s = GD.canonical("minecraft:" + b + ("[axis=y]" if b.endswith("_log") else ""))
            g = Design((1, 1, 1))
            g.set(0, 0, 0, s)
            plan = bom.compute(g, GD, None, None, None)["deficit"]["gather_plan"]
            assert all(x["via"] != "unknown" for x in plan), (name, b, plan)
            assert not any(w in b for w in o.GRAVITY), (name, b)


@case(3, "normal")
def bands_and_faces():
    occ = np.ones((10, 3, 3), bool)
    a = o.assign(occ, occ, "stone")
    assert a[9, 1, 1] == "polished_andesite" and a[0, 0, 1] == "cobblestone" and a[5, 0, 1] == "stone_bricks"
    assert a[5, 1, 1] == "cobblestone", "hidden core = fill"
    used = {b for b in a.ravel() if b}
    pal = o.PALETTES["stone"]
    assert used <= {pal["fill"]} | {b[k] for b in pal["bands"] for k in ("top", "side", "under")}


@case(3, "normal")
def pinhole_and_void_filled_thin_pole_kept():
    occ = np.zeros((8, 7, 7), bool)
    occ[0:5, 1:6, 1:6] = True
    occ[2, 2, 2] = False                                      # enclosed void
    occ[3, 3, 1] = False                                      # pinhole in the west face
    occ[5:8, 3, 3] = True                                     # thin 1x1 pole on top: an intended feature
    c, r = o.cleanup(occ)
    assert r["voids_filled"] == 1 and r["pinholes_filled"] == 1 and c[5:8, 3, 3].all() and r["after"] == r["before"] + 2


@case(3, "normal")
def edge_joint_bridged():
    occ = np.zeros((4, 3, 3), bool)
    occ[0:2, 1, 1] = True
    occ[2, 1, 2] = occ[3, 1, 2] = True                        # meets the pole only along an edge
    c, r = o.cleanup(occ)
    assert r["bridge_blocks_added"] == 1 and not o.floating(c) and c[1, 1, 2]


@case(3, "malformed")
def one_block_noise_removed_but_big_floating_part_kept():
    occ = np.zeros((8, 6, 6), bool)
    occ[0:3, 0:3, 0:3] = True
    occ[6, 5, 5] = True                                       # 1-block noise
    occ[5:8, 0:2, 4:6] = True                                 # 12-block floating chunk
    c, r = o.cleanup(occ, max_fragment=4)
    assert r["fragment_blocks_removed"] == 1 and o.floating(c) == [{"blocks": 12, "at": [4, 5, 0]}]


@case(3, "normal")
def tree_example_counts_and_palette():
    rep = json.loads((o.EX / "cp70-tree" / "organic.json").read_text())
    c = rep["cleanup"]
    assert c["after"] == c["before"] + c["voids_filled"] + c["pinholes_filled"] + c["bridge_blocks_added"] - c["fragment_blocks_removed"]
    pal = o.PALETTES["tree"]
    allowed = {pal["fill"]} | {b[k] for b in pal["bands"] for k in ("top", "side", "under")}
    assert set(rep["palette_used"]) <= allowed and rep["validation"]["passed"]
    g = Design.load(o.EX / "cp70-tree" / "design.json")
    assert {s.split("[")[0].removeprefix("minecraft:") for s in g.counts()} == set(rep["palette_used"])
    assert sum(g.counts().values()) == rep["blocks"] == rep["hollow"]["shell"]


@case(3, "normal")
def logs_get_a_placeable_axis():
    g = Design.load(o.EX / "cp70-tree" / "design.json")
    for x, y, z, s, _ in g.cells():
        if "_log" in s:
            ax = s.split("axis=")[1][0]
            d = {"y": [(0, 1, 0), (0, -1, 0)], "x": [(1, 0, 0), (-1, 0, 0)], "z": [(0, 0, 1), (0, 0, -1)]}[ax]
            assert y == 0 or any(g.in_bounds(x + a, y + b, z + c) and g.get(x + a, y + b, z + c) != o.AIR for a, b, c in d), (x, y, z, s)


@case(3, "normal")
def thicken_legs_hold_up_a_body_on_thin_stubs():
    occ = np.zeros((9, 9, 9), bool)
    occ[5:8, 1:8, 1:8] = True                                 # body block
    for x, z in ((1, 1), (7, 1), (1, 7), (7, 7)):
        occ[0:3, z, x] = True                                 # four 1-wide stubs that stop short of the body
    t, r = o.thicken(occ)
    assert o.floating(occ) and r["floating_after"] == [] and not o.floating(t)
    assert len(r["legs"]) == 4 and all(l["size"] == [2, 2] and l["to_y"] == 4 for l in r["legs"])
    assert r["after"] == r["before"] + r["added"] and (t | occ).sum() == t.sum(), "fills air only, removes nothing"
    t2, r2 = o.thicken(t)
    assert (t2 == t).all() and r2["added"] == 0 and not r2["legs"], "nothing floats: no change"
    t3, r3 = o.thicken(occ)
    assert (t3 == t).all() and r3["legs"] == r["legs"], "deterministic"


@case(3, "malformed")
def thicken_reports_what_it_cannot_hold():
    occ = np.zeros((6, 1, 1), bool)                           # a 1x1 grid: no 2x2 square fits
    occ[4, 0, 0] = True
    t, r = o.thicken(occ)
    assert r["floating_after"] and r["added"] == 0


@case(3, "normal")
def plinth_option():
    job_from("cube", *o.box((0, 0, 0), (3, 3, 3)))
    rep = o.build(TMP / "job-cube", TMP / "f-plinth", height=3, palette="stone", plinth=True, hollow_t=0)
    g = Design.load(TMP / "f-plinth" / "design.json")
    assert g.size == (5, 4, 5) and g.get(0, 0, 0) == "minecraft:stone_bricks" and g.role_at(0, 0, 0) == "foundation"
    assert rep["validation"]["passed"], rep["validation"]


@case(3, "malformed")
def unknown_palette_refused():
    try:
        o.assign(np.ones((2, 2, 2), bool), np.ones((2, 2, 2), bool), "gold")
    except KeyError:
        return
    raise AssertionError("unknown palette accepted")


# ============================================================================================ stage 4: CP-71 combine
@case(4, "normal")
def tower_statue_example_valid_both_ways():
    d = o.EX / "cp71-tower-statue"
    rep = json.loads((d / "organic.json").read_text())
    assert rep["validation"]["passed"] and not rep["validation"]["skipped"], rep["validation"]
    cli = rep["cli_validation"]
    assert cli["passed"] and cli["same_blocks"] and cli["exit"] == 0, cli
    tower = Design.load(o.TOWER / "design.json")
    m = Design.load(d / "design.json")
    assert [p["design_hash"] for p in rep["combined"]][0] == tower.hash()
    assert len(m.rooms) == len(tower.rooms) and rep["combined"][1]["yaw"] == 90
    _part_preserved(tower, m)


def _part_preserved(part, m):
    ox, oy, oz = (a - b for a, b in zip(part.origin, m.origin))
    for x, y, z, s, role in part.cells():
        assert m.get(x + ox, y + oy, z + oz) == s and m.role_at(x + ox, y + oy, z + oz) == role, (x, y, z)


def fast_checks(m, brief):
    """The merged grid's own checks, without the planner (O285: the cabin's roof takes 21 min to plan and fail)."""
    return validate(m, GD, brief=brief, skip_buildable=True)["checks"]


@case(4, "normal")
def other_side_and_rotation():
    tree = o.EX / "cp72-tree"
    m, parts = o.merge_designs(o.CABIN, tree, (-11, 0, 2), GD, yaw=180, project="t")
    c = fast_checks(m, {**o.PLOT_BRIEF, "site": {**o.PLOT_BRIEF["site"], "plot": {"min": {"x": -14, "z": -6}, "max": {"x": 16, "z": 18}}}})
    assert parts[1]["yaw"] == 180 and m.size[0] == 24
    assert all(c[k]["status"] == "pass" for k in ("site_fit", "walkable", "enclosed", "lit", "functional", "stable")), c
    _part_preserved(Design.load(o.CABIN / "design.json"), m)


@case(4, "malformed")
def collision_fails():
    raises(lambda: o.merge_designs(o.CABIN, o.EX / "cp72-tree", (2, 0, 2), GD), "collides")


@case(4, "malformed")
def blocking_the_door_fails_validation():
    """A 1x2 pillar in the cell in front of the cabin's door must break walkable."""
    job_from("pillar", *o.box((0, 0, 0), (1, 2, 1)))
    o.build(TMP / "job-pillar", TMP / "f-pillar", height=2, palette="stone", hollow_t=0)
    cabin = Design.load(o.CABIN / "design.json")
    dx, dy, dz = next((x, y, z) for x, y, z, s, _ in cabin.cells() if "door" in s and "lower" in s)
    m, _ = o.merge_designs(o.CABIN, TMP / "f-pillar", (dx, dy, dz + 1), GD)     # the doorstep side (door on the south wall)
    assert fast_checks(m, o.PLOT_BRIEF)["walkable"]["status"] == "fail"


@case(4, "malformed")
def outside_the_plot_fails():
    m, _ = o.merge_designs(o.CABIN, o.EX / "cp72-tree", (15, 0, 2), GD)
    c = fast_checks(m, {**o.PLOT_BRIEF, "site": {**o.PLOT_BRIEF["site"], "plot": {"min": {"x": -4, "z": -6}, "max": {"x": 20, "z": 18}}}})
    assert c["site_fit"]["status"] == "fail", c["site_fit"]


@case(4, "malformed")
def hut5_sim_door_fails_fresh_alone_and_merged():
    """hut5_sim's south door (2,1,4) has no doorstep: a FRESH validate() fails walkable alone and merged alike (its stored
    validation.json predates the door rule; design keeps it byte-unchanged as a schematic checkpoint)."""
    at = {"x": 2, "y": 1, "z": 4}
    alone = validate(Design.load(o.HUT / "design.json"), GD, skip_buildable=True)["checks"]["walkable"]
    m, _ = o.merge_designs(o.HUT, o.EX / "cp72-tree", (8, 0, 1), GD)
    merged = fast_checks(m, o.PLOT_BRIEF)["walkable"]
    assert alone["status"] == "fail" and alone["failures"][0]["pos"] == at, alone
    assert merged["status"] == "fail" and merged["failures"][0]["pos"] == at, merged


# ============================================================================================ stage 5: CP-72 complete example
@case(5, "normal")
def tree_folder_complete_and_valid():
    d = o.EX / "cp72-tree"
    need = {"design.json", "design.litematic", "scaffold.json", "bom.json", "validation.json", "build_order.json",
            "gate.json", "previews", "organic.json", "brief.json", "PACKAGE.md", "approval.png"}
    assert need <= {p.name for p in d.iterdir()}, need
    m = Design.load(d / "design.json")
    rep = json.loads((d / "organic.json").read_text())
    v = json.loads((d / "validation.json").read_text())
    assert v["passed"] and not v["skipped"] and v["design_hash"] == m.hash() == rep["design_hash"]
    assert v["checks"]["stand_audit"]["status"] == "pass" and o.stand_audit(d)["on_exterior"] == 0
    assert json.loads((d / "scaffold.json").read_text())["provenance"]["scaffold_for"] == m.hash()
    assert json.loads((d / "build_order.json").read_text())["design_hash"] == m.hash()
    assert json.loads((d / "gate.json").read_text())["checks"]["plan_fresh"]["ok"]
    assert all(o.sha256(d / f) == h for f, h in rep["files"].items() if f != "organic.json")
    assert json.loads((d / "bom.json").read_text())["totals"]["final_blocks"] == sum(m.counts().values()), "one block per cell"


@case(5, "malformed")
def the_stand_audit_catches_the_old_cabin_plan():
    """The plan that failed live (C2): 272 stands on the design's own exterior, kept for this check."""
    assert o.stand_audit(o.EX / "cp72-cabin-tree")["on_exterior"] == 272


@case(5, "normal")
def tree_reproduces_byte_for_byte():
    o.build(o.EX / "cp68-tree-mesh", TMP / "cp72-tree", height=10, hollow_t=2, clean=True, palette="tree",
            origin=(10, 64, 2), brief=o.TREE_BRIEF)
    a = json.loads((o.EX / "cp72-tree" / "organic.json").read_text())
    b = json.loads((TMP / "cp72-tree" / "organic.json").read_text())
    assert a["design_hash"] == b["design_hash"]
    for f in ("design.json", "bom.json", "validation.json", "build_order.json", "scaffold.json"):
        assert a["files"][f] == b["files"][f], f


def main() -> int:
    global GD
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", type=int, nargs="*", default=[1, 2, 3, 4, 5])
    a = ap.parse_args()
    GD = gamedata.load()
    ok = True
    summary = {}
    for st in a.stage:
        n = {"normal": [0, 0], "malformed": [0, 0]}
        for kind, name, fn in CASES[st]:
            try:
                fn()
                n[kind][0] += 1
                print(f"PASS stage{st} {kind:9} {name}")
            except Exception as e:   # noqa: BLE001 - report every case
                ok = False
                n[kind][1] += 1
                print(f"FAIL stage{st} {kind:9} {name}: {type(e).__name__}: {e}")
                traceback.print_exc(limit=2, file=sys.stdout)
        summary[f"stage{st}"] = {k: {"pass": v[0], "fail": v[1]} for k, v in n.items()}
    print(json.dumps(summary))
    print("ALL PASS" if ok else "SOME FAILED", f"(temp {TMP})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
