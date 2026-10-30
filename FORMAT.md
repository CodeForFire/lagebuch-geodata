# Pack format, version 2

This is the contract between this repository and the LageBuch app. A change to
anything below that an app at the current `formatVersion` cannot ignore raises
`formatVersion`; the app refuses a manifest or a pack whose version it does not
know, instead of guessing.

Every file is a published standard, readable with QGIS or GDAL and without
LageBuch. Nothing here is a custom binary layout.

## The manifest: `packs.json`

Served raw from `main`:
`https://raw.githubusercontent.com/CodeForFire/lagebuch-geodata/main/packs.json`.
No authentication, and no GitHub API rate limit.

```json
{
  "formatVersion": 2,
  "packs": [
    {
      "slug": "ffb",
      "name": "Landkreis Fürstenfeldbruck",
      "version": 1,
      "downloadUrl": "https://github.com/CodeForFire/lagebuch-geodata/releases/download/ffb-v1/ffb-v1.zip",
      "sizeBytes": 123456789,
      "sha256": "…64 hex digits…",
      "bounds": { "west": 10.95268, "south": 48.06073, "east": 11.45318, "north": 48.32370 },
      "boundaryUrl": "https://raw.githubusercontent.com/CodeForFire/lagebuch-geodata/main/boundaries/ffb.geojson",
      "minZoom": 0,
      "maxZoom": 15,
      "builtAt": "2026-10-27T20:00:00Z",
      "attribution": "Karte, Wasserentnahmestellen, Adressen: © OpenStreetMap-Mitwirkende (ODbL). Höhen: DGM5 © Bayerische Vermessungsverwaltung (CC BY 4.0)."
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `slug` | `^[a-z0-9]+(-[a-z0-9]+)*$`, at most 32 characters. Safe as a folder name. |
| `version` | Increments with every rebuild. The release tag is `<slug>-v<version>`. |
| `sizeBytes`, `sha256` | Of the zip. The app caps the download at `sizeBytes` and rejects a zip whose SHA-256 differs. |
| `bounds` | WGS84 envelope of the buffered pack area, read from the data. |
| `boundaryUrl` | The buffered area as a GeoJSON Feature (WGS84, simplified to ~20 m), for a "does my route fit?" check. |
| `minZoom`, `maxZoom` | The zoom levels present in `map.pmtiles`. |

Every release also carries a [build provenance attestation](https://docs.github.com/actions/security-for-github-actions/using-artifact-attestations),
verifiable with `gh attestation verify <zip> --repo CodeForFire/lagebuch-geodata`.

## The pack: `<slug>-v<version>.zip`

The zip holds exactly these seven files at its root, no folders and nothing else.
An app rejects a zip with any other entry, and one that lacks any of them.

### `pack.json`

The pack's own description. `files` gives the size and SHA-256 of each data
file, so a pack can be checked after it is unpacked.

```json
{
  "formatVersion": 2,
  "slug": "ffb",
  "name": "Landkreis Fürstenfeldbruck",
  "version": 1,
  "builtAt": "2026-10-27T20:00:00Z",
  "relationId": "62595",
  "bounds": { "west": 10.95268, "south": 48.06073, "east": 11.45318, "north": 48.32370 },
  "vectorMap": { "minZoom": 0, "maxZoom": 15, "schema": "protomaps-basemap", "schemaVersion": "4.15.2+lagebuch.1" },
  "elevation": {
    "crs": "EPSG:25832+7837",
    "minX": 645160.0, "minY": 5325160.0, "maxX": 682540.0, "maxY": 5354575.0,
    "cellSize": 5.0,
    "noData": -9999.0
  },
  "sources": {
    "osm": { "extractTimestamp": "2026-10-26T20:21:15Z", "licence": "ODbL-1.0" },
    "dgm5": { "metalinkPublished": "2026-10-21T23:10:11Z", "tiles": 857, "missingTiles": [], "licence": "CC-BY-4.0" }
  },
  "files": {
    "map.pmtiles": { "bytes": 0, "sha256": "…" },
    "elevation.tif": { "bytes": 0, "sha256": "…" },
    "water.geojson": { "bytes": 0, "sha256": "…" },
    "addresses.sqlite": { "bytes": 0, "sha256": "…" },
    "boundary.geojson": { "bytes": 0, "sha256": "…" }
  }
}
```

`sources.dgm5.missingTiles` lists the 1 km DGM5 tiles inside the area that
Bayern does not publish, because they lie outside Bayern. Their cells are
NoData in `elevation.tif`.

### `map.pmtiles`: the base map

The OSM map as [Mapbox Vector Tiles 2.1](https://github.com/mapbox/vector-tile-spec/tree/master/2.1)
in a [PMTiles v3](https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md)
archive. The app draws it itself, so streets stay sharp and names upright at
any zoom.

- **Schema:** the [Protomaps basemap](https://docs.protomaps.com/basemaps/layers)
  layers (`earth`, `landcover`, `landuse`, `water`, `roads`, `buildings`,
  `boundaries`, `places`, `pois`), at the version `vectorMap.schemaVersion`
  names, which is also the archive's `version` metadata. An app checks the
  major version before it styles the tiles, and refuses a pack whose major
  version it has no style for. So a new major version of the schema raises
  `formatVersion`. The version is upstream's with `+lagebuch.N` appended
  (SemVer build metadata), because the profile is patched to add the POI
  details below. An app compares only the major.
- **Names:** `name` is OSM's own, and `name:de` is the only translation.
  Upstream writes ~45 more, which a German app never reads.
- **POI details:** in `pois`, a `kind=social_facility` carries OSM's
  `social_facility=*` (`nursing_home`, `assisted_living`, `group_home`,
  `shelter`, `outreach`, `food_bank`, …) as `kind_detail`, so an Altenheim can
  be told from a Tafel. The value is OSM's as it is, so a list such as
  `group_home;nursing_home` stays one string. A `kind=emergency_access_point` (Rettungspunkt) carries
  OSM's `ref`, the number on the sign. Upstream writes neither. Either is
  absent when OSM has no such tag.
- Zoom `minZoom` to `maxZoom` (0 to 15). Deeper zooms are drawn from the z15
  tile, scaled up as vectors, not as pixels.
- **Every geometry is cut to the area exactly.** An edge tile has nothing
  outside the area, not even empty land, so two packs can be drawn on top of
  each other without clipping. A tile that doesn't touch the
  area is absent. The header's `bounds` are the area's bounding box.
- Tiles and directories are gzipped. Show the metadata's `attribution`
  whenever the map is visible.

Built with [Planetiler](https://github.com/onthegomap/planetiler) and the
Protomaps profile at a pinned commit (`docker/vector-tiles.Dockerfile`).

### `elevation.tif`: terrain heights

A [Cloud Optimized GeoTIFF](https://www.cogeo.org/) holding Bayern's
**Digitales Geländemodell 5 m** (DGM5): bare ground, without vegetation or
buildings, from airborne laser scanning.

| Property | Value |
|---|---|
| CRS | `EPSG:25832+7837`: ETRS89 / UTM zone 32N, heights in DHHN2016 |
| Grid | 5 m, north-up, cell edges on multiples of 5 m (`AREA_OR_POINT=Area`) |
| Band | one, `Float32`, metres |
| NoData | `-9999`: outside the buffered area, or outside Bayern |
| Layout | 256 × 256 internal tiles, DEFLATE with floating-point predictor, no overviews |

**The values are DGM5's own.** The grid is DGM5's grid: cells were copied,
never resampled or reprojected. Each source XYZ point is the centre of the cell
it lands in. `validate_pack.py` checks this against the published source values
for every build.

To sample a WGS84 point, transform it to EPSG:25832, then compute
`col = floor((E − minX) / 5)` and `row = floor((maxY − N) / 5)`. Interpolate
bilinearly between cell centres where a smoother profile is wanted. When a
corner is NoData, use the valid corners or report "no elevation". Never treat
`-9999` as a height.

### `water.geojson`: water points

An [RFC 7946](https://datatracker.ietf.org/doc/html/rfc7946) FeatureCollection,
WGS84, **Points only**. A pond or tank mapped as an area becomes its centroid;
`osm` still links to the original object.

Selected from OpenStreetMap by `emergency=fire_hydrant`, `suction_point`,
`water_tank` or `fire_water_pond`, without `disused=*` or `abandoned=*`.
Properties are restricted to this allowlist; any other tag is dropped:

`emergency`, `fire_hydrant:type`, `fire_hydrant:position`,
`fire_hydrant:diameter`, `fire_hydrant:pressure`, `couplings`,
`couplings:type`, `water_source`, `water_tank:volume`, `flow_rate`, `name`,
`ref`, `operator`, and `osm` (`node/<id>`, `way/<id>` or `relation/<id>`).

Values are the raw OSM strings: units and spellings vary, so parse them
defensively. **OSM is not the Wasserversorger's register.** A hydrant missing
from OSM is not missing on the street, and the reverse is also possible.

### `addresses.sqlite`: offline address lookup

SQLite 3, one table:

```sql
CREATE TABLE addresses (
  street      TEXT NOT NULL,  -- addr:street, or addr:place where there is no street
  housenumber TEXT NOT NULL,  -- as tagged: "12", "12a", "12-14"
  postcode    TEXT NOT NULL,  -- "" when untagged
  city        TEXT NOT NULL,  -- "" when untagged
  suburb      TEXT NOT NULL,  -- "" when untagged
  lat         REAL NOT NULL,  -- WGS84
  lon         REAL NOT NULL,
  osm         TEXT NOT NULL   -- node/<id> or way/<id>
);
CREATE INDEX addresses_street ON addresses (street, housenumber);
```

Every OSM node or area carrying `addr:housenumber` becomes one row, with the
area's centroid as its position. An entrance node and its building with the
same address collapse into one row, and the node wins. Open the file
read-only, and bind every query value as a parameter.

### `boundary.geojson`: the pack's area

The Landkreis plus 3 km, as one RFC 7946 Feature in WGS84 whose geometry is a
`Polygon` or `MultiPolygon`, simplified to ~20 m (a few hundred points, at most
1 MB). It is the same file `boundaryUrl` serves, and the area inside which
`map.pmtiles`, `water.geojson` and `addresses.sqlite` are
complete.

## Licences

`ATTRIBUTION.md` in the zip states them for the person using the pack:

- `map.pmtiles`, `water.geojson`, `addresses.sqlite`: © OpenStreetMap
  contributors. These are derived databases under the ODbL 1.0, so share-alike
  applies to them. Vector tiles keep the OSM geometry and tags in a form that
  can be read back out, so they are treated as data, not as a Produced Work.
- `boundary.geojson`: © OpenStreetMap contributors, ODbL 1.0; the Landkreis
  boundary, buffered and simplified.
- `elevation.tif`: DGM5 © Bayerische Vermessungsverwaltung,
  www.geodaten.bayern.de, CC BY 4.0.

## Changes from version 1

Version 1 is `lagebuch-regions` (`regions.json`, `region.mbtiles` and
`region.dem`, a custom binary grid called "FWDM"). Version 2 changes the
following:

- The elevation is a GeoTIFF instead of FWDM, and it is bare-earth DGM5
  instead of SRTM, a surface model.
- The manifest is versioned and carries `sha256`, and its bounds are read from
  the data.
- Packs follow the Landkreis polygon plus 3 km instead of a rectangle.
- Packs gain zoom levels 16 and 17, `water.geojson`, `addresses.sqlite`,
  `pack.json` and `ATTRIBUTION.md`.

Within version 2, packs later gained `boundary.geojson`, and every pack was
rebuilt with it. An app reads the area from the zip only and refuses a zip
without it. No released app read version 2 before that, so this did not raise
`formatVersion`.

For the same reason, packs then gained `map.pmtiles` and `pack.json` gained
`vectorMap`, again with every pack rebuilt. Later still, packs dropped the
raster `map.mbtiles` (OSM Carto PNGs, zoom 11 to 17) and `pack.json` its `map`
zoom range, and `minZoom`/`maxZoom` in `packs.json` came to describe
`map.pmtiles`. The raster tiles were about four fifths of every zip
(Fürstenfeldbruck: 212 of 266 MB), and the app had drawn the vector tiles
since they arrived. Still no released app read version 2.

Within version 2, `pois` later gained `social_facility` detail and the `ref` of
Rettungspunkte, and `schemaVersion` the `+lagebuch.1` suffix. Both are additive:
an app that does not read them draws the map as before, so this did not raise
`formatVersion`.
