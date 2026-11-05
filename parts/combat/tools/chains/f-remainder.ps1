# S96 lane F chain: reset, then one series per case, each capped at 10 min (--max-minutes stops cleanly at a trial boundary); combat 77af8324 + body adbce44f (registry 0.15.4); n 20; stop on a death row, traceback, refusal or a failed reset
# Copy next to an export of parts/combat/tools on the 45L, after `lanef-fire up -By eve-combat` and the body is up, and run it detached (Win32_Process Create). Body's rig cases (reflex.py auto_*, s28_low_hp, ...) follow it per the install runbook.
Set-Location $PSScriptRoot
$py = 'C:\eve-lanes\F\src\parts\mind\.venv\Scripts\python.exe'
$env:PYTHONIOENCODING = 'utf-8'
$lc = @('--lane-config', 'parts\body\tools\lanes\F.json')
"=== START reset $(Get-Date -Format HH:mm:ss)" | Out-File -Append -Encoding utf8 chain.log
& $py -u parts\combat\tools\laneb_combat.py reset @lc *>&1 | ForEach-Object { "$_" } | Out-File -Append -Encoding utf8 chain.log
"=== END reset exit $LASTEXITCODE $(Get-Date -Format HH:mm:ss)" | Out-File -Append -Encoding utf8 chain.log
if ($LASTEXITCODE -ne 0) { '=== CHAIN STOPPED (reset failed)' | Out-File -Append -Encoding utf8 chain.log; '=== CHAIN DONE' | Out-File -Append -Encoding utf8 chain.log; exit 1 }
$c = $lc + @('--n', '20', '--max-minutes', '10', '--expect-jar', '77af83243e588792272072e6b3c115f7415bd27b72506879f4166f7b0c294a17', '--expect-body', 'adbce44fca6b48a0115cd2716d5769c1a79d60b79d38b33856f16207258a3dd0')
# x_creeper first: the O208 gate. Each entry is one case; a list of two-plus entries does not flatten (15:48 KeyError 'c' was a one-element list)
$runs = @(@('cp28m', '--only', 'x_creeper'), @('cp28m', '--only', 'x_archer'), @('cp28m', '--only', 'x_spider'), @('cp27c', '--only', 'c_volley'), @('cp27c', '--only', 'c_rush'), @('cp26v', '--only', 'm_behind'), @('cp26v', '--only', 'm_side'), @('cp26v', '--only', 'm_far'), @('cp26v', '--only', 'm_wall'))
foreach ($a in $runs) {
  "=== START $($a -join ' ') $(Get-Date -Format HH:mm:ss)" | Out-File -Append -Encoding utf8 chain.log
  & $py -u parts\combat\tools\laneb_combat.py @a @c *>&1 | ForEach-Object { "$_" } | Out-File -Append -Encoding utf8 chain.log
  "=== END $($a -join ' ') exit $LASTEXITCODE $(Get-Date -Format HH:mm:ss)" | Out-File -Append -Encoding utf8 chain.log
  if (Select-String -Path chain.log -CaseSensitive -Pattern 'deaths [1-9]|Traceback|not running|not up' -Quiet) { '=== CHAIN STOPPED' | Out-File -Append -Encoding utf8 chain.log; break }
}
'=== CHAIN DONE' | Out-File -Append -Encoding utf8 chain.log
