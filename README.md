# reproducible-app-builder

Reproducible build pipeline for Privasys Enclave OS applications. Compiles adopter source code into deployment-ready artifacts (`.cwasm` for WASM workloads, container images for container workloads) with deterministic, auditable builds.

## What's in this repo

| Path | Purpose |
|------|---------|
| `compile/` | `enclave-os-wasm-compile` — AOT compiler for WASM components (kept in sync with `enclave-os-mini`) |
| `scripts/` | `inject-wit-docs.py` — extracts WIT doc comments into JSON for the developer portal |
| `.github/workflows/build-cwasm.yml` | GitHub Actions workflow dispatched by the management service to build apps |
| `.github/workflows/build-image.yml` | Builds and pushes the `ghcr.io/privasys/reproducible-app-builder` Docker image |
| `Dockerfile` | Builder image: Rust toolchain, `cargo-component`, WASM targets, and the AOT compiler |

## How it works

1. An admin approves a build from the developer dashboard
2. The management service dispatches a `workflow_dispatch` event to this repo
3. The workflow:
   - Checks out the adopter's repo at the specified commit
   - Builds the WASM component (`cargo component build --release`)
   - Optionally injects WIT doc comments into the artifact metadata
   - AOT-compiles to `.cwasm` with `enclave-os-wasm-compile`
   - Reports the result (hash, size, docs) back to the management service via callback
4. The `.cwasm` artifact is stored as a GitHub Actions artifact with a 30-day retention

## Workflow inputs

| Input | Description |
|-------|-------------|
| `repo_url` | Adopter's GitHub repository (`owner/repo`) |
| `commit` | Full commit SHA to build |
| `build_id` | Management service build job UUID |
| `callback_url` | URL to POST build status updates |
| `wasmtime` | Wasmtime line (major version) of the target runtime, e.g. `47` or `48`. Optional, defaults to `47`. Selects the builder image `:wasmtime-<line>`; the callback reports it back as `wasmtime_line` with the pinned `builder_image` digest |

## Builder images

A precompiled `.cwasm` only loads on the wasmtime release (and engine configuration) it was compiled with, so there is one builder image per wasmtime line the Enclave OS runtimes run:

| Image | Wasmtime | Privasys fork tag |
|-------|----------|-------------------|
| `ghcr.io/privasys/reproducible-app-builder:wasmtime-47` (also `:latest`) | 47 | `privasys-v0.2.0` |
| `ghcr.io/privasys/reproducible-app-builder:wasmtime-48` | 48 | `privasys-v0.3.0` |

`build-image.yml` builds them as a matrix on push to `main` when files in `compile/`, `scripts/`, `Dockerfile` or the workflow change. The `Dockerfile` takes the fork tag as the `WASMTIME_FORK_TAG` build argument and substitutes it into `compile/Cargo.toml`; the image records it in `/usr/local/share/enclave-os-wasm-compile/wasmtime-fork-tag` and in the `org.privasys.wasmtime-fork-tag` label. A build run pins the image by digest and names both in its summary. Each image contains:

- Rust stable toolchain with `wasm32-wasip1` and `wasm32-wasip2` targets
- `cargo-component` for building WASM components
- `enclave-os-wasm-compile` for AOT compilation
- Python 3 for the WIT doc injection script

## Keeping the compiler in sync

The `compile/` directory must use the **exact same Wasmtime fork and Engine configuration** as the enclave runtime. When an `enclave-os-mini` release moves to a new wasmtime fork tag, add a line to the matrix in `build-image.yml` (keep the lines still running somewhere) and keep `compile/src/main.rs` in step with the runtime engine configuration. `compile/Cargo.toml` pins the oldest supported line, which is also the image `:latest` points at.
