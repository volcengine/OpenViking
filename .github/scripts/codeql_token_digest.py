"""Apply one source-checked API-token exception to CodeQL's upload SARIF.

SHA-256 lookup digests of high-entropy tokens are not password hashes. Keep the
original SARIF as a workflow artifact; never exclude this query or entire files.
If the reviewed source contract changes, retain every finding for review.
"""

import argparse
import ast
import json
from pathlib import Path

RULE = "py/weak-sensitive-data-hashing"
SOURCE = "openviking/server/account_stores/sql.py"
ISSUER = "openviking/server/api_keys/key_mgr.py"
MARKER = f"# CodeQL token-digest exception: {RULE}"


def function(source, name):
    matches = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    return matches[0] if len(matches) == 1 else None


def reviewed_region(root):
    """Return the encoded-token span only while the reviewed contract holds."""
    source = (root / SOURCE).read_text(encoding="utf-8")
    digest = function(source, "_credential_digest")
    issuer = function((root / ISSUER).read_text(encoding="utf-8"), "generate_api_key")
    if digest is None or issuer is None:
        return None
    expected = ast.parse("return hashlib.sha256(api_key.encode()).hexdigest()").body[0]
    if (
        len(digest.body) != 2
        or ast.get_docstring(digest) is None
        or ast.dump(digest.body[-1]) != ast.dump(expected)
        or source.splitlines()[digest.body[-1].lineno - 2].strip() != MARKER
    ):
        return None
    # Verify the default issuance path, including seed=None. Seeded issuance
    # continues to require a high-entropy seed as documented at the digest.
    expected_issuance = ast.parse(
        "secret = (derive_seeded_api_key_secret(user_id, seed)"
        " if seed is not None else secrets.token_hex(32))\n"
        'return ".".join(map(_encode_segment, (account_id, user_id, secret)))'
    ).body
    if (
        [arg.arg for arg in issuer.args.args] != ["account_id", "user_id", "seed"]
        or [ast.dump(node) for node in issuer.args.defaults] != ["Constant(value=None)"]
        or [ast.dump(node) for node in issuer.body]
        != [ast.dump(node) for node in expected_issuance]
    ):
        return None
    encoded_token = digest.body[-1].value.func.value.args[0]
    return (
        encoded_token.lineno,
        encoded_token.col_offset + 1,
        encoded_token.end_lineno,
        encoded_token.end_col_offset + 1,
    )


def is_exception(result, span):
    if span is None or result.get("ruleId") != RULE:
        return False
    locations = result.get("locations") or []
    if not locations:
        return False
    for location in locations:
        physical = location.get("physicalLocation") or {}
        if (physical.get("artifactLocation") or {}).get("uri") != SOURCE:
            return False
        region = physical.get("region") or {}
        start = (region.get("startLine", 0), region.get("startColumn", 0))
        end = (region.get("endLine", start[0]), region.get("endColumn", 0))
        if not ((span[0], span[1]) <= start < end <= (span[2], span[3])):
            return False
    return True


def filter_sarif(document, span):
    removed = 0
    for run in document.get("runs", []):
        if (run.get("tool") or {}).get("driver", {}).get("name") != "CodeQL":
            continue
        if "results" not in run or run["results"] is None:
            continue
        retained = []
        for result in run["results"]:
            if is_exception(result, span):
                removed += 1
            else:
                retained.append(result)
        run["results"] = retained
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    files = sorted(args.input.glob("*.sarif"))
    if not files:
        raise SystemExit("No CodeQL SARIF files found; refusing to upload empty results.")
    if args.input.resolve() == args.output.resolve():
        raise SystemExit("Original SARIF must be preserved in a separate directory.")
    span = reviewed_region(args.source_root)
    args.output.mkdir(parents=True, exist_ok=True)
    for path in files:
        document = json.loads(path.read_text(encoding="utf-8"))
        removed = filter_sarif(document, span)
        (args.output / path.name).write_text(json.dumps(document), encoding="utf-8")
        print(f"{path.name}: applied {removed} reviewed token-digest exception(s).")
    if span is None:
        print("Source contract changed: all CodeQL findings retained.")


if __name__ == "__main__":
    main()
