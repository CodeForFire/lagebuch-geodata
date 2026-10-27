#!/usr/bin/env python3
"""Download the Bayern DGM5 tiles that cover the buffered pack area.

Runs in the toolbox image (needs OGR for the intersection test). DGM5 comes in
1 km x 1 km tiles named after their south-west corner in kilometres in
EPSG:25832 (`650_5340.zip` covers E 650000-651000, N 5340000-5341000), each a
zip holding one ASCII XYZ file of 200 x 200 cell-centre points.

The Bavaria-wide Metalink (`09.meta4`, ~22 MB) lists every tile with its size,
SHA-256 and two mirror URLs, so every download is verified against the
publisher's own hash. A tile the Metalink does not list lies outside Bayern; it
is recorded in build/dgm5.json as missing rather than failing the build.

Writes build/dgm5/txt/<tile>.txt and build/dgm5.json.
"""
import concurrent.futures
import hashlib
import json
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

from osgeo import ogr

ogr.UseExceptions()

BUILD_DIR = "build"
METALINK_URL = "https://geodaten.bayern.de/odd/a/dgm/dgm5xyz/meta/metalink/09.meta4"
NS = {"m": "urn:ietf:params:xml:ns:metalink"}
TILE_M = 1000
# A DGM5 tile zip is ~0.2 MB and its text ~1.1 MB; anything far beyond that is
# not a DGM5 tile, whatever the Metalink says.
MAX_ZIP_BYTES = 5 * 1024 * 1024
MAX_TXT_BYTES = 5 * 1024 * 1024
USER_AGENT = "lagebuch-geodata-build-bot/1.0 (+https://github.com/CodeForFire/lagebuch-geodata)"


def wanted_tiles(area: ogr.Geometry) -> list[str]:
    """Names of the 1 km tiles whose square intersects the area."""
    min_e, max_e, min_n, max_n = area.GetEnvelope()
    names = []
    for e_km in range(int(min_e // TILE_M), int(max_e // TILE_M) + 1):
        for n_km in range(int(min_n // TILE_M), int(max_n // TILE_M) + 1):
            e0, n0 = e_km * TILE_M, n_km * TILE_M
            ring = ogr.Geometry(ogr.wkbLinearRing)
            for x, y in ((e0, n0), (e0 + TILE_M, n0), (e0 + TILE_M, n0 + TILE_M), (e0, n0 + TILE_M), (e0, n0)):
                ring.AddPoint_2D(x, y)
            square = ogr.Geometry(ogr.wkbPolygon)
            square.AddGeometry(ring)
            if square.Intersects(area):
                names.append(f"{e_km}_{n_km}")
    return names


def parse_metalink(path: str) -> tuple[str, dict[str, dict]]:
    """Return (published timestamp, {tile name: {size, sha256, urls}})."""
    entries = {}
    published = ""
    for _, element in ET.iterparse(path, events=("end",)):
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "published":
            published = (element.text or "").strip()
        elif tag == "file":
            name = element.get("name", "")
            if name.endswith(".zip"):
                hash_el = element.find("m:hash[@type='sha-256']", NS)
                size_el = element.find("m:size", NS)
                entries[name[: -len(".zip")]] = {
                    "size": int(size_el.text) if size_el is not None and size_el.text else 0,
                    "sha256": (hash_el.text or "").strip().lower() if hash_el is not None else "",
                    "urls": [u.text.strip() for u in element.findall("m:url", NS) if u.text],
                }
            element.clear()
    return published, entries


def download(url: str, limit: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{url} is larger than {limit} bytes")
    return data


def fetch_tile(name: str, entry: dict, zip_dir: str, txt_dir: str) -> None:
    txt_path = os.path.join(txt_dir, f"{name}.txt")
    if os.path.exists(txt_path):
        return
    if entry["size"] > MAX_ZIP_BYTES or not entry["sha256"]:
        raise ValueError(f"Metalink entry for {name} has an implausible size or no SHA-256")

    last_error: Exception | None = None
    for url in entry["urls"]:
        try:
            data = download(url, MAX_ZIP_BYTES)
            digest = hashlib.sha256(data).hexdigest()
            if digest != entry["sha256"]:
                raise ValueError(f"{name}: SHA-256 {digest} does not match the Metalink")
            break
        except (OSError, ValueError) as exc:
            last_error = exc
    else:
        raise RuntimeError(f"Could not download DGM5 tile {name}: {last_error}")

    zip_path = os.path.join(zip_dir, f"{name}.zip")
    with open(zip_path, "wb") as f:
        f.write(data)

    expected = f"{name}.txt"
    with zipfile.ZipFile(zip_path) as archive:
        # Read only the one member we expect, by exact name: never extractall()
        # on downloaded archives.
        info = archive.getinfo(expected)
        if info.file_size > MAX_TXT_BYTES:
            raise ValueError(f"{name}: {expected} unpacks to {info.file_size} bytes")
        with archive.open(info) as src, open(txt_path + ".part", "wb") as dst:
            dst.write(src.read(MAX_TXT_BYTES + 1))
    os.replace(txt_path + ".part", txt_path)
    os.remove(zip_path)


def main() -> int:
    with open(os.path.join(BUILD_DIR, "area_25832.geojson"), encoding="utf-8") as f:
        area = ogr.CreateGeometryFromJson(json.dumps(json.load(f)["geometry"]))

    zip_dir = os.path.join(BUILD_DIR, "dgm5", "zip")
    txt_dir = os.path.join(BUILD_DIR, "dgm5", "txt")
    os.makedirs(zip_dir, exist_ok=True)
    os.makedirs(txt_dir, exist_ok=True)

    metalink_path = os.path.join(BUILD_DIR, "dgm5.meta4")
    if not os.path.exists(metalink_path):
        with open(metalink_path + ".part", "wb") as f:
            f.write(download(METALINK_URL, 200 * 1024 * 1024))
        os.replace(metalink_path + ".part", metalink_path)
    published, catalogue = parse_metalink(metalink_path)

    names = wanted_tiles(area)
    present = [n for n in names if n in catalogue]
    missing = [n for n in names if n not in catalogue]
    print(f"DGM5: {len(names)} tiles touch the area, {len(present)} published, {len(missing)} outside Bayern")
    if not present:
        print("::error::No DGM5 tile covers this area.", file=sys.stderr)
        return 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch_tile, n, catalogue[n], zip_dir, txt_dir) for n in present]
        for future in concurrent.futures.as_completed(futures):
            future.result()

    with open(os.path.join(BUILD_DIR, "dgm5.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"metalinkPublished": published, "tiles": len(present), "missingTiles": sorted(missing)},
            f,
            indent=2,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
