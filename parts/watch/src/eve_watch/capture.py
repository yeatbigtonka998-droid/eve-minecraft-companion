"""Grab Eve's game window (SDL/GLFW class, javaw) with PrintWindow: works while unfocused or covered, never focuses it,
never touches the mouse. Windows only; must run in the session that owns the window (Hunter's console session)."""
import ctypes
import ctypes.wintypes as W
import io
import subprocess
import threading
import time

from PIL import Image

PW_CLIENTONLY, PW_RENDERFULLCONTENT = 1, 2      # RENDERFULLCONTENT is what makes GPU (OpenGL) windows non-black
MAX_W = 960
GAME_CLASSES = ("SDL_app", "GLFW30")         # 26.3 client is SDL (seen on the 45L 2026-10-19); older ones GLFW

if hasattr(ctypes, "windll"):
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    u.SetProcessDPIAware()                       # real pixel sizes, not DPI-virtualised ones
    H = ctypes.c_void_p
    for f, res, args in [
        (u.GetDC, H, [H]), (u.ReleaseDC, ctypes.c_int, [H, H]), (u.PrintWindow, W.BOOL, [H, H, W.UINT]),
        (u.GetClientRect, W.BOOL, [H, ctypes.POINTER(W.RECT)]), (u.IsIconic, W.BOOL, [H]),
        (u.IsWindowVisible, W.BOOL, [H]), (u.GetClassNameW, ctypes.c_int, [H, W.LPWSTR, ctypes.c_int]),
        (u.GetWindowTextW, ctypes.c_int, [H, W.LPWSTR, ctypes.c_int]),
        (g.CreateCompatibleDC, H, [H]), (g.CreateCompatibleBitmap, H, [H, ctypes.c_int, ctypes.c_int]),
        (g.SelectObject, H, [H, H]), (g.DeleteObject, W.BOOL, [H]), (g.DeleteDC, W.BOOL, [H]),
        (g.GetDIBits, ctypes.c_int, [H, H, W.UINT, W.UINT, ctypes.c_void_p, ctypes.c_void_p, W.UINT]),
        (u.OpenInputDesktop, H, [W.DWORD, W.BOOL, W.DWORD]), (u.CloseDesktop, W.BOOL, [H]),
        (u.GetUserObjectInformationW, W.BOOL, [H, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.c_void_p]),
        (u.GetWindowThreadProcessId, W.DWORD, [H, ctypes.POINTER(W.DWORD)]),
    ]:
        f.restype, f.argtypes = res, args
    k = ctypes.windll.kernel32
    k.OpenProcess.restype, k.OpenProcess.argtypes = H, [W.DWORD, W.BOOL, W.DWORD]
    k.CloseHandle.argtypes = [H]
    k.GetProcessTimes.argtypes = [H] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4


def console_locked():
    """True when the input desktop is not ours ('Default'): the session is locked (Winlogon desktop) or on a UAC
    prompt. Only reads; never switches desktops."""
    d = u.OpenInputDesktop(0, False, 0x0001)     # DESKTOP_READOBJECTS; fails with access denied while locked
    if not d:
        return True
    try:
        name = ctypes.create_unicode_buffer(64)
        u.GetUserObjectInformationW(d, 2, name, ctypes.sizeof(name), None)   # UOI_NAME
        return name.value != "Default"
    finally:
        u.CloseDesktop(d)


def process_started_ms(h):
    """Wall-clock start (ms since epoch) of the process owning window h: the client's last (re)start."""
    pid = W.DWORD()
    u.GetWindowThreadProcessId(h, ctypes.byref(pid))
    p = k.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not p:
        return None
    try:
        t = [ctypes.c_ulonglong() for _ in range(4)]
        if not k.GetProcessTimes(p, *map(ctypes.byref, t)):
            return None
        return (t[0].value - 116444736000000000) // 10000   # FILETIME (100 ns since 1601) -> unix ms
    finally:
        k.CloseHandle(p)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG), ("biPlanes", W.WORD),
                ("biBitCount", W.WORD), ("biCompression", W.DWORD), ("biSizeImage", W.DWORD),
                ("biXPelsPerMeter", W.LONG), ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD),
                ("biClrImportant", W.DWORD)]


CLIENT_MATCH = None          # --client-match: only windows whose java process command line contains this text
_pids = (0.0, set())


def matching_pids():
    """PIDs of java processes whose command line contains CLIENT_MATCH, refreshed at most every 10 s.
    ponytail: one PowerShell/CIM call per 10 s instead of reading other processes' command lines via NT APIs."""
    global _pids
    if time.monotonic() - _pids[0] > 10:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='java.exe' or Name='javaw.exe'\" | Where-Object "
              f"{{ $_.CommandLine -like '*{CLIENT_MATCH.replace(chr(39), '')}*' }} | ForEach-Object {{ $_.ProcessId }}")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             timeout=20, creationflags=0x08000000).stdout          # CREATE_NO_WINDOW
        _pids = (time.monotonic(), {int(x) for x in out.split()})
    return _pids[1]


