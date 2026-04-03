const LEGACY_API_KEY_STORAGE_KEY = "ov_console_api_key";
const CONNECTION_SETTINGS_STORAGE_KEY = "ov_console_connection_settings_v1";

function normalizeValue(value) {
  if (value === undefined || value === null) {
    return "";
  }
  return String(value).trim();
}

export function normalizeConsoleSettings(settings = {}) {
  return {
    apiKey: normalizeValue(settings.apiKey),
    accountId: normalizeValue(settings.accountId),
    userId: normalizeValue(settings.userId),
    agentId: normalizeValue(settings.agentId),
  };
}

export function serializeConsoleSettings(settings = {}) {
  const normalized = normalizeConsoleSettings(settings);
  const payload = {};

  for (const [key, value] of Object.entries(normalized)) {
    if (value) {
      payload[key] = value;
    }
  }

  return JSON.stringify(payload);
}

export function parseConsoleSettings(rawValue) {
  if (!rawValue) {
    return normalizeConsoleSettings();
  }

  try {
    const parsed = JSON.parse(rawValue);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return normalizeConsoleSettings();
    }
    return normalizeConsoleSettings(parsed);
  } catch (_error) {
    return normalizeConsoleSettings();
  }
}

export function loadConsoleSettings(storage) {
  const stored = storage.getItem(CONNECTION_SETTINGS_STORAGE_KEY);
  if (stored) {
    return parseConsoleSettings(stored);
  }

  return normalizeConsoleSettings({
    apiKey: storage.getItem(LEGACY_API_KEY_STORAGE_KEY) || "",
  });
}

export function resolveRuntimeConsoleSettings(storage, sessionApiKey = "") {
  const saved = loadConsoleSettings(storage);
  const runtimeApiKey = normalizeValue(sessionApiKey) || saved.apiKey;
  return normalizeConsoleSettings({
    ...saved,
    apiKey: runtimeApiKey,
  });
}

export function saveConsoleSettings(storage, settings) {
  const normalized = normalizeConsoleSettings(settings);
  storage.setItem(CONNECTION_SETTINGS_STORAGE_KEY, serializeConsoleSettings(normalized));

  if (normalized.apiKey) {
    storage.setItem(LEGACY_API_KEY_STORAGE_KEY, normalized.apiKey);
  } else {
    storage.removeItem(LEGACY_API_KEY_STORAGE_KEY);
  }

  return normalized;
}

export function clearConsoleSettings(storage) {
  storage.removeItem(CONNECTION_SETTINGS_STORAGE_KEY);
  storage.removeItem(LEGACY_API_KEY_STORAGE_KEY);
}

export function buildRequestHeaders(baseHeaders = {}, settings = {}) {
  const normalized = normalizeConsoleSettings(settings);
  const headers = {
    ...baseHeaders,
  };

  if (normalized.apiKey) {
    headers["X-API-Key"] = normalized.apiKey;
  }
  if (normalized.accountId) {
    headers["X-OpenViking-Account"] = normalized.accountId;
  }
  if (normalized.userId) {
    headers["X-OpenViking-User"] = normalized.userId;
  }
  if (normalized.agentId) {
    headers["X-OpenViking-Agent"] = normalized.agentId;
  }

  return headers;
}
