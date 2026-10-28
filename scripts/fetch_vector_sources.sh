#!/usr/bin/env bash
# Download the worldwide sources the Protomaps basemap profile reads besides
# OSM, into build/vector-sources/raw/ under the names the profile expects.
# Runs on the host (plain curl). clip_vector_sources.sh then cuts them to the
# pack's area, so Planetiler and the runner's disk only ever see a few MB of
# the 2.4 GB downloaded here.
set -euo pipefail

RAW=build/vector-sources/raw
mkdir -p "$RAW"

fetch() {
  local name="$1" url="$2"
  if [ -s "$RAW/$name" ]; then
    echo "Have $name"
    return
  fi
  echo "Fetching $name ..."
  curl -fsSL --retry 3 "$url" -o "$RAW/$name.part"
  mv "$RAW/$name.part" "$RAW/$name"
}

# The same URLs the profile's --download uses (Basemap.java at the pinned commit).
fetch natural_earth_vector.gpkg.zip https://naciscdn.org/naturalearth/packages/natural_earth_vector.gpkg.zip
fetch water-polygons-split-3857.zip https://osmdata.openstreetmap.de/download/water-polygons-split-3857.zip
fetch land-polygons-split-3857.zip https://osmdata.openstreetmap.de/download/land-polygons-split-3857.zip
fetch daylight-landcover.gpkg https://r2-public.protomaps.com/datasets/daylight-landcover.gpkg
fetch qrank.csv.gz https://qrank.toolforge.org/download/qrank.csv.gz
fetch pgf-encoding.zip https://wipfli.github.io/pgf-encoding/pgf-encoding.zip
