#!/usr/bin/env bash
# Vérifie que le processus actif correspond exactement au commit attendu et au
# schéma Alembic embarqué. À lancer après le redémarrage systemd.
set -euo pipefail

expected=${1:-}
case "$expected" in
  *[!0-9a-f]*|'')
    echo "usage : $0 <sha-git-complet>" >&2
    exit 2
    ;;
esac
[ "${#expected}" -eq 40 ] || {
  echo "release: le SHA attendu doit être complet (40 caractères)" >&2
  exit 2
}

repo_root=$(git rev-parse --show-toplevel)
runtime_paths=(server scripts migrations alembic.ini pyproject.toml)
# Le coach committe ses notes dès qu'un tour les touche, y compris un message rejoué au
# démarrage : HEAD peut donc légitimement dépasser le SHA déployé. Ce qui doit être
# identique, c'est le CODE.
git -C "$repo_root" merge-base --is-ancestor "$expected" HEAD || {
  echo "release: le checkout ne contient pas $expected" >&2
  exit 1
}
git -C "$repo_root" diff --quiet "$expected" HEAD -- "${runtime_paths[@]}" || {
  echo "release: le code du checkout diffère de $expected" >&2
  exit 1
}

# Une projection Markdown sale est normale ; du runtime sale ne l'est jamais.
git -C "$repo_root" diff --quiet -- "${runtime_paths[@]}"
git -C "$repo_root" diff --cached --quiet -- "${runtime_paths[@]}"
# Même empreinte que `VaultRepo.runtime_tree` : sha256 des trois arbres, séparés par
# une espace, sans fin de ligne.
trees=$(git -C "$repo_root" rev-parse "$expected:server" "$expected:scripts" \
  "$expected:migrations" | paste -sd ' ' -)
expected_tree=$(printf '%s' "$trees" | { command -v sha256sum >/dev/null 2>&1 && sha256sum || shasum -a 256; } | cut -c1-12)

python_bin=${REDLINE_PYTHON:-python3}
"$repo_root/server/wait-ready.sh" "${REDLINE_READY_TIMEOUT_SECONDS:-90}"
health=$(curl -fsS --max-time 3 http://127.0.0.1:8000/health)
ready=$(curl -fsS --max-time 3 http://127.0.0.1:8000/ready)

"$python_bin" - "$expected" "$expected_tree" "$health" "$ready" <<'PY'
import json
import sys

expected, expected_tree, health_raw, ready_raw = sys.argv[1:]
health = json.loads(health_raw)
ready = json.loads(ready_raw)

errors = []
if health.get("status") != "ok":
    errors.append("/health n'est pas ok")
if health.get("runtime_tree") != expected_tree:
    errors.append(
        f"code du processus {health.get('runtime_tree')!r}, "
        f"code attendu {expected_tree!r} ({expected[:12]})"
    )
for key in ("storage", "schema", "worker"):
    if ready.get(key) is not True:
        errors.append(f"/ready {key}={ready.get(key)!r}")
if ready.get("status") != "ready":
    errors.append(f"/ready status={ready.get('status')!r}")
if ready.get("revision") != ready.get("expected_revision"):
    errors.append(
        "révision PostgreSQL différente de la tête Alembic "
        f"({ready.get('revision')!r} != {ready.get('expected_revision')!r})"
    )

if errors:
    raise SystemExit("release: " + " ; ".join(errors))
print(
    "release vérifiée : "
    f"code={health['runtime_tree']} ({expected[:12]}) schéma={ready['revision']} "
    "worker=actif"
)
PY
