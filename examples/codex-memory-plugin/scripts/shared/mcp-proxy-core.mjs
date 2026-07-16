// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * Shared stdio -> streamable-HTTP MCP proxy core for OpenViking memory plugins.
 *
 * Harness-specific entrypoints provide credential/config loading; this module
 * owns only transport, session retry, SSE parsing, and protocol-clean stdio.
 */

import { statSync } from "node:fs";
import { createInterface } from "node:readline";

const DEFAULT_PROTOCOL_VERSION = "2025-06-18";
const DELETE_TIMEOUT_MS = 2000;
const MAX_CONCURRENT_REQUESTS = 16;

class HttpStatusError extends Error {
  constructor(status, statusText, bodyText, messages = []) {
    super(`HTTP ${status}${statusText ? ` ${statusText}` : ""}`);
    this.name = "HttpStatusError";
    this.status = status;
    this.statusText = statusText;
    this.bodyText = bodyText;
    this.messages = messages;
  }
}

function snapshotPaths(paths) {
  const out = new Map();
  for (const path of paths || []) {
    try {
      const st = statSync(path);
      out.set(path, `${st.mtimeMs}:${st.size}`);
    } catch {
      out.set(path, "missing");
    }
  }
  return out;
}

function snapshotsDiffer(a, b) {
  if (a.size !== b.size) return true;
  for (const [key, value] of a.entries()) {
    if (b.get(key) !== value) return true;
  }
  return false;
}

function createSemaphore(limit) {
  let active = 0;
  const queue = [];
  return async function acquire() {
    if (active >= limit) {
      await new Promise((resolve) => queue.push(resolve));
    }
    active += 1;
    let released = false;
    return () => {
      if (released) return;
      released = true;
      active -= 1;
      const next = queue.shift();
      if (next) next();
    };
  };
}

function isRequest(message) {
  return Object.prototype.hasOwnProperty.call(message || {}, "id");
}

function messageId(message) {
  return isRequest(message) ? message.id : undefined;
}

function errorResponse(id, code, message, data) {
  const error = { code, message };
  if (data !== undefined) error.data = data;
  return { jsonrpc: "2.0", id: id ?? null, error };
}

