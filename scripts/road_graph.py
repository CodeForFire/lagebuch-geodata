#!/usr/bin/env python3
"""Turn the road ways of the extract into a routable graph, build/roads/roads.gpkg.

Reads build/roads.opl (written by build_road_graph.sh: the highway ways with
their node locations, plus every tagged node they pass) and keeps what touches
build/area_4326.geojson. Writes:

- build/roads/roads.gpkg: a GeoPackage with a `nodes` and an `edges` layer,
  the edges with an R*Tree index. The schema and the meaning of every flag are the
  contract in FORMAT.md, "The road graph".
- build/roads_stats.json: the node and edge counts, for roads.json.

A way is split into edges at its ends, at every node another way (or the way
itself, a second time) passes, and at barriers. Each edge says whether it can
be walked and driven, and in which direction, so one graph serves both modes.
"""
import json
import math
import os
import sys


BUILD_DIR = "build"
ROADS_DIR = os.path.join(BUILD_DIR, "roads")

DRIVE_CLASSES = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
    "residential", "living_street", "service", "track", "road",
}
WALK_ONLY_CLASSES = {"pedestrian", "footway", "path", "steps", "cycleway", "bridleway"}
# The highway values build_road_graph.sh filters for; everything else (construction,
# proposed, platform, corridor, raceway, bus_guideway, ...) is no way to lay a hose.
HIGHWAY_CLASSES = DRIVE_CLASSES | WALK_ONLY_CLASSES
NO_WALK_CLASSES = {"motorway", "motorway_link"}
IMPLIED_ONEWAY_CLASSES = {"motorway", "motorway_link"}

# Access values that open a way to everyone. Any other value is kept on the edge
# (foot_access, vehicle_access) for the app to show or avoid; only "no" closes it.
PUBLIC = {"yes", "permissive", "designated", "official"}
FOOT_KEYS = ("foot", "access")
VEHICLE_KEYS = ("motor_vehicle", "vehicle", "access")
# Barriers a vehicle cannot pass. A gate or a lift_gate is recorded but never
# blocks: the Feuerwehr usually has the key, and the app can warn about it.
BLOCKING_BARRIERS = {
    "bollard", "block", "jersey_barrier", "cycle_barrier", "kissing_gate",
    "stile", "turnstile", "full-height_turnstile", "chain", "log",
}

EARTH_RADIUS_M = 6371008.8


# --- OPL ----------------------------------------------------------------------


def unescape(text: str) -> str:
    """Undo OPL's escaping: every character outside its safe set is written %<hex>%."""
    if "%" not in text:
        return text
    out, i = [], 0
    while i < len(text):
        if text[i] == "%":
            end = text.index("%", i + 1)
            out.append(chr(int(text[i + 1 : end], 16)))
            i = end + 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def parse_tags(field: str) -> dict:
    tags = {}
    for pair in field.split(","):
        if pair:
            key, _, value = pair.partition("=")
            tags[unescape(key)] = unescape(value)
    return tags


def parse_location(text: str) -> tuple[float, float] | None:
    """`x<lon>y<lat>` as written after a node id or as a node's own fields; None when unknown."""
    x, _, y = text.partition("y")
    if not x.startswith("x") or len(x) == 1 or not y:
        return None
    return float(x[1:]), float(y)


def parse_opl(lines):
    """Yield ("node", id, tags, (lon, lat)) and ("way", id, tags, [(ref, (lon, lat) | None), ...])."""
    for line in lines:
        line = line.strip()
        if not line or line[0] not in "nw":
            continue
        fields = line.split(" ")
        kind, osm_id = line[0], int(fields[0][1:])
        tags, refs, x, y = {}, [], "", ""
        for field in fields[1:]:
            if field.startswith("T"):
                tags = parse_tags(field[1:])
            elif field.startswith("N"):
                for ref in field[1:].split(","):
                    if ref:
                        number, sep, location = ref[1:].partition("x")
                        refs.append((int(number), parse_location("x" + location) if sep else None))
            elif field.startswith("x"):
                x = field
            elif field.startswith("y"):
                y = field
        if kind == "n":
            yield "node", osm_id, tags, parse_location(x + y)
        else:
            yield "way", osm_id, tags, refs


# --- Rules --------------------------------------------------------------------


