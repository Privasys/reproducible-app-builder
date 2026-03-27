# syntax=docker/dockerfile:1
#
# Pre-built builder image for reproducible-app-builder GitHub Actions workflow.
# Contains: Rust toolchain, cargo-component, wasm targets, and the
# enclave-os-wasm-compile binary — ready to compile adopter apps
# without any setup time.
#
# Rebuild when the Wasmtime fork or engine config changes:
#   docker build --secret id=github_token,env=GH_PAT \
#     -t ghcr.io/privasys/reproducible-app-builder:latest .
#   docker push ghcr.io/privasys/reproducible-app-builder:latest

FROM rust:1.87-bookworm AS build
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
RUN --mount=type=secret,id=github_token \
    if [ -f /run/secrets/github_token ]; then \
      git config --global url."https://x-access-token:$(cat /run/secrets/github_token)@github.com/".insteadOf "https://github.com/"; \
    fi && \
    CARGO_NET_GIT_FETCH_WITH_CLI=true cargo build --release

# ---------------------------------------------------------------------------
# Runtime image — slim, with only what's needed to build adopter apps
# ---------------------------------------------------------------------------
FROM rust:1.87-slim-bookworm

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

# Strip debug symbols and unnecessary toolchain components to reduce image size
RUN strip /usr/local/bin/enclave-os-wasm-compile 2>/dev/null || true \
    && rm -rf /usr/local/rustup/toolchains/*/share/doc \
              /usr/local/rustup/toolchains/*/share/man \
              /usr/local/cargo/registry \
              /usr/local/cargo/git \
              /tmp/*

WORKDIR /workspace
