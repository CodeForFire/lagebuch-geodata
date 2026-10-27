#!/usr/bin/env python3
"""Check a built pack against the contract in FORMAT.md before it is released.

Runs in the toolbox image. Usage: validate_pack.py <pack.zip> [<dgm5 txt dir>]

With the DGM5 source directory (as in a build), it also compares the COG with
the publisher's own XYZ values at a few thousand cells, which proves that
nothing was shifted or resampled. Exits non-zero on the first broken rule.
"""
import hashlib
import json
import os
import random
import sqlite3
import struct
import sys
import tempfile
import zipfile

from osgeo import gdal

gdal.UseExceptions()
gdal.SetConfigOption("GDAL_PAM_ENABLED", "NO")  # never write .aux.xml next to the file

FORMAT_VERSION = 2
EXPECTED_FILES = {"map.mbtiles", "elevation.tif", "water.geojson", "addresses.sqlite", "boundary.geojson", "pack.json", "ATTRIBUTION.md"}
MAX_BOUNDARY_BYTES = 1024 * 1024  # the app refuses a larger one
DATA_FILES = EXPECTED_FILES - {"pack.json", "ATTRIBUTION.md"}
WATER_KINDS = {"fire_hydrant", "suction_point", "water_tank", "fire_water_pond"}
ADDRESS_COLUMNS = ["street", "housenumber", "postcode", "city", "suburb", "lat", "lon", "osm"]
NODATA = -9999.0
CELL = 5.0
SAMPLE_TILES = 40
SAMPLES_PER_TILE = 100
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PackError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PackError(message)


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inside(lon: float, lat: float, bounds: dict, slack: float = 1e-6) -> bool:
    return (
        bounds["west"] - slack <= lon <= bounds["east"] + slack
        and bounds["south"] - slack <= lat <= bounds["north"] + slack
    )


def check_zip(zip_path: str, out_dir: str) -> dict:
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        require(names == EXPECTED_FILES, f"zip holds {sorted(names)}, expected {sorted(EXPECTED_FILES)}")
        for name in names:
            archive.extract(name, out_dir)  # names checked against a fixed allowlist above
    with open(os.path.join(out_dir, "pack.json"), encoding="utf-8") as f:
        pack = json.load(f)
    require(pack.get("formatVersion") == FORMAT_VERSION, f"formatVersion {pack.get('formatVersion')!r}")
    for name in DATA_FILES:
        entry = pack["files"][name]
        path = os.path.join(out_dir, name)
        require(os.path.getsize(path) == entry["bytes"], f"{name}: size differs from pack.json")
        require(sha256(path) == entry["sha256"], f"{name}: sha256 differs from pack.json")
    return pack


