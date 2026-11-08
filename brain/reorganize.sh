#!/usr/bin/env bash
# One-time reorganisation of brain/ into one folder per axis:
#
#   brain/axes/<axis>/agent/    the orchestrator's agent for that axis (live Twin data)        [already in place]
#   brain/axes/<axis>/engine/   the axis team's own Brain, moved here unchanged (own simulation)
#   brain/control_center/       the unified dashboard
#
# Run once from anywhere:   bash brain/reorganize.sh            (or --dry-run to only print the moves)
# It only moves folders (mv) and removes folders left empty; nothing inside the engines is edited.
set -euo pipefail
BRAIN="$(cd "$(dirname "$0")" && pwd)"
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
OLD="$BRAIN/TSYP14BRAIN-v3/TSYP14BRAIN"
EMERG_OLD="$BRAIN/emergency-20261106T194052Z-1-001"

moves=(
  "$EMERG_OLD/emergency|$BRAIN/axes/emergency/engine"
  "$OLD/Trasnportation|$BRAIN/axes/transportation/engine"
  "$OLD/Pollution/triplet-XX|$BRAIN/axes/pollution/engine"
  "$OLD/Energy/Youssef/energy-brain-v0.7/energy-brain-v0.7|$BRAIN/axes/energy/engine"
  "$OLD/control_center|$BRAIN/control_center"
  "$OLD/README.md|$BRAIN/axes/ENGINES.md"
  "$OLD/Energy/Youssef/energy-brain-v0.7.zip|$BRAIN/axes/energy/docs/energy-brain-v0.7.zip"
  "$OLD/Energy/Youssef/Trust-Aware Predictive Energy Brain #U2014 Full Technical Report.pdf|$BRAIN/axes/energy/docs/Energy Brain - Full Technical Report.pdf"
)
say() { echo "$@"; }
do_() { if [ "$DRY" = 1 ]; then say "  would: $*"; else "$@"; fi; }

say "Reorganising $BRAIN"
for m in "${moves[@]}"; do
  src="${m%%|*}"; dst="${m##*|}"
  if [ -e "$dst" ]; then say "  skip: ${dst#$BRAIN/} already exists"; continue; fi
  if [ ! -e "$src" ]; then say "  skip: ${src#$BRAIN/} not found"; continue; fi
  do_ mkdir -p "$(dirname "$dst")"
  do_ mv "$src" "$dst"
  if [ "$DRY" = 0 ]; then say "  moved ${src#$BRAIN/}  ->  ${dst#$BRAIN/}"; fi
done

# old launchers (replaced by brain/axes/start_engines.sh, brain/start_brain.bat, brain/stop_brain.bat and ./start_city.sh)
for f in start_all.sh start_all.bat stop_all.bat; do
  if [ -e "$OLD/$f" ]; then do_ rm -f "$OLD/$f"; say "  removed old launcher TSYP14BRAIN-v3/TSYP14BRAIN/$f"; fi
done
# old agent files (the agents now live in brain/axes/<axis>/agent/)
for a in emergency transportation pollution energy; do
  f="$BRAIN/src/citybrain/agents/$a.py"
  if [ -e "$f" ]; then do_ rm -f "$f"; say "  removed src/citybrain/agents/$a.py (now brain/axes/$a/agent/$a.py)"; fi
done
if [ "$DRY" = 0 ]; then find "$BRAIN/src" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true; fi

# remove the folders left empty (never a folder that still holds a file)
for d in "$OLD/Energy/Youssef/energy-brain-v0.7" "$OLD/Energy/Youssef" "$OLD/Energy" "$OLD/Pollution" "$OLD" "$BRAIN/TSYP14BRAIN-v3" "$EMERG_OLD"; do
  if [ -d "$d" ]; then
    if do_ rmdir "$d" 2>/dev/null; then say "  removed empty ${d#$BRAIN/}"; else say "  kept ${d#$BRAIN/}: not empty, check what is left inside"; fi
  fi
done
chmod +x "$BRAIN/axes/start_engines.sh" "$BRAIN/../start_city.sh" 2>/dev/null || true
say "Done. Start everything with ./start_city.sh"
