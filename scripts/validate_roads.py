#!/usr/bin/env python3
"""Check a built road graph against the contract in FORMAT.md before it is released.

Runs in the toolbox image. Usage: validate_roads.py <roads.zip> <pack.zip>

The pack of the same build supplies the bounds and the hydrants: nearly every
hydrant must lie close to a walkable edge, or the graph cannot route to it.
Reads the GeoPackage with plain sqlite3, the way an app does. Exits non-zero
on the first broken rule.
"""
import hashlib
import json
import math
import os
import random
import sqlite3
import struct
import sys
import tempfile
import zipfile

from road_graph import Area

FORMAT_VERSION = 2
EXPECTED_FILES = {"roads.gpkg", "roads.json", "ATTRIBUTION.md"}
# The cap an app also holds the download to. About twice the largest graph measured.
MAX_ROADS_BYTES = 60 * 1000 * 1000
NODE_COLUMNS = ["fid", "geom", "osm", "barrier", "drive_blocked"]
EDGE_COLUMNS = [
    "fid", "geom", "from_node", "to_node", "length_m", "highway", "name", "osm",
    "walk_fwd", "walk_bwd", "drive_fwd", "drive_bwd", "foot_access", "vehicle_access",
]
FLAGS = ("walk_fwd", "walk_bwd", "drive_fwd", "drive_bwd")
SAMPLE_EDGES = 2000
HYDRANT_RADIUS_M = 50
MIN_HYDRANTS_NEAR = 0.95
# boundary.geojson is simplified to ~20 m, so an edge that only grazes the exact
# area the build clipped to may miss the simplified one by about that much.
BOUNDARY_TOLERANCE_M = 50
EARTH_RADIUS_M = 6371008.8
# Envelope sizes of a GeoPackage geometry header, by the flags' envelope indicator.
ENVELOPE_BYTES = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}


