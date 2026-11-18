import { readFile } from "node:fs/promises"
import { join } from "node:path"
import { parseExtraHeaders } from "./shared/mcp-proxy-config.mjs"
import { resolveDataDir } from "./config.mjs"
import { isCaptureEnabled } from "./shared/capture-utils.mjs"
import { assessProbes, createReport, describeApiKey, probeOpenViking } from "./shared/doctor-core.mjs"

export const BUNDLED_SKILLS = ["openviking-memory", "openviking-skills", "ov-experience-memory", "openviking-rule-search"]

// Select fields rather than serializing effective config, skill bodies or transcripts.
export async function diagnoseOpenCode({ ctx, config, pluginRoot = "", offline = false, timeoutMs = 5000, signal }) {
  const deadline = AbortSignal.timeout(timeoutMs)
  const bounded = signal ? AbortSignal.any([signal, deadline]) : deadline
  bounded.throwIfAborted()
  const report = createReport()
  const extraHeaders = config.extraHeaders ?? parseExtraHeaders(process.env.OPENVIKING_EXTRA_HEADERS)
  const redact = (text) => sanitizeDiagnosticText(config, text)
  const inventory = async (domain) => {
    if (!domain?.list) return { state: "unsupported" }
    try {
      const { data } = await domain.list(undefined, { signal: bounded })
      if (!Array.isArray(data)) throw new Error("Unexpected OpenCode inventory response")
      return { state: "ok", data }
    } catch (error) {
      return { state: "error", error: redact(error.message ?? error) }
    }
  }
  const [plugins, servers, skills] = await Promise.all([inventory(ctx?.plugin), inventory(ctx?.mcp), inventory(ctx?.skill)])
  bounded.throwIfAborted()
  report.section("OpenCode installation")
  const instances = plugins.data?.filter(({ id, source }) =>
    id === "openviking" || /^@openviking\/opencode-plugin(?:@.+)?$/.test(source?.target ?? ""))
  const installation = { state: plugins.state, hostVersion: ctx?.app?.version ?? null,
    instances: instances?.map((p) => ({ id: p.id, source: p.source.type, version: p.source.version ?? null, state: p.state.status })),
    ...(plugins.error ? { error: plugins.error } : {}) }
  try {
    installation.packageVersion = JSON.parse(await readFile(join(pluginRoot, "package.json"), "utf8")).version
  } catch { installation.packageVersion = null }
  report.info(`OpenCode ${installation.hostVersion || "(not in host)"}; plugin ${installation.packageVersion || "(unknown version)"}`)
  if (instances) {
    installation.state = instances.length === 1 && instances[0].state.status === "active" ? "ok" : "error"
    report[installation.state === "ok" ? "ok" : "fail"](`OpenViking plugin instances: ${instances.length}`, "Multiple sources can indicate duplicate discovery; inspect the OpenCode plugin inventory")
  } else report.info(`Native plugin inventory: ${plugins.state}`)

  const server = servers.data?.find((s) => s.name === "openviking")
  const mcp = !config.mcp?.enabled ? { state: "disabled" }
    : servers.state !== "ok" ? { state: servers.state, ...(servers.error ? { error: servers.error } : {}) }
      : { state: server?.status?.status ?? "missing", ...(server?.status?.error ? { error: redact(server.status.error) } : {}) }
  const missing = BUNDLED_SKILLS.filter((name) => !skills.data?.some((s) => s.name === name))
  const skillState = (!config.mcp?.enabled || mcp.state === "disabled") ? { state: "disabled" }
    : skills.state !== "ok" ? { state: skills.state, ...(skills.error ? { error: skills.error } : {}) }
      : { state: missing.length ? "error" : "ok", missing, registered: BUNDLED_SKILLS.filter((s) => !missing.includes(s)) }
  report.section("Native registration")
  report[["connected", "disabled", "unsupported"].includes(mcp.state) ? "info" : "fail"](`MCP: ${mcp.state}`, mcp.error)
  report[skillState.state === "error" ? "fail" : "info"](`Skills: ${skillState.state}`, skillState.missing?.join(", ") || skillState.error)

  report.section("Connection")
  let connection = { state: offline ? "disabled" : "ok" }
  if (!offline) {
    const conn = { baseUrl: config.baseUrl, apiKey: config.apiKey, account: config.sendIdentityHeaders ? config.accountId : "", user: config.sendIdentityHeaders ? config.userId : "", peerId: config.effectivePeer?.peerId || config.peerId, userAgent: config.userAgent }
    const probes = await probeOpenViking({ ...conn, extraHeaders }, { timeoutMs, mcp: mcp.state !== "disabled", signal: bounded })
    const summary = assessProbes(report, probes, conn, describeApiKey(config.apiKey))
    connection = { state: summary.authOk ? "ok" : "error", reachable: summary.reachable, authOk: summary.authOk, version: summary.version,
      mcpHttpStatus: probes.mcp?.status ?? null }
  } else report.info("Network probes disabled (--offline)")

  report.section("Capture activity")
  const capture = await inspectCapture(config, pluginRoot, bounded)
  bounded.throwIfAborted()
  report[capture.state === "error" ? "fail" : "info"](`Capture: ${capture.state}`, capture.error)
  let endpoint
  try { const url = new URL(config.baseUrl); url.username = ""; url.password = ""; url.search = ""; url.hash = ""; endpoint = url.href } catch { endpoint = "(invalid URL)" }
  const result = { schemaVersion: 1, installation, mcp, skills: skillState, connection, capture,
    effective: { enabled: config.enabled !== false, baseUrl: endpoint, apiKeyConfigured: Boolean(config.apiKey), peerId: config.effectivePeer?.peerId || config.peerId || null, autoRecall: config.autoRecall, mcpEnabled: config.mcp?.enabled, autoCapture: isCaptureEnabled(config), captureAssistantTurns: config.captureAssistantTurns, skipSubagentSessions: config.skipSubagentSessions, captureRedact: config.captureRedact },
    exitCode: report.exitCode(), ...report.toJSON(), text: report.render() }
  // Sanitize values before JSON serialization so quotes and other special
  // characters in credentials cannot corrupt the result or evade redaction.
  function sanitize(value) {
    if (typeof value === "string") return redact(value)
    if (Array.isArray(value)) return value.map(sanitize)
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).filter(([, v]) => v !== undefined).map(([key, v]) => [key, sanitize(v)]))
    return value
  }
  return sanitize(result)
}