def find_window():
    """First visible game window titled Minecraft* (on the 45L that is Eve's client; on Shark, lane B passes
    --client-match because Hunter's own game and other dev clients run there too)."""
    hits, allowed = [], matching_pids() if CLIENT_MATCH else None

    @ctypes.WINFUNCTYPE(W.BOOL, H, H)
    def each(h, _):
        cls, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, cls, 64)
        u.GetWindowTextW(h, title, 256)
        if cls.value in GAME_CLASSES and title.value.startswith("Minecraft") and u.IsWindowVisible(h):
            pid = W.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if allowed is None or pid.value in allowed:
                hits.append((h, title.value))
        return True

    u.EnumWindows(each, None)
    return hits[0] if hits else (None, None)


def grab(h):
    """-> (PIL image or None, note)."""
    if u.IsIconic(h):
        return None, "game window is minimised (capture is black while minimised; restore it on the 45L)"
    r = W.RECT()
    u.GetClientRect(h, ctypes.byref(r))
    w, ht = r.right, r.bottom
    if w <= 0 or ht <= 0:
        return None, "game window has no client area"
    wdc = u.GetDC(h)
    mdc = g.CreateCompatibleDC(wdc)
    bmp = g.CreateCompatibleBitmap(wdc, w, ht)
    old = g.SelectObject(mdc, bmp)
    try:
        if not u.PrintWindow(h, mdc, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
            return None, "PrintWindow failed"
        bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -ht, 1, 32, 0, 0, 0, 0, 0, 0)  # top-down BGRX
        buf = ctypes.create_string_buffer(w * ht * 4)
        if not g.GetDIBits(mdc, bmp, 0, ht, buf, ctypes.byref(bi), 0):
            return None, "GetDIBits failed"
    finally:
        g.SelectObject(mdc, old)
        g.DeleteObject(bmp)
        g.DeleteDC(mdc)
        u.ReleaseDC(h, wdc)
    img = Image.frombuffer("RGB", (w, ht), buf, "raw", "BGRX", 0, 1)
    if img.getextrema() == ((0, 0), (0, 0), (0, 0)):
        return img, "frame is all black (screen locked, display off or the game is loading)"
    return img, "ok"


class Camera:
    """Captures on demand, at most every `min_s` seconds, shared by all viewers.
    ponytail: no background loop, so nothing runs while nobody watches; one global lock, fine for a few viewers."""

    def __init__(self, min_s=0.4):
        self.min_s, self.lock = min_s, threading.Lock()
        self.jpeg, self.at, self.note, self.title, self.size = None, 0.0, "not captured yet", None, None
        self.started_ms, self.locked, self.probe, self.last_raw = None, False, None, None

    def frame(self):
        with self.lock:
            if time.monotonic() - self.at >= self.min_s:
                self.at = time.monotonic()
                try:
                    h, self.title = find_window()
                    self.started_ms = process_started_ms(h) if h else None
                    img, self.note = grab(h) if h else (None, "client not running (no Minecraft window found)")
                    self.locked = console_locked()
                except OSError as e:
                    img, self.note = None, f"capture error: {e}"
                if self.locked and img is not None:
                    # Never show a picture while locked (it may be stale). Keep a one-word probe of what the grab
                    # returned, logged on change, so we learn what Windows gives us here.
                    raw = img.tobytes()
                    probe = ("black" if self.note != "ok" else "unchanged" if raw == self.last_raw else "changing")
                    self.last_raw, img = raw, None
                    if probe != self.probe:
                        print(f"{time.strftime('%H:%M:%S')} console locked; grab while locked: {probe}", flush=True)
                    self.probe = probe
                    self.note = "console locked (the 45L is locked; live view resumes when it is unlocked)"
                elif not self.locked and self.probe:
                    print(f"{time.strftime('%H:%M:%S')} console unlocked", flush=True)
                    self.probe = self.last_raw = None
                if img is not None:
                    self.size = img.size
                    if img.width > MAX_W:
                        img = img.resize((MAX_W, img.height * MAX_W // img.width), Image.BILINEAR)
                    out = io.BytesIO()
                    img.save(out, "JPEG", quality=70)
                    self.jpeg = out.getvalue()
                else:
                    self.jpeg = None
            return self.jpeg

    def info(self):
        return {"note": self.note, "window": self.title, "size": self.size, "client_started_ms": self.started_ms,
                "locked": self.locked, "locked_grab": self.probe,
                "age_ms": int((time.monotonic() - self.at) * 1000) if self.at else None}
