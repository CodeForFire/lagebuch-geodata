#!/usr/bin/env python3
"""Buffer the Landkreis boundary so an Einsatz just across the border still fits.

Runs in the toolbox image (needs GDAL/OGR with GEOS). Reads build/boundary.geojson
(WGS84) and BUFFER_M (default 3000). The buffer is computed in EPSG:25832, where
metres are metres. Writes:

- build/area_25832.geojson   buffered polygon, EPSG:25832 (elevation cutline, DGM5 tile selection)
- build/area_4326.geojson    buffered polygon, WGS84 (OSM extract, tile selection)
- build/area_simple.geojson  the same, simplified to ~20 m, for the manifest's boundaryUrl
"""
import json
import os
import sys

from osgeo import ogr, osr

ogr.UseExceptions()
osr.UseExceptions()

BUILD_DIR = "build"
SIMPLIFY_M = 20.0


def srs(epsg: int) -> osr.SpatialReference:
    ref = osr.SpatialReference()
    ref.ImportFromEPSG(epsg)
    ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)  # lon/lat, as GeoJSON is
    return ref


def write(path: str, geometry: ogr.Geometry, properties: dict) -> None:
    feature = {"type": "Feature", "properties": properties, "geometry": json.loads(geometry.ExportToJson())}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(feature, f)


def main() -> int:
    buffer_m = float(os.environ.get("BUFFER_M", "3000"))
    with open(os.path.join(BUILD_DIR, "boundary.geojson"), encoding="utf-8") as f:
        boundary = json.load(f)

    wgs84, utm32 = srs(4326), srs(25832)
    to_utm = osr.CoordinateTransformation(wgs84, utm32)
    to_wgs = osr.CoordinateTransformation(utm32, wgs84)

    geometry = ogr.CreateGeometryFromJson(json.dumps(boundary["geometry"]))
    geometry.Transform(to_utm)
    area = geometry.Buffer(buffer_m)
    if area is None or area.IsEmpty() or not area.IsValid():
        print("::error::Buffering the boundary produced no valid polygon.", file=sys.stderr)
        return 1

    write(os.path.join(BUILD_DIR, "area_25832.geojson"), area, {"crs": "EPSG:25832", "bufferMetres": buffer_m})

    simple = area.SimplifyPreserveTopology(SIMPLIFY_M)
    simple.Transform(to_wgs)
    area.Transform(to_wgs)
    write(os.path.join(BUILD_DIR, "area_4326.geojson"), area, {"bufferMetres": buffer_m})
    write(os.path.join(BUILD_DIR, "area_simple.geojson"), simple, {"bufferMetres": buffer_m})

    min_lon, max_lon, min_lat, max_lat = area.GetEnvelope()
    print(f"Buffered area: {buffer_m:.0f} m, bounds {min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