export function sanitizeDiagnosticText(config, text) {
  const extras = config.extraHeaders ?? parseExtraHeaders(process.env.OPENVIKING_EXTRA_HEADERS)
  const secrets = [config.apiKey, ...Object.values(extras)].filter((v) => typeof v === "string" && v)
  return secrets.reduce((value, secret) => value.replaceAll(secret, "[redacted]"), String(text))
    .replace(/https?:[^\s"'<>]+/g, (value) => {
      try {
        const url = new URL(value)
        url.username = ""
        url.password = ""
        url.search = ""
        url.hash = ""
        return url.href
      } catch { return "[URL]" }
    })
}

async function inspectCapture(config, pluginRoot, signal) {
  if (!isCaptureEnabled(config)) return { state: "disabled" }
  try {
    const data = JSON.parse(await readFile(join(resolveDataDir(pluginRoot, config), "openviking-session-state.json"), { encoding: "utf8", signal }))
    const sessions = Object.values(data.sessions || {})
    return { state: "ok", sessions: sessions.length,
      pendingMessages: sessions.reduce((n, s) => n + (s.messages || []).filter(([, m]) => !m.captured).length, 0),
      lastActivityAt: sessions.reduce((n, s) => Math.max(n, s.lastActivityAt ?? 0), 0),
      lastCommitAcceptedAt: sessions.reduce((n, s) => Math.max(n, s.lastCommitTime ?? 0), 0) }
  } catch (error) {
    return error.code === "ENOENT" ? { state: "no_activity" } : { state: "error", error: "Cannot read capture state: " + error.message }
  }
}
