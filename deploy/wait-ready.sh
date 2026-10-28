#!/usr/bin/env bash
# Waits for real application readiness; systemd Type=simple does not.
set -euo pipefail

timeout_seconds=${1:-90}
if [[ ! "$timeout_seconds" =~ ^[1-9][0-9]*$ ]] || (( timeout_seconds > 600 )); then
    echo "usage : $0 [timeout-secondes entre 1 et 600]" >&2
    exit 2
fi

started_at=$SECONDS
deadline=$((started_at + timeout_seconds))
last_status=000
while (( SECONDS < deadline )); do
    last_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
        --connect-timeout 1 --max-time 2 http://127.0.0.1:8000/ready 2>/dev/null || true)
    if [[ "$last_status" == 200 ]]; then
        printf '/ready prêt après %d s\n' "$((SECONDS - started_at))"
        exit 0
    fi
    (( SECONDS >= deadline )) || sleep 1
done

printf '/ready indisponible après %d s (dernier HTTP %s)\n' \
    "$timeout_seconds" "$last_status" >&2
exit 1
