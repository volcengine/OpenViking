"""Build a release image with the gateway and changed pure-Python dependencies."""

import argparse
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", default="v0.5.2", help="release tag of the base image")
    parser.add_argument("--base", help="base image; defaults to the --base-ref release image")
    parser.add_argument("--tag", default="ovgw-e2e:local")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[3])
    args = parser.parse_args()
    args.base = args.base or f"ghcr.io/volcengine/openviking:{args.base_ref}"
    root = args.source_root.resolve()
    files = subprocess.check_output(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=AM",
            args.base_ref,
            "--",
            "openviking",
            "openviking_cli",
        ],
        cwd=root,
        text=True,
    ).splitlines()
    site = "/app/.venv/lib/python3.13/site-packages/"
    lines = [f"FROM {args.base}", f"COPY openviking_gateway/ {site}openviking_gateway/"]
    for name in files:
        if name.endswith(".py"):
            lines.append(f"COPY {name} {site}{name}")
    lines.append(
        'RUN python -I -c "import redis; from openviking_gateway.config import GatewayConfig; '
        "assert 'storage' in GatewayConfig.model_fields; "
        "from pathlib import Path; import openviking_gateway; "
        "assert list((Path(openviking_gateway.__file__).parent / 'lua').glob('*.lua'))\""
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".Dockerfile") as dockerfile:
        dockerfile.write("\n".join(lines) + "\n")
        dockerfile.flush()
        subprocess.run(
            [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "-t",
                args.tag,
                "-f",
                dockerfile.name,
                str(root),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
