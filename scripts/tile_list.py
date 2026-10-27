#!/usr/bin/env python3
"""List the XYZ map tiles that touch the buffered pack area.

Runs in the toolbox image (needs OGR). Reads build/area_simple.geojson and
MIN_ZOOM/MAX_ZOOM (default 11/17). Writes build/tiles.txt as "x y z" lines,
the format render_list reads on stdin. Every zoom is cut to the polygon, not
the bounding box: at z17 the corners of a Landkreis's box are most of its tiles.
"""
import json
import math
import os
import sys

from osgeo import ogr

ogr.UseExceptions()

BUILD_DIR = "build"


def tile_bounds(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    """(west, south, east, north) of an XYZ tile in degrees."""
    n = 2.0**z

    def lat(row: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * row / n))))

    return x / n * 360.0 - 180.0, lat(y + 1), (x + 1) / n * 360.0 - 180.0, lat(y)


def tile_at(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def box(west: float, south: float, east: float, north: float) -> ogr.Geometry:
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for lon, lat in ((west, south), (east, south), (east, north), (west, north), (west, south)):
        ring.AddPoint_2D(lon, lat)
    poly = ogr.Geometry(ogr.wkbPolygon)
    poly.AddGeometry(ring)
    return poly


def tiles_for(area: ogr.Geometry, min_zoom: int, max_zoom: int):
    west, east, south, north = area.GetEnvelope()
    for z in range(min_zoom, max_zoom + 1):
        x0, y0 = tile_at(west, north, z)
        x1, y1 = tile_at(east, south, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                if box(*tile_bounds(x, y, z)).Intersects(area):
                    yield x, y, z


def main() -> int:
    min_zoom = int(os.environ.get("MIN_ZOOM", "11"))
    max_zoom = int(os.environ.get("MAX_ZOOM", "17"))
    with open(os.path.join(BUILD_DIR, "area_simple.geojson"), encoding="utf-8") as f:
        area = ogr.CreateGeometryFromJson(json.dumps(json.load(f)["geometry"]))

    counts: dict[int, int] = {}
    with open(os.path.join(BUILD_DIR, "tiles.txt"), "w", encoding="ascii") as out:
        for x, y, z in tiles_for(area, min_zoom, max_zoom):
            out.write(f"{x} {y} {z}\n")
            counts[z] = counts.get(z, 0) + 1
    print("Tiles per zoom: " + ", ".join(f"z{z}={n}" for z, n in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
