import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import manifest  # noqa: E402


class Manifest(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)
        os.makedirs("build")

    def tearDown(self):
        os.chdir(self._cwd)

    def write_build(self, slug="ffb", version=1):
        pack = {
            "formatVersion": 2,
            "slug": slug,
            "name": "Landkreis Fürstenfeldbruck",
            "version": version,
            "builtAt": "2026-10-27T20:00:00Z",
            "bounds": {"west": 10.95, "south": 48.06, "east": 11.45, "north": 48.32},
            "map": {"minZoom": 11, "maxZoom": 17},
        }
        with open("build/pack.json", "w", encoding="utf-8") as f:
            json.dump(pack, f)
        with open(f"build/{slug}-v{version}.zip", "wb") as f:
            f.write(b"zip")
        with open("build/area_simple.geojson", "w", encoding="utf-8") as f:
            f.write("{}")

    def test_a_new_slug_starts_at_version_1(self):
        self.assertEqual(manifest.next_version("ffb"), 1)

    def test_upsert_takes_bounds_and_hash_from_the_built_pack(self):
        self.write_build()
        result = manifest.upsert("ffb", "62595", "CodeForFire/lagebuch-geodata")
        with open("packs.json", encoding="utf-8") as f:
            (entry,) = json.load(f)["packs"]
        self.assertEqual(result["tag"], "ffb-v1")
        self.assertEqual(entry["bounds"]["north"], 48.32)
        self.assertEqual(entry["sha256"], hashlib.sha256(b"zip").hexdigest())
        self.assertEqual(entry["sizeBytes"], 3)
        self.assertTrue(entry["downloadUrl"].endswith("/releases/download/ffb-v1/ffb-v1.zip"))
        self.assertTrue(os.path.exists("boundaries/ffb.geojson"))

    def test_the_next_build_of_a_published_slug_gets_the_next_version(self):
        self.write_build()
        manifest.upsert("ffb", "62595", "CodeForFire/lagebuch-geodata")
        self.assertEqual(manifest.next_version("ffb"), 2)

    def test_upsert_replaces_the_entry_instead_of_adding_a_second(self):
        self.write_build(version=1)
        manifest.upsert("ffb", "62595", "CodeForFire/lagebuch-geodata")
        self.write_build(version=2)
        manifest.upsert("ffb", "62595", "CodeForFire/lagebuch-geodata")
        with open("packs.json", encoding="utf-8") as f:
            packs = json.load(f)["packs"]
        self.assertEqual([(p["slug"], p["version"]) for p in packs], [("ffb", 2)])

    def test_a_pack_claiming_another_version_is_refused(self):
        self.write_build(version=5)
        with self.assertRaises(SystemExit):
            manifest.upsert("ffb", "62595", "CodeForFire/lagebuch-geodata")

    def test_a_pack_for_an_unsafe_slug_is_refused(self):
        self.write_build(slug="ffb\nbranch=main")
        with self.assertRaises(SystemExit):
            manifest.upsert("ffb\nbranch=main", "62595", "CodeForFire/lagebuch-geodata")

    def test_a_manifest_of_another_format_version_is_refused(self):
        with open("packs.json", "w", encoding="utf-8") as f:
            json.dump({"formatVersion": 1, "packs": []}, f)
        with self.assertRaises(SystemExit):
            manifest.next_version("ffb")


if __name__ == "__main__":
    unittest.main()
