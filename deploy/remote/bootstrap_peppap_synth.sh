#!/usr/bin/env bash
set -euo pipefail

ROOT="/sdd_data/pepagent"
RUN_ID="peppap-official-example-v1"
RUN_DIR="$ROOT/runs/$RUN_ID"
REPO_DIR="$ROOT/sources/PepPAP"
WORK_DIR="$ROOT/worktrees/PepPAP-compat-v2"
WEIGHTS_DIR="$ROOT/sources/PepPAP-weights"
ENV_DIR="$ROOT/envs/peppap-py38-v6-upstream"
WEIGHTS_ARCHIVE="$ROOT/bootstrap/peppap-weights.tar.gz"
VIRTUALENV_ARCHIVE="$ROOT/bootstrap/virtualenv-wheels.tar.gz"
WEIGHTS_REVISION="2481e8cdbe9b1e400c0a82c02d5e44488fa5784c"

mkdir -p "$ROOT/sources" "$ROOT/worktrees" "$ROOT/envs" "$RUN_DIR"
exec > >(tee -a "$RUN_DIR/bootstrap.log") 2>&1

date --iso-8601=seconds
hostname
nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv

if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone https://github.com/ChunhuaLab/PepPAP.git "$REPO_DIR"
fi
if [[ -f "$WEIGHTS_ARCHIVE" ]]; then
  mkdir -p "$WEIGHTS_DIR"
  tar -xzf "$WEIGHTS_ARCHIVE" -C "$WEIGHTS_DIR"
elif [[ ! -d "$WEIGHTS_DIR/.git" ]]; then
  git lfs install --skip-repo
  git clone https://huggingface.co/SXH01/PepPAP "$WEIGHTS_DIR"
fi

if [[ ! -x "$ENV_DIR/bin/pip" ]]; then
  if [[ -f "$VIRTUALENV_ARCHIVE" ]]; then
    VIRTUALENV_WHEELS="$ROOT/bootstrap/virtualenv-wheels-py38"
    mkdir -p "$VIRTUALENV_WHEELS"
    tar -xzf "$VIRTUALENV_ARCHIVE" -C "$VIRTUALENV_WHEELS"
    python3 -m pip install --user --no-index --find-links "$VIRTUALENV_WHEELS" virtualenv==20.28.1
  else
    python3 -m pip install --user 'virtualenv<21'
  fi
  python3 -m virtualenv "$ENV_DIR"
fi
"$ENV_DIR/bin/python" -m pip install --upgrade 'pip<25' wheel setuptools
"$ENV_DIR/bin/python" -m pip install --no-deps \
  'https://download.pytorch.org/whl/cpu/torch-1.7.1%2Bcpu-cp38-cp38-linux_x86_64.whl'
"$ENV_DIR/bin/python" -m pip install \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  biopython==1.78 numpy==1.19.4 pandas==1.2.5 scikit-learn==1.2.2 scipy==1.5.4 \
  typing-extensions==4.13.2

if [[ ! -d "$WORK_DIR/.git" ]]; then
  git clone --local "$REPO_DIR" "$WORK_DIR"
fi
if grep -q "sys.path.append('./units')" "$WORK_DIR/codes/predicted.py"; then
  sed -i "s#sys.path.append('./units')#sys.path.append('./utils')#" "$WORK_DIR/codes/predicted.py"
fi
if grep -q "M = torch.load(filepath)$" "$WORK_DIR/codes/predicted.py"; then
  sed -i "s#M = torch.load(filepath)#M = torch.load(filepath, map_location='cpu')#" \
    "$WORK_DIR/codes/predicted.py"
fi
git -C "$WORK_DIR" diff -- codes/predicted.py > "$RUN_DIR/compatibility_patch.diff"

mkdir -p "$WORK_DIR/models" "$WORK_DIR/results"
find "$WEIGHTS_DIR" -maxdepth 2 -type f -name 'model*.pkl' -exec cp -f {} "$WORK_DIR/models/" \;
test "$(find "$WORK_DIR/models" -maxdepth 1 -type f -name 'model*.pkl' | wc -l)" -eq 5

git -C "$REPO_DIR" rev-parse HEAD > "$RUN_DIR/source_commit.txt"
if [[ -d "$WEIGHTS_DIR/.git" ]]; then
  git -C "$WEIGHTS_DIR" rev-parse HEAD > "$RUN_DIR/weights_commit.txt"
else
  printf '%s\n' "$WEIGHTS_REVISION" > "$RUN_DIR/weights_commit.txt"
fi
sha256sum "$WORK_DIR"/models/model*.pkl > "$RUN_DIR/weights.sha256"
"$ENV_DIR/bin/python" -m pip freeze > "$RUN_DIR/environment.txt"

rm -f "$WORK_DIR/results/predected_result.txt"
(
  cd "$WORK_DIR"
  export PATH="$ENV_DIR/bin:$PATH"
  bash run.sh
) | tee "$RUN_DIR/inference.log"

cp "$WORK_DIR/results/predected_result.txt" "$RUN_DIR/predected_result.txt"
sha256sum "$RUN_DIR/predected_result.txt" > "$RUN_DIR/output.sha256"
date --iso-8601=seconds > "$RUN_DIR/SUCCEEDED"
