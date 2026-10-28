#!/usr/bin/env bash
set -euo pipefail

# Forced command of the GitHub Actions SSH key: no arbitrary command may run on the
# VPS with this identity.
[[ -z "${SSH_ORIGINAL_COMMAND:-}" ]] || {
  echo "commande distante interdite" >&2
  exit 64
}

backup_dir="${REDLINE_BACKUP_DIR:-/var/lib/redline-backups}"
max_age_seconds="${REDLINE_BACKUP_MAX_AGE_SECONDS:-10800}"
[[ -d "$backup_dir" ]] || { echo "répertoire de sauvegarde absent" >&2; exit 1; }
[[ "$max_age_seconds" =~ ^[1-9][0-9]*$ ]] || { echo "âge maximal invalide" >&2; exit 2; }

archive=$(find "$backup_dir" -maxdepth 1 -type f -name 'redline-*.dump.age' \
  -printf '%T@ %f\n' | sort -n | tail -1 | cut -d' ' -f2-)
[[ "$archive" =~ ^redline-[0-9]{8}T[0-9]{6}Z\.dump\.age$ ]] || {
  echo "aucune archive valide" >&2
  exit 1
}
checksum="$archive.sha256"
manifest="$archive.manifest.json"
[[ -f "$backup_dir/$checksum" ]] || { echo "checksum absent" >&2; exit 1; }
[[ -f "$backup_dir/$manifest" ]] || { echo "manifeste absent" >&2; exit 1; }

stamp=${archive#redline-}
stamp=${stamp%.dump.age}
stamp_epoch=$(date -u -d \
  "${stamp:0:4}-${stamp:4:2}-${stamp:6:2} ${stamp:9:2}:${stamp:11:2}:${stamp:13:2} UTC" +%s)
age_seconds=$(( $(date -u +%s) - stamp_epoch ))
(( age_seconds >= 0 && age_seconds <= max_age_seconds )) || {
  echo "dernière sauvegarde trop ancienne : ${age_seconds}s (> ${max_age_seconds}s)" >&2
  exit 1
}

(cd "$backup_dir" && sha256sum --check "$checksum" >/dev/null)
exec tar --create --file=- --directory="$backup_dir" "$archive" "$manifest" "$checksum"
