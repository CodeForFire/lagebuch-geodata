#!/usr/bin/env bash
# Turn the downloaded DGM5 XYZ tiles into build/pack/elevation.tif, a
# Cloud Optimized GeoTIFF on DGM5's own 5 m grid. Runs in the toolbox image.
#
# Nothing is resampled: the output keeps EPSG:25832 and the 5 m cell edges of
# the source (cells span 650000-650005, the XYZ points are their centres), and
# gdalwarp only copies cells and blanks those outside the buffered area.
# validate_pack.py checks that against the source text files.
set -euo pipefail

BUILD=build
TXT_DIR="$BUILD/dgm5/txt"
TIF_DIR="$BUILD/dgm5/tif"
OUT="$BUILD/pack/elevation.tif"
NODATA=-9999
CELL=5

mkdir -p "$TIF_DIR" "$BUILD/pack"

echo "Converting $(find "$TXT_DIR" -name '*.txt' | wc -l) XYZ tiles to GeoTIFF ..."
# shellcheck disable=SC2016 # $1/$2 belong to the inner sh, not this script
find "$TXT_DIR" -name '*.txt' -print0 \
  | xargs -0 -P "$(nproc)" -I{} sh -c '
      out="$1/$(basename "$2" .txt).tif"
      [ -f "$out" ] || gdal_translate -q -of GTiff -a_srs EPSG:25832 -ot Float32 "$2" "$out"
    ' _ "$TIF_DIR" {}

find "$TIF_DIR" -name '*.tif' > "$BUILD/dgm5/tiles.lst"
gdalbuildvrt -q -input_file_list "$BUILD/dgm5/tiles.lst" "$BUILD/dgm5/mosaic.vrt"

# Snap the area's extent outwards onto the 5 m grid so gdalwarp's target grid
# is the source grid.
read -r XMIN YMIN XMAX YMAX < <(python3 - "$BUILD/area_25832.geojson" "$CELL" <<'PY'
import json, math, sys
from osgeo import ogr
geom = ogr.CreateGeometryFromJson(json.dumps(json.load(open(sys.argv[1]))["geometry"]))
cell = float(sys.argv[2])
min_x, max_x, min_y, max_y = geom.GetEnvelope()
print(math.floor(min_x / cell) * cell, math.floor(min_y / cell) * cell,
      math.ceil(max_x / cell) * cell, math.ceil(max_y / cell) * cell)
PY
)

gdalwarp -q -overwrite \
  -of GTiff -co TILED=YES -co COMPRESS=DEFLATE -co BIGTIFF=IF_SAFER \
  -te "$XMIN" "$YMIN" "$XMAX" "$YMAX" -tr "$CELL" "$CELL" -r near \
  -dstnodata "$NODATA" \
  -cutline "$BUILD/area_25832.geojson" -cutline_srs EPSG:25832 \
  "$BUILD/dgm5/mosaic.vrt" "$BUILD/dgm5/clipped.tif"

# EPSG:25832+7837 is ETRS89 / UTM 32N with DHHN2016 heights, which is what the
# Bayerische Vermessungsverwaltung publishes DGM5 in.
gdal_translate -q -of COG \
  -a_srs EPSG:25832+7837 \
  -co COMPRESS=DEFLATE -co PREDICTOR=YES -co LEVEL=9 \
  -co BLOCKSIZE=256 -co OVERVIEWS=NONE \
  -mo "SOURCE=DGM5, Bayerische Vermessungsverwaltung, www.geodaten.bayern.de, CC BY 4.0" \
  -mo "UNITS=metre" \
  "$BUILD/dgm5/clipped.tif" "$OUT"

echo "Wrote $OUT ($(du -h "$OUT" | cut -f1))"
