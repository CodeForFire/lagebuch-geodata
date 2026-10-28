"""Read just enough of a PMTiles v3 archive of Mapbox Vector Tiles to describe and check it.

https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md. Standard library only, so the
same module serves make_pack_json.py and validate_pack.py in the toolbox and the unit tests on
the host.
"""
import gzip
import json
import struct

HEADER_LENGTH = 127
MAGIC = b"PMTiles"
TILE_TYPE_MVT = 1
COMPRESSION_GZIP = 2
_HEADER = struct.Struct("<7sBQQQQQQQQQQQBBBBBBiiiiBii")


class PmTilesError(Exception):
    pass


def read_header(data: bytes) -> dict:
    if len(data) < HEADER_LENGTH:
        raise PmTilesError("shorter than a PMTiles header")
    (magic, version, root_off, root_len, meta_off, meta_len, leaf_off, leaf_len, tile_off, tile_len,
     addressed, entries, contents, clustered, internal, tile_compression, tile_type, min_zoom, max_zoom,
     min_lon, min_lat, max_lon, max_lat, center_zoom, center_lon, center_lat) = _HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise PmTilesError("not a PMTiles archive")
    return {
        "version": version,
        "rootOffset": root_off, "rootLength": root_len,
        "metadataOffset": meta_off, "metadataLength": meta_len,
        "leafOffset": leaf_off, "leafLength": leaf_len,
        "tileDataOffset": tile_off, "tileDataLength": tile_len,
        "addressedTiles": addressed,
        "internalCompression": internal, "tileCompression": tile_compression, "tileType": tile_type,
        "minZoom": min_zoom, "maxZoom": max_zoom,
        "bounds": {"west": min_lon / 1e7, "south": min_lat / 1e7, "east": max_lon / 1e7, "north": max_lat / 1e7},
    }


def _section(data: bytes, offset: int, length: int) -> bytes:
    if offset + length > len(data):
        raise PmTilesError("a section reaches past the end of the file")
    return data[offset:offset + length]


def _varints(buf: bytes, pos: int, count: int) -> tuple[list[int], int]:
    out = []
    for _ in range(count):
        value = shift = 0
        while True:
            if pos >= len(buf) or shift > 63:
                raise PmTilesError("truncated varint")
            byte = buf[pos]
            pos += 1
            value |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                break
        out.append(value)
    return out, pos


def _directory(raw: bytes) -> list[tuple[int, int, int, int]]:
    buf = gzip.decompress(raw)
    (count,), pos = _varints(buf, 0, 1)
    ids, pos = _varints(buf, pos, count)
    runs, pos = _varints(buf, pos, count)
    lengths, pos = _varints(buf, pos, count)
    offsets, pos = _varints(buf, pos, count)
    entries: list[tuple[int, int, int, int]] = []
    tile_id = 0
    for i in range(count):
        tile_id += ids[i]
        offset = offsets[i] - 1 if offsets[i] else entries[-1][3] + entries[-1][2]
        entries.append((tile_id, runs[i], lengths[i], offset))
    return entries


def tile_zxy(tile_id: int) -> tuple[int, int, int]:
    """The inverse of the spec's Hilbert tile id."""
    acc = z = 0
    while tile_id >= acc + (1 << (2 * z)):
        acc += 1 << (2 * z)
        z += 1
    d, n, x, y, s = tile_id - acc, 1 << z, 0, 0, 1
    while s < n:
        rx = 1 & (d // 2)
        ry = 1 & (d ^ rx)
        if ry == 0:
            if rx == 1:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
        x += s * rx
        y += s * ry
        d //= 4
        s *= 2
    return z, x, y


def metadata(data: bytes, header: dict) -> dict:
    raw = _section(data, header["metadataOffset"], header["metadataLength"])
    return json.loads(gzip.decompress(raw) if header["internalCompression"] == COMPRESSION_GZIP else raw)


def tiles(data: bytes, header: dict):
    """Yields (z, x, y, compressed bytes) for every addressed tile, run-length entries expanded."""
    def walk(entries, depth):
        if depth > 3:
            raise PmTilesError("leaf directories nested deeper than the spec's writers go")
        for tile_id, run, length, offset in entries:
            if run == 0:
                leaf = _section(data, header["leafOffset"] + offset, length)
                yield from walk(_directory(leaf), depth + 1)
                continue
            blob = _section(data, header["tileDataOffset"] + offset, length)
            for t in range(tile_id, tile_id + run):
                yield (*tile_zxy(t), blob)

    root = _section(data, header["rootOffset"], header["rootLength"])
    yield from walk(_directory(root), 0)


def _fields(buf: bytes):
    pos = 0
    while pos < len(buf):
        (key,), pos = _varints(buf, pos, 1)
        field, wire = key >> 3, key & 7
        if wire == 0:
            (value,), pos = _varints(buf, pos, 1)
            yield field, value
        elif wire == 2:
            (length,), pos = _varints(buf, pos, 1)
            yield field, buf[pos:pos + length]
            pos += length
        elif wire == 1:
            pos += 8
        elif wire == 5:
            pos += 4
        else:
            raise PmTilesError(f"unsupported protobuf wire type {wire}")


def tile_layers(compressed: bytes) -> dict[str, set[str]]:
    """The layer names of a gzipped MVT tile, each with the property keys its features use."""
    layers: dict[str, set[str]] = {}
    for field, layer in _fields(gzip.decompress(compressed)):
        if field != 3:
            continue
        name, keys = "", set()
        for layer_field, value in _fields(layer):
            if layer_field == 1:
                name = value.decode("utf-8")
            elif layer_field == 3:
                keys.add(value.decode("utf-8"))
        layers.setdefault(name, set()).update(keys)
    return layers
