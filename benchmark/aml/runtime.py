"""Start the native server or AML adapter with one shared model admission ledger."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

from benchmark.aml.model_admission import Gate, install


def prepare_runtime(
    service: str,
    native_config: Path,
    database: Path,
    budget_file: Path | None = None,
) -> Gate:
    configuration = json.loads(native_config.read_text())
    source_model = configuration["vlm"]["model"]
    embedding = configuration["embedding"]
    embedding_model = (embedding.get("hybrid") or embedding.get("dense") or embedding)["model"]
    return install(
        source_model,
        embedding_model,
        native=service == "native",
        phase=f"aml-{service}",
        database=database,
        budget_file=budget_file,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=("native", "adapter"))
    parser.add_argument("--native-config", "--config", default=os.getenv("OPENVIKING_CONFIG_FILE"))
    parser.add_argument("--admission-db", default=os.getenv("AML_MODEL_ADMISSION_DB"))
    parser.add_argument("--admission-budgets", default=os.getenv("AML_MODEL_ADMISSION_BUDGETS"))
    args, remaining = parser.parse_known_args()
    if not args.native_config or not args.admission_db:
        parser.error("both a native config and a shared admission database are required")
    native_config = Path(args.native_config).expanduser().resolve()
    prepare_runtime(
        args.service,
        native_config,
        Path(args.admission_db).expanduser(),
        Path(args.admission_budgets).expanduser() if args.admission_budgets else None,
    )
    # Forward the original server arguments without changing model configuration.
    if args.service == "native":
        remaining = ["--config", str(native_config), *remaining]
    sys.argv = [sys.argv[0], *remaining]
    module = "openviking.server.bootstrap" if args.service == "native" else "benchmark.aml.server"
    importlib.import_module(module).main()


if __name__ == "__main__":
    main()
