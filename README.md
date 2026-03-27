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

## Builder image

The Docker image (`ghcr.io/privasys/reproducible-app-builder:latest`) is rebuilt automatically on push to `main` when files in `compile/`, `scripts/`, or `Dockerfile` change. It contains:

- Rust stable toolchain with `wasm32-wasip1` and `wasm32-wasip2` targets
- `cargo-component` for building WASM components
- `enclave-os-wasm-compile` for AOT compilation
- Python 3 for the WIT doc injection script

## Keeping the compiler in sync

The `compile/` directory must use the **exact same Wasmtime fork and Engine configuration** as the enclave runtime. When updating `enclave-os-mini`, also update the `compile/Cargo.toml` and `compile/src/main.rs` here.
