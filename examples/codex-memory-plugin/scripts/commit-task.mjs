export function countExtracted(result) {
  if (!result?.memories_extracted) return 0;
  if (typeof result.memories_extracted === "number") return result.memories_extracted;
  if (typeof result.memories_extracted === "object") {
    return Object.values(result.memories_extracted).reduce(
      (a, b) => a + (typeof b === "number" ? b : 0),
      0,
    );
  }
  return 0;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function taskStatus(task) {
  return typeof task?.status === "string" ? task.status.toLowerCase() : "";
}

export async function waitForCommitTask(commit, fetchJSON, cfg, log = () => {}) {
  if (!commit?.task_id) {
    return { commit, final: commit, task: null, status: "immediate" };
  }

  const taskId = commit.task_id;
  const timeoutMs = Math.max(0, cfg.commitPollTimeoutMs || 0);
  const intervalMs = Math.max(250, cfg.commitPollIntervalMs || 1000);
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    await sleep(Math.min(intervalMs, Math.max(0, deadline - Date.now())));
    const task = await fetchJSON(`/api/v1/tasks/${encodeURIComponent(taskId)}`);
    const status = taskStatus(task);

    if (!task) {
      log("commit_task_poll_miss", { taskId });
      continue;
    }

    if (status === "completed" || status === "succeeded" || status === "done") {
      const final = task.result && typeof task.result === "object"
        ? { ...commit, ...task.result, task_status: status }
        : { ...commit, task_status: status };
      return { commit, final, task, status };
    }

    if (status === "failed" || status === "error" || status === "cancelled" || status === "canceled") {
      return {
        commit,
        final: { ...commit, task_status: status, task_error: task.error || task.result?.error || null },
        task,
        status,
      };
    }

    log("commit_task_poll_pending", { taskId, status: status || "unknown" });
  }

  return { commit, final: commit, task: null, status: "timeout" };
}

export function describeCommitOutcome(ovSessionId, outcome, prefix = "OpenViking session") {
  const taskId = outcome?.commit?.task_id;
  if (outcome?.status === "timeout") {
    return `${prefix} ${ovSessionId} is committed; extraction is still running${taskId ? ` (${taskId})` : ""}`;
  }
  if (["failed", "error", "cancelled", "canceled"].includes(outcome?.status)) {
    return `${prefix} ${ovSessionId} is committed; extraction task failed${taskId ? ` (${taskId})` : ""}`;
  }
  const extracted = countExtracted(outcome?.final);
  const suffix = extracted === 1 ? "memory item" : "memory item(s)";
  return `${prefix} ${ovSessionId} is committed; ${extracted} ${suffix} extracted`;
}
