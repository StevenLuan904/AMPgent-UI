#!/usr/bin/env bash
set -euo pipefail

runtime_root="${1:?usage: bootstrap_pepglad_v1.sh RUNTIME_ROOT}"
source_revision="bad015ca50c312a89482adb5220c3d907f13df5c"
checkpoint_archive_sha256="a610e079492a1b1ceab213aea3a0ab875846415e16470897bb54850557f462a0"
checkpoint_sha256="5f05dc0f678ed7a75c2ce8fc19f63cc145bd4568f75cbfc7f15aeacdddbd3cfe"
micromamba_archive_sha256="8761c382127e6363bd9e0a2451aa3ef90d071a79133f736e2f759a3bf13040dd"

mkdir -p "$runtime_root/bin" "$runtime_root/downloads" "$runtime_root/mamba-root"

micromamba="$runtime_root/bin/micromamba"
if [[ ! -x "$micromamba" ]]; then
  archive="$runtime_root/downloads/micromamba.tar.bz2"
  curl --fail --location --retry 3 \
    'https://micro.mamba.pm/api/micromamba/linux-64/2.9.0' \
    --output "$archive"
  echo "$micromamba_archive_sha256  $archive" | sha256sum --check --status
  tar -xjf "$archive" -C "$runtime_root" bin/micromamba
fi

source_root="$runtime_root/PepGLAD"
if [[ ! -d "$source_root/.git" ]]; then
  git clone https://github.com/THUNLP-MT/PepGLAD.git "$source_root"
fi
git -C "$source_root" fetch --quiet origin "$source_revision"
git -C "$source_root" checkout --quiet --detach "$source_revision"
observed_revision="$(git -C "$source_root" rev-parse HEAD)"
[[ "$observed_revision" == "$source_revision" ]]

checkpoint_archive="$runtime_root/downloads/checkpoints.zip"
if [[ ! -f "$checkpoint_archive" ]]; then
  curl --fail --location --retry 3 \
    'https://github.com/THUNLP-MT/PepGLAD/releases/download/v1.0/checkpoints.zip' \
    --output "$checkpoint_archive"
fi
echo "$checkpoint_archive_sha256  $checkpoint_archive" | sha256sum --check --status
if [[ ! -f "$source_root/checkpoints/codesign.ckpt" ]]; then
  unzip -q "$checkpoint_archive" 'checkpoints/*' -d "$source_root"
fi
echo "$checkpoint_sha256  $source_root/checkpoints/codesign.ckpt" \
  | sha256sum --check --status

environment_prefix="$runtime_root/env"
if [[ ! -x "$environment_prefix/bin/python" ]]; then
  MAMBA_ROOT_PREFIX="$runtime_root/mamba-root" "$micromamba" create --yes \
    --prefix "$environment_prefix" \
    --file "$source_root/env.yaml"
fi

MAMBA_ROOT_PREFIX="$runtime_root/mamba-root" "$micromamba" run \
  --prefix "$environment_prefix" python -m pip install --disable-pip-version-check \
  'pydantic>=2.9,<3'

MAMBA_ROOT_PREFIX="$runtime_root/mamba-root" "$micromamba" run \
  --prefix "$environment_prefix" python - <<'PY'
import json
import torch
import ray
import openmm
print(json.dumps({
    "torch": torch.__version__,
    "cuda_available": torch.cuda.is_available(),
    "ray": ray.__version__,
    "openmm": openmm.__version__,
}))
PY

printf 'PEPGLAD_RUNTIME_ROOT=%s\n' "$runtime_root"
printf 'PEPGLAD_SOURCE_REVISION=%s\n' "$observed_revision"
printf 'PEPGLAD_PYTHON=%s\n' "$environment_prefix/bin/python"
printf 'PEPGLAD_CHECKPOINT=%s\n' "$source_root/checkpoints/codesign.ckpt"

