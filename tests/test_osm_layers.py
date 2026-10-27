import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import osm_layers  # noqa: E402


def write_seq(path, features):
    with open(path, "w", encoding="utf-8") as f:
        for feature in features:
            f.write("\x1e" + json.dumps(feature) + "\n")


def point(lon, lat, **props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


def square(x, y, size, **props):
    ring = [[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": props}


AREA = square(10.0, 47.0, 2.0)["geometry"]


class Inside(unittest.TestCase):
    def test_a_point_in_the_area_is_inside(self):
        self.assertTrue(osm_layers.inside(AREA, 11.0, 48.0))

    def test_a_point_beyond_the_area_is_outside(self):
        self.assertFalse(osm_layers.inside(AREA, 12.5, 48.0))

    def test_a_point_in_a_hole_is_outside(self):
        outer = square(10.0, 47.0, 2.0)["geometry"]["coordinates"][0]
        hole = square(10.5, 47.5, 1.0)["geometry"]["coordinates"][0]
        self.assertFalse(osm_layers.inside({"type": "Polygon", "coordinates": [outer, hole]}, 11.0, 48.0))


class RepresentativePoint(unittest.TestCase):
    def test_a_point_is_its_own_representative(self):
        self.assertEqual(osm_layers.representative_point({"type": "Point", "coordinates": [11.2, 48.1]}), (11.2, 48.1))

    def test_a_polygon_is_represented_by_its_centroid(self):
        lon, lat = osm_layers.representative_point(square(11.0, 48.0, 0.002)["geometry"])
        self.assertAlmostEqual(lon, 11.001, places=9)
        self.assertAlmostEqual(lat, 48.001, places=9)

    def test_a_multipolygon_is_represented_by_its_largest_part(self):
        big = square(11.0, 48.0, 0.01)["geometry"]["coordinates"]
        small = square(12.0, 49.0, 0.001)["geometry"]["coordinates"]
        lon, _ = osm_layers.representative_point({"type": "MultiPolygon", "coordinates": [small, big]})
        self.assertAlmostEqual(lon, 11.005, places=9)

    def test_a_line_has_no_representative_point(self):
        self.assertIsNone(osm_layers.representative_point({"type": "LineString", "coordinates": [[0, 0], [1, 1]]}))


class Water(unittest.TestCase):
    def build(self, features):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = os.path.join(tmp, "w.seq"), os.path.join(tmp, "water.geojson")
            write_seq(src, features)
            osm_layers.build_water(src, dst, AREA)
            with open(dst, encoding="utf-8") as f:
                return json.load(f)["features"]

    def test_water_keeps_only_allowlisted_tags(self):
        (feature,) = self.build(
            [point(11.0, 48.0, **{"@type": "node", "@id": 1, "emergency": "fire_hydrant",
                                   "fire_hydrant:type": "pillar", "note": "Herr Maier fragen"})]
        )
        self.assertEqual(
            feature["properties"], {"emergency": "fire_hydrant", "fire_hydrant:type": "pillar", "osm": "node/1"}
        )

    def test_water_drops_disused_points(self):
        features = self.build(
            [
                point(11.0, 48.0, **{"@type": "node", "@id": 1, "emergency": "fire_hydrant", "disused": "yes"}),
                point(11.0, 48.0, **{"@type": "node", "@id": 2, "emergency": "fire_hydrant"}),
            ]
        )
        self.assertEqual([f["properties"]["osm"] for f in features], ["node/2"])

    def test_a_hydrant_outside_the_area_is_dropped(self):
        features = self.build([point(13.0, 48.0, **{"@type": "node", "@id": 1, "emergency": "fire_hydrant"})])
        self.assertEqual(features, [])

    def test_a_pond_mapped_as_an_area_becomes_a_point(self):
        (feature,) = self.build([square(11.0, 48.0, 0.002, **{"@type": "way", "@id": 7, "emergency": "fire_water_pond"})])
        self.assertEqual(feature["geometry"]["type"], "Point")


class Addresses(unittest.TestCase):
    def build(self, features):
        tmp = tempfile.mkdtemp()
        src, dst = os.path.join(tmp, "a.seq"), os.path.join(tmp, "addresses.sqlite")
        write_seq(src, features)
        osm_layers.build_addresses(src, dst, AREA)
        with sqlite3.connect(dst) as conn:
            return conn.execute("SELECT street, housenumber, postcode, city, osm FROM addresses").fetchall()

    def test_a_building_and_its_entrance_node_collapse_into_one_address(self):
        address = {"addr:street": "Hauptstraße", "addr:housenumber": "1", "addr:postcode": "82256", "addr:city": "Fürstenfeldbruck"}
        rows = self.build(
            [
                square(11.0, 48.0, 0.0002, **{"@type": "way", "@id": 5, **address}),
                point(11.0001, 48.0001, **{"@type": "node", "@id": 9, **address}),
            ]
        )
        self.assertEqual(rows, [("Hauptstraße", "1", "82256", "Fürstenfeldbruck", "node/9")])

    def test_an_address_without_a_street_uses_its_place(self):
        rows = self.build([point(11.0, 48.0, **{"@type": "node", "@id": 1, "addr:place": "Weiherhof", "addr:housenumber": "3"})])
        self.assertEqual(rows[0][0], "Weiherhof")

    def test_an_object_without_a_housenumber_is_not_an_address(self):
        self.assertEqual(self.build([point(11.0, 48.0, **{"@type": "node", "@id": 1, "addr:street": "Hauptstraße"})]), [])


if __name__ == "__main__":
    unittest.main()
