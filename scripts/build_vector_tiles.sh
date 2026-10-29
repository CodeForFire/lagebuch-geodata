#!/usr/bin/env bash
# Build build/pack/map.pmtiles from build/region.osm.pbf with the pinned
# Protomaps basemap profile (docker/vector-tiles.Dockerfile). Runs on the
# host, because it drives docker. Expects build/area_4326.geojson and the
# sources clip_vector_sources.sh wrote under build/vector-sources/data/sources.
#
# Every geometry is cut to the area exactly (--clip-buffer=0), the same area
# the water points and addresses are complete in.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$REPO_DIR/build"
DATA="$BUILD/vector-sources/data"
IMAGE="lagebuch-geodata-vector-tiles:local"
MAX_ZOOM=15

docker build -q -t "$IMAGE" -f "$REPO_DIR/docker/vector-tiles.Dockerfile" "$REPO_DIR/docker" >/dev/null

# The archive's header bounds are the area's. Left alone, Planetiler takes the
# .osm.pbf header's, which osmium's "smart" extract widens to every relation
# that reaches into the area.
BOUNDS="$(python3 - "$BUILD/area_4326.geojson" <<'EOF'
import json, sys
geometry = json.load(open(sys.argv[1]))["geometry"]
polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
lons = [p[0] for poly in polygons for ring in poly for p in ring]
lats = [p[1] for poly in polygons for ring in poly for p in ring]
print(f"{min(lons)},{min(lats)},{max(lons)},{max(lats)}")
EOF
)"

# The profile finds its OSM input as data/sources/<area>.osm.pbf.
ln -f "$BUILD/region.osm.pbf" "$DATA/sources/region.osm.pbf"
mkdir -p "$BUILD/pack"

docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e JAVA_TOOL_OPTIONS="-Xmx3g" \
  -v "$DATA":/tiles/data \
  -v "$BUILD":/work/build \
  "$IMAGE" \
  --area=region \
  --bounds="$BOUNDS" \
  --clip=/work/build/area_4326.geojson --clip-buffer=0 \
  --maxzoom="$MAX_ZOOM" \
  --output=/work/build/pack/map.pmtiles --force

rm -f "$DATA/sources/region.osm.pbf"
rm -rf "$DATA/tmp"
echo "Wrote build/pack/map.pmtiles ($(du -h "$BUILD/pack/map.pmtiles" | cut -f1))"
