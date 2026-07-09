const DEFAULT_URI_KEYS = [
  "filePath",
  "file_path",
  "filepath",
  "path",
  "uri",
  "target_uri",
  "targetUri",
  "pattern",
];

export function normalizeToolName(value) {
  return String(value || "").trim().toLowerCase();
}

export function findVikingUri(args = {}, keys = DEFAULT_URI_KEYS) {
  if (!args || typeof args !== "object") return null;
  for (const key of keys) {
    const uri = findVikingUriInValue(args[key]);
    if (uri) return uri;
  }
  return findVikingUriInValue(args);
}

export function findVikingUriInValue(value) {
  if (typeof value === "string") {
    const match = value.match(/\bviking:\/\/[^\s"'`<>)]*/i);
    return match?.[0] || null;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const uri = findVikingUriInValue(item);
      if (uri) return uri;
    }
    return null;
  }
  if (value && typeof value === "object") {
    for (const item of Object.values(value)) {
      const uri = findVikingUriInValue(item);
      if (uri) return uri;
    }
  }
  return null;
}

export function buildGuardMessage(uri, hint = {}) {
  const tool = hint.tool || "the OpenViking MCP tools";
  const example = typeof hint.example === "function" ? hint.example(uri) : hint.example;
  const lines = [
    "viking:// URIs are OpenViking virtual paths, not local filesystem paths.",
    `Use ${tool} instead.`,
  ];
  if (example) lines.push(`Example: ${example}`);
  return lines.join("\n");
}
