#!/usr/bin/env bash
# Cut the worldwide sources in build/vector-sources/raw/ down to the features
# that touch the pack's area, into build/vector-sources/data/sources/, keeping
# each file's name and inner layout so the Protomaps profile opens them as if
# it had downloaded them itself. Runs in the toolbox image (ogr2ogr, zip).
#
# Features are selected by the area's bounding box plus a margin, not cut:
# Planetiler's --clip cuts every geometry to the area exactly. Expects
# build/area_4326.geojson.
set -euo pipefail

RAW=build/vector-sources/raw
OUT=build/vector-sources/data/sources
WORK=build/vector-sources/work
rm -rf "$OUT" "$WORK"
mkdir -p "$OUT" "$WORK"

# Bounding box of the buffered area, widened by 0.1° (~10 km) so a feature
# just outside it still reaches the tiles on the area's edge.
read -r WEST SOUTH EAST NORTH < <(python3 - <<'EOF'
import json
geometry = json.load(open("build/area_4326.geojson"))["geometry"]
polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
lons = [p[0] for poly in polygons for ring in poly for p in ring]
lats = [p[1] for poly in polygons for ring in poly for p in ring]
m = 0.1
print(min(lons) - m, min(lats) - m, max(lons) + m, max(lats) + m)
EOF
)
# OGC:CRS84, not EPSG:4326: GDAL 3 reads EPSG:4326 as latitude first, which
# turns this box into an empty one once it is reprojected for the
# EPSG:3857 land and water polygons.
SPAT=(-spat "$WEST" "$SOUTH" "$EAST" "$NORTH" -spat_srs OGC:CRS84)
echo "Selecting features within $WEST,$SOUTH,$EAST,$NORTH"

echo "Natural Earth ..."
mkdir -p "$WORK/ne/packages"
ogr2ogr -f GPKG "$WORK/ne/packages/natural_earth_vector.gpkg" \
  /vsizip/"$RAW"/natural_earth_vector.gpkg.zip/packages/natural_earth_vector.gpkg "${SPAT[@]}"
(cd "$WORK/ne" && zip -q -r ../natural_earth_vector.gpkg.zip packages)
mv "$WORK/natural_earth_vector.gpkg.zip" "$OUT/"

for kind in water land; do
  echo "OSM $kind polygons ..."
  dir="$kind-polygons-split-3857"
  mkdir -p "$WORK/$dir"
  # Cut to the area itself, not just selected by its box: inland, a land
  # polygon is a 100 km square, and Planetiler fills every tile under it with
  # "earth" past the reach of --clip, so the archive would claim land far
  # outside the pack.
  ogr2ogr -f "ESRI Shapefile" "$WORK/$dir/${kind}_polygons.shp" \
    /vsizip/"$RAW/$dir.zip/$dir/${kind}_polygons.shp" "${SPAT[@]}" \
    -clipsrc build/area_4326.geojson
  (cd "$WORK" && zip -q -r "$dir.zip" "$dir")
  mv "$WORK/$dir.zip" "$OUT/"
done

echo "Landcover ..."
ogr2ogr -f GPKG "$OUT/daylight-landcover.gpkg" "$RAW/daylight-landcover.gpkg" "${SPAT[@]}"

# Not spatial: QRank ranks places by Wikidata popularity, pgf-encoding holds
# font tables. Both are small and used as they are.
cp "$RAW/qrank.csv.gz" "$RAW/pgf-encoding.zip" "$OUT/"

rm -rf "$WORK"
du -sh "$OUT"/*
