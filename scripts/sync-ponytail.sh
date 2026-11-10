#!/usr/bin/env bash
# Keep ponytail.md in sync with upstream (DietrichGebert/ponytail) and
# rebuild AGENTS.md from it. ponytail.md is a byte-exact copy of upstream's
# AGENTS.md, so the sync is a plain overwrite — no merge logic.
# One command does the whole job: sync, regenerate, commit, push.
#
# Usage: scripts/sync-ponytail.sh [--check | --no-push]
#   (no flag)  fetch upstream; if it differs, overwrite ponytail.md,
#              regenerate AGENTS.md, commit both files and push.
#              If nothing actually changed, no commit is made.
#   --check    fetch upstream and compare with ponytail.md; exit 0 if they
#              match, exit 1 if ponytail.md is stale. Makes no changes.
#   --no-push  sync and commit, but do not push.
# Run from the repo root. Needs network access to raw.githubusercontent.com.
# Refuses to run if ponytail.md or AGENTS.md has uncommitted changes,
# so it never overwrites work in progress.
set -euo pipefail

cd "$(dirname "$0")/.."

UPSTREAM_URL="https://raw.githubusercontent.com/DietrichGebert/ponytail/main/AGENTS.md"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

MODE="${1:-}"
if [ "$MODE" != "" ] && [ "$MODE" != "--check" ] && [ "$MODE" != "--no-push" ]; then
    echo "usage: $0 [--check | --no-push]" >&2
    exit 2
fi

if ! curl -fsSL --max-time 30 "$UPSTREAM_URL" -o "$TMP"; then
    echo "sync-ponytail: could not fetch $UPSTREAM_URL" >&2
    exit 2
fi

if cmp -s "$TMP" ponytail.md; then
    echo "ponytail.md is up to date with upstream."
    exit 0
fi

if [ "$MODE" = "--check" ]; then
    echo "ponytail.md is STALE — run scripts/sync-ponytail.sh to update it." >&2
    exit 1
fi

if ! git diff --quiet -- ponytail.md AGENTS.md; then
    echo "sync-ponytail: ponytail.md or AGENTS.md has uncommitted changes — commit or stash them first." >&2
    exit 2
fi

SHA="$(git ls-remote https://github.com/DietrichGebert/ponytail.git HEAD | cut -f1)"
cp "$TMP" ponytail.md
scripts/generate-agents-md.sh "$SHA"

if git diff --quiet -- ponytail.md AGENTS.md; then
    echo "ponytail.md restored to upstream @ ${SHA}; nothing to commit."
    exit 0
fi

git add ponytail.md AGENTS.md
git commit -m "chore: sync ponytail.md to upstream @ ${SHA:0:7}"
echo "Committed ponytail.md sync @ ${SHA:0:7}."

if [ "$MODE" != "--no-push" ]; then
    git push
    echo "Pushed."
fi
