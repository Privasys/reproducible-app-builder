# syntax=docker/dockerfile:1
#
# Pre-built builder image for cwasm-builder GitHub Actions workflow.
# Contains: Rust toolchain, cargo-component, wasm targets, and the
# enclave-os-wasm-compile binary — ready to compile adopter apps
# without any setup time.
#
# Rebuild when the Wasmtime fork or engine config changes:
#   docker build --secret id=github_token,env=GH_PAT \
#     -t ghcr.io/privasys/cwasm-builder:latest .
#   docker push ghcr.io/privasys/cwasm-builder:latest

FROM rust:1.87-bookworm AS build

# Install wasm targets
RUN rustup target add wasm32-wasip1 wasm32-wasip2

# Install cargo-component
RUN cargo install cargo-component

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

# Copy toolchain additions from build stage
COPY --from=build /usr/local/rustup/toolchains/ /usr/local/rustup/toolchains/
COPY --from=build /usr/local/cargo/bin/cargo-component /usr/local/cargo/bin/cargo-component

# Copy wasm targets
RUN rustup target add wasm32-wasip1 wasm32-wasip2

# Copy the pre-built AOT compiler
COPY --from=build /compiler/target/release/enclave-os-wasm-compile /usr/local/bin/enclave-os-wasm-compile

WORKDIR /workspace
