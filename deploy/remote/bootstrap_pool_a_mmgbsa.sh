#!/usr/bin/env bash
set -euo pipefail
root="${1:?usage: bootstrap_pool_a_mmgbsa.sh ROOT}"
mkdir -p "$root/runtime" "$root/state"
archive="$root/runtime/micromamba.tar.bz2"
binary="$root/runtime/bin/micromamba"
if [[ ! -x "$binary" ]]; then
  curl -fL --retry 8 --retry-delay 15 https://micro.mamba.pm/api/micromamba/linux-64/latest -o "$archive"
  tar -xjf "$archive" -C "$root/runtime" bin/micromamba
fi
export MAMBA_ROOT_PREFIX="$root/runtime/mamba-root"
"$binary" create -y -p "$root/mmgbsa-env" -c conda-forge \
  python=3.11 ambertools=26 openmm=8.3.1 mdtraj scipy
"$binary" list -p "$root/mmgbsa-env" --explicit > "$root/state/mmgbsa-env-explicit.txt"
"$root/mmgbsa-env/bin/python" - <<'PY' > "$root/state/mmgbsa-env-versions.json"
import json, shutil
import mdtraj, scipy
print(json.dumps({"mdtraj":mdtraj.__version__,"scipy":scipy.__version__,
 "MMPBSA.py":shutil.which("MMPBSA.py"),"cpptraj":shutil.which("cpptraj"),"sander":shutil.which("sander")},indent=2))
PY