function parseMaybeJson(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

function parseSseMessages(text) {
  const messages = [];
  let dataLines = [];

  function flush() {
    if (dataLines.length === 0) return;
    const data = dataLines.join("\n").trim();
    dataLines = [];
    if (!data || data === "[DONE]") return;
    messages.push(JSON.parse(data));
  }

  for (const rawLine of String(text || "").split(/\r?\n/)) {
    if (rawLine === "") {
      flush();
      continue;
    }
    if (rawLine.startsWith(":")) continue;
    const colon = rawLine.indexOf(":");
    const field = colon === -1 ? rawLine : rawLine.slice(0, colon);
    let value = colon === -1 ? "" : rawLine.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") dataLines.push(value);
  }
  flush();
  return messages;
}

function parseHttpBody(contentType, text) {
  const ctype = String(contentType || "").toLowerCase();
  if (!String(text || "").trim()) return [];
  if (ctype.includes("text/event-stream")) return parseSseMessages(text);
  const json = parseMaybeJson(text);
  return json == null ? [] : [json];
}

function serializeBodyForError(bodyText) {
  const parsed = parseMaybeJson(bodyText);
  if (parsed?.error?.message) return parsed.error.message;
  if (parsed?.detail) return typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
  const compact = String(bodyText || "").replace(/\s+/g, " ").trim();
  return compact.slice(0, 500);
}

function cloneMessage(message) {
  return JSON.parse(JSON.stringify(message));
}

export function createOpenVikingMcpProxy({
  stdin = process.stdin,
  stdout = process.stdout,
  readConfig,
  loggerFactory,
  fetchImpl = globalThis.fetch,
  localToolProvider = null,
} = {}) {
  if (typeof fetchImpl !== "function") {
    throw new Error("global fetch is required; use Node.js 18 or newer");
  }
  if (typeof readConfig !== "function") {
    throw new Error("readConfig function is required");
  }
  if (typeof loggerFactory !== "function") {
    throw new Error("loggerFactory function is required");
  }

  let proxyConfig = readConfig();
  let logger = loggerFactory("mcp-proxy", proxyConfig);
  let watchedSnapshot = snapshotPaths(proxyConfig.watchedPaths);
  let sessionId = "";
  let initializeRequest = null;
  let initializedNotification = null;
  let protocolVersion = DEFAULT_PROTOCOL_VERSION;
  let initializeInFlight = null;
  let reinitializeInFlight = null;
  let stdoutChain = Promise.resolve();
  let shuttingDown = false;
  let reinitCounter = 0;
  const acquire = createSemaphore(MAX_CONCURRENT_REQUESTS);

  function log(stage, data) {
    try {
      logger.log(stage, data);
    } catch { /* debug logging must never affect protocol IO */ }
  }

  function logError(stage, err) {
    try {
      logger.logError(stage, err);
    } catch { /* debug logging must never affect protocol IO */ }
  }

  function localTools() {
    if (!localToolProvider || typeof localToolProvider.listTools !== "function") return [];
    const tools = localToolProvider.listTools();
    return Array.isArray(tools) ? tools : [];
  }

  function appendLocalTools(message, outbound) {
    if (message.method !== "tools/list" || !Array.isArray(outbound?.result?.tools)) {
      return outbound;
    }
    const additions = localTools().filter((tool) => tool?.name);
    if (additions.length === 0) return outbound;
    const localNames = new Set(additions.map((tool) => tool.name));
    const upstreamTools = outbound.result.tools.filter((tool) => !localNames.has(tool?.name));
    return {
      ...outbound,
      result: {
        ...outbound.result,
        tools: [...upstreamTools, ...additions],
      },
    };
  }

  async function callLocalTool(message) {
    if (
      message.method !== "tools/call"
      || !localToolProvider
      || typeof localToolProvider.callTool !== "function"
    ) {
      return null;
    }
    const name = message.params?.name;
    if (!localTools().some((tool) => tool?.name === name)) return null;
    reloadIfCredentialFilesChanged("local_tool_call");
    return localToolProvider.callTool(message.params, { config: proxyConfig });
  }

  function reloadConfig(reason) {
    proxyConfig = readConfig();
    logger = loggerFactory("mcp-proxy", proxyConfig);
    watchedSnapshot = snapshotPaths(proxyConfig.watchedPaths);
    log("credentials_reloaded", {
      reason,
      credentialSource: proxyConfig.credentialSource,
      credentialPath: proxyConfig.credentialPath,
      mcpUrl: proxyConfig.mcpUrl,
      hasApiKey: Boolean(proxyConfig.apiKey),
      hasIdentity: Boolean(proxyConfig.account || proxyConfig.user),
      hasPeer: Boolean(proxyConfig.peerId),
    });
  }

  function reloadIfCredentialFilesChanged(reason) {
    const next = snapshotPaths(proxyConfig.watchedPaths);
    if (!snapshotsDiffer(watchedSnapshot, next)) return false;
    reloadConfig(reason);
    return true;
  }

  function headersForRequest(includeSession = true) {
    const headers = {
      "Content-Type": "application/json",
      "Accept": "application/json, text/event-stream",
      // Always the proxy's current version (default, then server-negotiated) —
      // never the client's un-negotiated ask, which strict upstreams reject
      // with HTTP 400 before initialize negotiation can run.
      "MCP-Protocol-Version": protocolVersion,
    };
    if (includeSession && sessionId) headers["Mcp-Session-Id"] = sessionId;
    if (proxyConfig.apiKey) headers.Authorization = `Bearer ${proxyConfig.apiKey}`;
    if (proxyConfig.account) headers["X-OpenViking-Account"] = proxyConfig.account;
    if (proxyConfig.user) headers["X-OpenViking-User"] = proxyConfig.user;
    if (proxyConfig.peerId) headers["X-OpenViking-Actor-Peer"] = proxyConfig.peerId;
    return headers;
  }

  function writeMessage(obj) {
    const line = `${JSON.stringify(obj)}\n`;
    stdoutChain = stdoutChain
      .then(() => new Promise((resolve) => stdout.write(line, resolve)))
      .catch(() => {});
    return stdoutChain;
  }

  function mapError(message, err) {
    const id = messageId(message);
    if (err instanceof HttpStatusError) {
      if (err.status === 401 || err.status === 403) {
        return errorResponse(
          id,
          -32001,
          `OpenViking MCP authentication failed (HTTP ${err.status}). Check ~/.openviking/ovcli.conf or OPENVIKING_API_KEY, and verify the configured account/user for trusted mode.`,
          {
            status: err.status,
            credentialSource: proxyConfig.credentialSource,
            credentialPath: proxyConfig.credentialPath || undefined,
            serverMessage: serializeBodyForError(err.bodyText) || undefined,
          },
        );
      }
      return errorResponse(
        id,
        -32002,
        `OpenViking MCP upstream returned HTTP ${err.status}.`,
        {
          status: err.status,
          serverMessage: serializeBodyForError(err.bodyText) || undefined,
        },
      );
    }
    const msg = err instanceof Error ? err.message : String(err);
    return errorResponse(
      id,
      -32001,
      `OpenViking MCP request failed. Check the configured URL (${proxyConfig.mcpUrl}) and that 'ov serve' is running.`,
      { cause: msg },
    );
  }

  async function postToMcp(message, { includeSession = true, timeoutMs = proxyConfig.timeoutMs } = {}) {
    const release = await acquire();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetchImpl(proxyConfig.mcpUrl, {
        method: "POST",
        headers: headersForRequest(includeSession),
        body: JSON.stringify(message),
        signal: controller.signal,
      });
      const text = await res.text();
      const messages = parseHttpBody(res.headers.get("content-type"), text);
      const nextSessionId = res.headers.get("mcp-session-id");
      if (nextSessionId) sessionId = nextSessionId;
      if (message?.method === "initialize" && res.ok) {
        const negotiated = messages.find((m) => typeof m?.result?.protocolVersion === "string")?.result.protocolVersion;
        if (negotiated) protocolVersion = negotiated;
      }
      if (!res.ok) {
        throw new HttpStatusError(res.status, res.statusText, text, messages);
      }
      return { status: res.status, messages };
    } finally {
      clearTimeout(timer);
      release();
    }
  }

  async function deleteSession() {
    if (!sessionId || shuttingDown) return;
    shuttingDown = true;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), DELETE_TIMEOUT_MS);
    try {
      await fetchImpl(proxyConfig.mcpUrl, {
        method: "DELETE",
        headers: headersForRequest(true),
        signal: controller.signal,
      });
    } catch (err) {
      logError("delete_session_failed", err);
    } finally {
      clearTimeout(timer);
      sessionId = "";
    }
  }

  async function reinitialize(failedSessionId) {
    if (!initializeRequest) {
      throw new Error("MCP session expired before initialize parameters were cached");
    }
    if (reinitializeInFlight) return reinitializeInFlight;
    reinitializeInFlight = (async () => {
      if (failedSessionId && sessionId && sessionId !== failedSessionId) return;
      const reinit = cloneMessage(initializeRequest);
      reinit.id = `openviking-proxy-reinit-${Date.now()}-${++reinitCounter}`;
      sessionId = "";
      const result = await postToMcp(reinit, { includeSession: false });
      if (initializedNotification) {
        await postToMcp(cloneMessage(initializedNotification), { includeSession: true });
      }
      log("reinitialized", { mcpUrl: proxyConfig.mcpUrl, status: result.status, sessionId: Boolean(sessionId) });
    })().finally(() => {
      reinitializeInFlight = null;
    });
    return reinitializeInFlight;
  }

  async function sendWithRetry(message, { expectsResponse }) {
    if (message.method !== "initialize" && initializeInFlight) {
      await initializeInFlight.catch(() => {});
    }

    const failedSessionId = sessionId;
    try {
      return await postToMcp(message, { includeSession: message.method !== "initialize" });
    } catch (err) {
      if (err instanceof HttpStatusError && (err.status === 401 || err.status === 403)) {
        if (reloadIfCredentialFilesChanged("auth_failure")) {
          sessionId = "";
          if (message.method !== "initialize" && initializeRequest) {
            await reinitialize(failedSessionId);
          }
          return await postToMcp(message, { includeSession: message.method !== "initialize" });
        }
      }
      if (
        err instanceof HttpStatusError
        && (err.status === 400 || err.status === 404)
        && message.method !== "initialize"
        && initializeRequest
      ) {
        await reinitialize(failedSessionId);
        return await postToMcp(message, { includeSession: true });
      }
      if (!expectsResponse) {
        logError("notification_failed", err);
      }
      throw err;
    }
  }

  async function handleMessage(message) {
    if (!message || typeof message !== "object" || message.jsonrpc !== "2.0") {
      await writeMessage(errorResponse(null, -32600, "Invalid JSON-RPC message"));
      return;
    }

    const expectsResponse = isRequest(message);
    if (message.method === "initialize") {
      initializeRequest = cloneMessage(message);
      protocolVersion = DEFAULT_PROTOCOL_VERSION;
      sessionId = "";
    }
    if (message.method === "notifications/initialized") {
      initializedNotification = cloneMessage(message);
    }

    try {
      const localResult = await callLocalTool(message);
      if (localResult !== null) {
        if (expectsResponse) {
          await writeMessage({ jsonrpc: "2.0", id: message.id, result: localResult });
        }
        return;
      }
      const send = sendWithRetry(message, { expectsResponse });
      if (message.method === "initialize") {
        initializeInFlight = send
          .catch(() => {})
          .finally(() => {
            initializeInFlight = null;
          });
      }
      const result = await send;
      if (!expectsResponse) return;
      if (result.messages.length === 0) {
        await writeMessage(errorResponse(message.id, -32003, "OpenViking MCP upstream returned an empty response"));
        return;
      }
      for (const outbound of result.messages) {
        await writeMessage(appendLocalTools(message, outbound));
      }
    } catch (err) {
      if (expectsResponse) {
        await writeMessage(mapError(message, err));
      } else {
        logError("notification_unhandled", err);
      }
    }
  }

  async function handleLine(line) {
    const trimmed = line.trim();
    if (!trimmed) return;
    let message;
    try {
      message = JSON.parse(trimmed);
    } catch {
      await writeMessage(errorResponse(null, -32700, "Parse error"));
      return;
    }
    await handleMessage(message);
  }

  async function closeAndExit(code = 0) {
    await deleteSession();
    await stdoutChain;
    process.exit(code);
  }

  async function closeSession() {
    await deleteSession();
    await stdoutChain;
  }

  function start() {
    log("start", {
      mcpUrl: proxyConfig.mcpUrl,
      credentialSource: proxyConfig.credentialSource,
      credentialPath: proxyConfig.credentialPath,
      hasApiKey: Boolean(proxyConfig.apiKey),
    });
    const rl = createInterface({ input: stdin, crlfDelay: Infinity, terminal: false });
    rl.on("line", (line) => {
      void handleLine(line);
    });
    rl.on("close", () => {
      void closeAndExit(0);
    });
    process.on("SIGINT", () => {
      void closeAndExit(130);
    });
    process.on("SIGTERM", () => {
      void closeAndExit(143);
    });
    return { close: () => closeAndExit(0) };
  }

  return { start, handleMessage, parseSseMessages, closeSession };
}
