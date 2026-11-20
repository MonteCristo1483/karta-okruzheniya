# tippecanoe pinned build for pipeline/build_tiles.py.
# There is no apt package and felt publishes no release binaries,
# and ghcr.io/felt/tippecanoe is not pullable (registry: denied),
# so the tool is built from source at a fixed tag.
FROM debian:bookworm-slim

ARG TIPPECANOE_VERSION=2.79.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential git ca-certificates libsqlite3-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${TIPPECANOE_VERSION}" \
        https://github.com/felt/tippecanoe.git /src/tippecanoe \
    && make -C /src/tippecanoe -j"$(nproc)" \
    && make -C /src/tippecanoe install \
    && rm -rf /src

WORKDIR /work
ENTRYPOINT ["tippecanoe"]
