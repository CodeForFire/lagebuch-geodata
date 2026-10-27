#!/usr/bin/env python3
"""Print the Geofabrik download path for the state/district in ./build/region.json.

Prefers a Regierungsbezirk-level sub-extract (currently only published for
Bayern) over the full Bundesland, to keep the downloaded .osm.pbf small.
Prints just the path under https://download.geofabrik.de/europe/germany/,
e.g. "bayern/oberbayern-latest.osm.pbf" or "hessen-latest.osm.pbf".
"""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    with open("build/region.json", encoding="utf-8") as f:
        region = json.load(f)
    state = region["state"]
    district = region.get("stateDistrict")

    with open(os.path.join(SCRIPT_DIR, "geofabrik_regions.json"), encoding="utf-8") as f:
        table = json.load(f)

    entry = table.get(state)
    if entry is None:
        print(f"::error::No Geofabrik mapping for state {state!r}.", file=sys.stderr)
        return 1

    districts = entry.get("districts", {})
    if district and district in districts:
        print(f"{entry['slug']}/{districts[district]}-latest.osm.pbf")
    else:
        print(f"{entry['slug']}-latest.osm.pbf")
    return 0


if __name__ == "__main__":
    sys.exit(main())
