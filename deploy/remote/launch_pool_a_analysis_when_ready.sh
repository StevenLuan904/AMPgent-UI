#!/usr/bin/env bash
set -euo pipefail
root="${1:?usage: launch_pool_a_analysis_when_ready.sh ROOT}"
until "$root/analysis-env/bin/python" -c 'import mdtraj' >/dev/null 2>&1; do
  sleep 60
done
exec "$root/analysis-env/bin/python" "$root/inputs/supervise_pool_a_md_analysis.py" \
  --results-root "$root/results" \
  --python "$root/analysis-env/bin/python" \
  --analyzer "$root/inputs/analyze_pool_a_md.py" \
  --workers 2 --poll-seconds 120