def effective(tags: dict, keys: tuple) -> str | None:
    """The most specific access value among keys, its first part where OSM lists several."""
    for key in keys:
        value = tags.get(key)
        if value:
            return value.split(";")[0].strip()
    return None


def restriction(value: str | None) -> str | None:
    return None if value is None or value in PUBLIC else value


def oneway_direction(value: str | None) -> int | None:
    """1 forward only, -1 backward only, 0 both ways, None when untagged."""
    if value is None:
        return None
    if value in ("yes", "1", "true"):
        return 1
    if value in ("-1", "reverse"):
        return -1
    return 0  # no, reversible, alternating: either direction can be used


def way_modes(tags: dict) -> dict | None:
    """The edge attributes a way gives all its edges, or None when it is no way to route on."""
    highway = tags.get("highway")
    if highway not in HIGHWAY_CLASSES:
        return None
    emergency = tags.get("emergency") in PUBLIC

    foot = effective(tags, FOOT_KEYS)
    walk = highway not in NO_WALK_CLASSES and tags.get("motorroad") != "yes" and (foot != "no" or emergency)
    foot_oneway = oneway_direction(tags.get("oneway:foot")) or 0

    vehicle = effective(tags, VEHICLE_KEYS)
    drive = highway in DRIVE_CLASSES and (vehicle != "no" or emergency)
    oneway = oneway_direction(tags.get("oneway"))
    if oneway is None:
        implied = highway in IMPLIED_ONEWAY_CLASSES or tags.get("junction") in ("roundabout", "circular")
        oneway = 1 if implied else 0

    modes = {
        "highway": highway,
        "name": tags.get("name") or None,
        "walk_fwd": int(walk and foot_oneway != -1),
        "walk_bwd": int(walk and foot_oneway != 1),
        "drive_fwd": int(drive and oneway != -1),
        "drive_bwd": int(drive and oneway != 1),
        "foot_access": restriction(foot),
        "vehicle_access": restriction(vehicle),
    }
    if not any(modes[flag] for flag in ("walk_fwd", "walk_bwd", "drive_fwd", "drive_bwd")):
        return None  # e.g. a motorway, or a footway with foot=no
    return modes


def barrier_blocks_vehicles(tags: dict) -> bool:
    if tags.get("barrier") not in BLOCKING_BARRIERS:
        return False
    if tags.get("emergency") in PUBLIC:
        return False
    vehicle = effective(tags, VEHICLE_KEYS)
    return vehicle is None or vehicle not in PUBLIC and vehicle != "destination"


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def line_length(coords: list) -> float:
    return sum(haversine(a, b) for a, b in zip(coords, coords[1:]))


# --- Area ---------------------------------------------------------------------


class Area:
    """A (Multi)Polygon that answers "does this line touch it?" quickly.

    The ring segments are bucketed into latitude bands, so a test only looks at
    the few segments near the point instead of every vertex of the boundary.
    Even-odd over every ring, so holes are holes, as osm_layers.inside does.
    """

    BANDS = 1024

    def __init__(self, geometry: dict):
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        segments = [
            (x0, y0, x1, y1)
            for polygon in polygons
            for ring in polygon
            for (x0, y0, *_), (x1, y1, *_) in zip(ring, ring[1:])
        ]
        self.south = min(min(s[1], s[3]) for s in segments)
        self.north = max(max(s[1], s[3]) for s in segments)
        self.west = min(min(s[0], s[2]) for s in segments)
        self.east = max(max(s[0], s[2]) for s in segments)
        self.step = (self.north - self.south) / self.BANDS or 1.0
        self.bands: list[list] = [[] for _ in range(self.BANDS)]
        for seg in segments:
            for band in range(self.band(min(seg[1], seg[3])), self.band(max(seg[1], seg[3])) + 1):
                self.bands[band].append(seg)

    def band(self, lat: float) -> int:
        return min(self.BANDS - 1, max(0, int((lat - self.south) / self.step)))

    def candidates(self, lat0: float, lat1: float):
        """Every ring segment that may reach latitudes lat0..lat1."""
        if lat1 < self.south or lat0 > self.north:
            return set()
        found = set()
        for band in range(self.band(lat0), self.band(lat1) + 1):
            found.update(self.bands[band])
        return found

    def inside(self, lon: float, lat: float) -> bool:
        if not (self.south <= lat <= self.north and self.west <= lon <= self.east):
            return False
        hit = False
        for x0, y0, x1, y1 in self.bands[self.band(lat)]:
            if (y0 > lat) != (y1 > lat) and lon < x0 + (lat - y0) * (x1 - x0) / (y1 - y0):
                hit = not hit
        return hit

    def crosses(self, a: tuple[float, float], b: tuple[float, float]) -> bool:
        """Whether the segment a-b meets the boundary."""
        return any(
            segments_intersect(a, b, (x0, y0), (x1, y1))
            for x0, y0, x1, y1 in self.candidates(min(a[1], b[1]), max(a[1], b[1]))
        )

    def touches(self, coords: list) -> bool:
        """Whether the line has a vertex inside the area or a segment across its boundary."""
        return any(self.inside(lon, lat) for lon, lat in coords) or any(
            self.crosses(a, b) for a, b in zip(coords, coords[1:])
        )


