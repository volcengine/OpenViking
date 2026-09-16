"""Read-only projection of the live, server-wide scheduler for administrators."""

from dataclasses import asdict


def snapshot(cron):
    if cron is None:
        return {"available": False, "running": False, "jobs": []}
    return {
        "available": True,
        "running": cron.status()["enabled"],
        "jobs": [
            {
                "id": job.id,
                "name": job.name,
                "enabled": job.enabled,
                "message": job.payload.message,
                "deliver": job.payload.deliver,
                "schedule": asdict(job.schedule),
                "state": {
                    "next_run_at_ms": job.state.next_run_at_ms,
                    "last_run_at_ms": job.state.last_run_at_ms,
                    "last_status": job.state.last_status,
                },
            }
            for job in cron.list_jobs(include_disabled=True)
        ],
    }
