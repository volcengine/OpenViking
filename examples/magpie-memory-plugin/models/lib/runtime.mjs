import fs from "node:fs/promises";
import { pluginEnabled } from "../../config.mjs";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { settings } from "./config.mjs";
import { readJSON, writeJSON, withLock, AdapterError } from "./files.mjs";
import { createAPI, validateSelection, detectBot } from "./api.mjs";
import { acknowledge, desiredSettings, equal, plan } from "./plan.mjs";
import { modelParameters } from "./parameters.mjs";
import { activate } from "./activation.mjs";

const route = "/api/v1/admin/configuration?source=file";
const digest = (value) => createHash("sha256").update(JSON.stringify(value)).digest("hex");
function ownsConfiguration(ownership, config, file, document) {
  return ownership?.serverUrl === config.serverUrl && ownership.filePath === file.file_path
    && ownership.contentHash === digest(document) && (!ownership.revision || ownership.revision === file.revision);
}
export async function readSelection(config) {
  const value = await readJSON(config.selectionFile);
  if (value) await fs.chmod(config.selectionFile, 0o600);
  return value;
}
export async function synchronize(config, { restore = false, resume = false, fetcher = fetch } = {}) {
  return withLock(config.directory, async () => {
    const raw = await readSelection(config);
    const fingerprint = digest(raw);
    const pauseFile = join(config.directory, "paused.json");
    if (restore) await writeJSON(pauseFile, { fingerprint, restorePending: true });
    if (resume) await fs.rm(pauseFile, { force: true });
    const paused = await readJSON(pauseFile);
    let state = await readJSON(config.stateFile);
    const disconnected = !raw || (!raw.model && !raw.gateway?.key && !raw.gateway?.baseURL);
    // An incomplete write is not a disconnect: only a missing file or Magpie's
    // restored empty object is. Preserve recovery data until selection settles.
    if (!disconnected && (!raw.model || !raw.gateway?.key || !raw.gateway?.baseURL)) throw new AdapterError("incomplete_selection");
    const restoring = restore || disconnected || (paused?.fingerprint === fingerprint && paused.restorePending === true && !!state);
    if (!restoring && paused?.fingerprint === fingerprint) return { status: "paused" };
    if (!restoring && paused) await fs.rm(pauseFile, { force: true });
    if (state && (state.version !== 1 || state.serverUrl !== config.serverUrl)) throw new AdapterError("recovery_target_changed");
    if (!state && restoring) {
      const last = await readJSON(config.statusFile);
      // Restoration removed the ownership journal, not the running process's
      // old model. Do not erase a pending restart on the next disconnected poll.
      return publish(config, ["restored", "applied", "apply_failed"].includes(last?.status) ? last : { status: "disconnected", restartRequired: false });
    }
    const savedFile = join(config.directory, "saved.json");
    const api = createAPI(config, fetcher);
    const current = await api(route);
    if (typeof current.content !== "string" || typeof current.revision !== "string" || typeof current.file_path !== "string") throw new AdapterError("unsupported_config_api");
    if (!current.writable) throw new AdapterError("config_readonly");
    if (state && state.filePath !== current.file_path) throw new AdapterError("recovery_target_changed");
    const blocked = current.overrides?.cluster || [];
    if (!restoring && config.manageVlm && blocked.includes("vlm")) throw new AdapterError("runtime_vlm_override");
    let document;
    try { document = JSON.parse(current.content.replace(/^\uFEFF/, "")); }
    catch { throw new AdapterError("invalid_server_config"); }
    if (!document || Array.isArray(document) || typeof document !== "object") throw new AdapterError("invalid_server_config");
    const previousOwnership = await readJSON(savedFile);
    // A new whole-file save must not turn someone else's pending changes into
    // our own revision. Preserve the journal and wait for explicit activation.
    if (current.restart_required && !ownsConfiguration(previousOwnership, config, current, document)) {
      return publish(config, { status: "external_restart_required", revision: current.revision, restartRequired: true });
    }
    let selection = null;
    if (!restoring) selection = await validateSelection(raw, config, fetcher);
    const bot = await detectBot(config, fetcher);
    if (!restoring && config.botMode !== "preserve" && !bot.enabled) throw new AdapterError("bot_not_enabled_or_detected");
    const effectiveConfig = { ...config, botEnabled: bot.enabled };
    const desired = desiredSettings(selection, effectiveConfig);
    const parameters = selection ? modelParameters(document, state?.records || {}, desired, selection, effectiveConfig) : { adjustments: [], warnings: [] };
    const oldRecords = state?.records || {};
    const vlmManaged = Object.keys({ ...oldRecords, ...desired }).some((key) => key.startsWith("vlm."));
    const botManaged = Object.keys({ ...oldRecords, ...desired }).some((key) => key.startsWith("bot.agents."));
    const botInherits = !document.bot?.agents?.model && !document.bot?.agents?.credentials?.length;
    const botAffected = bot.enabled && (botManaged || (vlmManaged && botInherits));
    const result = plan(document, oldRecords, desired);
    if (result.conflicts.length) return publish(config, { status: "conflict", conflicts: result.conflicts });
    // Return exactly the original absence of structural containers. Do this
    // only when empty, so settings added by the user are retained.
    const structure = state?.structure || { vlm: Object.hasOwn(document, "vlm"), bot: Object.hasOwn(document, "bot"), agents: Object.hasOwn(document.bot || {}, "agents") };
    if (!structure.agents && result.document.bot?.agents && !Object.keys(result.document.bot.agents).length) delete result.document.bot.agents;
    if (!structure.bot && result.document.bot && !Object.keys(result.document.bot).length) delete result.document.bot;
    if (!structure.vlm && result.document.vlm && !Object.keys(result.document.vlm).length) delete result.document.vlm;
    const changed = !equal(result.document, document);
    const content = JSON.stringify(result.document, null, 2) + "\n";
    if (changed) {
      await api(`${route}&dry_run=true`, { content });
      // Persist original values before the potentially ambiguous network write.
      state = { version: 1, serverUrl: config.serverUrl, filePath: current.file_path, structure, records: result.records };
      await writeJSON(config.stateFile, state);
      // Keep the full intended document fingerprint before the ambiguous write,
      // so a lost response can be recovered without claiming an external edit.
      const ownership = { serverUrl: config.serverUrl, filePath: current.file_path, contentHash: digest(result.document) };
      await writeJSON(savedFile, ownership);
      const saved = await api(route, { content, revision: current.revision });
      current.revision = saved.revision;
      await writeJSON(savedFile, { ...ownership, revision: current.revision });
    }
    const records = acknowledge(result.records);
    if (Object.keys(records).length) {
      await writeJSON(config.stateFile, { version: 1, serverUrl: config.serverUrl, filePath: current.file_path, structure, records });
    } else await fs.rm(config.stateFile, { force: true });
    if (restoring && paused) await writeJSON(pauseFile, { fingerprint: paused.fingerprint, restorePending: false });
    const ownership = await readJSON(savedFile);
    const ownsRevision = ownsConfiguration(ownership, config, current, result.document);
    const savedRevision = ownsRevision ? current.revision : undefined;
    if (ownsRevision && !ownership.revision) await writeJSON(savedFile, { ...ownership, revision: savedRevision });
    const activation = await readJSON(join(config.directory, "activation.json"));
    const verified = !changed && !current.restart_required && activation?.status === "complete"
      && activation.serverUrl === config.serverUrl && activation.revision === current.revision
      && activation.instanceId === current.restart?.instance_id;
    return publish(config, {
      status: verified ? "applied" : !changed && current.restart_required && !ownsRevision ? "external_restart_required" : restoring ? "restored" : "saved",
      revision: current.revision,
      ...(savedRevision ? { savedRevision } : {}),
      restartRequired: changed || current.restart_required === true || (verified && botAffected && !activation.botVerified),
      ovRuntime: vlmManaged ? (verified ? "applied" : changed || current.restart_required ? "restart_required" : "startup_file_matches") : "not_managed",
      botRuntime: botAffected ? (verified && activation.botVerified ? "managed_restart_verified" : "restart_or_verify_required") : "not_managed",
      ...(restoring ? {} : { botMode: config.botMode }),
      accountOverrides: "not_managed",
      capabilityCheck: "catalog_only",
      botDetection: bot.state,
      ...parameters,
    });
  });
}
async function publish(config, result) {
  await writeJSON(config.statusFile, result);
  return result;
}
export async function status(config) {
  return {
    last: await readJSON(config.statusFile), recoveryPending: !!await readJSON(config.stateFile),
    paused: !!await readJSON(join(config.directory, "paused.json")),
  };
}
export function startAdapter(input, options = {}) {
  let timer, stopped = false, lastSelection, previousMessage;
  const log = (code) => {
    if (code === previousMessage) return;
    previousMessage = code;
    try { input?.client?.app?.log?.({ body: { service: "openviking-models", level: "info", message: `OpenViking models: ${code}` } })?.catch?.(() => {}); } catch {}
  };
  const tick = async () => {
    let delay = 2000;
    try {
      if (!pluginEnabled()) { log("disabled"); return; }
      const config = await settings(options); delay = config.pollMs;
      const selection = digest(await readSelection(config));
      // Magpie writes model, URL and key separately. Let one quiet poll elapse.
      if (selection !== lastSelection) lastSelection = selection;
      else {
        let result = await synchronize(config);
        if (pluginEnabled() && config.restartOnChange && result.restartRequired && result.savedRevision && result.savedRevision === result.revision && ["saved", "restored"].includes(result.status)) result = await activate(config, { automatic: true });
        log([result.status, result.code, result.ovRuntime && `OV:${result.ovRuntime}`, result.botRuntime && `Bot:${result.botRuntime}`,
          ...(result.adjustments || []).map((entry) => `${entry.field}=${entry.value}`),
          ...(result.warnings || [])].filter(Boolean).join(" · "));
      }
    } catch (error) {
      const code = error instanceof AdapterError ? error.code : "adapter_unavailable";
      log(code);
      try {
        const config = await settings(options);
        await withLock(config.directory, () => publish(config, { status: "error", code }));
      } catch {}
    } finally {
      if (!stopped) { timer = setTimeout(tick, delay); timer.unref?.(); }
    }
  };
  timer = setTimeout(tick, 0); timer.unref?.();
  return () => { stopped = true; clearTimeout(timer); };
}