def segments_intersect(p1, p2, q1, q2) -> bool:
    def orient(a, b, c) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def within(a, b, c) -> bool:  # c, known to be collinear with a-b, lies on a-b
        return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])

    d1, d2, d3, d4 = orient(q1, q2, p1), orient(q1, q2, p2), orient(p1, p2, q1), orient(p1, p2, q2)
    if ((d1 > 0) != (d2 > 0)) and d1 != 0 and d2 != 0 and ((d3 > 0) != (d4 > 0)) and d3 != 0 and d4 != 0:
        return True
    return (
        (d1 == 0 and within(q1, q2, p1)) or (d2 == 0 and within(q1, q2, p2))
        or (d3 == 0 and within(p1, p2, q1)) or (d4 == 0 and within(p1, p2, q2))
    )


# --- Graph --------------------------------------------------------------------


def build_graph(elements, area: dict) -> dict:
    """Split the ways into edges between junctions, keeping the edges that touch the area.

    An edge touches the area when a vertex lies inside it or a segment crosses its
    boundary, so an edge that cuts across a corner with every vertex outside is
    kept too. Such an edge is kept whole, on to its next node.

    Returns {"nodes": [...], "edges": [...]} with dense ids starting at 1: a node
    is {id, osm, lon, lat, barrier, drive_blocked}; an edge is {id, from_node,
    to_node, coords, length_m, osm, **way_modes}.
    """
    area = Area(area)
    node_tags: dict[int, dict] = {}
    ways = []
    for kind, osm_id, tags, payload in elements:
        if kind == "node":
            if tags.get("barrier"):
                node_tags[osm_id] = tags
        else:
            modes = way_modes(tags)
            if modes is not None:
                ways.append((osm_id, modes, payload))

    # A node passed more than once, by different ways or the same way twice, is a junction.
    passes: dict[int, int] = {}
    for _, _, refs in ways:
        for ref, _ in refs:
            passes[ref] = passes.get(ref, 0) + 1

    raw_edges = []
    for way_id, modes, refs in ways:
        # A node without a location breaks the way: there is nothing to draw or measure.
        for run in located_runs(refs):
            start = 0
            for i in range(1, len(run)):
                ref = run[i][0]
                if i == len(run) - 1 or passes[ref] > 1 or ref in node_tags:
                    piece = run[start : i + 1]
                    coords = [loc for _, loc in piece]
                    if area.touches(coords):
                        raw_edges.append((way_id, piece[0][0], ref, coords, modes))
                    start = i

    used = sorted({e[1] for e in raw_edges} | {e[2] for e in raw_edges})
    node_ids = {ref: i for i, ref in enumerate(used, start=1)}
    locations = {}
    for _, start_ref, end_ref, coords, _ in raw_edges:
        locations[start_ref], locations[end_ref] = coords[0], coords[-1]
    nodes = []
    for ref in used:
        tags = node_tags.get(ref, {})
        lon, lat = locations[ref]
        nodes.append(
            {
                "id": node_ids[ref],
                "osm": f"node/{ref}",
                "lon": lon,
                "lat": lat,
                "barrier": tags.get("barrier"),
                "drive_blocked": int(barrier_blocks_vehicles(tags)),
            }
        )
    edges = []
    for i, (way_id, start_ref, end_ref, coords, modes) in enumerate(raw_edges, start=1):
        edges.append(
            {
                "id": i,
                "from_node": node_ids[start_ref],
                "to_node": node_ids[end_ref],
                "coords": coords,
                "length_m": round(line_length(coords), 2),
                "osm": f"way/{way_id}",
                **modes,
            }
        )
    return {"nodes": nodes, "edges": edges}


