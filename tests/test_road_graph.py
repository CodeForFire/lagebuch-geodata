import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import road_graph  # noqa: E402

try:
    from osgeo import ogr  # noqa: F401

    HAVE_GDAL = True
except ImportError:
    HAVE_GDAL = False

# 10..12 E, 47..49 N
AREA = {"type": "Polygon", "coordinates": [[[10, 47], [12, 47], [12, 49], [10, 49], [10, 47]]]}


def loc(i):
    """Node i sits on a line heading east from 11.0 E, 48.0 N, 0.001° apart."""
    return f"x{11 + i / 1000:.7f}y48.0000000"


def way(way_id, refs, **tags):
    tag_field = ",".join(f"{k.replace('__', ':')}={v}" for k, v in tags.items())
    node_field = ",".join(f"n{r}{loc(r)}" for r in refs)
    return f"w{way_id} T{tag_field} N{node_field}"


def node(node_id, **tags):
    tag_field = ",".join(f"{k}={v}" for k, v in tags.items())
    return f"n{node_id} T{tag_field} {loc(node_id).replace('y', ' y')}"


def graph(*lines, area=AREA):
    return road_graph.build_graph(road_graph.parse_opl(lines), area)


def edge(g, way_id):
    (e,) = [e for e in g["edges"] if e["osm"] == f"way/{way_id}"]
    return e


class Opl(unittest.TestCase):
    def test_escaped_characters_are_decoded(self):
        self.assertEqual(road_graph.unescape("Am%20%Bach%2c%"), "Am Bach,")
        self.assertEqual(road_graph.unescape("Stra%df%e"), "Straße")

    def test_a_way_with_locations_is_read(self):
        ((kind, osm_id, tags, refs),) = road_graph.parse_opl(["w7 Thighway=residential,name=Am%20%Bach Nn1x11.1y48.2,n2x11.3y48.4"])
        self.assertEqual((kind, osm_id), ("way", 7))
        self.assertEqual(tags, {"highway": "residential", "name": "Am Bach"})
        self.assertEqual(refs, [(1, (11.1, 48.2)), (2, (11.3, 48.4))])

    def test_a_node_without_a_location_is_read_as_unknown(self):
        ((_, _, _, refs),) = road_graph.parse_opl(["w7 Thighway=residential Nn1x,n2x11.3y48.4"])
        self.assertEqual(refs, [(1, None), (2, (11.3, 48.4))])

    def test_a_node_is_read_with_its_tags_and_location(self):
        ((kind, osm_id, tags, location),) = road_graph.parse_opl(["n5 Tbarrier=bollard x11.5 y48.5"])
        self.assertEqual((kind, osm_id, tags, location), ("node", 5, {"barrier": "bollard"}, (11.5, 48.5)))


