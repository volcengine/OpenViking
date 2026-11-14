"""Compile independent source assignments into Skill-shaped Markdown drafts."""

from vikingbot.compile.pipeline_io import bounded_jobs


async def run(runtime, node, sources):
    """Generate Markdown per range or complete file, retaining only completed jobs."""
    transform = getattr(runtime.contract, node.task)
    if transform.input_unit == "file":
        assignments = {}
        for source in sources:
            assignments.setdefault(source.metadata["uri"], []).append(source)
        jobs = [
            sorted(parts, key=lambda source: source.metadata["start_char"])
            for parts in assignments.values()
        ]
    else:
        jobs = [[source] for source in sources]

    async def generate(item):
        """Read assigned snapshots through evidence tools; provenance is attached by runtime."""
        index, assigned = item
        refs = [source.source_id for source in assigned]
        try:
            outputs = await runtime.generate(
                f"{node.name}-{index}",
                transform.instructions,
                {
                    "inputs": [
                        {
                            "id": source.source_id,
                            "payload": {**source.metadata, "source_ranges": [source.source_id]},
                        }
                        for source in assigned
                    ]
                },
                refs,
            )
        except BaseException:
            for ref in refs:
                runtime.status[ref] = "failed"
            raise
        for ref in refs:
            runtime.status[ref] = "prepared" if outputs else "unreferenced"
        return outputs

    outputs = await bounded_jobs(
        enumerate(jobs),
        generate,
        concurrency=runtime.limits.source_concurrency,
        metrics=runtime.metrics,
        failures=runtime.failures,
    )
    return [document for job in outputs for document in job]
