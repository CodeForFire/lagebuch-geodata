#!/usr/bin/env bash
# Build one pack end to end: boundary -> buffer -> DGM5 -> OSM layers and
# road graph -> vector tiles -> pack.json + zips -> validation. Expects SLUG, NAME and
# RELATION_ID in the environment (validated first). Produces
# build/<slug>-v<N>.zip, build/<slug>-roads-v<N>.zip and build/pack.json.
#
# Runs locally the way CI runs it; the host needs only bash, curl, python3 and
# docker. Every geodata tool runs in the pinned toolbox image.
set -euo pipefail

: "${SLUG:?}" "${NAME:?}" "${RELATION_ID:?}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."
TOOLBOX="$SCRIPT_DIR/toolbox.sh"

mkdir -p build

echo "== Validating inputs =="
python3 scripts/validate_inputs.py

PACK_VERSION="$(python3 scripts/manifest.py next-version)"
export PACK_VERSION
echo "Building $SLUG v$PACK_VERSION"

echo "== Looking up boundary =="
python3 scripts/lookup_boundary.py
"$TOOLBOX" python3 scripts/boundary_buffer.py

echo "== Elevation (DGM5) =="
"$TOOLBOX" python3 scripts/fetch_dgm5.py
"$TOOLBOX" bash scripts/build_elevation.sh

echo "== OSM extract =="
EXTRACT_PATH="$(python3 scripts/resolve_geofabrik_extract.py)"
echo "Using $EXTRACT_PATH"
# CI restores the day's extract into build/osm-extract from the Actions cache,
# so a refresh downloads it once instead of once per pack; Geofabrik refuses
# runners that download too often. Without a cached copy it is fetched here.
OSM_DIR=build/osm-extract
EXTRACT="$OSM_DIR/${EXTRACT_PATH//\//-}"
mkdir -p "$OSM_DIR"
if [ -s "$EXTRACT" ]; then
  echo "Have $EXTRACT"
else
  # --retry alone does not retry a failed connection (curl exit 7);
  # --retry-all-errors covers it.
  curl -fsSL --connect-timeout 30 --retry 5 --retry-delay 30 --retry-all-errors \
    "https://download.geofabrik.de/europe/germany/$EXTRACT_PATH" -o "$EXTRACT.part"
  mv "$EXTRACT.part" "$EXTRACT"
fi
# A file osmium cannot read must not be saved to the cache for the next pack.
if ! OSM_TIMESTAMP="$("$TOOLBOX" osmium fileinfo -g header.option.osmosis_replication_timestamp "$EXTRACT")"; then
  rm -f "$EXTRACT"
  echo "::error::$EXTRACT is not a readable OSM file" >&2
  exit 1
fi
export OSM_TIMESTAMP
mv "$EXTRACT" build/state.osm.pbf
"$TOOLBOX" bash scripts/build_osm_layers.sh
"$TOOLBOX" bash scripts/build_road_graph.sh
# Free disk, the tight resource on a runner. When CI still has to save the
# extract to the cache (KEEP_OSM_EXTRACT=1), it is kept for that step.
if [ "${KEEP_OSM_EXTRACT:-0}" = 1 ]; then
  mv build/state.osm.pbf "$EXTRACT"
else
  rm -f build/state.osm.pbf
fi

echo "== Vector tiles =="
bash scripts/fetch_vector_sources.sh
"$TOOLBOX" bash scripts/clip_vector_sources.sh
rm -rf build/vector-sources/raw # 2.4 GB of worldwide sources, cut to a few MB above
bash scripts/build_vector_tiles.sh
rm -rf build/vector-sources

echo "== Packaging =="
"$TOOLBOX" python3 scripts/make_pack_json.py
"$TOOLBOX" python3 scripts/validate_pack.py "build/$SLUG-v$PACK_VERSION.zip" build/dgm5/txt
"$TOOLBOX" python3 scripts/validate_roads.py "build/$SLUG-roads-v$PACK_VERSION.zip" "build/$SLUG-v$PACK_VERSION.zip"

echo "Built build/$SLUG-v$PACK_VERSION.zip and build/$SLUG-roads-v$PACK_VERSION.zip"
