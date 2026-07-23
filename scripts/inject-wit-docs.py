#!/usr/bin/env python3
"""Inject WIT doc comments into a WASM component as a `package-docs` custom section.

cargo-component doesn't embed /// doc comments from WIT files into the
compiled WASM binary. Enclave OS reads a `package-docs` custom section
(flat JSON map) to surface function and parameter descriptions in the
MCP tool manifest, and to derive per-function auth policy from @auth
annotations.

Usage:
    python inject-wit-docs.py <wit-dir> <wasm-file> [--output <output-file>]

The script parses every .wit file under <wit-dir> and extracts:
  - export func descriptions           ("func-name"       -> func doc)
  - inline parameter descriptions      ("func-name.param" -> param doc)
  - @auth annotations on exports       ("auth:func-name"  -> policy)
  - @default-auth on world definition  ("auth:__default__" -> policy)
  - @config-api on a single export     ("config-api"      -> func-name)
  - @price annotations on exports      ("price:func-name" -> price rule JSON)
  - @default-price on world definition ("price:__default__" -> price rule JSON)

Plain // comments (e.g. section dividers) are ignored — only /// is captured.

@auth annotation syntax (in /// doc comments):
  /// @auth public           — no authentication required
  /// @auth authenticated    — any authenticated caller
  /// @auth role(role-name)  — caller must have the named role(s)
  /// @auth owner            — restricted to the app owner (deployer)

@default-auth sets the world-level default for unannotated exports:
  /// @default-auth authenticated

@config-api marks the *single* export that initialises the app. While
the app is unconfigured all other exports are blocked by the runtime
freeze gate; the marked function is implicitly owner-only and any
@auth annotation on it is ignored. At most one @config-api function
may be declared per world.

@price declares a developer-set per-call API fee (x-privasys.price) as a
JSON rule. The enclave folds it into the measured permissions (an attested
price) and, on each successful call, the payer is debited and the owner
credited 85% (platform 15%):
  /// @price {"credits":10000}                                   — caller pays
  /// @price {"credits":10000,"payer":"caller","free_for":["wallet"]}
  /// @price {"credits":10000,"payer":"sponsor","sponsor_from":"rp-id"}
The JSON is validated at build time — a malformed rule fails the build
rather than shipping an app that silently runs unpriced.

The output JSON uses flat keys consumed by normalise_package_docs():
  "func-name"         -> function description    (normalised to func:func-name)
  "func-name.param"   -> parameter description   (normalised to param:func-name.param)
  "auth:func-name"    -> auth policy             (per-function override)
  "auth:__default__"  -> default auth policy      (world-level default)
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

    @auth annotations are extracted into "auth:func-name" keys.
    @default-auth annotations (on the world line) become "auth:__default__".
    @config-api on a function becomes "config-api" -> "<func-name>".
    """
    docs: dict[str, str] = {}
    pending_doc_lines: list[str] = []
    pending_auth: str | None = None
    pending_price: str | None = None
    pending_config_api: bool = False
    current_func: str | None = None
    in_func_params = False
    brace_depth = 0

    # Regex for @auth, @default-auth, @config-api and @price annotations
    auth_re = re.compile(r"^@auth\s+(.+)$")
    default_auth_re = re.compile(r"^@default-auth\s+(.+)$")
    config_api_re = re.compile(r"^@config-api\s*$")
    price_re = re.compile(r"^@price\s+(.+)$")
    default_price_re = re.compile(r"^@default-price\s+(.+)$")

    def validate_price(raw: str, where: str) -> str:
        """Validate a @price JSON rule at build time (fail fast on typos)."""
        try:
            rule = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"@price on {where}: invalid JSON ({e}): {raw}") from e
        if not isinstance(rule, dict):
            raise ValueError(f"@price on {where}: must be a JSON object: {raw}")
        credits = rule.get("credits", 0)
        if not isinstance(credits, int) or credits < 0:
            raise ValueError(f"@price on {where}: 'credits' must be a non-negative integer")
        payer = rule.get("payer", "caller")
        if payer not in ("caller", "sponsor"):
            raise ValueError(f"@price on {where}: 'payer' must be 'caller' or 'sponsor'")
        if payer == "sponsor" and not rule.get("sponsor_from"):
            raise ValueError(f"@price on {where}: payer 'sponsor' requires 'sponsor_from'")
        free_for = rule.get("free_for", [])
        if not isinstance(free_for, list) or any(not isinstance(c, str) for c in free_for):
            raise ValueError(f"@price on {where}: 'free_for' must be a list of strings")
        # Re-serialise compactly so the measured annotation is canonical.
        return json.dumps(rule, ensure_ascii=False, separators=(",", ":"))

    for raw_line in wit_text.splitlines():
        line = raw_line.strip()

        # Accumulate /// doc comments only (not plain // comments)
        if line.startswith("///"):
            comment = line[3:]
            if comment.startswith(" "):
                comment = comment[1:]

            # Check for @auth / @default-auth / @price / @default-price
            auth_match = auth_re.match(comment.strip())
            default_auth_match = default_auth_re.match(comment.strip())
            price_match = price_re.match(comment.strip())
            default_price_match = default_price_re.match(comment.strip())

            if default_auth_match:
                docs["auth:__default__"] = default_auth_match.group(1).strip()
                continue
            elif default_price_match:
                docs["price:__default__"] = validate_price(
                    default_price_match.group(1).strip(), "world default"
                )
                continue
            elif auth_match:
                pending_auth = auth_match.group(1).strip()
                continue
            elif price_match:
                pending_price = price_match.group(1).strip()
                continue
            elif config_api_re.match(comment.strip()):
                pending_config_api = True
                continue

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
            pending_auth = None
            pending_price = None
            pending_config_api = False
            brace_depth += line.count("{") - line.count("}")
            continue

        if brace_depth > 0:
            pending_doc_lines.clear()
            pending_auth = None
            pending_price = None
            pending_config_api = False
            brace_depth += line.count("{") - line.count("}")
            continue

        # Exported function — may be single-line or multi-line
        export_match = re.match(r"export\s+([\w-]+)\s*:\s*func\s*\(", line)
        if export_match:
            func_name = export_match.group(1)
            if pending_doc_lines:
                docs[func_name] = "\n".join(pending_doc_lines).strip()
            if pending_auth:
                docs[f"auth:{func_name}"] = pending_auth
            if pending_price:
                docs[f"price:{func_name}"] = validate_price(pending_price, f"'{func_name}'")
            if pending_config_api:
                if "config-api" in docs and docs["config-api"] != func_name:
                    raise ValueError(
                        f"@config-api may be applied to at most one export per world; "
                        f"already set to '{docs['config-api']}', cannot also set '{func_name}'"
                    )
                docs["config-api"] = func_name
                # @config-api implies owner-only auth; override any @auth.
                docs[f"auth:{func_name}"] = "owner"
            pending_doc_lines.clear()
            pending_auth = None
            pending_price = None
            pending_config_api = False

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
            pending_auth = None
            pending_price = None
            pending_config_api = False

            if ");" in line or ") ->" in line:
                current_func = None
                in_func_params = False
            continue

        # World definition — attach any pending @default-auth
        # (already handled above in the /// parsing, but clear state)
        if re.match(r"world\s+", line):
            pending_doc_lines.clear()
            pending_auth = None
            pending_price = None
            pending_config_api = False
            continue

        # Any other non-blank, non-comment line clears accumulated docs
        pending_doc_lines.clear()
        pending_auth = None
        pending_price = None
        pending_config_api = False

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
        print(f"Usage: {sys.argv[0]} <wit-dir> <wasm-file> [--output <output-file>] [--output-json <json-file>]", file=sys.stderr)
        sys.exit(1)

    wit_dir = Path(sys.argv[1])
    wasm_path = Path(sys.argv[2])

    output_path = wasm_path
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            output_path = Path(sys.argv[idx + 1])

    json_output_path: Path | None = None
    if "--output-json" in sys.argv:
        idx = sys.argv.index("--output-json")
        if idx + 1 < len(sys.argv):
            json_output_path = Path(sys.argv[idx + 1])

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

    # Also write standalone JSON file if requested
    if json_output_path:
        json_output_path.write_text(
            json.dumps(all_docs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Wrote docs JSON to {json_output_path}")


if __name__ == "__main__":
    main()
