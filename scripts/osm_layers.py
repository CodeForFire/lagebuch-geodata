#!/usr/bin/env python3
"""Turn osmium's GeoJSON-sequence exports into the pack's water and address files.

Reads build/water.geojsonseq and build/addresses.geojsonseq (written by
build_osm_layers.sh) and keeps what lies inside build/area_4326.geojson. Writes:

- build/pack/water.geojson: an RFC 7946 FeatureCollection of Points. A pond or
  tank mapped as an area becomes its centroid, so the app has one shape to draw.
- build/pack/addresses.sqlite: one row per address, a building and a node
  carrying the same address collapsed into one.

The column set and the tag allowlist are the contract in FORMAT.md; anything
else OSM carries is dropped, which also keeps personal names out of the pack.
"""
import json
import os
import sqlite3
import sys

BUILD_DIR = "build"
PACK_DIR = os.path.join(BUILD_DIR, "pack")

WATER_TAGS = (
    "emergency",
    "fire_hydrant:type",
    "fire_hydrant:position",
    "fire_hydrant:diameter",
    "fire_hydrant:pressure",
    "couplings",
    "couplings:type",
    "water_source",
    "water_tank:volume",
    "flow_rate",
    "name",
    "ref",
    "operator",
)
DISUSED_VALUES = {"yes", "disused", "abandoned"}


def representative_point(geometry: dict) -> tuple[float, float] | None:
    """A Point's own coordinates, or the area-weighted centroid of a polygon's outer ring."""
    kind = geometry.get("type")
    if kind == "Point":
        lon, lat = geometry["coordinates"][:2]
        return lon, lat
    if kind == "Polygon":
        ring = geometry["coordinates"][0]
    elif kind == "MultiPolygon":
        # The largest part stands for the whole; a tank with an annex is still one tank.
        ring = max((part[0] for part in geometry["coordinates"]), key=lambda r: abs(signed_area(relative(r))))
    else:
        return None
    # Work relative to the first vertex: the shoelace sums of raw degrees cancel
    # out most of their digits at 48° N.
    ox, oy = ring[0][0], ring[0][1]
    local = relative(ring)
    area = signed_area(local)
    if area == 0:
        return ox + sum(p[0] for p in local) / len(local), oy + sum(p[1] for p in local) / len(local)
    cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(local, local[1:]):
        cross = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    return ox + cx / (6 * area), oy + cy / (6 * area)


def relative(ring: list) -> list[tuple[float, float]]:
    return [(p[0] - ring[0][0], p[1] - ring[0][1]) for p in ring]


def signed_area(ring: list) -> float:
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:])) / 2


def inside(area: dict, lon: float, lat: float) -> bool:
    """Even-odd point-in-polygon over every ring of a (Multi)Polygon, so holes are holes.

    osmium's "smart" extract keeps every node of a way that crosses the edge, so
    a hydrant on a water main far outside the area would otherwise come along.
    """
    polygons = area["coordinates"] if area["type"] == "MultiPolygon" else [area["coordinates"]]
    hit = False
    for polygon in polygons:
        for ring in polygon:
            for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
                if (y0 > lat) != (y1 > lat) and lon < x0 + (lat - y0) * (x1 - x0) / (y1 - y0):
                    hit = not hit
    return hit


def read_seq(path: str):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip().lstrip("\x1e")  # RFC 8142 record separator
            if line:
                yield json.loads(line)


def osm_ref(properties: dict) -> str:
    return f"{properties.get('@type', '')}/{properties.get('@id', '')}"


def build_water(src: str, dst: str, area: dict) -> int:
    features = []
    for feature in read_seq(src):
        props = feature.get("properties", {})
        if props.get("disused") in DISUSED_VALUES or props.get("abandoned") in DISUSED_VALUES:
            continue
        point = representative_point(feature.get("geometry") or {})
        if point is None or not inside(area, *point):
            continue
        kept = {k: props[k] for k in WATER_TAGS if k in props}
        kept["osm"] = osm_ref(props)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(point[0], 7), round(point[1], 7)]},
                "properties": kept,
            }
        )
    features.sort(key=lambda f: f["properties"]["osm"])
    with open(dst, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False, separators=(",", ":"))
    return len(features)


def build_addresses(src: str, dst: str, area: dict) -> int:
    rows: dict[tuple, tuple] = {}
    for feature in read_seq(src):
        props = feature.get("properties", {})
        street = props.get("addr:street") or props.get("addr:place")
        housenumber = props.get("addr:housenumber")
        if not street or not housenumber:
            continue
        point = representative_point(feature.get("geometry") or {})
        if point is None or not inside(area, *point):
            continue
        postcode = props.get("addr:postcode", "")
        city = props.get("addr:city", "")
        key = (street, housenumber, postcode, city)
        # A node inside the building is the more precise entrance; prefer it.
        if key in rows and props.get("@type") != "node":
            continue
        rows[key] = (
            street,
            housenumber,
            postcode,
            city,
            props.get("addr:suburb", ""),
            round(point[1], 7),
            round(point[0], 7),
            osm_ref(props),
        )

    if os.path.exists(dst):
        os.remove(dst)
    with sqlite3.connect(dst) as conn:
        conn.execute(
            "CREATE TABLE addresses ("
            "street TEXT NOT NULL, housenumber TEXT NOT NULL, postcode TEXT NOT NULL, "
            "city TEXT NOT NULL, suburb TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, osm TEXT NOT NULL)"
        )
        conn.executemany("INSERT INTO addresses VALUES (?, ?, ?, ?, ?, ?, ?, ?)", sorted(rows.values()))
        conn.execute("CREATE INDEX addresses_street ON addresses (street, housenumber)")
    with sqlite3.connect(dst) as conn:
        conn.execute("VACUUM")
    return len(rows)


def main() -> int:
    os.makedirs(PACK_DIR, exist_ok=True)
    with open(os.path.join(BUILD_DIR, "area_4326.geojson"), encoding="utf-8") as f:
        area = json.load(f)["geometry"]
    water = build_water(os.path.join(BUILD_DIR, "water.geojsonseq"), os.path.join(PACK_DIR, "water.geojson"), area)
    addresses = build_addresses(
        os.path.join(BUILD_DIR, "addresses.geojsonseq"), os.path.join(PACK_DIR, "addresses.sqlite"), area
    )
    print(f"Wrote {water} water points and {addresses} addresses")
    if water == 0 or addresses == 0:
        print("::error::The OSM extract produced no water points or no addresses.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
