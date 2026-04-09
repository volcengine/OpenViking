import { spawn } from "node:child_process"
import { mkdir, readFile, readdir, rm, stat, unlink, writeFile } from "node:fs/promises"
import { join, resolve as resolvePath } from "node:path"
import { fileURLToPath } from "node:url"
import { upsertFact } from "./fact-index.mjs"

const LOCK_STALE_MS = 10 * 60 * 1000

function queueJobsDir(cfg) {
  return join(cfg.captureQueueDir, "jobs")
}

function workerLockDir(cfg) {
  return join(cfg.captureQueueDir, "worker.lock")
}

function workerScriptPath() {
  return resolvePath(fileURLToPath(new URL("./capture-worker.mjs", import.meta.url)))
}

export async function enqueueCaptureJob(cfg, job) {
  await mkdir(queueJobsDir(cfg), { recursive: true })
  const id = `${Date.now()}-${process.pid}-${Math.random().toString(36).slice(2, 8)}`
  const path = join(queueJobsDir(cfg), `${id}.json`)
  await writeFile(path, `${JSON.stringify({ ...job, id }, null, 2)}\n`)
  return path
}

export async function seedFactIndex(cfg, fact) {
  if (!fact) return
  await upsertFact(cfg.factsPath, {
    ...fact,
    status: "pending",
    updatedAt: new Date().toISOString(),
  })
}

export function kickCaptureWorker() {
  const child = spawn(process.execPath, [workerScriptPath()], {
    detached: true,
    stdio: "ignore",
    env: process.env,
  })
  child.unref()
}

export async function drainCaptureQueue(cfg, log = () => {}, logError = () => {}) {
  const release = await acquireWorkerLock(cfg)
  if (!release) return false

  try {
    while (true) {
      const next = await nextJobPath(cfg)
      if (!next) break
      await processJobFile(cfg, next, log, logError)
    }
    return true
  } finally {
    await release()
  }
}

async function nextJobPath(cfg) {
  try {
    const dir = queueJobsDir(cfg)
    const files = (await readdir(dir))
      .filter((name) => name.endsWith(".json"))
      .sort()
    if (files.length === 0) return null
    return join(dir, files[0])
  } catch {
    return null
  }
}

async function processJobFile(cfg, jobPath, log, logError) {
  let job
  try {
    job = JSON.parse(await readFile(jobPath, "utf-8"))
  } catch (err) {
    logError("queue_read", err)
    await unlink(jobPath).catch(() => {})
    return
  }

  if (!cfg.allowMemoryWrites) {
    log("queue_skip", {
      id: job.id,
      sessionId: job.sessionId,
      reason: `mode=${cfg.mode}`,
    })
    await unlink(jobPath).catch(() => {})
    return
  }

  const result = await captureToOpenViking(cfg, job.text)
  log("queue_capture", {
    id: job.id,
    sessionId: job.sessionId,
    result,
  })

  if (job.fact) {
    await upsertFact(cfg.factsPath, {
      ...job.fact,
      status: result.ok ? "confirmed" : "failed",
      updatedAt: new Date().toISOString(),
    }).catch((err) => logError("fact_upsert", err))
  }

  await unlink(jobPath).catch(() => {})
}

async function acquireWorkerLock(cfg) {
  const lockDir = workerLockDir(cfg)
  await mkdir(cfg.captureQueueDir, { recursive: true })

  try {
    await mkdir(lockDir)
    return async () => {
      await rm(lockDir, { recursive: true, force: true })
    }
  } catch (err) {
    if (err?.code !== "EEXIST") throw err
    if (await isStale(lockDir)) {
      await rm(lockDir, { recursive: true, force: true })
      return acquireWorkerLock(cfg)
    }
    return null
  }
}

async function isStale(path) {
  try {
    const info = await stat(path)
    return Date.now() - info.mtimeMs > LOCK_STALE_MS
  } catch {
    return false
  }
}

async function fetchJSON(cfg, path, init = {}, timeoutMs = cfg.captureTimeoutMs) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const headers = { "Content-Type": "application/json" }
    if (cfg.apiKey) headers["X-API-Key"] = cfg.apiKey
    if (cfg.agentId) headers["X-OpenViking-Agent"] = cfg.agentId
    const response = await fetch(`${cfg.baseUrl}${path}`, { ...init, headers, signal: controller.signal })
    const body = await response.json().catch(() => null)
    if (!response.ok || !body || body.status === "error") return null
    return body.result ?? body
  } catch {
    return null
  } finally {
    clearTimeout(timer)
  }
}

async function getTask(cfg, taskId) {
  return fetchJSON(cfg, `/api/v1/tasks/${encodeURIComponent(taskId)}`, { method: "GET" }, cfg.captureTimeoutMs)
}

async function commitSession(cfg, sessionId) {
  const result = await fetchJSON(cfg, `/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`, {
    method: "POST",
    body: JSON.stringify({}),
  }, cfg.captureTimeoutMs)

  if (!result?.task_id) {
    return {
      status: result?.status || "completed",
      memoriesExtracted: Object.values(result?.memories_extracted || {}).reduce((sum, count) => sum + count, 0),
      taskId: result?.task_id || null,
    }
  }

  const deadline = Date.now() + cfg.captureTimeoutMs
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 500))
    const task = await getTask(cfg, result.task_id)
    if (!task) break
    if (task.status === "completed") {
      const memoriesExtracted = Object.values(task.result?.memories_extracted || {}).reduce((sum, count) => sum + count, 0)
      return { status: "completed", memoriesExtracted, taskId: result.task_id }
    }
    if (task.status === "failed") {
      return { status: "failed", memoriesExtracted: 0, taskId: result.task_id, error: task.error }
    }
  }

  return { status: "timeout", memoriesExtracted: 0, taskId: result.task_id }
}

async function captureToOpenViking(cfg, text) {
  const created = await fetchJSON(cfg, "/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({}),
  })
  if (!created?.session_id) return { ok: false, reason: "session_create_failed" }

  const sessionId = created.session_id
  try {
    await fetchJSON(cfg, `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: "POST",
      body: JSON.stringify({ role: "user", content: text }),
    })

    const commit = await commitSession(cfg, sessionId)
    if (commit.status === "failed") {
      return { ok: false, reason: "commit_failed", error: commit.error }
    }
    return {
      ok: true,
      status: commit.status,
      count: commit.memoriesExtracted,
      taskId: commit.taskId,
    }
  } finally {
    await fetchJSON(cfg, `/api/v1/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    }).catch(() => {})
  }
}
