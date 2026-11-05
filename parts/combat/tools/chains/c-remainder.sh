#!/usr/bin/env bash
# S96 lane C chain (watch's slot; Shark, run from the repo root): one series per case, each capped at 10 min (--max-minutes stops
# cleanly at a trial boundary, real n reported); x_creeper first (O208 gate, O259 bar), x_archer (shield), then the remainder.
# The harness takes and releases lane C's lock per series; -Next eve-combat holds it between series and eve-watch gets it after the last.
#   bash parts/combat/tools/chains/c-remainder.sh <body-sha256> [log] [n=10] [cap minutes=5] [first case index=0]   (O260: very short tests)
# Stops on a death row, a traceback or a refused start.
BODY=${1:?body sha256 from the lane refresh}; LOG=${2:-evidence/c-remainder.log}; N=${3:-10}; CAP=${4:-5}; FIRST=${5:-0}
COMBAT=77af83243e588792272072e6b3c115f7415bd27b72506879f4166f7b0c294a17
export PYTHONIOENCODING=utf-8
RUNS=("cp28m x_creeper" "cp28m x_archer" "cp28m x_spider" "cp27c c_volley" "cp27c c_rush" "cp26v m_behind" "cp26v m_side" "cp26v m_far" "cp26v m_wall")
for i in "${!RUNS[@]}"; do
  [ "$i" -lt "$FIRST" ] && continue
  set -- ${RUNS[$i]}
  next=eve-combat; [ "$i" -eq $((${#RUNS[@]} - 1)) ] && next=eve-watch
  echo "=== START $1 $2 $(date +%T)" | tee -a "$LOG"
  py -3.12 -u parts/combat/tools/laneb_combat.py "$1" --lane c --n "$N" --only "$2" --max-minutes "$CAP" --expect-jar "$COMBAT" --expect-body "$BODY" --next "$next" >> "$LOG" 2>&1
  echo "=== END $1 $2 exit $? $(date +%T)" | tee -a "$LOG"
  if grep -qE 'deaths [1-9]|Traceback|not running|not up' "$LOG"; then
    echo "=== CHAIN STOPPED" | tee -a "$LOG"
    powershell -NoProfile -ExecutionPolicy Bypass -File 'C:\eve-shark-c\ops\lane-lock.ps1' show 2>&1 | head -2 | tee -a "$LOG"
    break
  fi
done
echo "=== CHAIN DONE $(date +%T)" | tee -a "$LOG"