class RoadsError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RoadsError(message)


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gpkg_coords(blob: bytes) -> list[tuple[float, float]]:
    """The vertices of a GeoPackage Point or LineString: the "GP" header, then WKB."""
    require(blob[:2] == b"GP", "geometry blob has no GeoPackage header")
    flags = blob[3]
    envelope = (flags >> 1) & 0b111
    require(envelope in ENVELOPE_BYTES, f"geometry header has envelope code {envelope}")
    wkb = blob[8 + ENVELOPE_BYTES[envelope] :]
    order = "<" if wkb[0] == 1 else ">"
    (kind,) = struct.unpack_from(order + "I", wkb, 1)
    if kind == 1:
        return [struct.unpack_from(order + "dd", wkb, 5)]
    require(kind == 2, f"geometry of WKB type {kind}, expected Point or LineString")
    (count,) = struct.unpack_from(order + "I", wkb, 5)
    return [struct.unpack_from(order + "dd", wkb, 9 + 16 * i) for i in range(count)]


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def segment_distance_m(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance from p to the segment a-b, in a local plane around p (fine at this scale)."""
    kx = math.cos(math.radians(p[1])) * math.pi * EARTH_RADIUS_M / 180
    ky = math.pi * EARTH_RADIUS_M / 180
    ax, ay, bx, by = (a[0] - p[0]) * kx, (a[1] - p[1]) * ky, (b[0] - p[0]) * kx, (b[1] - p[1]) * ky
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + t * dx, ay + t * dy)


def check_zip(zip_path: str, out_dir: str) -> dict:
    require(os.path.getsize(zip_path) <= MAX_ROADS_BYTES, f"road graph zip is larger than {MAX_ROADS_BYTES} bytes")
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        require(names == EXPECTED_FILES, f"zip holds {sorted(names)}, expected {sorted(EXPECTED_FILES)}")
        for name in names:
            archive.extract(name, out_dir)  # names checked against a fixed allowlist above
    with open(os.path.join(out_dir, "roads.json"), encoding="utf-8") as f:
        roads = json.load(f)
    require(roads.get("formatVersion") == FORMAT_VERSION, f"formatVersion {roads.get('formatVersion')!r}")
    require(set(roads["files"]) == {"roads.gpkg"}, f"roads.json lists {sorted(roads['files'])}")
    entry = roads["files"]["roads.gpkg"]
    path = os.path.join(out_dir, "roads.gpkg")
    require(os.path.getsize(path) == entry["bytes"], "roads.gpkg: size differs from roads.json")
    require(sha256(path) == entry["sha256"], "roads.gpkg: sha256 differs from roads.json")
    return roads


def read_pack(zip_path: str) -> tuple[dict, list, dict]:
    with zipfile.ZipFile(zip_path) as archive:
        pack = json.loads(archive.read("pack.json"))
        water = json.loads(archive.read("water.geojson"))
        boundary = json.loads(archive.read("boundary.geojson"))["geometry"]
    hydrants = [
        tuple(f["geometry"]["coordinates"][:2])
        for f in water["features"]
        if f["properties"].get("emergency") == "fire_hydrant"
    ]
    return pack, hydrants, boundary


def near_area(area: Area, coords: list, tolerance_m: float) -> bool:
    """Whether the line touches the area, or passes within tolerance_m of its boundary."""
    if area.touches(coords):
        return True
    # Segments that do not cross lie closest at an end point of one of them, so
    # the four end-point distances cover a segment whose middle passes near.
    dlat = tolerance_m / 111_000
    for a, b in zip(coords, coords[1:]):
        for x0, y0, x1, y1 in area.candidates(min(a[1], b[1]) - dlat, max(a[1], b[1]) + dlat):
            c, d = (x0, y0), (x1, y1)
            if min(
                segment_distance_m(a, c, d), segment_distance_m(b, c, d),
                segment_distance_m(c, a, b), segment_distance_m(d, a, b),
            ) <= tolerance_m:
                return True
    return False


def check_graph(path: str, roads: dict, pack: dict, hydrants: list, boundary: dict) -> None:
    require(roads["slug"] == pack["slug"] and roads["version"] == pack["version"], "roads.json is for another pack")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        require(conn.execute("PRAGMA application_id").fetchone()[0] == 0x47504B47, "roads.gpkg is not a GeoPackage")
        contents = dict(conn.execute("SELECT table_name, data_type FROM gpkg_contents"))
        require(contents.get("nodes") == "features" and contents.get("edges") == "features", f"gpkg_contents {contents}")
        for table, expected in (("nodes", NODE_COLUMNS), ("edges", EDGE_COLUMNS)):
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
            require(columns == expected, f"{table} columns {columns}")
            srs = conn.execute("SELECT srs_id FROM gpkg_geometry_columns WHERE table_name = ?", (table,)).fetchone()
            require(srs == (4326,), f"{table} is not in EPSG:4326")
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            require(count > 0 and count == roads["graph"][table], f"{table}: {count} rows, roads.json says {roads['graph'][table]}")
        require(
            conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'rtree_edges_geom'").fetchone()[0] == 1,
            "edges have no R*Tree index",
        )
        indexed = conn.execute("SELECT COUNT(*) FROM rtree_edges_geom").fetchone()[0]
        require(indexed == roads["graph"]["edges"], f"{indexed} edges in the R*Tree, roads.json says {roads['graph']['edges']}")

        dangling = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE from_node NOT IN (SELECT fid FROM nodes) "
            "OR to_node NOT IN (SELECT fid FROM nodes)"
        ).fetchone()[0]
        require(dangling == 0, f"{dangling} edges name a node that does not exist")
        flags = " OR ".join(f"{flag} NOT IN (0, 1)" for flag in FLAGS)
        require(conn.execute(f"SELECT COUNT(*) FROM edges WHERE {flags}").fetchone()[0] == 0, "an edge flag is not 0 or 1")
        require(conn.execute("SELECT COUNT(*) FROM edges WHERE length_m <= 0").fetchone()[0] == 0, "an edge has no length")
        require(
            conn.execute("SELECT COUNT(*) FROM edges WHERE walk_fwd + walk_bwd + drive_fwd + drive_bwd = 0").fetchone()[0] == 0,
            "an edge can be neither walked nor driven",
        )
        node_at = {}
        for fid, blob in conn.execute("SELECT fid, geom FROM nodes"):
            node_at[fid] = gpkg_coords(blob)[0]
        rows = conn.execute("SELECT fid, from_node, to_node, length_m, geom FROM edges").fetchall()
        # An edge that crosses the boundary runs on to its next node, however far
        # outside that is; an edge that never touches the area means the clip failed.
        area = Area(boundary)
        away = [fid for fid, _, _, _, blob in rows if not near_area(area, gpkg_coords(blob), BOUNDARY_TOLERANCE_M)]
        require(not away, f"{len(away)} edges do not touch the pack's area, e.g. edge {away[:5]}")
        for fid, start, end, length, blob in random.Random(42).sample(rows, min(SAMPLE_EDGES, len(rows))):
            coords = gpkg_coords(blob)
            require(len(coords) >= 2, f"edge {fid} has fewer than two vertices")
            require(coords[0] == node_at[start] and coords[-1] == node_at[end], f"edge {fid} does not run between its nodes")
            measured = sum(haversine(p, q) for p, q in zip(coords, coords[1:]))
            require(abs(measured - length) <= max(0.05, 0.01 * measured), f"edge {fid}: length_m {length}, geometry {measured:.2f}")

        near = 0
        for lon, lat in hydrants:
            dlat = HYDRANT_RADIUS_M / 111_000
            dlon = dlat / math.cos(math.radians(lat))
            candidates = conn.execute(
                "SELECT e.geom FROM rtree_edges_geom r JOIN edges e ON e.fid = r.id "
                "WHERE r.maxx >= ? AND r.minx <= ? AND r.maxy >= ? AND r.miny <= ? AND e.walk_fwd + e.walk_bwd > 0",
                (lon - dlon, lon + dlon, lat - dlat, lat + dlat),
            )
            if any(
                segment_distance_m((lon, lat), a, c) <= HYDRANT_RADIUS_M
                for (blob,) in candidates
                for a, c in zip(gpkg_coords(blob), gpkg_coords(blob)[1:])
            ):
                near += 1
    share = near / len(hydrants) if hydrants else 1.0
    require(share >= MIN_HYDRANTS_NEAR, f"only {near} of {len(hydrants)} hydrants lie within {HYDRANT_RADIUS_M} m of a walkable edge")
    print(f"  {len(node_at)} nodes, {len(rows)} edges; {near} of {len(hydrants)} hydrants within {HYDRANT_RADIUS_M} m of a walkable edge")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    roads_zip, pack_zip = sys.argv[1:]
    with tempfile.TemporaryDirectory() as out_dir:
        try:
            roads = check_zip(roads_zip, out_dir)
            pack, hydrants, boundary = read_pack(pack_zip)
            check_graph(os.path.join(out_dir, "roads.gpkg"), roads, pack, hydrants, boundary)
        except RoadsError as exc:
            print(f"::error::Road graph validation failed: {exc}", file=sys.stderr)
            return 1
    print(f"{roads_zip}: valid ({os.path.getsize(roads_zip) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
