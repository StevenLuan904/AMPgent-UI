#!/usr/bin/env bash
set -euo pipefail

wait_pid="$1"
shift
while kill -0 "$wait_pid" 2>/dev/null; do
  sleep 120
done
exec "$@"
