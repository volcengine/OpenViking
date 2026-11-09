import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { createAPI, detectBot } from "./api.mjs";
import { AdapterError, readJSON, writeJSON, withLock } from "./files.mjs";

const route = "/api/v1/admin/configuration?source=file";

// The startup API does not hot-reload root VLM or Bot. Use the existing server
// lifecycle (including --with-bot cleanup/recreation), never a shell command.
export async function activate(config, { fetcher = fetch, retry = false, automatic = false, wait = delay, now = Date.now } = {}) {
  return withLock(config.directory, async () => {
    const api = createAPI(config, fetcher);
    const last = await readJSON(config.statusFile);
    if (!last?.revision || !["saved", "restored", "applied", "applying", "apply_failed"].includes(last.status)) throw new AdapterError("no_saved_configuration_to_apply");
    const file = await api(route);
    if (file.revision !== last.revision) throw new AdapterError("config_changed_before_apply");
    if (automatic) {
      const ownership = await readJSON(join(config.directory, "saved.json"));
      if (!last.savedRevision || last.savedRevision !== file.revision || ownership?.revision !== file.revision
        || ownership.serverUrl !== config.serverUrl || ownership.filePath !== file.file_path) {
        throw new AdapterError("configuration_not_saved_by_plugin");
      }
    }
    const attemptFile = join(config.directory, "activation.json");
    const attempt = await readJSON(attemptFile);
    if (attempt && attempt.serverUrl !== config.serverUrl) throw new AdapterError("recovery_target_changed");
    if (attempt?.revision === file.revision && attempt.status === "failed" && !retry) {
      const failed = { ...last, status: "apply_failed", restartRequired: true, code: attempt.code };
      await writeJSON(config.statusFile, failed);
      return failed;
    }
    const botAffected = last.botRuntime && last.botRuntime !== "not_managed";
    const managedBot = botAffected ? (await detectBot(config, fetcher)).enabled : false;
    const complete = async (current) => {
      const botVerified = managedBot && (await detectBot(config, fetcher)).healthy;
      const result = { ...last, status: "applied", restartRequired: !!botAffected && !botVerified,
        ovRuntime: last.ovRuntime === "not_managed" ? "not_managed" : "applied",
        botRuntime: !botAffected ? "not_managed" : botVerified ? "managed_restart_verified" : "standalone_restart_or_verify_required",
        instanceId: current.restart?.instance_id };
      await writeJSON(config.statusFile, result);
      await writeJSON(attemptFile, { serverUrl: config.serverUrl, revision: current.revision, instanceId: current.restart?.instance_id, status: "complete", botVerified });
      return result;
    };
    if (!file.restart_required && attempt?.revision === file.revision
      && ((attempt.status === "complete" && attempt.instanceId === file.restart?.instance_id)
        || (attempt.status === "requested" && file.restart?.instance_id && attempt.instanceId !== file.restart.instance_id))) return complete(file);
    // File equality alone cannot prove an independent Bot process reloaded.
    if (!file.restart_required) {
      const result = { ...last, status: "applied", restartRequired: !!botAffected,
        ovRuntime: last.ovRuntime === "not_managed" ? "not_managed" : "startup_file_matches",
        botRuntime: botAffected ? "restart_or_verify_required" : "not_managed" };
      await writeJSON(config.statusFile, result);
      return result;
    }
    if (last.ovRuntime === "not_managed" && !managedBot) throw new AdapterError("standalone_bot_restart_required");
    if (!file.restart?.supported || !file.restart.instance_id) throw new AdapterError("server_restart_unsupported");
    const sameRequest = attempt?.status === "requested" && attempt.revision === file.revision;
    const previousInstance = sameRequest ? attempt.instanceId : file.restart.instance_id;
    const deadline = now() + (config.restartTimeoutMs || 120000);
    await writeJSON(config.statusFile, { ...last, status: "applying" });
    if (!sameRequest) await writeJSON(attemptFile, { serverUrl: config.serverUrl, revision: file.revision, instanceId: previousInstance, status: "requested" });
    try {
      if (!sameRequest && !file.restart.restarting) {
        try { await api("/api/v1/admin/restart", { revision: file.revision }, "POST"); }
        catch (error) {
          // A lost accepted response is ambiguous: observe the instance rather
          // than issuing a second restart. Definitive HTTP failures stop here.
          if (error.code !== "server_unreachable") throw error;
        }
      }
      while (now() < deadline) {
        let current;
        try { current = await api(route); } catch { await wait(500); continue; }
        if (current.restart?.rolled_back) throw new AdapterError("server_restart_rolled_back");
        if (current.revision !== file.revision) throw new AdapterError("config_changed_during_restart");
        if (current.restart?.instance_id !== previousInstance && current.restart?.instance_id && !current.restart.restarting && !current.restart_required) {
          if (managedBot && !(await detectBot(config, fetcher)).healthy) { await wait(500); continue; }
          return await complete(current);
        }
        await wait(500);
      }
      throw new AdapterError("server_restart_timeout");
    } catch (error) {
      const code = error instanceof AdapterError ? error.code : "server_restart_failed";
      await writeJSON(attemptFile, { serverUrl: config.serverUrl, revision: file.revision, instanceId: previousInstance, status: "failed", code });
      const result = { ...last, status: "apply_failed", restartRequired: true, code };
      await writeJSON(config.statusFile, result);
      return result;
    }
  });
}
