#!/usr/bin/env bash
set -euo pipefail

message="${1:?commit message required}"
shift
if [[ "$#" -lt 1 ]]; then
  echo "usage: push-generated.sh <message> <file>..." >&2
  exit 1
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

for attempt in 1 2 3 4 5; do
  git fetch origin main
  git reset --mixed origin/main
  git add -- "$@"
  if git diff --cached --quiet; then
    echo "unchanged"
    exit 0
  fi
  git commit -m "${message}"
  if git push origin HEAD:main; then
    exit 0
  fi
  echo "push rejected, retry ${attempt}"
  sleep $((attempt * 8))
done

echo "push failed after retries" >&2
exit 1
