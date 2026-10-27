# Every build step except tile rendering runs in this image: GDAL (with its
# Python bindings and GEOS) for the elevation and geometry work, osmium for the
# OSM extracts, zip for packaging. The base is pinned by digest and kept current
# by Dependabot's docker ecosystem (.github/dependabot.yml).
FROM ghcr.io/osgeo/gdal:ubuntu-small-3.13.3@sha256:64250faf833c06d4b21afce4c27190039ba7ab58d70f0eebc87cf77d929c0b40

RUN apt-get update \
    && apt-get install -y --no-install-recommends osmium-tool zip curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
