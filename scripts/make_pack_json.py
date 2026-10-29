#!/usr/bin/env python3
"""Write build/pack/pack.json and build/pack/ATTRIBUTION.md, then zip the pack.

Runs in the toolbox image (reads the elevation extent through GDAL). Env: SLUG,
NAME, PACK_VERSION. Bounds come from the data itself, never from input, so the
manifest cannot disagree with the files.

Writes build/<slug>-v<version>.zip, holding exactly the files in PACK_FILES.
"""
import hashlib
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime, timezone

from osgeo import gdal

import pmtiles_read

gdal.UseExceptions()

BUILD_DIR = "build"
PACK_DIR = os.path.join(BUILD_DIR, "pack")
FORMAT_VERSION = 2
DATA_FILES = ("map.pmtiles", "elevation.tif", "water.geojson", "addresses.sqlite", "boundary.geojson")
# The same simplified area the manifest's boundaryUrl serves, so a pack carries its own.
BOUNDARY_SOURCE = os.path.join(BUILD_DIR, "area_simple.geojson")
PACK_FILES = DATA_FILES + ("pack.json", "ATTRIBUTION.md")
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates", "ATTRIBUTION.md")


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def vector_map(path: str) -> tuple[dict, dict]:
    """Zoom range and tile schema of map.pmtiles, and its bounds, read from the archive itself."""
    with open(path, "rb") as f:
        data = f.read()
    header = pmtiles_read.read_header(data)
    vector = {
        "minZoom": header["minZoom"],
        "maxZoom": header["maxZoom"],
        "schema": "protomaps-basemap",
        "schemaVersion": pmtiles_read.metadata(data, header)["version"],
    }
    return vector, header["bounds"]


def elevation_extent(path: str) -> dict:
    ds = gdal.Open(path)
    x0, dx, _, y0, _, dy = ds.GetGeoTransform()
    return {
        "crs": "EPSG:25832+7837",
        "minX": x0,
        "minY": y0 + dy * ds.RasterYSize,
        "maxX": x0 + dx * ds.RasterXSize,
        "maxY": y0,
        "cellSize": dx,
        "noData": ds.GetRasterBand(1).GetNoDataValue(),
    }


def main() -> int:
    slug = os.environ["SLUG"]
    name = os.environ["NAME"]
    version = int(os.environ["PACK_VERSION"])

    shutil.copyfile(BOUNDARY_SOURCE, os.path.join(PACK_DIR, "boundary.geojson"))
    for leftover in os.listdir(PACK_DIR):
        if leftover not in DATA_FILES:
            os.remove(os.path.join(PACK_DIR, leftover))  # e.g. a GDAL .aux.xml
    missing = [f for f in DATA_FILES if not os.path.exists(os.path.join(PACK_DIR, f))]
    if missing:
        print(f"::error::Pack is missing {', '.join(missing)}", file=sys.stderr)
        return 1

    # The archive's bounds are the buffered area's envelope (build_vector_tiles.sh).
    vector, bounds = vector_map(os.path.join(PACK_DIR, "map.pmtiles"))
    with open(os.path.join(BUILD_DIR, "dgm5.json"), encoding="utf-8") as f:
        dgm5 = json.load(f)
    with open(os.path.join(BUILD_DIR, "region.json"), encoding="utf-8") as f:
        region = json.load(f)
    osm_timestamp = os.environ.get("OSM_TIMESTAMP", "")

    pack = {
        "formatVersion": FORMAT_VERSION,
        "slug": slug,
        "name": name,
        "version": version,
        "builtAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "relationId": region["relationId"],
        "bounds": bounds,
        "vectorMap": vector,
        "elevation": elevation_extent(os.path.join(PACK_DIR, "elevation.tif")),
        "sources": {
            "osm": {"extractTimestamp": osm_timestamp, "licence": "ODbL-1.0"},
            "dgm5": {
                "metalinkPublished": dgm5["metalinkPublished"],
                "tiles": dgm5["tiles"],
                "missingTiles": dgm5["missingTiles"],
                "licence": "CC-BY-4.0",
            },
        },
        "files": {
            f: {"bytes": os.path.getsize(os.path.join(PACK_DIR, f)), "sha256": sha256(os.path.join(PACK_DIR, f))}
            for f in DATA_FILES
        },
    }
    with open(os.path.join(PACK_DIR, "pack.json"), "w", encoding="utf-8") as f:
        json.dump(pack, f, indent=2, ensure_ascii=False)
        f.write("\n")

    with open(TEMPLATE, encoding="utf-8") as f:
        attribution = f.read().replace("{{NAME}}", name).replace("{{OSM_TIMESTAMP}}", osm_timestamp or "unbekannt")
    with open(os.path.join(PACK_DIR, "ATTRIBUTION.md"), "w", encoding="utf-8") as f:
        f.write(attribution)

    zip_path = os.path.join(BUILD_DIR, f"{slug}-v{version}.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    # Gzipped vector tiles and the DEFLATE-compressed COG do not shrink
    # further, so store those and only compress the text and SQLite files.
    stored = {"map.pmtiles", "elevation.tif"}
    with zipfile.ZipFile(zip_path, "w") as archive:
        for f in PACK_FILES:
            archive.write(
                os.path.join(PACK_DIR, f),
                arcname=f,
                compress_type=zipfile.ZIP_STORED if f in stored else zipfile.ZIP_DEFLATED,
            )
    shutil.copyfile(os.path.join(PACK_DIR, "pack.json"), os.path.join(BUILD_DIR, "pack.json"))
    print(f"Wrote {zip_path} ({os.path.getsize(zip_path) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
