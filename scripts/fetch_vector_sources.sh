#!/usr/bin/env bash
# Download the worldwide sources the Protomaps basemap profile reads besides
# OSM, into build/vector-sources/raw/ under the names the profile expects.
# Runs on the host (plain curl). clip_vector_sources.sh then cuts them to the
# pack's area, so Planetiler and the runner's disk only ever see a few MB of
# the 2.4 GB downloaded here.
set -euo pipefail

RAW=build/vector-sources/raw
mkdir -p "$RAW"

# fetch NAME URL [FALLBACK_URL ...]: try each URL in turn, so one dead host
# does not fail the build. --connect-timeout makes an unreachable host fail
# in seconds instead of curl's ~2 minute default per attempt.
fetch() {
  local name="$1" url
  shift
  if [ -s "$RAW/$name" ]; then
    echo "Have $name"
    return
  fi
  for url in "$@"; do
    echo "Fetching $name from $url ..."
    if curl -fsSL --connect-timeout 30 --retry 3 "$url" -o "$RAW/$name.part"; then
      mv "$RAW/$name.part" "$RAW/$name"
      return
    fi
    echo "Failed to fetch $name from $url" >&2
  done
  rm -f "$RAW/$name.part"
  echo "Could not fetch $name from any source" >&2
  return 1
}

# osmdata.openstreetmap.de has no official mirror. Fallbacks, in order:
# - www.osmdata.org: third-party mirror with the same paths; it stops getting
#   updates while the primary is down.
# - ftp.fau.de: FAU Erlangen's dated archive of the same files. There is no
#   "latest" link, so this names a fixed snapshot; bump the date when a newer
#   complete folder appears.
OSMDATA_MIRROR=https://www.osmdata.org/download
OSMDATA_ARCHIVE=https://ftp.fau.de/pub/openstreetmapdata.com-archive/2026-10-31

# The same URLs the profile's --download uses (Basemap.java at the pinned
# commit), plus fallbacks for the osmdata.openstreetmap.de files.
fetch natural_earth_vector.gpkg.zip https://naciscdn.org/naturalearth/packages/natural_earth_vector.gpkg.zip
fetch water-polygons-split-3857.zip \
  https://osmdata.openstreetmap.de/download/water-polygons-split-3857.zip \
  "$OSMDATA_MIRROR/water-polygons-split-3857.zip" \
  "$OSMDATA_ARCHIVE/water-polygons-split-3857.zip"
fetch land-polygons-split-3857.zip \
  https://osmdata.openstreetmap.de/download/land-polygons-split-3857.zip \
  "$OSMDATA_MIRROR/land-polygons-split-3857.zip" \
  "$OSMDATA_ARCHIVE/land-polygons-split-3857.zip"
fetch daylight-landcover.gpkg https://r2-public.protomaps.com/datasets/daylight-landcover.gpkg
fetch qrank.csv.gz https://qrank.toolforge.org/download/qrank.csv.gz
fetch pgf-encoding.zip https://wipfli.github.io/pgf-encoding/pgf-encoding.zip
