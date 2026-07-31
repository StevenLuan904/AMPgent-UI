#!/usr/bin/env bash
set -euo pipefail

ROOT="/sdd_data/pepagent"
WORK="$ROOT/runs/pepmlm-smoke-v1"
MODEL="$ROOT/models/PepMLM-650M/898fca941a9057aebdd1a6164b5ee09a1a71780e"
PYTHON="$ROOT/envs/gpu-worker-py311-v1/bin/python"
mkdir -p "$WORK"
cat > "$WORK/request.json" <<JSON
{
  "target_sequence": "MKTRTQQIEELQKEWTQPRWEGITRPYSAEDVVKLRGSVNPECTLAQLGAAKMWRLLHGE",
  "peptide_length": 8,
  "count": 1,
  "seed": 20260731,
  "model": "$MODEL",
  "revision": "898fca941a9057aebdd1a6164b5ee09a1a71780e",
  "top_k": 3,
  "temperature": 1.0
}
JSON
CUDA_VISIBLE_DEVICES=7 "$PYTHON" -m pepagent.model_workers.pepmlm_cli \
  --request "$WORK/request.json" \
  --output "$WORK/result.json"
cat "$WORK/result.json"
