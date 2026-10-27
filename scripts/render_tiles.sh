#!/usr/bin/env bash
# Render the tiles listed in build/tiles.txt from build/region.osm.pbf with the
# pinned OSM Carto tile server (docker/tile-server.Dockerfile), into
# build/tiles/{z}/{x}/{y}.png. Runs on the host, because it drives docker.
#
# render_list writes mod_tile's hashed metatiles, not a {z}/{x}/{y}.png tree,
# so the tiles are pre-rendered in bulk and then fetched one by one from
# mod_tile's own HTTP endpoint, which decodes them from that cache.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$REPO_DIR/build"
TILES_DIR="$BUILD/tiles"
IMAGE="lagebuch-geodata-tile-server:local"
CONTAINER="geodata-tile-server-$$"
VOLUME="geodata-osm-$$"
NPROC="$(nproc)"

docker build -q -t "$IMAGE" -f "$REPO_DIR/docker/tile-server.Dockerfile" "$REPO_DIR/docker" >/dev/null

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker volume rm "$VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Importing region.osm.pbf ..."
docker run --rm -e THREADS="$NPROC" \
  -v "$BUILD/region.osm.pbf":/data/region.osm.pbf:ro \
  -v "$VOLUME":/data/database/ \
  "$IMAGE" import

echo "Starting the tile server ..."
docker run -d --name "$CONTAINER" -e THREADS="$NPROC" \
  -p 127.0.0.1:0:80 -v "$VOLUME":/data/database/ "$IMAGE" run >/dev/null
PORT="$(docker port "$CONTAINER" 80/tcp | head -n1 | cut -d: -f2)"
BASE="http://127.0.0.1:$PORT/tile"

for _ in $(seq 1 90); do
  if docker exec "$CONTAINER" pgrep renderd >/dev/null 2>&1 \
     && curl -fsS -o /dev/null "$BASE/0/0/0.png"; then
    break
  fi
  sleep 2
done

# Without -a, render_list reads "x y z" lines from stdin and renders the 8x8
# metatile holding each of them -- once per line, so a list of single tiles
# would render the same metatile up to 64 times. Feed it one line per metatile.
awk '{ print int($1 / 8) * 8, int($2 / 8) * 8, $3 }' "$BUILD/tiles.txt" | sort -u > "$BUILD/metatiles.txt"
echo "Rendering $(wc -l < "$BUILD/tiles.txt") tiles in $(wc -l < "$BUILD/metatiles.txt") metatiles ..."
docker exec -i "$CONTAINER" render_list -n "$NPROC" < "$BUILD/metatiles.txt"

echo "Fetching tiles into $TILES_DIR ..."
while read -r x y z; do
  mkdir -p "$TILES_DIR/$z/$x"
  printf '%s\n%s\n' "$TILES_DIR/$z/$x/$y.png" "$BASE/$z/$x/$y.png"
done < "$BUILD/tiles.txt" | xargs -P "$NPROC" -n2 curl -fsS --retry 3 -o

echo "Tiles written under $TILES_DIR"
