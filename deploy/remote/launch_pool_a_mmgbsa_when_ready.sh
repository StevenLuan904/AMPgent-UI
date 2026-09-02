#!/usr/bin/env bash
set -euo pipefail
root="${1:?usage: launch_pool_a_mmgbsa_when_ready.sh ROOT}"
until [[ -x "$root/mmgbsa-env/bin/MMPBSA.py" ]] && "$root/mmgbsa-env/bin/python" -c 'import openmm,parmed' >/dev/null 2>&1; do
  sleep 60
done
exec "$root/mmgbsa-env/bin/python" "$root/inputs/supervise_pool_a_mmgbsa.py" \
  --results-root "$root/results" --python "$root/mmgbsa-env/bin/python" \
  --runner "$root/inputs/run_pool_a_mmgbsa.py" --amberhome "$root/mmgbsa-env" \
  --workers 4 --poll-seconds 120
