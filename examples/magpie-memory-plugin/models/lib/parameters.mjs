import { AdapterError } from "./files.mjs";
import { at } from "./plan.mjs";

const limits = {
  max_concurrent: (v) => Number.isSafeInteger(v) && v > 0,
  max_tokens: (v) => v === null || (Number.isSafeInteger(v) && v > 0),
  temperature: (v) => v === null || (typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 2),
  thinking: (v) => typeof v === "boolean",
  timeout: (v) => typeof v === "number" && Number.isFinite(v) && v > 0,
};
export function validateParameters(value = {}, role = "vlm") {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new AdapterError("invalid_model_parameters");
  for (const [key, entry] of Object.entries(value)) {
    if (!Object.hasOwn(limits, key) || (role === "bot" && key === "max_concurrent") || !limits[key](entry)) throw new AdapterError("invalid_model_parameters");
  }
  return value;
}

const present = (value) => ({ exists: true, value });
const absent = () => ({ exists: false });
const positive = (v) => Number.isSafeInteger(v) && v > 0;

// Use original settings, not last poll's clamped result, so switching to a
// larger model can recover the user's requested budget and disconnect restores it.
export function modelParameters(document, records, desired, selection, config) {
  const adjustments = [], warnings = [];
  const original = (key) => records[key]?.before || at(document, key);
  function role(prefix, parameters, metadata) {
    const bot = prefix === "bot.agents";
    const spelling = (name) => bot && name === "max_tokens" ? "maxTokens" : name;
    let credentialBudget = false;
    const valueFor = (name) => {
      if (Object.hasOwn(parameters, name)) return present(parameters[name]);
      if (name === "max_tokens") {
        const primary = original(`${prefix}.credentials`).value?.[0];
        const budget = primary?.max_tokens ?? primary?.maxTokens;
        if (budget != null) { credentialBudget = true; return present(budget); }
      }
      const camel = original(`${prefix}.${spelling(name)}`);
      return camel.exists ? camel : original(`${prefix}.${name}`);
    };
    for (const [name, value] of Object.entries(parameters)) {
      desired[`${prefix}.${spelling(name)}`] = present(value);
      if (spelling(name) !== name) desired[`${prefix}.${name}`] = absent();
    }
    const request = valueFor("max_tokens");
    const cap = metadata?.output;
    if (request.exists && request.value !== null && !positive(request.value)) throw new AdapterError("max_tokens_requires_explicit_number");
    if (credentialBudget) {
      desired[`${prefix}.${spelling("max_tokens")}`] = request;
      if (bot) desired[`${prefix}.max_tokens`] = absent();
    }
    if (request.exists && positive(request.value) && positive(cap)) {
      const value = Math.min(request.value, cap);
      // Own this field even when currently below the limit, so a later model
      // switch can clamp it without losing the initial value or conflict checks.
      desired[`${prefix}.${spelling("max_tokens")}`] = present(value);
      if (bot) desired[`${prefix}.max_tokens`] = absent();
      if (value !== request.value) adjustments.push({ field: `${prefix}.${spelling("max_tokens")}`, requested: request.value, value, reason: "model_output_limit" });
    } else if (!positive(cap)) warnings.push(`${prefix}:output_limit_unknown`);
    const temperature = valueFor("temperature");
    if (temperature.exists && temperature.value !== null && metadata?.temperature === false) {
      if (Object.hasOwn(parameters, "temperature")) throw new AdapterError("model_temperature_unsupported");
      desired[`${prefix}.temperature`] = present(null);
      adjustments.push({ field: `${prefix}.temperature`, reason: "model_temperature_unsupported", value: null });
    }
    if (!positive(metadata?.context)) warnings.push(`${prefix}:context_window_unknown`);
    // The boolean thinking control isn't portable across model protocols.
    // Keep the user's choice and make the required capability check visible.
    if (valueFor("thinking").value === true) warnings.push(`${prefix}:verify_thinking_support`);
  }
  if (config.manageVlm) role("vlm", validateParameters(config.vlmParameters), selection.vlmMetadata);
  const explicitBot = document.bot?.agents || {};
  const botInherited = !explicitBot.model && !explicitBot.credentials?.length;
  if (config.botEnabled !== false && (config.botMode !== "preserve" || (config.manageVlm && botInherited))) {
    const metadata = config.botMode === "independent" ? selection.botMetadata : selection.vlmMetadata;
    role("bot.agents", validateParameters(config.botParameters, "bot"), metadata);
  }
  return { adjustments, warnings };
}
