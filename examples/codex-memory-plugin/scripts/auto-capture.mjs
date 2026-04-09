#!/usr/bin/env node

import { readFile, writeFile, mkdir } from "node:fs/promises"
import { join } from "node:path"
import { loadConfig } from "./config.mjs"
import { createLogger } from "./debug-log.mjs"
import { enqueueCaptureJob, kickCaptureWorker, seedFactIndex, drainCaptureQueue } from "./capture-queue.mjs"
import { inferFactFromText } from "./fact-index.mjs"
import { buildHookDedupeKey, claimHookInvocation } from "./hook-dedupe.mjs"

const cfg = loadConfig()
const { log, logError } = createLogger("auto-capture")

function output(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`)
}

function approve(systemMessage) {
  const out = { continue: true }
  output(out)
}

function stateFilePath(sessionId) {
  const safe = sessionId.replace(/[^a-zA-Z0-9_-]/g, "_")
  return join(cfg.captureStateDir, `${safe}.json`)
}

async function loadState(sessionId) {
  try {
    return JSON.parse(await readFile(stateFilePath(sessionId), "utf-8"))
  } catch {
    return { capturedTurnCount: 0 }
  }
}

async function saveState(sessionId, state) {
  try {
    await mkdir(cfg.captureStateDir, { recursive: true })
    await writeFile(stateFilePath(sessionId), JSON.stringify(state))
  } catch {}
}

const MEMORY_TRIGGERS = [
  /for future reference/i,
  /remember this/i,
  /remember|preference|prefer|important|decision|decided|always|never/i,
  /记住|偏好|喜欢|喜爱|崇拜|讨厌|害怕|重要|决定|总是|永远|优先|习惯|爱好|擅长|最爱|不喜欢/i,
  /[\w.-]+@[\w.-]+\.\w+/,
  /\+\d{10,}/,
  /(?:我|my)\s*(?:是|叫|名字|name|住在|live|来自|from|生日|birthday|电话|phone|邮箱|email)/i,
  /(?:我|i)\s*(?:喜欢|崇拜|讨厌|害怕|擅长|不会|爱|恨|想要|需要|希望|觉得|认为|相信)/i,
  /(?:favorite|favourite|love|hate|enjoy|dislike|admire|idol|fan of)/i,
]

const RELEVANT_MEMORIES_BLOCK_RE = /<relevant-memories>[\s\S]*?<\/relevant-memories>/gi
const COMMAND_TEXT_RE = /^\/[a-z0-9_-]{1,64}\b/i
const NON_CONTENT_TEXT_RE = /^[\p{P}\p{S}\s]+$/u
const CJK_CHAR_RE = /[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uac00-\ud7af]/

function sanitize(text) {
  return text
    .replace(RELEVANT_MEMORIES_BLOCK_RE, " ")
    .replace(/\u0000/g, "")
    .replace(/\s+/g, " ")
    .trim()
}

function shouldCapture(text) {
  const normalized = sanitize(text)
  if (!normalized) return { capture: false, reason: "empty", text: "" }

  const compact = normalized.replace(/\s+/g, "")
  const minLen = CJK_CHAR_RE.test(compact) ? 4 : 10
  if (compact.length < minLen || normalized.length > cfg.captureMaxLength) {
    return { capture: false, reason: "length_out_of_range", text: normalized }
  }

  if (COMMAND_TEXT_RE.test(normalized)) {
    return { capture: false, reason: "command", text: normalized }
  }

  if (NON_CONTENT_TEXT_RE.test(normalized)) {
    return { capture: false, reason: "non_content", text: normalized }
  }

  const inferredFact = inferFactFromText(normalized)

  if (cfg.captureMode === "keyword") {
    for (const trigger of MEMORY_TRIGGERS) {
      if (trigger.test(normalized)) {
        return { capture: true, reason: `trigger:${trigger}`, text: normalized, fact: inferredFact }
      }
    }
    return { capture: false, reason: "no_trigger", text: normalized }
  }

  if (cfg.captureMode === "semantic") {
    return { capture: true, reason: "semantic", text: normalized, fact: inferredFact }
  }

  if (inferredFact) {
    return { capture: true, reason: "fact_pattern", text: normalized, fact: inferredFact }
  }

  for (const trigger of MEMORY_TRIGGERS) {
    if (trigger.test(normalized)) {
      return { capture: true, reason: `durable_trigger:${trigger}`, text: normalized, fact: inferredFact }
    }
  }

  return { capture: false, reason: "not_durable", text: normalized }
}

function parseTranscript(content) {
  try {
    const parsed = JSON.parse(content)
    if (Array.isArray(parsed)) return parsed
  } catch {}

  const lines = content.split("\n").filter((line) => line.trim())
  const messages = []
  for (const line of lines) {
    try {
      messages.push(JSON.parse(line))
    } catch {}
  }
  return messages
}

function extractAllTurns(messages) {
  const turns = []
  for (const message of messages) {
    if (!message || typeof message !== "object") continue

    if (message.type === "response_item" && message.payload && typeof message.payload === "object") {
      const payload = message.payload
      if (payload.type === "message" && payload.role === "assistant") {
        const content = Array.isArray(payload.content) ? payload.content : []
        const text = content
          .filter((block) =>
            (block?.type === "input_text" || block?.type === "output_text") && typeof block.text === "string")
          .map((block) => block.text)
          .join("\n")
          .trim()
        if (text) turns.push({ role: payload.role, text })
      }
      continue
    }

    if (message.type === "event_msg" && message.payload && typeof message.payload === "object") {
      const payload = message.payload
      if (payload.type === "user_message" && typeof payload.message === "string" && payload.message.trim()) {
        turns.push({ role: "user", text: payload.message.trim() })
      }
      if (payload.type === "agent_message" && typeof payload.message === "string" && payload.message.trim()) {
        turns.push({ role: "assistant", text: payload.message.trim() })
      }
      continue
    }

    let role = message.role
    let text = ""

    if (typeof message.content === "string") {
      text = message.content
    } else if (Array.isArray(message.content)) {
      text = message.content
        .filter((block) => block?.type === "text" && typeof block.text === "string")
        .map((block) => block.text)
        .join("\n")
    } else if (typeof message.message === "object" && message.message) {
      role = message.message.role || role
      if (typeof message.message.content === "string") {
        text = message.message.content
      } else if (Array.isArray(message.message.content)) {
        text = message.message.content
          .filter((block) => block?.type === "text" && typeof block.text === "string")
          .map((block) => block.text)
          .join("\n")
      }
    }

    if ((role === "user" || role === "assistant") && text.trim()) {
      turns.push({ role, text: text.trim() })
    }
  }
  return turns
}

async function main() {
  if (!cfg.autoCapture) {
    log("skip", {
      stage: "init",
      reason: cfg.mode === "recall_only" ? "recall_only mode" : "autoCapture disabled",
    })
    approve()
    return
  }

  let input
  try {
    const chunks = []
    for await (const chunk of process.stdin) chunks.push(chunk)
    input = JSON.parse(Buffer.concat(chunks).toString())
  } catch {
    log("skip", { stage: "stdin_parse", reason: "invalid input" })
    approve()
    return
  }

  const transcriptPath = input.transcript_path
  const sessionId = input.session_id || "unknown"
  const turnId = input.turn_id || ""
  const dedupeKey = buildHookDedupeKey("Stop", input)
  log("start", { sessionId, turnId, transcriptPath })

  let claimed
  try {
    claimed = await claimHookInvocation(cfg.hookDedupeDir, dedupeKey, {
      eventName: "Stop",
      sessionId,
      turnId,
      transcriptPath,
    })
  } catch (err) {
    logError("dedupe_claim", err)
    approve()
    return
  }
  if (!claimed) {
    log("skip", { stage: "dedupe", reason: "already_processed", sessionId, turnId, dedupeKey })
    return
  }

  if (!transcriptPath) {
    log("skip", { stage: "input_check", reason: "no transcript_path" })
    approve()
    return
  }

  let transcriptContent
  try {
    transcriptContent = await readFile(transcriptPath, "utf-8")
  } catch (err) {
    logError("transcript_read", err)
    approve()
    return
  }

  if (!transcriptContent.trim()) {
    log("skip", { stage: "transcript_read", reason: "empty transcript" })
    approve()
    return
  }

  const messages = parseTranscript(transcriptContent)
  const allTurns = extractAllTurns(messages)
  if (allTurns.length === 0) {
    log("skip", { stage: "transcript_parse", reason: "no user/assistant turns found" })
    approve()
    return
  }

  const state = await loadState(sessionId)
  const newTurns = allTurns.slice(state.capturedTurnCount)
  const captureTurns = cfg.captureAssistantTurns
    ? newTurns
    : newTurns.filter((turn) => turn.role === "user")

  log("transcript_parse", {
    totalTurns: allTurns.length,
    previouslyCaptured: state.capturedTurnCount,
    newTurns: newTurns.length,
    captureTurns: captureTurns.length,
    assistantTurnsSkipped: newTurns.length - captureTurns.length,
  })

  if (newTurns.length === 0) {
    log("skip", { stage: "incremental_check", reason: "no new turns" })
    approve()
    return
  }

  if (captureTurns.length === 0) {
    await saveState(sessionId, { capturedTurnCount: allTurns.length })
    approve()
    return
  }

  const turnText = cfg.captureAssistantTurns
    ? captureTurns.map((turn) => `[${turn.role}]: ${turn.text}`).join("\n")
    : captureTurns.map((turn) => turn.text).join("\n\n")
  const decision = shouldCapture(turnText)
  log("should_capture", {
    capture: decision.capture,
    reason: decision.reason,
    textPreview: decision.text.slice(0, 100),
    factFamily: decision.fact?.familyKey || null,
  })

  if (!decision.capture) {
    await saveState(sessionId, { capturedTurnCount: allTurns.length })
    approve()
    return
  }

  const fact = decision.fact
    ? {
      ...decision.fact,
      sourceText: decision.text,
      sessionId,
    }
    : null

  if (fact) {
    await seedFactIndex(cfg, fact).catch((err) => logError("fact_seed", err))
  }

  const job = {
    sessionId,
    text: decision.text,
    fact,
    capturedTurnCount: allTurns.length,
  }
  await enqueueCaptureJob(cfg, job)
  await saveState(sessionId, { capturedTurnCount: allTurns.length })

  if (cfg.captureDispatch === "inline") {
    await drainCaptureQueue(cfg, log, logError)
  } else {
    kickCaptureWorker()
  }

  approve()
}

main().catch((err) => {
  logError("uncaught", err)
  approve()
})
