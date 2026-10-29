#!/usr/bin/env bash
# Clip the state extract to the buffered area, then export the water points
# and the addresses. Runs in the toolbox image. Expects build/state.osm.pbf and
# build/area_4326.geojson; writes build/region.osm.pbf (for the vector tiles),
# build/pack/water.geojson and build/pack/addresses.sqlite.
set -euo pipefail

BUILD=build
mkdir -p "$BUILD/pack"

echo "Clipping the extract to the buffered area ..."
# "smart" keeps multipolygons that cross the edge whole, so a lake or a
# building on the boundary is not cut in half.
osmium extract --overwrite --strategy smart \
  --polygon "$BUILD/area_4326.geojson" "$BUILD/state.osm.pbf" -o "$BUILD/region.osm.pbf"

echo "Exporting water points ..."
osmium tags-filter --overwrite "$BUILD/region.osm.pbf" \
  nwr/emergency=fire_hydrant,suction_point,water_tank,fire_water_pond \
  -o "$BUILD/water.osm.pbf"
osmium export --overwrite "$BUILD/water.osm.pbf" -f geojsonseq \
  --geometry-types=point,polygon --attributes=type,id -o "$BUILD/water.geojsonseq"

echo "Exporting addresses ..."
osmium tags-filter --overwrite "$BUILD/region.osm.pbf" nwr/addr:housenumber -o "$BUILD/addresses.osm.pbf"
osmium export --overwrite "$BUILD/addresses.osm.pbf" -f geojsonseq \
  --geometry-types=point,polygon --attributes=type,id -o "$BUILD/addresses.geojsonseq"

python3 scripts/osm_layers.py