class Splitting(unittest.TestCase):
    def test_a_lone_way_is_one_edge(self):
        g = graph(way(1, [1, 2, 3], highway="residential"))
        self.assertEqual(len(g["edges"]), 1)
        self.assertEqual(len(g["nodes"]), 2)
        self.assertEqual(len(g["edges"][0]["coords"]), 3)

    def test_ways_are_split_where_they_share_a_node(self):
        g = graph(way(1, [1, 2, 3], highway="residential"), way(2, [2, 4], highway="footway"))
        self.assertEqual(sorted(len(e["coords"]) for e in g["edges"]), [2, 2, 2])
        middle = {n["osm"]: n["id"] for n in g["nodes"]}["node/2"]
        touching = [e for e in g["edges"] if middle in (e["from_node"], e["to_node"])]
        self.assertEqual(len(touching), 3)

    def test_a_way_is_split_where_it_passes_a_node_twice(self):
        g = graph(way(1, [1, 2, 3, 4, 2], highway="service"))
        self.assertEqual([len(e["coords"]) for e in g["edges"]], [2, 4])

    def test_a_way_is_split_at_a_barrier(self):
        g = graph(node(2, barrier="bollard"), way(1, [1, 2, 3], highway="residential"))
        self.assertEqual(len(g["edges"]), 2)
        bollard = next(n for n in g["nodes"] if n["osm"] == "node/2")
        self.assertEqual((bollard["barrier"], bollard["drive_blocked"]), ("bollard", 1))

    def test_a_gate_is_recorded_but_does_not_block(self):
        g = graph(node(2, barrier="gate"), way(1, [1, 2, 3], highway="residential"))
        gate = next(n for n in g["nodes"] if n["osm"] == "node/2")
        self.assertEqual((gate["barrier"], gate["drive_blocked"]), ("gate", 0))

    def test_a_bollard_open_to_emergency_vehicles_does_not_block(self):
        g = graph(node(2, barrier="bollard", emergency="yes"), way(1, [1, 2, 3], highway="residential"))
        self.assertEqual(next(n for n in g["nodes"] if n["osm"] == "node/2")["drive_blocked"], 0)

    def test_a_tagged_node_that_is_no_barrier_does_not_split(self):
        g = graph(node(2, highway="crossing"), way(1, [1, 2, 3], highway="residential"))
        self.assertEqual(len(g["edges"]), 1)

    def test_a_node_without_location_breaks_the_way(self):
        g = road_graph.build_graph(
            road_graph.parse_opl([f"w1 Thighway=residential Nn1{loc(1)},n2{loc(2)},n3x,n4{loc(4)},n5{loc(5)}"]), AREA
        )
        self.assertEqual([len(e["coords"]) for e in g["edges"]], [2, 2])

    def test_edges_end_at_their_nodes(self):
        g = graph(way(1, [1, 2, 3], highway="residential"), way(2, [3, 4], highway="residential"))
        at = {n["id"]: (n["lon"], n["lat"]) for n in g["nodes"]}
        for e in g["edges"]:
            self.assertEqual((e["coords"][0], e["coords"][-1]), (at[e["from_node"]], at[e["to_node"]]))

    def test_edges_outside_the_area_are_dropped(self):
        small = {"type": "Polygon", "coordinates": [[[10, 47], [11.0025, 47], [11.0025, 49], [10, 49], [10, 47]]]}
        g = graph(way(1, [1, 2], highway="residential"), way(2, [2, 3], highway="residential"),
                  way(3, [3, 4], highway="residential"), area=small)
        # Way 2 runs from inside (node 2) to outside (node 3) and is kept whole.
        self.assertEqual(sorted(e["osm"] for e in g["edges"]), ["way/1", "way/2"])
        self.assertEqual(len(g["nodes"]), 3)

    def test_the_length_follows_the_way(self):
        g = graph(way(1, [0, 1, 2], highway="residential"))
        # 0.002° of longitude at 48° N.
        self.assertAlmostEqual(g["edges"][0]["length_m"], 148.79, delta=0.05)


