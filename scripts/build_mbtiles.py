#!/usr/bin/env python3
"""Pack the rendered tiles listed in build/tiles.txt into build/pack/map.mbtiles.

MBTiles 1.3 (https://github.com/mapbox/mbtiles-spec/blob/master/1.3/spec.md):
rows are stored in TMS numbering (row 0 = south), so tile_row = 2**z - 1 - y
for the XYZ y the renderer used. The metadata carries the attribution, so the
licence travels with the file even when it is copied out of the pack.

Env: NAME (display name), PACK_VERSION. Reads build/area_4326.geojson for
the bounds.
"""
import json
import os
import sqlite3
import sys

BUILD_DIR = "build"
ATTRIBUTION = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>-Mitwirkende (ODbL)'
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def bounds_of(geometry: dict) -> tuple[float, float, float, float]:
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    lons = [p[0] for poly in polygons for ring in poly for p in ring]
    lats = [p[1] for poly in polygons for ring in poly for p in ring]
    return min(lons), min(lats), max(lons), max(lats)


def main() -> int:
    name = os.environ.get("NAME", "region")
    version = os.environ.get("PACK_VERSION", "0")
    tiles_dir = os.path.join(BUILD_DIR, "tiles")
    out_path = os.path.join(BUILD_DIR, "pack", "map.mbtiles")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(os.path.join(BUILD_DIR, "area_4326.geojson"), encoding="utf-8") as f:
        west, south, east, north = bounds_of(json.load(f)["geometry"])

    if os.path.exists(out_path):
        os.remove(out_path)
    conn = sqlite3.connect(out_path)
    conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    conn.execute("CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)")
    conn.execute("CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)")

    zooms: set[int] = set()
    count = 0
    with open(os.path.join(BUILD_DIR, "tiles.txt"), encoding="ascii") as listing:
        for line in listing:
            x, y, z = (int(v) for v in line.split())
            with open(os.path.join(tiles_dir, str(z), str(x), f"{y}.png"), "rb") as f:
                data = f.read()
            if not data.startswith(PNG_SIGNATURE):
                print(f"::error::Tile {z}/{x}/{y} is not a PNG", file=sys.stderr)
                return 1
            conn.execute(
                "INSERT INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?, ?, ?, ?)",
                (z, x, (2**z - 1) - y, sqlite3.Binary(data)),
            )
            zooms.add(z)
            count += 1

    if not zooms:
        print("::error::No tiles to pack", file=sys.stderr)
        return 1

    metadata = {
        "name": name,
        "format": "png",
        "type": "baselayer",
        "version": version,
        "description": "LageBuch Kartenpaket: OpenStreetMap Carto",
        "attribution": ATTRIBUTION,
        "bounds": f"{west:.6f},{south:.6f},{east:.6f},{north:.6f}",
        "center": f"{(west + east) / 2:.6f},{(south + north) / 2:.6f},{min(zooms) + 2}",
        "minzoom": str(min(zooms)),
        "maxzoom": str(max(zooms)),
    }
    conn.executemany("INSERT INTO metadata (name, value) VALUES (?, ?)", metadata.items())
    conn.commit()
    conn.close()
    print(f"Wrote {out_path}: {count} tiles, zoom {min(zooms)}-{max(zooms)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
