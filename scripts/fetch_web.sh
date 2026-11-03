#!/usr/bin/env bash
# Put the ribbon interface (the `web` branch) into ./web, or update it.
#   ALFRED_REPO=https://github.com/parsabe/Alfred.git  ALFRED_WEB_BRANCH=web
set -euo pipefail
REPO="${ALFRED_REPO:-https://github.com/parsabe/Alfred.git}"
BRANCH="${ALFRED_WEB_BRANCH:-web}"
DEST="${1:-$(cd "$(dirname "$0")/.." && pwd)/web}"
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" pull --ff-only --quiet
else
  rm -rf "$DEST"
  git clone --quiet --depth 1 --branch "$BRANCH" "$REPO" "$DEST"
fi
echo "web interface: $DEST ($(git -C "$DEST" log -1 --format='%h %s'))"
