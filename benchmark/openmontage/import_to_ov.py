#!/usr/bin/env python3
"""Import the OpenMontage benchmark fixture into OpenViking."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FIXTURE_PATH = ROOT / "data" / "fixture.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def build_fixture_tree(base_dir: Path, fixture: dict) -> Path:
    project_dir = base_dir / fixture["project_id"]
    for artifact in fixture["artifacts"]:
        path = project_dir / artifact["relative_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(artifact["content"], encoding="utf-8")
    return project_dir


def build_client(mode: str, workspace: str | None, url: str | None):
    import openviking as ov

    if mode == "http":
        client = ov.SyncHTTPClient(url=url or "http://localhost:1933")
    else:
        client = ov.OpenViking(path=workspace or "./data/openmontage-workspace")
    client.initialize()
    return client


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["embedded", "http"], default="embedded")
    parser.add_argument("--workspace", help="Embedded-mode workspace path")
    parser.add_argument("--url", help="HTTP server base URL")
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the generated fixture directory instead of deleting it",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fixture = load_fixture()
    temp_root = Path(tempfile.mkdtemp(prefix="openmontage_fixture_"))
    try:
        project_dir = build_fixture_tree(temp_root, fixture)
        client = build_client(args.mode, args.workspace, args.url)
        result = client.add_resource(path=str(project_dir), wait=True)
        print(json.dumps({"project_uri": fixture["project_uri"], "import_result": result}, indent=2))
        return 0
    except Exception as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if not args.keep_temp and temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
