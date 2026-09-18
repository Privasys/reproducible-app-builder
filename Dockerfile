# syntax=docker/dockerfile:1
#
# Pre-built builder image for reproducible-app-builder GitHub Actions workflow.
# Contains: Rust toolchain, cargo-component, wasm targets, and the
# enclave-os-wasm-compile binary — ready to compile adopter apps
# without any setup time.
#
# One image per wasmtime line. A precompiled .cwasm only loads on the
# wasmtime release (and engine configuration) it was compiled with, so each
# Enclave OS runtime line needs its own compiler. WASMTIME_FORK_TAG selects
# the release tag of the Privasys wasmtime fork the compiler is built
# against; build-image.yml builds every supported line as a matrix and tags
# the images :wasmtime-<major>.
#
#   docker build --secret id=github_token,env=GH_PAT \
#     --build-arg WASMTIME_FORK_TAG=privasys-v0.3.0 \
#     -t ghcr.io/privasys/reproducible-app-builder:wasmtime-48 .

# The default is the fork tag compile/Cargo.toml pins, so a plain build
# reproduces the manifest as committed.
ARG WASMTIME_FORK_TAG=privasys-v0.2.0

FROM rust:1.87-bookworm AS build
ARG WASMTIME_FORK_TAG
RUN rustup update stable && rustup default stable

# Install wasm targets
RUN rustup target add wasm32-wasip1 wasm32-wasip2

# Install cargo-binstall, then use it to install cargo-component (avoids source compilation).
# Pin to a specific version so that WIT features (top-level enums, records) are supported.
RUN curl -sSfL https://raw.githubusercontent.com/cargo-bins/cargo-binstall/main/install-from-binstall-release.sh | bash
RUN cargo binstall cargo-component@0.21.1 --no-confirm

# Copy and build the AOT compiler (needs access to private Privasys/wasmtime fork)
COPY compile/ /compiler/
WORKDIR /compiler
# Point every wasmtime fork dependency at the requested release. The build
# fails unless each fork tag in the manifest is then exactly that one, so a
# changed Cargo.toml layout cannot silently build the wrong wasmtime.
RUN sed -i -E "s/tag = \"privasys-v[^\"]+\"/tag = \"${WASMTIME_FORK_TAG}\"/" Cargo.toml \
    && test "$(grep -c 'tag = "privasys-v' Cargo.toml)" -ge 1 \
    && test "$(grep -c 'tag = "privasys-v' Cargo.toml)" = "$(grep -cF "tag = \"${WASMTIME_FORK_TAG}\"" Cargo.toml)"
RUN --mount=type=secret,id=github_token \
    if [ -f /run/secrets/github_token ]; then \
      git config --global url."https://x-access-token:$(cat /run/secrets/github_token)@github.com/".insteadOf "https://github.com/"; \
    fi && \
    CARGO_NET_GIT_FETCH_WITH_CLI=true cargo build --release

# ---------------------------------------------------------------------------
# Runtime image — slim, with only what's needed to build adopter apps
# ---------------------------------------------------------------------------
FROM rust:1.87-slim-bookworm
ARG WASMTIME_FORK_TAG
LABEL org.privasys.wasmtime-fork-tag="${WASMTIME_FORK_TAG}"

# Install git (needed for cargo to fetch dependencies) and minimal tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install wasm targets and cargo-component (small additions to the base toolchain).
# Pin version to match the build stage.
RUN rustup target add wasm32-wasip1 wasm32-wasip2
RUN curl -sSfL https://raw.githubusercontent.com/cargo-bins/cargo-binstall/main/install-from-binstall-release.sh | bash \
    && cargo binstall cargo-component@0.21.1 --no-confirm

# Install Python 3 for the WIT doc-comment injection script
RUN apt-get update && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

# Copy the WIT doc injection script
COPY scripts/inject-wit-docs.py /usr/local/bin/inject-wit-docs.py
RUN chmod +x /usr/local/bin/inject-wit-docs.py

# Copy only the pre-built AOT compiler binary (~10MB)
COPY --from=build /compiler/target/release/enclave-os-wasm-compile /usr/local/bin/enclave-os-wasm-compile
# The fork release the compiler was built against, so a build run can name
# the wasmtime it compiled with from inside the image.
RUN mkdir -p /usr/local/share/enclave-os-wasm-compile \
    && echo "${WASMTIME_FORK_TAG}" > /usr/local/share/enclave-os-wasm-compile/wasmtime-fork-tag

# Strip debug symbols and unnecessary toolchain components to reduce image size
RUN strip /usr/local/bin/enclave-os-wasm-compile 2>/dev/null || true \
    && rm -rf /usr/local/rustup/toolchains/*/share/doc \
              /usr/local/rustup/toolchains/*/share/man \
              /usr/local/cargo/registry \
              /usr/local/cargo/git \
              /tmp/*

WORKDIR /workspace
