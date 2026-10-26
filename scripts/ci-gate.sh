#!/usr/bin/env bash
set -euo pipefail

# Un seul verdict final pour le code ; le contenu du vault se vérifie sur la machine
# qui le porte (scripts/vault-check.py).
# Usage : ci-gate.sh [SHA] [nom de workflow requis…]
# Code checks est toujours requis ; les autres runs déclenchés sont tous contrôlés.
sha=${1:-$(git rev-parse HEAD)}
if (( $# )); then shift; fi
required=("Code checks" "$@")
timeout=${REDLINE_CI_WAIT_SECONDS:-600}

command -v gh >/dev/null || { echo "ci-gate: gh absent" >&2; exit 2; }
git cat-file -e "${sha}^{commit}" 2>/dev/null || {
  echo "ci-gate: commit introuvable : $sha" >&2
  exit 2
}
[[ "$timeout" =~ ^[1-9][0-9]*$ ]] || {
  echo "ci-gate: REDLINE_CI_WAIT_SECONDS invalide : $timeout" >&2
  exit 2
}

sha=$(git rev-parse "${sha}^{commit}")
deadline=$((SECONDS + timeout))
previous=""
while :; do
  # group_by + max_by : une relance verte remplace un ancien run rouge du même
  # workflow. Aucun filtrage de workflow : Vault content et la sauvegarde comptent.
  runs=$(gh run list --commit "$sha" --limit 100 \
    --json databaseId,workflowName,status,conclusion,url \
    --jq 'group_by(.workflowName) | map(max_by(.databaseId)) | .[] | [.workflowName,.databaseId,.status,.conclusion,.url] | map(tostring) | join("|")')
  if [[ "$runs" != "$previous" ]]; then
    while IFS='|' read -r name run_id status conclusion url; do
      [[ -n "$name" ]] || continue
      echo "→ $name $run_id · $status ${conclusion:-} · $url"
    done <<< "$runs"
    previous=$runs
  fi

  pending=0
  missing=()
  for expected in "${required[@]}"; do
    found=0
    while IFS='|' read -r name _; do
      if [[ "$name" == "$expected" ]]; then found=1; break; fi
    done <<< "$runs"
    if (( ! found )); then missing+=("$expected"); pending=1; fi
  done
  while IFS='|' read -r name run_id status conclusion url; do
    [[ -n "$name" ]] || continue
    if [[ "$status" != "completed" ]]; then
      pending=1
    elif [[ "$conclusion" != "success" ]]; then
      gh run view "$run_id" --log-failed || true
      echo "ci-gate: $name $conclusion pour $sha · $url" >&2
      exit 1
    fi
  done <<< "$runs"

  if (( ! pending )); then
    echo "✓ Tous les workflows déclenchés sont verts pour $sha"
    exit 0
  fi
  if (( SECONDS >= deadline )); then
    echo "ci-gate: délai dépassé pour $sha (${timeout}s)" >&2
    if (( ${#missing[@]} )); then
      printf 'ci-gate: workflow requis absent : %s\n' "${missing[@]}" >&2
    fi
    exit 1
  fi
  sleep 2
done
