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

// Arguments that carry file CONTENT rather than a location. The sweep below
// looks past the known path keys so an unusual one (`paths`, a nested target)
// is still caught, but text a tool is asked to WRITE is not a path: a local
// `write` whose body merely mentions viking://user/default/ was denied, and no
// file was created. Skipped by name at any depth.
const DEFAULT_CONTENT_KEYS = [
  "content",
  "contents",
  "text",
  "body",
  "old_string",
  "oldString",
  "new_string",
  "newString",
  "old_str",
  "new_str",
  "file_text",
  "insert_line",
  "replacement",
];

export function findVikingUri(args = {}, keys = DEFAULT_URI_KEYS, contentKeys = DEFAULT_CONTENT_KEYS) {
  if (!args || typeof args !== "object") return null;
  for (const key of keys) {
    const uri = findVikingUriInValue(args[key]);
    if (uri) return uri;
  }
  return findVikingUriInValue(args, new Set(contentKeys));
}

export function findVikingUriInValue(value, skipKeys) {
  if (typeof value === "string") {
    const match = value.match(/\bviking:\/\/[^\s"'`<>)]*/i);
    return match?.[0] || null;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const uri = findVikingUriInValue(item, skipKeys);
      if (uri) return uri;
    }
    return null;
  }
  if (value && typeof value === "object") {
    for (const [key, item] of Object.entries(value)) {
      if (skipKeys?.has(key)) continue;
      const uri = findVikingUriInValue(item, skipKeys);
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
