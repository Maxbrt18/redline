#!/usr/bin/env bash
# Verifies that the running process matches the expected commit and the embedded
# Alembic schema exactly. Run after the systemd restart.
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
runtime_paths=(deploy src migrations alembic.ini pyproject.toml requirements.txt)
# The coach commits its notes as soon as a turn touches them, including a message
# replayed at startup: HEAD may therefore legitimately be ahead of the deployed SHA.
# What must be identical is the CODE.
git -C "$repo_root" merge-base --is-ancestor "$expected" HEAD || {
  echo "release: le checkout ne contient pas $expected" >&2
  exit 1
}
git -C "$repo_root" diff --quiet "$expected" HEAD -- "${runtime_paths[@]}" || {
  echo "release: le code du checkout diffère de $expected" >&2
  exit 1
}

# A dirty Markdown projection is normal; a dirty runtime never is.
git -C "$repo_root" diff --quiet -- "${runtime_paths[@]}"
git -C "$repo_root" diff --cached --quiet -- "${runtime_paths[@]}"
# Same fingerprint as `VaultRepo.runtime_tree`: sha256 of the three trees, separated
# by a space, without a trailing newline.
trees=$(git -C "$repo_root" rev-parse "$expected:deploy" "$expected:src" \
  "$expected:migrations" | paste -sd ' ' -)
expected_tree=$(printf '%s' "$trees" | { command -v sha256sum >/dev/null 2>&1 && sha256sum || shasum -a 256; } | cut -c1-12)

python_bin=${REDLINE_PYTHON:-python3}
"$repo_root/deploy/wait-ready.sh" "${REDLINE_READY_TIMEOUT_SECONDS:-90}"
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
