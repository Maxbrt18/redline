#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  echo "backup: ne pas lancer ce script en root ; utiliser systemctl start redline-backup.service" >&2
  exit 2
fi

compose_file="${REDLINE_POSTGRES_COMPOSE:-/home/ubuntu/redline/deploy/postgres/compose.yml}"
env_file="${REDLINE_POSTGRES_ENV:-/etc/redline/postgres.env}"
backup_dir="${REDLINE_BACKUP_DIR:-/var/lib/redline-backups}"
recipient_file="${REDLINE_AGE_RECIPIENT_FILE:-/etc/redline/secrets/backup-age-recipient.txt}"
retention_days="${REDLINE_BACKUP_RETENTION_DAYS:-3}"

command -v age >/dev/null || { echo "age absent" >&2; exit 1; }
command -v sha256sum >/dev/null || { echo "sha256sum absent" >&2; exit 1; }
[[ -r "$recipient_file" ]] || { echo "destinataire age illisible : $recipient_file" >&2; exit 1; }
[[ "$retention_days" =~ ^[1-9][0-9]*$ ]] || { echo "rétention invalide" >&2; exit 2; }

umask 077
install -d -m 0700 "$backup_dir"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
name="redline-${stamp}.dump.age"
manifest="$name.manifest.json"
temporary=$(mktemp "$backup_dir/.${name}.XXXXXX")
manifest_temporary=$(mktemp "$backup_dir/.${manifest}.XXXXXX")
trap 'rm -f "$temporary" "$manifest_temporary"' EXIT

snapshot=$(docker compose --env-file "$env_file" -f "$compose_file" exec -T \
  --user postgres postgres psql --username postgres --dbname redline \
  --tuples-only --no-align --field-separator='|' --command \
  "SELECT (SELECT version_num FROM alembic_version), count(*), \
   coalesce(max(occurred_on)::text, 'none') \
   FROM redline.observations")
IFS='|' read -r revision observations latest_observation <<< "$snapshot"
[[ "$revision" =~ ^[a-z0-9_]+$ && "$observations" =~ ^[1-9][0-9]*$ \
   && "$latest_observation" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}$ ]] || {
  echo "manifeste source incohérent : $snapshot" >&2
  exit 1
}
printf '{"created_at":"%s","revision":"%s","observations":%s,"latest_observation":"%s"}\n' \
  "$stamp" "$revision" "$observations" "$latest_observation" > "$manifest_temporary"

docker compose --env-file "$env_file" -f "$compose_file" exec -T --user postgres postgres \
  pg_dump --username postgres --dbname redline --format=custom --compress=9 \
  | age --encrypt --recipients-file "$recipient_file" --output "$temporary"

mv "$temporary" "$backup_dir/$name"
mv "$manifest_temporary" "$backup_dir/$manifest"
trap - EXIT
(
  cd "$backup_dir"
  sha256sum "$name" "$manifest" > "$name.sha256"
)

find "$backup_dir" -type f \( -name 'redline-*.dump.age' -o -name 'redline-*.dump.age.sha256' \
  -o -name 'redline-*.dump.age.manifest.json' \) \
  -mtime "+$retention_days" -delete
echo "$backup_dir/$name"
