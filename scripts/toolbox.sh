#!/usr/bin/env bash
# Run a command inside the toolbox image (docker/toolbox.Dockerfile) with the
# repository mounted at /work. Builds the image on first use.
#
#   scripts/toolbox.sh python3 scripts/boundary_buffer.py
#   scripts/toolbox.sh osmium tags-filter ...
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="lagebuch-geodata-toolbox:local"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker build -q -t "$IMAGE" -f "$REPO_DIR/docker/toolbox.Dockerfile" "$REPO_DIR/docker" >/dev/null
fi

# Pass through only the variables the scripts read, never the whole
# environment: the CI runner's environment holds the GitHub token.
ENV_ARGS=()
for name in SLUG NAME RELATION_ID BUFFER_M MIN_ZOOM MAX_ZOOM PACK_VERSION OSM_TIMESTAMP; do
  if [ -n "${!name:-}" ]; then
    ENV_ARGS+=(-e "$name")
  fi
done

exec docker run --rm \
  --user "$(id -u):$(id -g)" \
  -v "$REPO_DIR":/work \
  -w /work \
  "${ENV_ARGS[@]}" \
  "$IMAGE" "$@"
