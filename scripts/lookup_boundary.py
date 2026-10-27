#!/usr/bin/env python3
"""Look up a Landkreis boundary via Nominatim and enforce the guardrails.

Reads RELATION_ID from the environment. Writes build/boundary.geojson (the
unbuffered polygon, WGS84) and build/region.json on success. On a guardrail
failure, prints an issue-postable message and exits 1 without writing anything.

Guardrails:
- admin_level 6 (Landkreis / kreisfreie Stadt). A flat area threshold cannot
  tell a Kreis from a Bundesland (Mecklenburgische Seenplatte is bigger than
  Saarland), so admin_level is the primary check and area only a backstop.
- address.state == "Bayern", because the elevation comes from Bayern's DGM5.
  Other Länder need their own elevation source first.
"""
import json
import math
import os
import sys
import urllib.error
import urllib.request

NOMINATIM_URL = (
    "https://nominatim.openstreetmap.org/lookup"
    "?osm_ids=R{relation_id}&format=json&polygon_geojson=1"
    "&addressdetails=1&extratags=1"
)
USER_AGENT = "lagebuch-geodata-build-bot/1.0 (+https://github.com/CodeForFire/lagebuch-geodata)"
REQUIRED_ADMIN_LEVEL = "6"
SUPPORTED_STATES = {"Bayern"}
MAX_AREA_KM2 = 7000
EARTH_RADIUS_KM = 6371.0
BUILD_DIR = "build"


def fail(message: str):
    print(f"::error::{message}", file=sys.stderr)
    print(message)
    sys.exit(1)


def fetch_nominatim(relation_id: str) -> dict:
    url = NOMINATIM_URL.format(relation_id=relation_id)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.load(resp)
    except urllib.error.URLError as exc:
        fail(f"Nominatim lookup failed for relation {relation_id}: {exc}")
    if not body:
        fail(
            f"Nominatim has no record for relation {relation_id}. "
            f"Double check the ID at https://www.openstreetmap.org/relation/{relation_id}"
        )
    return body[0]


def bbox_area_km2(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> float:
    """Rough equirectangular area, adequate for a sanity backstop."""
    mean_lat_rad = math.radians((min_lat + max_lat) / 2)
    lat_km = (max_lat - min_lat) * (math.pi / 180) * EARTH_RADIUS_KM
    lon_km = (max_lon - min_lon) * (math.pi / 180) * EARTH_RADIUS_KM * math.cos(mean_lat_rad)
    return abs(lat_km * lon_km)


def check(result: dict, relation_id: str) -> tuple[str, str | None]:
    """Return (state, state_district) or fail with an issue-postable message."""
    admin_level = str(result.get("extratags", {}).get("admin_level", ""))
    if admin_level != REQUIRED_ADMIN_LEVEL:
        fail(
            f"Relation {relation_id} has admin_level={admin_level!r}, expected "
            f"{REQUIRED_ADMIN_LEVEL!r} (Kreis / kreisfreie Stadt). Pick the OSM relation "
            "for the Kreis itself, not a Bundesland or a Gemeinde."
        )

    address = result.get("address", {})
    state = address.get("state")
    if state not in SUPPORTED_STATES:
        fail(
            f"Relation {relation_id} lies in {state or 'an unknown state'!r}. Packs are "
            "built only for Bayern for now, because the elevation comes from the "
            "Bayerische Vermessungsverwaltung's DGM5."
        )

    try:
        min_lat, max_lat, min_lon, max_lon = (float(v) for v in result["boundingbox"])
    except (KeyError, ValueError) as exc:
        fail(f"Relation {relation_id} has no usable bounding box: {exc}")

    area_km2 = bbox_area_km2(min_lat, min_lon, max_lat, max_lon)
    if area_km2 > MAX_AREA_KM2:
        fail(
            f"Relation {relation_id}'s bounding box is ~{area_km2:.0f} km^2, above the "
            f"{MAX_AREA_KM2} km^2 backstop for a Landkreis. Please double check the relation ID."
        )

    if result.get("geojson", {}).get("type") not in ("Polygon", "MultiPolygon"):
        fail(f"Relation {relation_id} has no polygon geometry from Nominatim.")

    return state, address.get("state_district")


def main() -> int:
    relation_id = os.environ["RELATION_ID"]
    result = fetch_nominatim(relation_id)
    state, state_district = check(result, relation_id)

    os.makedirs(BUILD_DIR, exist_ok=True)
    with open(os.path.join(BUILD_DIR, "boundary.geojson"), "w", encoding="utf-8") as f:
        json.dump({"type": "Feature", "properties": {}, "geometry": result["geojson"]}, f)

    region = {
        "relationId": relation_id,
        "displayName": result.get("display_name"),
        "state": state,
        "stateDistrict": state_district,
    }
    with open(os.path.join(BUILD_DIR, "region.json"), "w", encoding="utf-8") as f:
        json.dump(region, f, indent=2, ensure_ascii=False)

    print(f"Boundary OK: {result.get('display_name')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
