# O83f lane proof: eve-sense 0.1.20 + envelope v8 (home.doorway), the malformed-doorway refusals, and the ordering trap
# (sense 0.1.18 refuses v8). Any free Shark lane, a few minutes. The CALLER holds the lane lock (-By); this script never
# takes or releases it.
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\eveuser\Downloads\eve\parts\server-helper\tools\v8-lane-proof.ps1 -Lane E -By eve-watch
# Inputs, sha-checked (refuses on a mismatch):
#   -New eve-sense-0.1.20.jar bdb7e64a...  -Old eve-sense-0.1.18.jar 745f418b...  (both reproducible: bcc00d6 / 9e2f22e)
#   -V8  parts/foundation/ops/envelope-v8-doorway.json 9afa289e... (foundation fcf3fdc)
# The lane copies of v8 come from the lane's own envelope_laneb.py (world_id, account, lane zones), exactly as setup-laneb
# makes them, only from v8 instead of src\config\envelope.json. Expected output, one PASS line per check:
#   T1 old sense + lane v8           -> refused at startup: "home.doorway is not a known field"
#   T2 0.1.20 + 3 doorway cells      -> refused at startup: "home.doorway must hold one or two {x,y,z} cells"
#   T3 0.1.20 + non-integer cell     -> refused at startup: "x must be an integer in [-30000000, 30000000]"
#   T4 0.1.20 + lane v8 (3 copies)   -> log "envelope loaded: ... version=8", "link: hello.ok"; status "world binding: OK", "body: up"
#   "v8 lane proof: 7/7 PASS" (T1, T2, T3, T4a-d), then "restored: sense <previous jar>; world binding: OK ... | body: up ..."
# Always (finally): the lane's previous sense jar and its three envelope copies are put back byte for byte, then server,
# mind, client up, binding OK, Eve parked. Nothing is written to the world besides park-eve.
param([ValidateSet('B', 'C', 'D', 'E')][string]$Lane = 'E', [Parameter(Mandatory)][string]$By,
      [string]$Repo = 'C:\Users\eveuser\Downloads\eve', [string]$New, [string]$Old, [string]$V8)
$ErrorActionPreference = 'Stop'
if (-not $New) { $New = "$Repo\parts\server-helper\build\libs\eve-sense-0.1.20.jar" }
if (-not $Old) { $Old = "$Repo\parts\server-helper\build\libs\eve-sense-0.1.18.jar" }
if (-not $V8) { $V8 = "$Repo\parts\foundation\ops\envelope-v8-doorway.json" }
$H = @{ B = 'C:\eve-shark'; C = 'C:\eve-shark-c'; D = 'C:\eve-shark-d'; E = 'C:\eve-shark-e' }[$Lane]
$Sfx = @{ B = ''; C = ' C'; D = ' D'; E = ' E' }[$Lane]
$Dev = @{ B = 'dev-shark'; C = 'dev-shark-c'; D = 'dev-shark-d'; E = 'dev-shark-e' }[$Lane]
$Acct = @{ B = 'EveDev'; C = 'EveDev2'; D = 'EveDev3'; E = 'EveDev4' }[$Lane]
$ops, $R = "$H\ops", "$H\server-dev"
$py = "$H\src\parts\mind\.venv\Scripts\python.exe"
. "$ops\common.ps1"
$Envs = "$R\config\envelope.json", "$H\mind\config\envelope.json", "$H\src\parts\body\run\config\envelope.json"
$held = powershell -NoProfile -ExecutionPolicy Bypass -File "$ops\lane-lock.ps1" show
if ("$held" -notmatch "by=$([regex]::Escape($By))(\s|$)" -or "$held" -match 'expired') { throw "refused: lane $Lane is not held by $By ($held); take the lock first" }
foreach ($p in @(@($New, 'bdb7e64aaecb9528761b4e4dfbf2900d4fcbb2c5967ef36ed81198d46a8a3b32'), @($Old, '745f418bfc1990c05d8daa16c60bb96769defa73532e0eea72b542e5950211bf'),
                 @($V8, '9afa289ec9a693fe649ecc7141e9225ad7fa69f9f85e2af33fdfc0d230ca079d'))) {
  $got = (Get-FileHash $p[0] -Algorithm SHA256).Hash.ToLower()   # never $h: PowerShell names ignore case, $h IS $H (the lane root)
  if ($got -ne $p[1]) { throw "refused: $($p[0]) is $got, expected $($p[1])" }
}

