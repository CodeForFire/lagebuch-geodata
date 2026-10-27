# lagebuch-geodata

Offline map packs for the [LageBuch](https://github.com/CodeForFire/lagebuch)
fire-brigade incident log, one per Landkreis in Bayern. A pack holds:

- an OpenStreetMap base map, zoom 11–17 (`map.mbtiles`);
- bare-earth terrain heights from Bayern's DGM5 at 5 m (`elevation.tif`), for
  planning a Wasserförderung over a long distance;
- hydrants, Saugstellen, Löschwasserbehälter and Löschteiche (`water.geojson`);
- every address, for finding a location without a network (`addresses.sqlite`);
- the pack's own area, for drawing neighbouring packs as one map (`boundary.geojson`).

Each pack covers its Landkreis's real boundary plus 3 km, so an Einsatz just
across the border still fits.

**[FORMAT.md](FORMAT.md)** is the contract the app reads, and documents every
file. All of them are open standards (MBTiles, Cloud Optimized GeoTIFF,
GeoJSON, SQLite) that QGIS opens as they are.

The app fetches [`packs.json`](packs.json). Each entry points at a GitHub
Release asset and carries its SHA-256, and every release has a build provenance
attestation.

## Requesting a pack

Open a [new pack request](../../issues/new?template=new-pack.yml) with the
Landkreis's OSM relation ID. A maintainer adds the `approved` label, and the
build runs, publishes a release and opens a pull request that adds the pack to
`packs.json`. Merging that pull request publishes the pack to the app.

Only Bayern is supported for now, because the elevation comes from the
Bayerische Vermessungsverwaltung's open DGM5. Published packs rebuild
automatically every quarter.

## How a pack is built

[`scripts/run_build.sh`](scripts/run_build.sh) runs the whole build, locally
the same way as in CI. The host needs only `bash`, `curl`, `python3` and
`docker`. Every geodata tool runs in [`docker/toolbox.Dockerfile`](docker/toolbox.Dockerfile)
(GDAL and osmium, pinned by digest).

```bash
SLUG=ffb NAME="Landkreis Fürstenfeldbruck" RELATION_ID=62595 scripts/run_build.sh
```

1. **Boundary.** The Landkreis polygon comes from Nominatim and is buffered by
   3 km in EPSG:25832.
2. **Elevation.** The DGM5 1 km tiles that touch the area are taken from
   Bayern's open-data Metalink, and each download is checked against the
   publisher's SHA-256. GDAL converts them to a COG and cuts it to the area,
   without resampling.
3. **OSM.** The Geofabrik extract is clipped to the area with osmium.
   Water points and addresses are exported from it.
4. **Map.** OSM Carto tiles are rendered with the pinned
   `overv/openstreetmap-tile-server`, for every tile that touches the area,
   and packed into MBTiles.
5. **Check.** [`scripts/validate_pack.py`](scripts/validate_pack.py) checks
   every file against FORMAT.md. That includes comparing the elevation with
   DGM5's own values at a few thousand sampled cells. A pack that fails is never released.

## Licences

- Code in this repository: [MIT](LICENSE).
- Map tiles: © [OpenStreetMap](https://www.openstreetmap.org/copyright)
  contributors, ODbL 1.0, as a Produced Work.
- `water.geojson`, `addresses.sqlite`: © OpenStreetMap contributors. These are
  derived databases under the ODbL 1.0, so share-alike applies.
- Elevation: DGM5 © Bayerische Vermessungsverwaltung,
  [www.geodaten.bayern.de](https://geodaten.bayern.de/opengeodata/), CC BY 4.0.

## Replaces `lagebuch-regions`

This repository replaces [CodeForFire/lagebuch-regions](https://github.com/CodeForFire/lagebuch-regions),
whose packs used SRTM heights in a custom binary format. That repository stays
online and unchanged until the app reads this format; already-released app
builds keep downloading from it.
