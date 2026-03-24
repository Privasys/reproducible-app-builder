#!/usr/bin/env python3
"""Inject WIT doc comments into a WASM component as a `package-docs` custom section.

cargo-component doesn't embed /// doc comments from WIT files into the
compiled WASM binary. Enclave OS reads a `package-docs` custom section
(flat JSON map) to surface function and parameter descriptions in the
MCP tool manifest.

Usage:
    python inject-wit-docs.py <wit-dir> <wasm-file> [--output <output-file>]

The script parses every .wit file under <wit-dir> and extracts:
  - export func descriptions           ("func-name"       -> func doc)
  - inline parameter descriptions      ("func-name.param" -> param doc)

Plain // comments (e.g. section dividers) are ignored — only /// is captured.

The output JSON uses flat keys consumed by normalise_package_docs():
  "func-name"         -> function description    (normalised to func:func-name)
  "func-name.param"   -> parameter description   (normalised to param:func-name.param)
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from pathlib import Path


def encode_leb128(value: int) -> bytes:
    """Encode an unsigned integer as LEB128."""
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            byte |= 0x80
        result.append(byte)
        if not value:
            break
    return bytes(result)


def make_custom_section(name: str, payload: bytes) -> bytes:
    """Build a WASM custom section (id=0) with the given name and payload."""
    name_bytes = name.encode("utf-8")
    name_len = encode_leb128(len(name_bytes))
    body = name_len + name_bytes + payload
    section_len = encode_leb128(len(body))
    return b"\x00" + section_len + body


def parse_wit_docs(wit_text: str) -> dict[str, str]:
    """Parse a WIT file and extract /// doc comments for exports and params.

    Returns a flat dict suitable for the package-docs custom section.
    Only captures /// (triple-slash) doc comments — plain // comments
    such as section dividers are silently ignored.
    """
    docs: dict[str, str] = {}
    pending_doc_lines: list[str] = []
    current_func: str | None = None
    in_func_params = False
    brace_depth = 0

    for raw_line in wit_text.splitlines():
        line = raw_line.strip()

        # Accumulate /// doc comments only (not plain // comments)
        if line.startswith("///"):
            comment = line[3:]
            if comment.startswith(" "):
                comment = comment[1:]
            pending_doc_lines.append(comment)
            continue

        # Plain // comment — ignore and do NOT clear pending docs.
        # This lets section dividers sit between /// blocks and exports
        # without breaking the association.
        if line.startswith("//"):
            continue

        # Blank lines between /// block and the export — keep pending
        if not line:
            continue

        # Track brace depth for type blocks (enum, record, variant, flags)
        if re.match(r"(enum|record|variant|flags)\s+", line) and "{" in line:
            # Type docs are not used in MCP — just clear
            pending_doc_lines.clear()
            brace_depth += line.count("{") - line.count("}")
            continue

        if brace_depth > 0:
            pending_doc_lines.clear()
            brace_depth += line.count("{") - line.count("}")
            continue

        # Exported function — may be single-line or multi-line
        export_match = re.match(r"export\s+([\w-]+)\s*:\s*func\s*\(", line)
        if export_match:
            func_name = export_match.group(1)
            if pending_doc_lines:
                docs[func_name] = "\n".join(pending_doc_lines).strip()
            pending_doc_lines.clear()

            # Check if the func signature closes on this line
            if ");" in line or ") ->" in line:
                current_func = None
                in_func_params = False
            else:
                current_func = func_name
                in_func_params = True
            continue

        # Inside a multi-line function signature
        if in_func_params and current_func:
            if pending_doc_lines:
                param_match = re.match(r"([\w-]+)\s*:", line)
                if param_match:
                    param_name = param_match.group(1)
                    docs[f"{current_func}.{param_name}"] = "\n".join(pending_doc_lines).strip()
            pending_doc_lines.clear()

            if ");" in line or ") ->" in line:
                current_func = None
                in_func_params = False
            continue

        # Any other non-blank, non-comment line clears accumulated docs
        pending_doc_lines.clear()

    return docs


def inject_package_docs(wasm_path: Path, docs: dict[str, str], output_path: Path) -> None:
    """Append a package-docs custom section to a WASM binary."""
    wasm_bytes = wasm_path.read_bytes()

    if wasm_bytes[:4] != b"\x00asm":
        raise ValueError(f"Not a valid WASM file: {wasm_path}")

    payload = json.dumps(docs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    section = make_custom_section("package-docs", payload)

    output_path.write_bytes(wasm_bytes + section)


def main() -> None:
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <wit-dir> <wasm-file> [--output <output-file>]", file=sys.stderr)
        sys.exit(1)

    wit_dir = Path(sys.argv[1])
    wasm_path = Path(sys.argv[2])

    output_path = wasm_path
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            output_path = Path(sys.argv[idx + 1])

    if not wit_dir.is_dir():
        print(f"Error: WIT directory not found: {wit_dir}", file=sys.stderr)
        sys.exit(1)
    if not wasm_path.exists():
        print(f"Error: WASM file not found: {wasm_path}", file=sys.stderr)
        sys.exit(1)

    # Parse all .wit files in the directory (not in deps/)
    all_docs: dict[str, str] = {}
    for wit_file in sorted(wit_dir.glob("*.wit")):
        wit_text = wit_file.read_text(encoding="utf-8")
        file_docs = parse_wit_docs(wit_text)
        all_docs.update(file_docs)

    if not all_docs:
        print("No doc comments found — skipping injection.", file=sys.stderr)
        sys.exit(0)

    print(f"Extracted {len(all_docs)} doc entries:")
    for key, val in all_docs.items():
        preview = val[:60].replace("\n", " ")
        if len(val) > 60:
            preview += "…"
        print(f"  {key}: {preview}")

    inject_package_docs(wasm_path, all_docs, output_path)
    print(f"\nInjected package-docs section into {output_path}")


if __name__ == "__main__":
    main()
