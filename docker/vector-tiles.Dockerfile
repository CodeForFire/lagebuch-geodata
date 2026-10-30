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
# protomaps-pois-detail.patch keeps two OSM tags on POIs that LageBuch shows:
# `social_facility=*` as the `kind_detail` of `kind=social_facility`, and `ref`
# on `kind=emergency_access_point`. It also marks the schema version as
# `<upstream>+lagebuch.N`, and adds tests to PoisTest, which the build runs.
#
# To bump: change BASEMAPS_COMMIT and BASEMAPS_SHA256 together
# (`curl -sL https://codeload.github.com/protomaps/basemaps/tar.gz/<sha> | sha256sum`),
# and carry the `+lagebuch.N` suffix over to the new upstream version.
FROM maven:3.9-eclipse-temurin-26-alpine@sha256:4c85d516dd8c7e50d49a403d604e565e0dbb09a790186fe3245725d313ae7cb9 AS build

# protomaps/basemaps main as of 2026-09-11, tiles version 4.15.2.
ARG BASEMAPS_COMMIT=42ffaaa4a85a41bfcb23e43cc0f5b492a5eca123
ADD --checksum=sha256:f89ff8ee6aff13baf60c83b5e98d3811ddb946cc1089d437c435885395764696 \
    https://codeload.github.com/protomaps/basemaps/tar.gz/${BASEMAPS_COMMIT} /src/basemaps.tar.gz

COPY protomaps-names-de.patch protomaps-pois-detail.patch /src/
WORKDIR /src
RUN apk add --no-cache patch \
    && tar -xzf basemaps.tar.gz --strip-components=1 \
    && patch -p1 --forward --fuzz=0 < protomaps-names-de.patch \
    && patch -p1 --forward --fuzz=0 < protomaps-pois-detail.patch \
    && cd tiles \
    && mvn -B -q -Dtest=PoisTest -Dsurefire.failIfNoSpecifiedTests=false package \
    && cp target/protomaps-basemap-HEAD-with-deps.jar /src/protomaps-basemap.jar

FROM eclipse-temurin:24.0.2_12-jre-alpine@sha256:4044b6c87cb088885bcd0220f7dc7a8a4aab76577605fa471945d2e98270741f
COPY --from=build /src/protomaps-basemap.jar /opt/protomaps-basemap.jar
# The profile reads its sources from ./data/sources, relative to this directory.
WORKDIR /tiles
ENTRYPOINT ["java", "-jar", "/opt/protomaps-basemap.jar"]
