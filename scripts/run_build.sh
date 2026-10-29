#!/usr/bin/env bash
# Build one pack end to end: boundary -> buffer -> DGM5 -> OSM layers ->
# vector tiles -> pack.json + zip -> validation. Expects SLUG, NAME and
# RELATION_ID in the environment (validated first). Produces
# build/<slug>-v<N>.zip and build/pack.json.
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
curl -fsSL --retry 3 "https://download.geofabrik.de/europe/germany/$EXTRACT_PATH" -o build/state.osm.pbf
OSM_TIMESTAMP="$("$TOOLBOX" osmium fileinfo -g header.option.osmosis_replication_timestamp build/state.osm.pbf)"
export OSM_TIMESTAMP
"$TOOLBOX" bash scripts/build_osm_layers.sh
rm -f build/state.osm.pbf # free disk, the tight resource on a runner

echo "== Vector tiles =="
bash scripts/fetch_vector_sources.sh
"$TOOLBOX" bash scripts/clip_vector_sources.sh
rm -rf build/vector-sources/raw # 2.4 GB of worldwide sources, cut to a few MB above
bash scripts/build_vector_tiles.sh
rm -rf build/vector-sources

echo "== Packaging =="
"$TOOLBOX" python3 scripts/make_pack_json.py
"$TOOLBOX" python3 scripts/validate_pack.py "build/$SLUG-v$PACK_VERSION.zip" build/dgm5/txt

echo "Built build/$SLUG-v$PACK_VERSION.zip"
