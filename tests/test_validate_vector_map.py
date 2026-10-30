import gzip
import json
import os
import struct
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

# validate_pack.py runs in the toolbox image; the vector check needs no GDAL, so the unit tests
# stand it in when the host has none.
try:
    import osgeo  # noqa: F401
except ImportError:
    _gdal = types.SimpleNamespace(UseExceptions=lambda: None, SetConfigOption=lambda *args: None)
    sys.modules["osgeo"] = types.SimpleNamespace(gdal=_gdal)
    sys.modules["osgeo.gdal"] = _gdal

import pmtiles_read  # noqa: E402
import validate_pack  # noqa: E402

BOUNDS = {"west": 11.0, "south": 48.0, "east": 11.4, "north": 48.3}
LAYERS = ("earth", "landuse", "water", "roads", "buildings", "places")
# z14 tiles inside BOUNDS.
INSIDE = [(14, 8696, 5680), (14, 8697, 5680)]


def varint(value: int) -> bytes:
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def field(number: int, payload: bytes) -> bytes:
    return varint((number << 3) | 2) + varint(len(payload)) + payload


def tile_id(z: int, x: int, y: int) -> int:
    acc = sum(1 << (2 * i) for i in range(z))
    d, s = 0, (1 << z) // 2
    while s > 0:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
        s //= 2
    return acc + d


def mvt(layers=LAYERS, keys=("kind", "name", "name:de")) -> bytes:
    return b"".join(field(3, field(1, name.encode()) + b"".join(field(3, k.encode()) for k in keys)) for name in layers)


def archive(tiles, min_zoom=0, max_zoom=14, tile_type=1, version="4.15.2", attribution="© OpenStreetMap",
            bounds=(11.05, 48.05, 11.35, 48.25)) -> bytes:
    data, entries = b"", []
    for z, x, y, content in sorted(tiles, key=lambda t: tile_id(*t[:3])):
        blob = gzip.compress(content)
        entries.append((tile_id(z, x, y), len(blob), len(data)))
        data += blob
    directory = varint(len(entries))
    last = 0
    for tid, _, _ in entries:
        directory += varint(tid - last)
        last = tid
    directory += b"".join(varint(1) for _ in entries)
    directory += b"".join(varint(length) for _, length, _ in entries)
    directory += b"".join(varint(offset + 1) for _, _, offset in entries)
    root = gzip.compress(directory)
    meta = gzip.compress(json.dumps({"version": version, "attribution": attribution}).encode())
    root_off = pmtiles_read.HEADER_LENGTH
    meta_off = root_off + len(root)
    data_off = meta_off + len(meta)
    w, s, e, n = (round(v * 1e7) for v in bounds)
    header = struct.pack(
        "<7sBQQQQQQQQQQQBBBBBBiiiiBii", b"PMTiles", 3, root_off, len(root), meta_off, len(meta),
        data_off, 0, data_off, len(data), len(entries), len(entries), len(entries), 1, 2, 2, tile_type,
        min_zoom, max_zoom, w, s, e, n, min_zoom, 0, 0)
    return header + root + meta + data


PACK = {"bounds": BOUNDS, "vectorMap": {"minZoom": 0, "maxZoom": 14, "schema": "protomaps-basemap", "schemaVersion": "4.15.2"}}


class VectorMap(unittest.TestCase):
    def check(self, data: bytes, pack=PACK):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "map.pmtiles")
            with open(path, "wb") as f:
                f.write(data)
            validate_pack.check_vector_map(path, pack)

    def refuses(self, data: bytes, message: str, pack=PACK):
        with self.assertRaises(validate_pack.PackError) as caught:
            self.check(data, pack)
        self.assertIn(message, str(caught.exception))

    def test_an_archive_built_to_the_contract_passes(self):
        self.check(archive([(z, x, y, mvt()) for z, x, y in INSIDE]))

    def test_the_patched_schema_version_passes_when_pack_json_names_it(self):
        pack = {**PACK, "vectorMap": {**PACK["vectorMap"], "schemaVersion": "4.15.2+lagebuch.1"}}
        self.check(archive([(z, x, y, mvt()) for z, x, y in INSIDE], version="4.15.2+lagebuch.1"), pack)

    def test_translations_other_than_german_are_refused(self):
        tiles = [(z, x, y, mvt(keys=("kind", "name", "name:de", "name:en"))) for z, x, y in INSIDE]
        self.refuses(archive(tiles), "translations ['name:en']")

    def test_a_tile_outside_the_pack_is_refused(self):
        self.refuses(archive([(z, x, y, mvt()) for z, x, y in INSIDE] + [(14, 8800, 5680, mvt())]), "lies outside the pack")

    def test_zooms_that_disagree_with_pack_json_are_refused(self):
        self.refuses(archive([(z, x, y, mvt()) for z, x, y in INSIDE], max_zoom=15), "pack.json says 0-14")

    def test_a_missing_layer_is_refused(self):
        self.refuses(archive([(z, x, y, mvt(layers=LAYERS[:-1])) for z, x, y in INSIDE]), "lacks layers ['places']")

    def test_raster_tiles_are_refused(self):
        self.refuses(archive([(z, x, y, mvt()) for z, x, y in INSIDE], tile_type=2), "does not hold vector tiles")

    def test_bounds_reaching_past_the_pack_are_refused(self):
        self.refuses(archive([(z, x, y, mvt()) for z, x, y in INSIDE], bounds=(10.2, 47.6, 12.1, 48.5)), "bounds reach outside")

    def test_a_schema_version_other_than_pack_json_says_is_refused(self):
        self.refuses(archive([(z, x, y, mvt()) for z, x, y in INSIDE], version="5.0.0"), "schema version differs")

    def test_a_directory_pointing_past_the_end_is_refused(self):
        data = bytearray(archive([(z, x, y, mvt()) for z, x, y in INSIDE]))
        struct.pack_into("<Q", data, 16, 10_000_000)  # root directory length
        self.refuses(bytes(data), "past the end of the file")


class TileIds(unittest.TestCase):
    def test_the_hilbert_id_round_trips_through_the_reader(self):
        for z, x, y in [(0, 0, 0), (1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0), (2, 0, 0), (15, 17406, 11342)]:
            with self.subTest((z, x, y)):
                self.assertEqual((z, x, y), pmtiles_read.tile_zxy(tile_id(z, x, y)))

    def test_the_spec_numbers_the_first_tiles_in_hilbert_order(self):
        self.assertEqual([(0, 0, 0), (1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0), (2, 0, 0)], [pmtiles_read.tile_zxy(i) for i in range(6)])


if __name__ == "__main__":
    unittest.main()