class Modes(unittest.TestCase):
    def modes(self, **tags):
        return road_graph.way_modes({k.replace("__", ":"): v for k, v in tags.items()})

    def flags(self, **tags):
        m = self.modes(**tags)
        return None if m is None else (m["walk_fwd"], m["walk_bwd"], m["drive_fwd"], m["drive_bwd"])

    def test_a_residential_street_is_walked_and_driven_both_ways(self):
        self.assertEqual(self.flags(highway="residential"), (1, 1, 1, 1))

    def test_a_footway_is_walked_only(self):
        self.assertEqual(self.flags(highway="footway"), (1, 1, 0, 0))
        self.assertEqual(self.flags(highway="steps"), (1, 1, 0, 0))

    def test_a_oneway_street_is_driven_one_way_and_walked_both(self):
        self.assertEqual(self.flags(highway="residential", oneway="yes"), (1, 1, 1, 0))
        self.assertEqual(self.flags(highway="residential", oneway="-1"), (1, 1, 0, 1))

    def test_a_roundabout_is_oneway_unless_tagged_otherwise(self):
        self.assertEqual(self.flags(highway="primary", junction="roundabout"), (1, 1, 1, 0))
        self.assertEqual(self.flags(highway="primary", junction="roundabout", oneway="no"), (1, 1, 1, 1))

    def test_oneway_foot_applies_to_walking(self):
        self.assertEqual(self.flags(highway="footway", oneway__foot="yes"), (1, 0, 0, 0))

    def test_a_motorway_is_driven_one_way_and_never_walked(self):
        self.assertEqual(self.flags(highway="motorway"), (0, 0, 1, 0))
        self.assertEqual(self.flags(highway="trunk", motorroad="yes"), (0, 0, 1, 1))

    def test_foot_no_closes_walking(self):
        self.assertEqual(self.flags(highway="residential", foot="no"), (0, 0, 1, 1))

    def test_motor_vehicle_no_closes_driving_unless_emergency_vehicles_may(self):
        self.assertEqual(self.flags(highway="track", motor_vehicle="no"), (1, 1, 0, 0))
        m = self.modes(highway="track", motor_vehicle="no", emergency="yes")
        self.assertEqual((m["drive_fwd"], m["vehicle_access"]), (1, "no"))

    def test_the_most_specific_access_key_wins(self):
        m = self.modes(highway="service", access="no", motor_vehicle="yes")
        self.assertEqual((m["drive_fwd"], m["walk_fwd"], m["vehicle_access"], m["foot_access"]), (1, 0, None, "no"))

    def test_a_forestry_track_stays_drivable_and_says_so(self):
        m = self.modes(highway="track", motor_vehicle="agricultural;forestry")
        self.assertEqual((m["drive_fwd"], m["vehicle_access"], m["foot_access"]), (1, "agricultural", None))

    def test_a_private_yard_stays_routable_and_says_so(self):
        m = self.modes(highway="service", access="private")
        self.assertEqual((m["walk_fwd"], m["drive_fwd"], m["foot_access"], m["vehicle_access"]), (1, 1, "private", "private"))

    def test_a_way_nobody_may_use_is_dropped(self):
        self.assertIsNone(self.modes(highway="footway", foot="no"))
        self.assertIsNone(self.modes(highway="construction"))
        self.assertIsNone(self.modes(highway="platform"))

    def test_the_name_comes_along(self):
        self.assertEqual(self.modes(highway="residential", name="Am Bach")["name"], "Am Bach")
        self.assertIsNone(self.modes(highway="residential")["name"])


@unittest.skipUnless(HAVE_GDAL, "needs GDAL (run inside the toolbox)")
class GeoPackage(unittest.TestCase):
    def test_the_graph_is_written_with_its_indexes(self):
        g = graph(node(2, barrier="bollard"), way(1, [1, 2, 3], highway="residential", name="Am%20%Bach"),
                  way(2, [3, 4], highway="footway"))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "roads.gpkg")
            road_graph.write_gpkg(path, g)
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
            import validate_roads

            with sqlite3.connect(path) as conn:
                rows = conn.execute(
                    "SELECT fid, from_node, to_node, highway, name, walk_fwd, drive_fwd, geom FROM edges ORDER BY fid"
                ).fetchall()
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM rtree_edges_geom").fetchone()[0], 3)
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
                barrier = conn.execute("SELECT barrier, drive_blocked FROM nodes WHERE osm = 'node/2'").fetchone()
                columns = [row[1] for row in conn.execute("PRAGMA table_info(edges)")]
            self.assertEqual(columns, validate_roads.EDGE_COLUMNS)
            self.assertEqual(barrier, ("bollard", 1))
            self.assertNotIn("rtree_nodes_geom", tables)
            self.assertEqual([r[0] for r in rows], [1, 2, 3])
            self.assertEqual(rows[0][3:7], ("residential", "Am Bach", 1, 1))
            self.assertEqual(rows[2][3:7], ("footway", None, 1, 0))
            self.assertEqual(validate_roads.gpkg_coords(rows[0][7]), g["edges"][0]["coords"])


if __name__ == "__main__":
    unittest.main()
