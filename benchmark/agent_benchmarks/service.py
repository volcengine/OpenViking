"""HTTP runtime adapters for the generic OV session.train batch runner."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
from pathlib import Path
from uuid import uuid4

from benchmark.agent_benchmarks.catalog import Catalog


class AdapterExecutor:
    def __init__(self, catalog: Catalog, output: Path, options: dict):
        self.catalog, self.output, self.options = catalog, output, options

    async def execute(self, cases, policy_set, context):
        from openviking.session.train.components.dataset_service import (
            case_to_dict,
            rollout_from_dict,
        )

        results = []
        for case in cases:
            item = self.catalog.resolve(case)
            folder = self.output / uuid4().hex
            folder.mkdir(parents=True, exist_ok=False)
            runtime = self.catalog.runtime
            allowed = {
                "model",
                "api_base",
                "reasoning_effort",
                "temperature",
                "max_tokens",
                "max_iterations",
            }
            options = {k: v for k, v in self.options.items() if k in allowed}
            stage = str(context.metadata.get("rollout_stage", context.metadata.get("stage", "")))
            if not stage:
                raise ValueError(
                    "Rollout stage is required to distinguish baseline from memory-enabled execution"
                )
            # Credentials travel only through the subprocess pipe, never files/argv.
            payload = {
                "dataset": self.catalog.dataset,
                "runtime": runtime,
                "task": item,
                "case": case_to_dict(case),
                "output": str(folder),
                "options": options,
                "snapshot_id": context.policy_snapshot_id,
                "memory": {}
                if stage.startswith("baseline")
                else {**policy_set.metadata, "root_uri": policy_set.root_uri},
            }
            repo = Path(__file__).resolve().parents[2]
            roots = [str(repo), str(Path(runtime["openspace_repo"]) / "openspace")]
            env = {
                **os.environ,
                "PYTHONPATH": os.pathsep.join(roots),
                "PYTHONUNBUFFERED": "1",
                "DOCKER_DEFAULT_PLATFORM": "linux/amd64",
            }
            with (folder / "worker.log").open("wb") as log:
                process = await asyncio.create_subprocess_exec(
                    runtime["python"],
                    "-u",
                    "-m",
                    "benchmark.agent_benchmarks.worker",
                    cwd=repo,
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=log,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    await asyncio.wait_for(
                        process.communicate(json.dumps(payload).encode()),
                        timeout=float(runtime.get("timeout_seconds", 3600)),
                    )
                except BaseException:
                    if process.returncode is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            await asyncio.wait_for(process.wait(), timeout=30)
                        except TimeoutError:
                            os.killpg(process.pid, signal.SIGKILL)
                            await process.wait()
                    raise
            result_path = folder / "rollout.json"
            if process.returncode or not result_path.is_file():
                raise RuntimeError(
                    f"Benchmark worker failed (exit={process.returncode}); native log: {folder / 'worker.log'}"
                )
            rollout = rollout_from_dict(json.loads(result_path.read_text()))
            rollout.case = case  # Keep trial-expanded case identity and rubric intact.
            rollout.policy_snapshot_id = context.policy_snapshot_id
            rollout.metadata.update(
                execution_metadata=dict(context.metadata),
                dataset=self.catalog.dataset,
                data_split=f"{self.catalog.dataset}_{case.input['split']}",
                task_no=self.catalog.splits[case.input["split"]].index(item),
            )
            results.append(rollout)
        return results


def create_app(manifest: Path, output: Path, *, concurrency: int = 20):
    from fastapi import HTTPException

    from openviking.session.train.components.case_loader import ListCaseLoader
    from openviking.session.train.components.dataset_service import create_dataset_service_app

    catalog = Catalog(manifest)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    def loader(dataset, domain, split, filters):
        if dataset != catalog.dataset:
            raise HTTPException(status_code=400, detail="Dataset does not match service manifest")
        try:
            return ListCaseLoader(
                [catalog.case(x, split) for x in catalog.rows(domain, split, filters)]
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    app = create_dataset_service_app(
        service_name=catalog.dataset,
        make_case_loader=loader,
        make_rollout_executor=lambda options: AdapterExecutor(catalog, output, options),
        max_rollout_concurrency=concurrency,
    )
    # Extend the existing /health handler without adding an ambiguous duplicate route.
    route = next(x for x in app.routes if getattr(x, "path", None) == "/health")
    original = route.endpoint

    async def health():
        result = await original()
        return {
            **result,
            "runtime_fingerprint": catalog.fingerprint,
            "case_counts": {k: len(v) for k, v in catalog.splits.items()},
        }

    route.endpoint = health
    route.dependant.call = health
    app.state.catalog = catalog
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1945)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=20,
        help="Global maximum hosted rollouts; 1 runs sequentially",
    )
    parser.add_argument(
        "--check", action="store_true", help="Validate manifest/splits without models, Docker or OV"
    )
    args = parser.parse_args()
    if args.check:
        catalog = Catalog(args.manifest)
        print(
            json.dumps(
                {
                    "dataset": catalog.dataset,
                    "counts": {k: len(v) for k, v in catalog.splits.items()},
                    "runtime_fingerprint": catalog.fingerprint,
                }
            )
        )
        return
    import uvicorn

    uvicorn.run(
        create_app(args.manifest, args.output, concurrency=args.concurrency),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
