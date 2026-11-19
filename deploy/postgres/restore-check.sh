#!/usr/bin/env bash
# Restores one encrypted backup into a throwaway PostgreSQL 17 container and holds it to
# its manifest: same Alembic revision, same number of observations, same latest day.
#
#   deploy/postgres/restore-check.sh <redline-STAMP.dump.age> <age identity file>
#
# It runs where the private age key lives (the operator's workstation), never on the
# server, which only ever holds the public key. The container and the decrypted dump
# are removed on exit, whatever the outcome. The elapsed time it prints is the database
# part of the recovery time.
set -euo pipefail

[[ $# -eq 2 ]] || { sed -n '5p' "$0" | sed 's/^# *//' >&2; exit 2; }
archive="$1"
identity="$2"
manifest="$archive.manifest.json"
checksum="$archive.sha256"
image="${REDLINE_RESTORE_IMAGE:-postgres:17-alpine}"

die() {
  echo "restore-check: $*" >&2
  exit 1
}

for tool in age docker python3; do
  command -v "$tool" >/dev/null || die "$tool is missing"
done
[[ -r "$archive" ]] || die "unreadable archive: $archive"
[[ -r "$manifest" ]] || die "unreadable manifest: $manifest"
[[ -r "$identity" ]] || die "unreadable identity: $identity"

if [[ -r "$checksum" ]]; then
  verify=(sha256sum -c --quiet)
  command -v sha256sum >/dev/null || verify=(shasum -a 256 -c --quiet)
  (cd "$(dirname "$archive")" && "${verify[@]}" "$(basename "$checksum")") \
    || die "the checksum does not match: the archive or its manifest changed"
else
  echo "restore-check: no $checksum, integrity not checked" >&2
fi

field() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' \
    "$manifest" "$1"
}
expected_revision=$(field revision)
expected_observations=$(field observations)
expected_latest=$(field latest_observation)

started=$(date +%s)
container="redline-restore-check-$$"
password=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
trap 'docker rm --force "$container" >/dev/null 2>&1 || true' EXIT
docker run --detach --rm --name "$container" -e POSTGRES_PASSWORD="$password" \
  -e POSTGRES_DB=redline "$image" >/dev/null
for _ in $(seq 60); do
  docker exec "$container" pg_isready --username postgres --dbname redline \
    --host 127.0.0.1 >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$container" pg_isready --username postgres --dbname redline \
  --host 127.0.0.1 >/dev/null || die "the container's PostgreSQL did not start"

# The roles of the source cluster do not exist here: ownership and grants are dropped.
age --decrypt --identity "$identity" "$archive" \
  | docker exec -i "$container" pg_restore --username postgres --dbname redline \
      --exit-on-error --no-owner --no-privileges \
  || die "pg_restore failed"

observed=$(docker exec "$container" psql --username postgres --dbname redline \
  --tuples-only --no-align --field-separator='|' --command \
  "SELECT (SELECT version_num FROM alembic_version), count(*),
          coalesce(max(occurred_on)::text, 'none') FROM redline.observations")
IFS='|' read -r revision observations latest <<< "$observed"

problems=()
[[ "$revision" == "$expected_revision" ]] \
  || problems+=("revision $revision, manifest $expected_revision")
[[ "$observations" == "$expected_observations" ]] \
  || problems+=("observations $observations, manifest $expected_observations")
[[ "$latest" == "$expected_latest" ]] \
  || problems+=("latest observation $latest, manifest $expected_latest")
[[ ${#problems[@]} -eq 0 ]] || die "restored, but it differs from its manifest: ${problems[*]}"

echo "restore-check: $(basename "$archive") restored in $(( $(date +%s) - started )) s:" \
  "revision $revision, $observations observations, latest $latest"
