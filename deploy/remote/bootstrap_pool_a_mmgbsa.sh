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
ENV_DIR="$root/mmgbsa-env" "$root/mmgbsa-env/bin/python" - <<'PY' > "$root/state/mmgbsa-env-versions.json"
import json, os
from pathlib import Path
import mdtraj, scipy
env = Path(os.environ["ENV_DIR"])
print(json.dumps({"mdtraj":mdtraj.__version__,"scipy":scipy.__version__,
 "MMPBSA.py":str(env / "bin/MMPBSA.py"),"MMPBSA.py_executable":(env / "bin/MMPBSA.py").exists(),
 "cpptraj":str(env / "bin/cpptraj"),"cpptraj_executable":(env / "bin/cpptraj").exists(),
 "sander":str(env / "bin/sander"),"sander_executable":(env / "bin/sander").exists()},indent=2))
PY
