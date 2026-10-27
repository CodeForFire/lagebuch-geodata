import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

# validate_pack.py runs in the toolbox image; the boundary check needs no GDAL, so the unit
# tests stand it in when the host has none.
try:
    import osgeo  # noqa: F401
except ImportError:
    _gdal = types.SimpleNamespace(UseExceptions=lambda: None, SetConfigOption=lambda *args: None)
    sys.modules["osgeo"] = types.SimpleNamespace(gdal=_gdal)
    sys.modules["osgeo.gdal"] = _gdal

import validate_pack  # noqa: E402

REPO = os.path.join(os.path.dirname(__file__), "..")
BOUNDS = {"west": 11.0, "south": 48.0, "east": 11.4, "north": 48.3}
RING = [[11.0, 48.0], [11.4, 48.0], [11.4, 48.3], [11.0, 48.0]]


class Boundary(unittest.TestCase):
    def check(self, feature, bounds=BOUNDS):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "boundary.geojson")
            with open(path, "w", encoding="utf-8") as f:
                f.write(feature if isinstance(feature, str) else json.dumps(feature))
            validate_pack.check_boundary(path, {"bounds": bounds})

    def test_every_published_boundary_fits_its_pack(self):
        with open(os.path.join(REPO, "packs.json"), encoding="utf-8") as f:
            packs = json.load(f)["packs"]
        for pack in packs:
            with self.subTest(pack["slug"]):
                validate_pack.check_boundary(os.path.join(REPO, "boundaries", f"{pack['slug']}.geojson"), pack)

    def test_polygon_and_multipolygon_are_accepted(self):
        self.check({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [RING]}})
        self.check({"type": "Feature", "geometry": {"type": "MultiPolygon", "coordinates": [[RING], [RING]]}})

    def test_a_point_outside_the_pack_bounds_is_refused(self):
        ring = [[11.0, 48.0], [11.9, 48.0], [11.4, 48.3], [11.0, 48.0]]
        with self.assertRaises(validate_pack.PackError):
            self.check({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}})

    def test_an_open_ring_is_refused(self):
        with self.assertRaises(validate_pack.PackError):
            self.check({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [RING[:-1]]}})

    def test_anything_but_a_polygon_feature_is_refused(self):
        for feature in (
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [11.2, 48.1]}},
            {"type": "FeatureCollection", "features": []},
        ):
            with self.subTest(feature["type"]), self.assertRaises(validate_pack.PackError):
                self.check(feature)

    def test_a_boundary_larger_than_the_app_accepts_is_refused(self):
        padding = " " * validate_pack.MAX_BOUNDARY_BYTES
        with self.assertRaises(validate_pack.PackError):
            self.check(json.dumps({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [RING]}}) + padding)


if __name__ == "__main__":
    unittest.main()
