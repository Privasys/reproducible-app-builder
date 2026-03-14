# cwasm-builder

Automated build pipeline for compiling `.cwasm` AOT artifacts for Privasys Enclave OS.

This repository contains:

- **`compile/`** — The `enclave-os-wasm-compile` tool (standalone copy, kept in sync with `enclave-os-mini`)
- **`.github/workflows/build-cwasm.yml`** — GitHub Actions workflow triggered by the management service

## How it works

1. An admin triggers a build from the developer dashboard
2. The management service dispatches a `workflow_dispatch` to this repo
3. The workflow:
   - Checks out the adopter's repo at the specified commit
   - Builds the WASM component with `cargo component build --release`
   - AOT-compiles to `.cwasm` with `enclave-os-wasm-compile`
   - Reports the result back to the management service via callback
4. The `.cwasm` artifact is stored as a GitHub Actions artifact

## Inputs

| Input | Description |
|-------|-------------|
| `repo_url` | Adopter's GitHub repo (e.g. `alice/my-wasm-app`) |
| `commit` | Full commit SHA to build |
| `build_id` | Management service build job UUID |
| `callback_url` | URL to POST build status updates |

## Keeping the compiler in sync

The `compile/` directory must use the **exact same Wasmtime fork and Engine configuration** as the enclave runtime. When updating `enclave-os-mini`, also update the `compile/Cargo.toml` and `compile/src/main.rs` here.
