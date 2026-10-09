"""Construct a new experiment from a clone draft, without inherited run identity."""

from copy import deepcopy


def launch_request(draft, *, selection, row_ids, name, concurrency, settings):
    if draft.get("warnings"):
        raise ValueError(f"Template clone warnings require review: {draft['warnings']}")
    source = draft["launch_request"]
    # Allow-list: no source task name, rows, notifications, hooks or leased Memory identity.
    body = {
        key: deepcopy(source[key])
        for key in ("execution_type", "execution_plan", "operator_steps", "config")
        if key in source
    }
    body.update(
        experiment_set_id=selection["experiment_set_id"],
        version=selection["version"],
        name=name,
        target_row_ids=row_ids,
        run_times=1,
        run_parallel=False,
        task_time_limit_seconds=settings.task_time_limit_seconds,
        sandbox_config=deepcopy(settings.sandbox_config),
    )
    if not row_ids or not body.get("operator_steps"):
        raise ValueError("Experiment requires selected rows and operator steps")
    config = body.setdefault("config", {})
    config.update(
        concurrency=concurrency, ramp_up_enabled=False, ramp_up_concurrency_per_minute=concurrency
    )
    groups = (body.get("execution_plan") or {}).get("groups", [])
    unknown_groups = set(settings.group_concurrency) - {group["key"] for group in groups}
    if unknown_groups:
        raise ValueError(f"Unknown group_concurrency keys in template: {sorted(unknown_groups)}")
    unknown_timeouts = set(settings.group_timeout_seconds) - {group["key"] for group in groups}
    if unknown_timeouts:
        raise ValueError(
            f"Unknown group_timeout_seconds keys in template: {sorted(unknown_timeouts)}"
        )
    for group in groups:
        group_concurrency = settings.group_concurrency.get(group["key"], concurrency)
        group["execution_overrides"].update(
            concurrency=group_concurrency,
            ramp_up_enabled=False,
            ramp_up_concurrency_per_minute=group_concurrency,
        )
        if group["key"] in settings.group_timeout_seconds:
            group["execution_overrides"]["timeout_seconds"] = settings.group_timeout_seconds[
                group["key"]
            ]
    if body.get("execution_type", "standard") == "standard":
        body.pop("execution_plan", None)
    return body


async def preflight(client, body):
    resources = body["config"].get("resources", [])
    if not resources:
        return
    result = await client.request(
        "POST",
        "resource-bindings/preflight",
        json={
            "operator_steps": body["operator_steps"],
            "resources": resources,
        },
    )
    if not result["ok"]:
        raise ValueError(f"Viking resource validation failed: {result.get('errors')}")
    # Do not create/change pools automatically. Capacity is an upper bound, not a reservation.
    for resource in resources:
        pool = await client.request("GET", f"resource-pools/{resource['pool_id']}")
        if pool.get("capacity", 0) < body["config"]["concurrency"]:
            raise ValueError(
                f"Resource pool {resource['pool_id']} capacity is below requested concurrency"
            )