def check_elevation(path: str, pack: dict, txt_dir: str | None) -> None:
    info = gdal.Info(path, format="json")
    require(info["metadata"].get("IMAGE_STRUCTURE", {}).get("LAYOUT") == "COG", "elevation.tif is not a COG")
    require(info["bands"][0]["type"] == "Float32", "elevation band is not Float32")
    require(info["bands"][0].get("noDataValue") == NODATA, "elevation NoData is not -9999")

    ds = gdal.Open(path)
    srs = ds.GetSpatialRef()
    require(srs.IsCompound(), "elevation CRS has no vertical part")
    horizontal = srs.Clone()
    horizontal.StripVertical()
    require(horizontal.GetAuthorityCode(None) == "25832", "elevation horizontal CRS is not EPSG:25832")
    require(srs.GetAuthorityCode("COMPD_CS|VERT_CS") == "7837", "elevation heights are not DHHN2016 (EPSG:7837)")
    x0, dx, rx, y0, ry, dy = ds.GetGeoTransform()
    require(dx == CELL and dy == -CELL and rx == 0 and ry == 0, "elevation is not a north-up 5 m grid")
    require(x0 % CELL == 0 and y0 % CELL == 0, "elevation grid is not aligned to DGM5's 5 m cells")
    require(pack["elevation"]["minX"] == x0 and pack["elevation"]["maxY"] == y0, "pack.json extent differs")

    if not txt_dir:
        return
    band = ds.GetRasterBand(1)
    tiles = sorted(n for n in os.listdir(txt_dir) if n.endswith(".txt"))
    rng = random.Random(42)
    compared = 0
    for name in rng.sample(tiles, min(SAMPLE_TILES, len(tiles))):
        with open(os.path.join(txt_dir, name), encoding="ascii") as f:
            lines = f.readlines()
        for line in rng.sample(lines, min(SAMPLES_PER_TILE, len(lines))):
            x, y, z = (float(v) for v in line.split())
            col, row = int((x - x0) // CELL), int((y0 - y) // CELL)
            if not (0 <= col < ds.RasterXSize and 0 <= row < ds.RasterYSize):
                continue
            (value,) = struct.unpack("<f", band.ReadRaster(col, row, 1, 1, buf_type=gdal.GDT_Float32))
            if value == NODATA:
                continue  # outside the buffered area
            require(abs(value - z) < 0.005, f"elevation at {x},{y} is {value}, DGM5 says {z}")
            compared += 1
    require(compared > SAMPLE_TILES * SAMPLES_PER_TILE // 4, f"only {compared} cells could be compared")
    print(f"  elevation matches DGM5 at {compared} sampled cells")


def check_map(path: str, pack: dict) -> None:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        meta = dict(conn.execute("SELECT name, value FROM metadata"))
        zooms = [z for (z,) in conn.execute("SELECT DISTINCT zoom_level FROM tiles ORDER BY 1")]
        bad = conn.execute("SELECT COUNT(*) FROM tiles WHERE substr(tile_data, 1, 8) != ?", (PNG_SIGNATURE,)).fetchone()[0]
    require(meta.get("format") == "png", "map.mbtiles format is not png")
    require("OpenStreetMap" in meta.get("attribution", ""), "map.mbtiles has no OSM attribution")
    expected = list(range(pack["map"]["minZoom"], pack["map"]["maxZoom"] + 1))
    require(zooms == expected, f"map.mbtiles has zooms {zooms}, expected {expected}")
    require(bad == 0, f"{bad} tiles in map.mbtiles are not PNG")


def check_water(path: str, pack: dict) -> None:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    require(data.get("type") == "FeatureCollection", "water.geojson is not a FeatureCollection")
    hydrants = 0
    for feature in data["features"]:
        geometry = feature["geometry"]
        require(geometry["type"] == "Point", "water.geojson holds a non-Point feature")
        require(inside(*geometry["coordinates"], pack["bounds"]), f"water point {feature['properties'].get('osm')} outside bounds")
        kind = feature["properties"].get("emergency")
        require(kind in WATER_KINDS, f"unexpected water kind {kind!r}")
        hydrants += kind == "fire_hydrant"
    require(hydrants > 0, "water.geojson has no hydrants")
    print(f"  {len(data['features'])} water points, {hydrants} hydrants")


def check_addresses(path: str, pack: dict) -> None:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(addresses)")]
        count = conn.execute("SELECT COUNT(*) FROM addresses").fetchone()[0]
        b = pack["bounds"]
        outside = conn.execute(
            "SELECT COUNT(*) FROM addresses WHERE lon < ? OR lon > ? OR lat < ? OR lat > ?",
            (b["west"] - 1e-6, b["east"] + 1e-6, b["south"] - 1e-6, b["north"] + 1e-6),
        ).fetchone()[0]
    require(columns == ADDRESS_COLUMNS, f"addresses columns {columns}")
    require(count > 0, "addresses.sqlite is empty")
    require(outside == 0, f"{outside} addresses lie outside the pack bounds")
    print(f"  {count} addresses")


def check_boundary(path: str, pack: dict) -> None:
    require(os.path.getsize(path) <= MAX_BOUNDARY_BYTES, "boundary.geojson is larger than 1 MB")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    require(data.get("type") == "Feature", "boundary.geojson is not a Feature")
    geometry = data["geometry"]
    require(geometry["type"] in ("Polygon", "MultiPolygon"), f"boundary.geojson holds a {geometry['type']}")
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    points = 0
    for polygon in polygons:
        for ring in polygon:
            require(len(ring) >= 4 and ring[0] == ring[-1], "boundary.geojson has a ring that is not closed")
            for lon, lat, *_ in ring:
                require(inside(lon, lat, pack["bounds"]), f"boundary point {lon},{lat} outside bounds")
            points += len(ring)
    print(f"  boundary: {points} points")


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    zip_path = sys.argv[1]
    txt_dir = sys.argv[2] if len(sys.argv) == 3 else None
    with tempfile.TemporaryDirectory() as out_dir:
        try:
            pack = check_zip(zip_path, out_dir)
            check_elevation(os.path.join(out_dir, "elevation.tif"), pack, txt_dir)
            check_map(os.path.join(out_dir, "map.mbtiles"), pack)
            check_water(os.path.join(out_dir, "water.geojson"), pack)
            check_addresses(os.path.join(out_dir, "addresses.sqlite"), pack)
            check_boundary(os.path.join(out_dir, "boundary.geojson"), pack)
        except PackError as exc:
            print(f"::error::Pack validation failed: {exc}", file=sys.stderr)
            return 1
    print(f"{zip_path}: valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
