#!/usr/bin/env bash
# Export the road network of the clipped extract as a routable graph. Runs in
# the toolbox image. Expects build/region.osm.pbf (build_osm_layers.sh) and
# build/area_4326.geojson; writes build/roads/roads.gpkg and build/roads_stats.json.
set -euo pipefail

BUILD=build
mkdir -p "$BUILD/roads"

# The same list as HIGHWAY_CLASSES in road_graph.py: public roads, service ways,
# tracks, and every way a hose can be carried along on foot.
HIGHWAYS=motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link,tertiary,tertiary_link,unclassified,residential,living_street,service,track,road,pedestrian,footway,path,steps,cycleway,bridleway

echo "Exporting the road graph ..."
osmium tags-filter --overwrite "$BUILD/region.osm.pbf" "w/highway=$HIGHWAYS" -o "$BUILD/roads.osm.pbf"
# Writes each node's location into the way (OPL: n<id>x<lon>y<lat>), and keeps
# only tagged nodes, which covers the barriers the graph needs.
osmium add-locations-to-ways --overwrite "$BUILD/roads.osm.pbf" -f opl,add_metadata=false -o "$BUILD/roads.opl"

python3 scripts/road_graph.py
rm -f "$BUILD/roads.osm.pbf" "$BUILD/roads.opl"