def located_runs(refs: list) -> list[list]:
    """The way's nodes as runs of consecutive located nodes, repeated nodes in a row dropped."""
    runs, run = [], []
    for ref, loc in refs:
        if loc is None:
            if len(run) > 1:
                runs.append(run)
            run = []
        elif not run or run[-1][0] != ref:
            run.append((ref, (round(loc[0], 7), round(loc[1], 7))))
    if len(run) > 1:
        runs.append(run)
    return runs


# --- GeoPackage ---------------------------------------------------------------

NODE_FIELDS = (("osm", "str"), ("barrier", "str"), ("drive_blocked", "int"))
EDGE_FIELDS = (
    ("from_node", "int"), ("to_node", "int"), ("length_m", "real"), ("highway", "str"),
    ("name", "str"), ("osm", "str"), ("walk_fwd", "int"), ("walk_bwd", "int"),
    ("drive_fwd", "int"), ("drive_bwd", "int"), ("foot_access", "str"), ("vehicle_access", "str"),
)


def write_gpkg(path: str, graph: dict) -> None:
    """Write the graph as a GeoPackage in EPSG:4326, with an R*Tree index on the edges.

    The nodes get no index: an app finds the nearest edge, not the nearest node,
    and a node's position is also its edges' end point. It would add a third to
    the file (Fürstenfeldbruck: 15 of 51 MB).
    """
    import sqlite3

    from osgeo import gdal, ogr, osr

    gdal.UseExceptions()
    if os.path.exists(path):
        os.remove(path)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    types = {"str": ogr.OFTString, "int": ogr.OFTInteger, "real": ogr.OFTReal}
    options = ["FID=fid", "GEOMETRY_NAME=geom"]

    ds = ogr.GetDriverByName("GPKG").CreateDataSource(path)
    for name, geom_type, fields, rows, geometry, index in (
        ("nodes", ogr.wkbPoint, NODE_FIELDS, graph["nodes"], point_wkt, "NO"),
        ("edges", ogr.wkbLineString, EDGE_FIELDS, graph["edges"], line_wkt, "YES"),
    ):
        layer = ds.CreateLayer(name, srs, geom_type, options + [f"SPATIAL_INDEX={index}"])
        for field, kind in fields:
            definition = ogr.FieldDefn(field, types[kind])
            if field not in ("barrier", "name", "foot_access", "vehicle_access"):
                definition.SetNullable(False)
            layer.CreateField(definition)
        layer.StartTransaction()
        defn = layer.GetLayerDefn()
        for row in rows:
            feature = ogr.Feature(defn)
            feature.SetFID(row["id"])
            for field, _ in fields:
                if row[field] is not None:
                    feature.SetField(field, row[field])
            feature.SetGeometry(ogr.CreateGeometryFromWkt(geometry(row)))
            layer.CreateFeature(feature)
        layer.CommitTransaction()
    ds = None  # closes the file

    with sqlite3.connect(path) as conn:
        conn.execute("VACUUM")


def point_wkt(node: dict) -> str:
    return f"POINT ({node['lon']} {node['lat']})"


def line_wkt(edge: dict) -> str:
    return "LINESTRING (" + ", ".join(f"{lon} {lat}" for lon, lat in edge["coords"]) + ")"


def main() -> int:
    os.makedirs(ROADS_DIR, exist_ok=True)
    with open(os.path.join(BUILD_DIR, "area_4326.geojson"), encoding="utf-8") as f:
        area = json.load(f)["geometry"]
    with open(os.path.join(BUILD_DIR, "roads.opl"), encoding="utf-8") as f:
        graph = build_graph(parse_opl(f), area)
    counts = {"nodes": len(graph["nodes"]), "edges": len(graph["edges"])}
    print(f"Road graph: {counts['nodes']} nodes, {counts['edges']} edges")
    if not graph["edges"]:
        print("::error::The OSM extract produced no road edges.", file=sys.stderr)
        return 1
    write_gpkg(os.path.join(ROADS_DIR, "roads.gpkg"), graph)
    with open(os.path.join(BUILD_DIR, "roads_stats.json"), "w", encoding="utf-8") as f:
        json.dump(counts, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
