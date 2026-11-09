import { AdapterError } from "./files.mjs";

const absent = () => ({ exists: false });
const present = (value) => ({ exists: true, value });
export const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
export function at(document, key) {
  let value = document;
  for (const part of key.split(".")) {
    if (!value || typeof value !== "object" || !Object.hasOwn(value, part)) return absent();
    value = value[part];
  }
  return present(structuredClone(value));
}
function set(document, key, entry) {
  const parts = key.split(".");
  let target = document;
  for (const part of parts.slice(0, -1)) {
    if (!Object.hasOwn(target, part)) {
      if (!entry.exists) return;
      target[part] = {};
    }
    if (!target[part] || typeof target[part] !== "object" || Array.isArray(target[part])) throw new AdapterError("invalid_config_structure");
    target = target[part];
  }
  if (entry.exists) target[parts.at(-1)] = structuredClone(entry.value);
  else delete target[parts.at(-1)];
}
const identity = ["model", "provider", "backend", "api_key", "api_base", "api_version", "forward_api_key", "credentials", "providers", "default_provider", "backup", "extra_headers", "extra_request_body", "reasoning_effort", "keepalive_expiry"];
const botIdentity = ["model", "provider", "apiKey", "apiBase", "extraHeaders", "credentials", "api_key", "api_base", "extra_headers"];
function bind(target, prefix, fields, model, gateway) {
  for (const key of fields) target[`${prefix}.${key}`] = absent();
  target[`${prefix}.model`] = present(model);
  target[`${prefix}.provider`] = present("openai");
  const bot = prefix === "bot.agents";
  target[`${prefix}.${bot ? "apiKey" : "api_key"}`] = present(gateway.key);
  target[`${prefix}.${bot ? "apiBase" : "api_base"}`] = present(gateway.baseURL);
}
export function desiredSettings(selection, config) {
  const target = {};
  if (!selection) return target;
  if (config.manageVlm) bind(target, "vlm", identity, selection.model, selection.gateway);
  if (config.botEnabled !== false && config.botMode === "follow") {
    for (const key of botIdentity) target[`bot.agents.${key}`] = absent();
  } else if (config.botEnabled !== false && config.botMode === "independent") {
    bind(target, "bot.agents", botIdentity, config.botModel || selection.model, selection.gateway);
  }
  // Keep model-specific context budgets coherent. Unknown metadata does not
  // invent a context size; the status tells users to verify it themselves.
  if (config.botEnabled !== false && config.botMode !== "preserve") {
    const context = selection.botMetadata ? selection.botMetadata.context : selection.metadata?.context;
    if (Number.isSafeInteger(context) && context > 0) {
      target["bot.agents.contextWindowTokens"] = present(context);
      target["bot.agents.context_window_tokens"] = absent();
    }
  }
  return target;
}

export function plan(document, records, desired) {
  const output = structuredClone(document), next = {}, conflicts = [];
  const keys = new Set([...Object.keys(records), ...Object.keys(desired)]);
  for (const key of keys) {
    if (!/^(vlm\.[a-z_]+|bot\.agents\.[a-zA-Z_]+)$/.test(key)) throw new AdapterError("invalid_journal");
    const current = at(document, key), old = records[key];
    // before is accepted only for an interrupted, not-yet-acknowledged write.
    if (old && !equal(current, old.after) && !(old.pending && equal(current, old.previous))) {
      conflicts.push(key); continue;
    }
    const wanted = desired[key] || old.before;
    set(output, key, wanted);
    if (Object.hasOwn(desired, key)) {
      next[key] = { before: old?.before || current, after: wanted, previous: current, pending: true };
    } else if (!equal(current, wanted)) {
      // Retain released keys in a pending journal until the server acknowledges
      // restoration, so a lost response can be recovered without guessing.
      next[key] = { before: old.before, after: wanted, previous: current, pending: true, releasing: true };
    }
  }
  if (conflicts.length) return { conflicts };
  return { conflicts, document: output, records: next, changed: !equal(output, document) };
}

export function acknowledge(records) {
  return Object.fromEntries(Object.entries(records).filter(([, value]) => !value.releasing)
    .map(([key, { previous, pending, ...record }]) => [key, record]));
}
