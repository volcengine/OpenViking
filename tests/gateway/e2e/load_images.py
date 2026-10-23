"""Load only the requested images into one explicitly named kind cluster."""

import argparse
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--kind", default="kind")
    parser.add_argument("images", nargs="+")
    args = parser.parse_args()
    nodes = subprocess.check_output(
        [args.kind, "get", "nodes", "--name", args.cluster], text=True
    ).splitlines()
    with tempfile.TemporaryDirectory(prefix="ovgw-e2e-images-") as temporary:
        archive = Path(temporary) / "images.tar"
        subprocess.run(
            ["docker", "save", "--platform", "linux/amd64", "-o", str(archive), *args.images],
            check=True,
        )
        for node in nodes:
            with archive.open("rb") as source:
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        "-i",
                        node,
                        "ctr",
                        "--namespace=k8s.io",
                        "images",
                        "import",
                        "--no-unpack",
                        "--snapshotter=overlayfs",
                        "-",
                    ],
                    stdin=source,
                    check=True,
                )


if __name__ == "__main__":
    main()
