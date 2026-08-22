#!/usr/bin/env python3
"""Fail closed when dependency-audit findings drift from approved exceptions."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {
    "id",
    "ecosystem",
    "package_path",
    "scanner_command",
    "scanner_version",
    "expires_on",
    "owner",
    "rationale",
    "removal_condition",
}
SCANNER_COMMANDS = {
    "pip": "pip-audit -r .security-requirements.txt --format json",
    "cargo": "cargo audit --json",
    "npm": "npm audit --omit=dev --json",
}


@dataclass(frozen=True, order=True)
class Finding:
    id: str
    ecosystem: str
    package_path: str


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON from {path}: {exc}") from exc


def load_baseline(path: Path, today: date) -> set[Finding]:
    data = load_json(path)
    if not isinstance(data, list):
        raise ValueError("baseline must be a JSON array")

    findings: set[Finding] = set()
    for index, entry in enumerate(data):
        if not isinstance(entry, dict) or set(entry) != REQUIRED_FIELDS:
            raise ValueError(
                f"baseline[{index}] must contain exactly: {', '.join(sorted(REQUIRED_FIELDS))}"
            )
        if not all(isinstance(entry[field], str) and entry[field].strip() for field in REQUIRED_FIELDS):
            raise ValueError(f"baseline[{index}] fields must be non-empty strings")
        ecosystem = entry["ecosystem"]
        if ecosystem not in SCANNER_COMMANDS:
            raise ValueError(f"baseline[{index}] has unsupported ecosystem {ecosystem!r}")
        if entry["scanner_command"] != SCANNER_COMMANDS[ecosystem]:
            raise ValueError(f"baseline[{index}] has an unexpected scanner command")
        try:
            expires_on = date.fromisoformat(entry["expires_on"])
        except ValueError as exc:
            raise ValueError(f"baseline[{index}] has invalid expires_on") from exc
        if expires_on < today:
            raise ValueError(
                f"baseline[{index}] for {entry['id']} expired on {entry['expires_on']}"
            )
        finding = Finding(entry["id"], ecosystem, entry["package_path"])
        if finding in findings:
            raise ValueError(f"baseline contains duplicate entry: {finding}")
        findings.add(finding)
    return findings


def parse_pip_audit(path: Path) -> set[Finding]:
    data = load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("dependencies"), list):
        raise ValueError(f"{path} is not pip-audit JSON")
    findings: set[Finding] = set()
    for dependency in data["dependencies"]:
        if not isinstance(dependency, dict):
            raise ValueError(f"{path} contains a malformed dependency")
        name, version, vulns = dependency.get("name"), dependency.get("version"), dependency.get("vulns", [])
        if not isinstance(vulns, list):
            raise ValueError(f"{path} contains malformed vulnerabilities")
        for vuln in vulns:
            if not isinstance(vuln, dict) or not isinstance(vuln.get("id"), str):
                raise ValueError(f"{path} contains a malformed pip-audit vulnerability")
            if not isinstance(name, str) or not isinstance(version, str):
                raise ValueError(f"{path} vulnerability has no package name/version")
            findings.add(Finding(vuln["id"], "pip", f"uv.lock:{name}@{version}"))
    return findings


def parse_cargo_audit(path: Path) -> set[Finding]:
    data = load_json(path)
    try:
        vulnerabilities = data["vulnerabilities"]["list"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"{path} is not cargo-audit JSON") from exc
    if not isinstance(vulnerabilities, list):
        raise ValueError(f"{path} contains malformed cargo-audit vulnerabilities")
    findings: set[Finding] = set()
    for vuln in vulnerabilities:
        try:
            advisory_id = vuln["advisory"]["id"]
            package = vuln["package"]
            name, version = package["name"], package["version"]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"{path} contains a malformed cargo-audit vulnerability") from exc
        if not all(isinstance(value, str) for value in (advisory_id, name, version)):
            raise ValueError(f"{path} contains a malformed cargo-audit field")
        findings.add(Finding(advisory_id, "cargo", f"Cargo.lock:{name}@{version}"))
    return findings


def parse_npm_audit(path: Path, package_lock: Path) -> set[Finding]:
    data = load_json(path)
    vulnerabilities = data.get("vulnerabilities") if isinstance(data, dict) else None
    if not isinstance(vulnerabilities, dict):
        raise ValueError(f"{path} is not npm audit JSON")
    findings: set[Finding] = set()
    for package, vulnerability in vulnerabilities.items():
        if not isinstance(vulnerability, dict) or not isinstance(vulnerability.get("via"), list):
            raise ValueError(f"{path} contains malformed npm audit data for {package}")
        nodes = vulnerability.get("nodes")
        if not isinstance(nodes, list) or not nodes or not isinstance(nodes[0], str):
            raise ValueError(f"{path} does not identify the installed path for {package}")
        for advisory in vulnerability["via"]:
            if not isinstance(advisory, dict):
                continue
            url = advisory.get("url")
            if not isinstance(url, str):
                raise ValueError(f"{path} contains an advisory without an ID URL for {package}")
            marker = "GHSA-"
            start = url.find(marker)
            if start < 0:
                raise ValueError(f"{path} has no GHSA ID in {url!r}")
            advisory_id = url[start:].split("/")[0]
            findings.add(
                Finding(advisory_id, "npm", f"{package_lock.as_posix()}:{nodes[0]}")
            )
    return findings


def parse_scanner_versions(values: list[str]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for value in values:
        ecosystem, separator, version = value.partition("=")
        if not separator or ecosystem not in SCANNER_COMMANDS or not version:
            raise ValueError("--scanner-version must use pip|cargo|npm=VERSION")
        if ecosystem in versions:
            raise ValueError(f"duplicate scanner version for {ecosystem}")
        versions[ecosystem] = version
    return versions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--pip-audit", type=Path, required=True)
    parser.add_argument("--cargo-audit", type=Path, required=True)
    parser.add_argument("--npm-audit", action="append", nargs=2, metavar=("REPORT", "PACKAGE_LOCK"), default=[])
    parser.add_argument("--scanner-version", action="append", default=[])
    args = parser.parse_args()

    try:
        versions = parse_scanner_versions(args.scanner_version)
        if set(versions) != set(SCANNER_COMMANDS):
            raise ValueError("provide exactly one --scanner-version for pip, cargo, and npm")
        baseline = load_baseline(args.baseline, date.today())
        for entry in load_json(args.baseline):
            if entry["scanner_version"] != versions[entry["ecosystem"]]:
                raise ValueError(
                    f"baseline scanner version for {entry['ecosystem']} is {entry['scanner_version']}, "
                    f"but this scan used {versions[entry['ecosystem']]}"
                )
        actual = parse_pip_audit(args.pip_audit) | parse_cargo_audit(args.cargo_audit)
        for report, package_lock in args.npm_audit:
            actual |= parse_npm_audit(Path(report), Path(package_lock))
    except ValueError as exc:
        print(f"security audit baseline verification failed: {exc}", file=sys.stderr)
        return 2

    unexpected = sorted(actual - baseline)
    stale = sorted(baseline - actual)
    if unexpected or stale:
        print("security audit baseline verification failed:", file=sys.stderr)
        for finding in unexpected:
            print(f"  NEW_OR_MOVED {finding.ecosystem} {finding.id} {finding.package_path}", file=sys.stderr)
        for finding in stale:
            print(f"  STALE_BASELINE {finding.ecosystem} {finding.id} {finding.package_path}", file=sys.stderr)
        return 1

    print(f"Security audit baseline matches {len(actual)} reported advisory exceptions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
