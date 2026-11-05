# Saves PNG frames of the dev Minecraft client window only (PrintWindow on its handle; nothing else on the desktop).
#   powershell -File tools/capture-window.ps1 -Out <dir> [-Frames 3] [-IntervalMs 1000]
param([Parameter(Mandatory)][string]$Out, [int]$Frames = 3, [int]$IntervalMs = 1000)
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class W {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
  public struct RECT { public int L, T, R, B; }
}
"@
$pid_ = (Get-CimInstance Win32_Process -Filter "name='java.exe'" | Where-Object { $_.CommandLine -match 'KnotClient' }).ProcessId
if (-not $pid_) { throw "dev client not running" }
$h = (Get-Process -Id $pid_).MainWindowHandle
New-Item -ItemType Directory -Force $Out | Out-Null
for ($i = 1; $i -le $Frames; $i++) {
  $r = New-Object W+RECT; [W]::GetClientRect($h, [ref]$r) | Out-Null
  $bmp = New-Object System.Drawing.Bitmap ([Math]::Max(1, $r.R - $r.L)), ([Math]::Max(1, $r.B - $r.T))
  $g = [System.Drawing.Graphics]::FromImage($bmp); $dc = $g.GetHdc()
  [W]::PrintWindow($h, $dc, 3) | Out-Null   # PW_CLIENTONLY | PW_RENDERFULLCONTENT
  $g.ReleaseHdc($dc); $g.Dispose()
  $f = Join-Path $Out ("frame-{0:yyyyMMdd-HHmmss-fff}.png" -f (Get-Date)); $bmp.Save($f); $bmp.Dispose(); $f
  Start-Sleep -Milliseconds $IntervalMs
}
