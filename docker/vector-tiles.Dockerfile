# Vector tiles: the Protomaps basemap profile for Planetiler, built from a
# pinned commit. The upstream project publishes no jar or image, so the source
# tarball is fetched by commit and checked against its SHA-256; a different
# archive fails the build instead of being compiled.
#
# protomaps-names-de.patch keeps German as the only translated name (OSM's own
# `name` stays as it is). Upstream writes ~45 `name:xx` keys into every tile.
# `patch` refuses a hunk that no longer applies, so a profile bump that touches
# these lines fails here rather than shipping every language again.
#
# To bump: change BASEMAPS_COMMIT and BASEMAPS_SHA256 together
# (`curl -sL https://codeload.github.com/protomaps/basemaps/tar.gz/<sha> | sha256sum`).
FROM maven:3.9.11-eclipse-temurin-21-alpine@sha256:922927df2c662cdd47ddb116443d6bec4696cfae3de1a0ddac8fcc7b87ce61ae AS build

# protomaps/basemaps main as of 2026-09-11, tiles version 4.15.2.
ARG BASEMAPS_COMMIT=42ffaaa4a85a41bfcb23e43cc0f5b492a5eca123
ADD --checksum=sha256:f89ff8ee6aff13baf60c83b5e98d3811ddb946cc1089d437c435885395764696 \
    https://codeload.github.com/protomaps/basemaps/tar.gz/${BASEMAPS_COMMIT} /src/basemaps.tar.gz

COPY protomaps-names-de.patch /src/
WORKDIR /src
RUN apk add --no-cache patch \
    && tar -xzf basemaps.tar.gz --strip-components=1 \
    && patch -p1 --forward --fuzz=0 < protomaps-names-de.patch \
    && cd tiles \
    && mvn -B -q -DskipTests package \
    && cp target/protomaps-basemap-HEAD-with-deps.jar /src/protomaps-basemap.jar

FROM eclipse-temurin:21.0.8_9-jre-alpine@sha256:990397e0495ac088ab6ee3d949a2e97b715a134d8b96c561c5d130b3786a489d
COPY --from=build /src/protomaps-basemap.jar /opt/protomaps-basemap.jar
# The profile reads its sources from ./data/sources, relative to this directory.
WORKDIR /tiles
ENTRYPOINT ["java", "-jar", "/opt/protomaps-basemap.jar"]