$results = [System.Collections.Generic.List[bool]]::new()
function Check([string]$Name, [bool]$Ok, [string]$Got) { $results.Add($Ok); "{0} {1}: {2}" -f $(if ($Ok) { 'PASS' } else { 'FAIL' }), $Name, $Got }
function Stop-Lane {   # as fresh-world.ps1: client, body JVM, server, mind
  Stop-ScheduledTask "Eve Shark Client$Sfx"
  Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'java.exe', 'javaw.exe' -and $_.CommandLine -like "*$H\src\parts\body*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  if (Get-ServerProcess $R) { powershell -NoProfile -ExecutionPolicy Bypass -File "$ops\stop-server.ps1" -Root $R | Select-Object -Last 1 }
  Stop-ScheduledTask "Eve Shark Mind$Sfx"
  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*$H\ops\run-mind.ps1*" -or ($_.ExecutablePath -like "$H\src\parts\mind\*" -and $_.CommandLine -like '*-m eve_mind*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
}
function Up-Mind-Client {
  Start-ScheduledTask "Eve Shark Mind$Sfx"; Start-Sleep 6
  Start-ScheduledTask "Eve Shark Client$Sfx"
  $end = (Get-Date).AddSeconds(240)
  do { Start-Sleep 5; $st = powershell -NoProfile -ExecutionPolicy Bypass -File "$ops\eve.ps1" status } while ((Get-Date) -lt $end -and -not (($st -match 'world binding: OK') -and ($st -match 'body: up')))
  $st
}
function Set-Jar([string]$Jar) {   # exactly one eve-sense jar (devtools untouched)
  Get-ChildItem "$R\mods\eve-sense-[0-9]*.jar" | Remove-Item
  Copy-Item $Jar "$R\mods\"
}
function Expect-Refusal([string]$Name, [string]$Want) {   # start the server task, wait for sense's refusal (or an unexpected start)
  $since = Get-Date
  Start-ScheduledTask -TaskName (TaskName $R)
  $log, $line, $end = "$R\logs\latest.log", $null, (Get-Date).AddSeconds(120)
  while ((Get-Date) -lt $end -and -not $line) {
    Start-Sleep 2
    if (-not ((Test-Path $log) -and (Get-Item $log).LastWriteTime -ge $since)) { continue }
    $line = (Select-String $log -Pattern 'refusing to start' | Select-Object -First 1).Line
    if (-not $line -and (Select-String $log -Pattern 'RCON running on' -Quiet)) { $line = 'STARTED (not refused)' }
  }
  $end = (Get-Date).AddSeconds(30)
  while ((Get-ServerProcess $R) -and (Get-Date) -lt $end) { Start-Sleep 2 }
  if ($line -eq 'STARTED (not refused)') { powershell -NoProfile -ExecutionPolicy Bypass -File "$ops\stop-server.ps1" -Root $R | Out-Null }
  Get-ServerProcess $R | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  Check $Name ([bool]$line -and $line.Contains($Want)) "$line"
}

$bak = "$H\logs\v8proof-$(Get-Date -Format yyyyMMdd-HHmmss)"
New-Item -ItemType Directory -Force "$bak\mods", "$bak\env" | Out-Null
$oldJars = @(Get-ChildItem "$R\mods\eve-sense-[0-9]*.jar")
$oldJars | Copy-Item -Destination "$bak\mods\"
for ($i = 0; $i -lt 3; $i++) { if (Test-Path $Envs[$i]) { Copy-Item $Envs[$i] "$bak\env\$i.json" } }
"backup: $bak (sense jar $($oldJars.Name -join ', '))"

