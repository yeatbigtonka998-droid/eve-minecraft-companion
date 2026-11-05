"""TripoSR image -> mesh for the organic job (`LocalCmd`), run with its own venv (D:/eve-organic/venv, see STATUS.md):
  D:/eve-organic/venv/Scripts/python organic/triposr_run.py IMAGE OUT.obj [--resolution 256]
Exit 0 = OUT written (+ OUT.json provenance); 4 = a guard refused; other = TripoSR failed.

Guards (orchestrator O-decision 2026-10-21): no lane reservation in force (C:/eve-shark/LANE-IN-USE.txt), no Java client
running, >= 8 GB free VRAM, and parts/watch/laneb/lane_health.py --headroom --by eve-organic says GO.
TripoSR (MIT, VAST-AI-Research/TripoSR, pinned commit in STATUS) is used unmodified: its compiled `torchmcubes`
is replaced in memory by scikit-image marching cubes, and `rembg` (background removal) by a stub: the input must already
be a cut-out (RGBA) or on a plain background."""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(os.environ.get("EVE_ORGANIC_TOOLS", "D:/eve-organic"))
REPO = Path(__file__).resolve().parents[3]
NEED_VRAM_MB = 8192


def lane_java() -> list[str]:
    """Command lines of Java processes that are Minecraft servers/clients (Gradle daemons etc. do not count)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='java.exe' or Name='javaw.exe'\" | "
          "ForEach-Object { $_.CommandLine }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
    return [l for l in out.splitlines() if re.search(r"fabric|minecraft|runClient|runServer|server-dev", l, re.I)]


def guards() -> list[str]:
    why = []
    lane = Path("C:/eve-shark/LANE-IN-USE.txt")
    if lane.exists():
        m = re.search(r"exp=(\d+)", lane.read_text(errors="replace"))
        if m and int(m.group(1)) > time.time():
            why.append(f"lane reserved: {lane.read_text(errors='replace').strip()[:160]}")
    if lane_java():
        why.append("a Minecraft/Fabric Java process is running (lane series): " + lane_java()[0][:120])
    q = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True)
    free = int(q.stdout.split()[0]) if q.returncode == 0 and q.stdout.strip() else 0
    if free < NEED_VRAM_MB:
        why.append(f"free VRAM {free} MB < {NEED_VRAM_MB} MB")
    if why:                                             # only ask lane_health (it logs every call) when all else is clear
        return why
    h = subprocess.run(["py", "-3.12", str(REPO / "parts/watch/laneb/lane_health.py"), "--headroom", "--by", "eve-organic",
                        "--job", "TripoSR image-to-mesh (GPU ~6 GB VRAM, <4 GB RAM, ~1 min)"],
                       capture_output=True, text=True)
    if h.returncode != 0:
        why.append("lane_health: " + (h.stdout + h.stderr).strip()[:200])
    return why


def shims():
    import numpy as np
    import torch
    from skimage.measure import marching_cubes as sk_mc

    def marching_cubes(vol, thresh):            # torchmcubes contract: verts in (axis2, axis1, axis0) index order
        v, f, _, _ = sk_mc(vol.detach().cpu().numpy(), level=thresh)
        return torch.from_numpy(np.ascontiguousarray(v[:, ::-1])).float(), torch.from_numpy(f.astype(np.int64))
    sys.modules["torchmcubes"] = types.SimpleNamespace(marching_cubes=marching_cubes)
    sys.modules["rembg"] = types.SimpleNamespace(new_session=None, remove=None)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def main() -> int:
    if sys.argv[1:] == ["--lane-busy"]:                # cheap poll for a waiting caller: exit 0 = a lane runs
        return 0 if lane_java() else 1
    image, out = Path(sys.argv[1]), Path(sys.argv[2])
    res = int(sys.argv[sys.argv.index("--resolution") + 1]) if "--resolution" in sys.argv else 256
    why = guards()
    if why:
        print("REFUSED: " + " | ".join(why), file=sys.stderr)
        return 4
    os.environ.setdefault("HF_HOME", str(ROOT / "hf"))
    os.environ["HF_HUB_OFFLINE"] = "1"                 # weights were fetched once at install; no network per job
    shims()
    sys.path.insert(0, str(ROOT / "TripoSR"))
    import numpy as np
    import torch
    from PIL import Image
    from huggingface_hub import hf_hub_download
    from tsr.system import TSR
    from tsr.utils import resize_foreground

    t0 = time.time()
    model = TSR.from_pretrained("stabilityai/TripoSR", config_name="config.yaml", weight_name="model.ckpt")
    model.renderer.set_chunk_size(8192)
    model.to("cuda")
    img = Image.open(image)
    if img.mode == "RGBA":                               # cut-out: same framing + grey background as TripoSR's run.py
        img = resize_foreground(img, 0.85)
        a = np.array(img).astype(np.float32) / 255.0
        img = Image.fromarray(((a[:, :, :3] * a[:, :, 3:4] + (1 - a[:, :, 3:4]) * 0.5) * 255).astype(np.uint8))
    else:
        img = img.convert("RGB")
    t1 = time.time()
    torch.cuda.reset_peak_memory_stats()
    with torch.no_grad():
        codes = model([img], device="cuda")
        mesh = model.extract_mesh(codes, False, resolution=res)[0]
    t2 = time.time()
    mesh.export(out)                                  # TripoSR's frame is Z-up (its demo app rotates before display):
                                                      # the organic job must be given --up z (see job.json params)
    ckpt = Path(hf_hub_download("stabilityai/TripoSR", "model.ckpt"))
    rev = subprocess.run(["git", "-C", str(ROOT / "TripoSR"), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    meta = {"tool": "TripoSR", "repo_commit": rev, "licence": "MIT (code and weights)", "weights_sha256": sha256(ckpt),
            "image_encoder": "facebook/dino-vitb16 (Apache-2.0)", "mc_resolution": res,
            "torch": torch.__version__, "gpu": torch.cuda.get_device_name(0), "load_s": round(t1 - t0, 1),
            "infer_s": round(t2 - t1, 1), "peak_vram_mb": int(torch.cuda.max_memory_allocated() / 2 ** 20),
            "vertices": int(len(mesh.vertices)), "faces": int(len(mesh.faces)), "shims": ["torchmcubes->skimage", "rembg stub"]}
    Path(str(out) + ".json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
