#!/usr/bin/env bash
# O277 lane C chain (Shark, run from the repo root): the two creeper bars the body jar ships on. x_creeper first; any FAIL stops the
# chain, because a failed gate needs a fix before the suite means anything. Then the CP-29 suite, all cases, 4 trials each.
#   bash parts/combat/tools/chains/c-creeper.sh <body-sha256> [log]
# A failed gate or a death leaves the lock released -Next eve-combat; a clean end hands it to eve-watch.
BODY=${1:?body sha256 from the lane refresh}; LOG=${2:-evidence/c-creeper.log}
COMBAT=77af83243e588792272072e6b3c115f7415bd27b72506879f4166f7b0c294a17
export PYTHONIOENCODING=utf-8
PINS=(--lane c --expect-jar "$COMBAT" --expect-body "$BODY")
echo "=== START x_creeper $(date +%T)" | tee -a "$LOG"
py -3.12 -u parts/combat/tools/laneb_combat.py cp28m "${PINS[@]}" --n 10 --only x_creeper --max-minutes 10 --next eve-combat >> "$LOG" 2>&1
echo "=== END x_creeper exit $? passes $(grep -c '^PASS x_creeper' "$LOG") fails $(grep -c '^FAIL x_creeper' "$LOG") $(date +%T)" | tee -a "$LOG"
if [ "$(grep -c '^PASS x_creeper' "$LOG")" -lt 10 ] || grep -qE '^FAIL x_creeper|deaths [1-9]|Traceback|not running|not up' "$LOG"; then
  echo "=== CHAIN STOPPED at the gate $(date +%T)" | tee -a "$LOG"
  exit 1
fi
echo "=== START CP-29 $(date +%T)" | tee -a "$LOG"
py -3.12 -u parts/combat/tools/cp29_creeper.py "${PINS[@]}" --n 4 --max-minutes 10 --next eve-watch >> "$LOG" 2>&1
echo "=== END CP-29 exit $? $(date +%T)" | tee -a "$LOG"
echo "=== CHAIN DONE $(date +%T)" | tee -a "$LOG"
