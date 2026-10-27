#!/usr/bin/env python3
"""Read and update packs.json, the manifest the app fetches.

  manifest.py next-version         print the version the next build of SLUG gets
  manifest.py upsert               add or replace SLUG's entry from build/pack.json

Env: SLUG, RELATION_ID (upsert), GITHUB_REPOSITORY (owner/repo, set in Actions).
upsert also copies build/area_simple.geojson to boundaries/<slug>.geojson and
writes version/tag/branch to GITHUB_OUTPUT.

Everything in an entry is read from the built pack, never typed in, so the
manifest cannot disagree with the data.
"""
import hashlib
import json
import os
import re
import shutil
import sys

MANIFEST = "packs.json"
SOURCES = "pack-sources.json"
FORMAT_VERSION = 2
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ATTRIBUTION = (
    "Karte, Wasserentnahmestellen, Adressen: © OpenStreetMap-Mitwirkende (ODbL). "
    "Höhen: DGM5 © Bayerische Vermessungsverwaltung (CC BY 4.0)."
)


def load(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def manifest() -> dict:
    data = load(MANIFEST, {"formatVersion": FORMAT_VERSION, "packs": []})
    if data.get("formatVersion") != FORMAT_VERSION:
        raise SystemExit(f"{MANIFEST} has formatVersion {data.get('formatVersion')!r}, expected {FORMAT_VERSION}")
    return data


def next_version(slug: str) -> int:
    entry = next((p for p in manifest()["packs"] if p["slug"] == slug), None)
    return entry["version"] + 1 if entry else 1


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def upsert(slug: str, relation_id: str, repo: str) -> dict:
    with open(os.path.join("build", "pack.json"), encoding="utf-8") as f:
        pack = json.load(f)
    # pack.json arrives as an artifact from the build job, which ran third-party
    # data and images; check what reaches tags, branches and GITHUB_OUTPUT.
    if not SLUG_RE.match(slug) or pack["slug"] != slug:
        raise SystemExit(f"build/pack.json is for {pack['slug']!r}, not {slug!r}")
    version = pack["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version != next_version(slug):
        raise SystemExit(f"build/pack.json has version {version!r}, expected {next_version(slug)}")
    tag = f"{slug}-v{version}"
    zip_name = f"{tag}.zip"
    zip_path = os.path.join("build", zip_name)

    os.makedirs("boundaries", exist_ok=True)
    shutil.copyfile(os.path.join("build", "area_simple.geojson"), os.path.join("boundaries", f"{slug}.geojson"))

    entry = {
        "slug": slug,
        "name": pack["name"],
        "version": version,
        "downloadUrl": f"https://github.com/{repo}/releases/download/{tag}/{zip_name}",
        "sizeBytes": os.path.getsize(zip_path),
        "sha256": sha256(zip_path),
        "bounds": pack["bounds"],
        "boundaryUrl": f"https://raw.githubusercontent.com/{repo}/main/boundaries/{slug}.geojson",
        "minZoom": pack["map"]["minZoom"],
        "maxZoom": pack["map"]["maxZoom"],
        "builtAt": pack["builtAt"],
        "attribution": ATTRIBUTION,
    }

    data = manifest()
    data["packs"] = sorted([p for p in data["packs"] if p["slug"] != slug] + [entry], key=lambda p: p["slug"])
    save(MANIFEST, data)

    sources = load(SOURCES, {})
    sources[slug] = {"relationId": relation_id}
    save(SOURCES, dict(sorted(sources.items())))
    return {"version": version, "tag": tag, "branch": f"pack/{tag}", "zip": zip_path}


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    slug = os.environ["SLUG"]
    if command == "next-version":
        print(next_version(slug))
        return 0
    if command == "upsert":
        result = upsert(slug, os.environ["RELATION_ID"], os.environ.get("GITHUB_REPOSITORY", "CodeForFire/lagebuch-geodata"))
        print(f"{MANIFEST} updated: {slug} -> {result['tag']}")
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a", encoding="utf-8") as f:
                for key, value in result.items():
                    f.write(f"{key}={value}\n")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