# Lane copies of v8: envelope_laneb.py on the v8 file, the lane's world_id, as setup-laneb does
$wid = (Get-Content $Envs[0] -Raw | ConvertFrom-Json).world_id
$env:EVE_LANE, $env:EVE_LANE_ACCOUNT = $Lane, $Acct
if (Test-Path "$ops\home-center.txt") { $env:EVE_HOME_CENTER = [IO.File]::ReadAllText("$ops\home-center.txt").Trim() } else { Remove-Item Env:EVE_HOME_CENTER -ErrorAction SilentlyContinue }
& $py "$ops\envelope_laneb.py" $V8 $wid "$bak\v8-lane.json"; if ($LASTEXITCODE) { throw 'envelope_laneb failed on v8' }
& $py -c @"
import json, sys
b = sys.argv[1]
e = json.load(open(b + r'\v8-lane.json', encoding='utf-8'))
assert e['envelope_version'] == 8 and len(e['home']['doorway']) == 2, 'lane v8 lost its version or doorway'
d = e['home']['doorway']
for name, dw in (('v8-3cells', d + [{'x': d[1]['x'], 'y': d[1]['y'] + 1, 'z': d[1]['z']}]), ('v8-float', [{'x': 1.5, 'y': d[0]['y'], 'z': d[0]['z']}])):
    e['home']['doorway'] = dw
    open(b + '\\' + name + '.json', 'w', encoding='utf-8', newline='\n').write(json.dumps(e, indent=2) + '\n')
print('lane v8 + 2 malformed variants written')
"@ $bak
if ($LASTEXITCODE) { throw 'could not make the v8 variants' }

try {
  Stop-Lane
  Set-Jar $Old; Copy-Item "$bak\v8-lane.json" $Envs[0] -Force
  Expect-Refusal 'T1 old sense 745f418b + v8 refused (the ordering trap)' 'home.doorway is not a known field'
  Set-Jar $New; Copy-Item "$bak\v8-3cells.json" $Envs[0] -Force
  Expect-Refusal 'T2 0.1.20 + 3 doorway cells refused' 'home.doorway must hold one or two {x,y,z} cells'
  Copy-Item "$bak\v8-float.json" $Envs[0] -Force
  Expect-Refusal 'T3 0.1.20 + non-integer doorway cell refused' 'must be an integer'
  foreach ($f in $Envs) { Copy-Item "$bak\v8-lane.json" $f -Force }
  powershell -NoProfile -ExecutionPolicy Bypass -File "$ops\start-server.ps1" $Dev -Root $R | Select-Object -First 1
  $loaded = (Select-String "$R\logs\latest.log" -Pattern 'envelope loaded: .* version=(\d+)' | Select-Object -Last 1).Line
  Check 'T4a 0.1.20 starts on v8: envelope_version 8' ("$loaded" -match 'version=8 ') "$loaded"
  $st = Up-Mind-Client
  $hello = (Select-String "$R\logs\latest.log" -Pattern 'link: hello.ok' | Select-Object -Last 1).Line
  Check 'T4b sense link hello.ok on v8' ([bool]$hello) "$hello"
  Check 'T4c mind binding OK on v8 (same hash in mind and sense)' ("$st" -match 'world binding: OK') (($st | Select-String 'world binding') -join '')
  Check 'T4d body up on v8' ("$st" -match 'body: up') (($st | Select-String 'body:') -join '')
} finally {
  "restoring lane $Lane ..."
  Stop-Lane
  Get-ChildItem "$R\mods\eve-sense-[0-9]*.jar" | Remove-Item
  Copy-Item "$bak\mods\*" "$R\mods\"
  for ($i = 0; $i -lt 3; $i++) { if (Test-Path "$bak\env\$i.json") { Copy-Item "$bak\env\$i.json" $Envs[$i] -Force } }
  powershell -NoProfile -ExecutionPolicy Bypass -File "$ops\start-server.ps1" $Dev -Root $R | Select-Object -First 1
  $st = Up-Mind-Client
  powershell -NoProfile -ExecutionPolicy Bypass -File "$ops\park-eve.ps1" -Root $R | Select-Object -Last 1
  "restored: sense $((Get-ChildItem "$R\mods\eve-sense-[0-9]*.jar").Name -join ', '); $(($st | Select-String 'world binding|body:') -join ' | ')"
}
"v8 lane proof: $(($results | Where-Object { $_ }).Count)/$($results.Count) $(if ($results.Count -eq 7 -and -not ($results -contains $false)) { 'PASS' } else { 'FAIL' })"
