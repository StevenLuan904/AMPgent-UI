#!/usr/bin/env bash
set -euo pipefail
BASE="${BASE:?}"; PY="${PY:?}"; WORKER="${WORKER:?}"; ROUND="${ROUND:?}"; EXPECTED_ROUND="${EXPECTED_ROUND:?}"
[ "$ROUND" = "$EXPECTED_ROUND" ] || { echo "ROUND_ENV_MISMATCH" >&2; exit 16; }
mkdir -p "$BASE/claims" "$BASE/acea" "$BASE/vegfa"
exec 9>"$BASE/round${ROUND}.lock"
flock -n 9 || { echo "ROUND${ROUND}_LOCK_REFUSE" >&2; exit 19; }
declare -A PIDS
for target in acea vegfa; do
  req="$BASE/r${ROUND}_${target}_request.json"; claim="$BASE/claims/${target}_claim.json"; out="$BASE/$target/output.json"; log="$BASE/$target/worker.log"
  queued_at=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
  [ ! -e "$claim" ] && [ ! -e "$out" ] || { echo "EXACT_ONCE_REFUSE $target" >&2; exit 18; }
  sha=$(sha256sum "$req" | awk '{print $1}'); proposal_round=$($PY -c 'import json,sys;print(json.load(open(sys.argv[1]))["proposal_round"])' "$req")
  [ "$proposal_round" = "$EXPECTED_ROUND" ] || { echo "ROUND${EXPECTED_ROUND}_REQUEST_REFUSE $target" >&2; exit 17; }
  printf '{"proposal_round":%s,"target":"%s","status":"queued","queued_at":"%s","request_sha256":"%s","launch_at":null,"started_at":null,"completed_at":null,"exit_code":null,"pid":null}\n' "$proposal_round" "$target" "$queued_at" "$sha" > "$claim"
  launch_at=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
  (exec env CUDA_VISIBLE_DEVICES= OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 nice -n 19 "$PY" "$WORKER" --request "$req" --output "$out" >"$log" 2>&1) </dev/null & pid=$!; PIDS[$target]=$pid; started_at=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
  "$PY" - "$claim" "$pid" "$launch_at" "$started_at" <<'PY'
import json,sys
p=json.load(open(sys.argv[1])); p.update(status='running',pid=int(sys.argv[2]),launch_at=sys.argv[3],started_at=sys.argv[4]); json.dump(p,open(sys.argv[1],'w'),sort_keys=True)
PY
done
rc=0
for target in acea vegfa; do
  claim="$BASE/claims/${target}_claim.json"; pid=${PIDS[$target]}; set +e; wait "$pid"; code=$?; set -e; [ "$code" -eq 0 ] || rc=1; completed_at=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
  "$PY" - "$claim" "$code" "$completed_at" <<'PY'
import json,sys
p=json.load(open(sys.argv[1])); p.update(status='completed' if int(sys.argv[2])==0 else 'failed',exit_code=int(sys.argv[2]),completed_at=sys.argv[3]); json.dump(p,open(sys.argv[1],'w'),sort_keys=True)
PY
done
exit "$rc"
