#!/usr/bin/env bash
set -euo pipefail

archive="${1:-}"
identity="${2:-}"
[[ -f "$archive" ]] || { echo "usage : $0 <backup.dump.age> <cle-age.txt>" >&2; exit 2; }
[[ -r "$identity" ]] || { echo "clé age illisible : $identity" >&2; exit 2; }
command -v age >/dev/null || { echo "age absent" >&2; exit 1; }
command -v docker >/dev/null || { echo "docker absent" >&2; exit 1; }

root=$(cd "$(dirname "$0")/../.." && pwd)
manifest="$archive.manifest.json"
checksum="$archive.sha256"
[[ -r "$manifest" && -r "$checksum" ]] || {
  echo "manifeste ou checksum adjacent absent" >&2
  exit 2
}
(cd "$(dirname "$archive")" && sha256sum --check "$(basename "$checksum")" >/dev/null)
expected_head=$("$root/.venv/bin/alembic" -c "$root/alembic.ini" heads | awk 'NR == 1 {print $1}')
manifest_revision=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["revision"])' "$manifest")
manifest_observations=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["observations"])' "$manifest")
manifest_latest=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["latest_observation"])' "$manifest")
[[ -n "$expected_head" && "$manifest_revision" == "$expected_head" \
   && "$manifest_observations" =~ ^[1-9][0-9]*$ \
   && "$manifest_latest" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}$ ]] || {
  echo "manifeste incompatible avec le code courant" >&2
  exit 1
}

container="redline-restore-drill-$$"
cleanup() { docker rm -f "$container" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run --detach --name "$container" \
  --env POSTGRES_PASSWORD=restore-drill-only postgres:17-bookworm >/dev/null
for _ in $(seq 1 30); do
  if docker exec "$container" pg_isready --username postgres >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$container" pg_isready --username postgres >/dev/null
docker exec "$container" createdb --username postgres redline_restore_audit
age --decrypt --identity "$identity" "$archive" \
  | docker exec -i "$container" pg_restore --username postgres \
      --dbname redline_restore_audit --exit-on-error --no-owner --no-privileges

revision=$(docker exec "$container" psql --username postgres --dbname redline_restore_audit \
  --tuples-only --no-align --command 'SELECT version_num FROM alembic_version')
observations=$(docker exec "$container" psql --username postgres --dbname redline_restore_audit \
  --tuples-only --no-align --command 'SELECT count(*) FROM redline.observations')
latest=$(docker exec "$container" psql --username postgres --dbname redline_restore_audit \
  --tuples-only --no-align --command 'SELECT max(occurred_on) FROM redline.observations')
[[ "$revision" == "$expected_head" && "$revision" == "$manifest_revision" \
   && "$observations" =~ ^[1-9][0-9]*$ \
   && "$observations" -ge "$manifest_observations" \
   && ( "$latest" > "$manifest_latest" || "$latest" == "$manifest_latest" ) ]] || {
  echo "restauration incohérente : schéma=$revision observations=$observations" >&2
  exit 1
}
echo "restauration validée : schéma $revision, $observations observation(s)"
