const EXPERIENCE_TARGET_URI = "viking://user/memories/experiences/";
const DEFAULT_LIMIT = 5;
const MAX_LIMIT = 20;
const DEFAULT_TIMEOUT_MS = 15000;
const EXPERIENCE_SIDECAR_FILENAMES = new Set([".abstract.md", ".overview.md", ".relations.json"]);
const EXCLUDED_EXPERIENCE_STATUSES = new Set(["deprecated", "archived"]);

const EXPERIENCE_TOOL_DEFINITIONS = [
  {
    name: "search_experience",
    description: "Search reusable execution experiences for the current OpenViking user.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Task or situation to search for." },
        limit: {
          type: "integer",
          minimum: 1,
          maximum: MAX_LIMIT,
          default: DEFAULT_LIMIT,
        },
      },
      required: ["query"],
      additionalProperties: false,
    },
  },
  {
    name: "read_experience",
    description: "Read one Experience returned by search_experience.",
    inputSchema: {
      type: "object",
      properties: {
        uri: {
          type: "string",
          description: "Canonical viking:// URI of an Experience memory file.",
        },
      },
      required: ["uri"],
      additionalProperties: false,
    },
  },
];

function result(payload) {
  return {
    content: [{ type: "text", text: JSON.stringify(payload) }],
  };
}

function errorResult(message) {
  return {
    isError: true,
    content: [{ type: "text", text: String(message) }],
  };
}

function normalizedBaseUrl(config) {
  const explicit = String(config?.baseUrl || "").trim().replace(/\/+$/, "");
  if (explicit) return explicit;
  const mcpUrl = new URL(String(config?.mcpUrl || ""));
  mcpUrl.pathname = mcpUrl.pathname.replace(/\/mcp\/?$/, "").replace(/\/+$/, "");
  mcpUrl.search = "";
  mcpUrl.hash = "";
  return mcpUrl.toString().replace(/\/+$/, "");
}

function requestHeaders(config, includeJson = false) {
  const headers = { Accept: "application/json" };
  if (includeJson) headers["Content-Type"] = "application/json";
  if (config?.apiKey) headers.Authorization = `Bearer ${config.apiKey}`;
  if (config?.account) headers["X-OpenViking-Account"] = config.account;
  if (config?.user) headers["X-OpenViking-User"] = config.user;
  if (config?.peerId) headers["X-OpenViking-Actor-Peer"] = config.peerId;
  return headers;
}

function experiencePrefix(user) {
  const owner = String(user || "").trim();
  return owner ? `viking://user/${owner}/memories/experiences/` : "";
}

function isExperienceUri(uri, user) {
  const value = String(uri || "").trim();
  if (!value || value.includes("?") || value.includes("#")) return false;
  const prefix = experiencePrefix(user);
  const relative = prefix
    ? (value.startsWith(prefix) ? value.slice(prefix.length) : "")
    : value.match(/^viking:\/\/user\/[^/]+\/memories\/experiences\/(.+)$/)?.[1] || "";
  const segments = relative.split("/");
  const basename = segments.at(-1) || "";
  return Boolean(
    relative
    && segments.length > 0
    && segments.every((segment) => segment && segment !== "." && segment !== "..")
    && !EXPERIENCE_SIDECAR_FILENAMES.has(basename),
  );
}

function titleFromUri(uri) {
  const basename = String(uri).split("/").at(-1) || String(uri);
  const withoutExtension = basename.replace(/\.md$/i, "");
  try {
    return decodeURIComponent(withoutExtension);
  } catch {
    return withoutExtension;
  }
}

function clampLimit(value) {
  const parsed = Number.isFinite(Number(value)) ? Math.trunc(Number(value)) : DEFAULT_LIMIT;
  return Math.max(1, Math.min(MAX_LIMIT, parsed));
}

function requestSignal(config) {
  const configured = Number(config?.timeoutMs);
  const timeoutMs = Math.max(1000, Number.isFinite(configured) ? configured : DEFAULT_TIMEOUT_MS);
  return AbortSignal.timeout(timeoutMs);
}

async function readJsonResponse(response) {
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`OpenViking returned invalid JSON (HTTP ${response.status})`);
  }
  if (!response.ok) {
    const detail = payload?.detail || payload?.message || payload?.error?.message || response.statusText;
    throw new Error(`OpenViking request failed (HTTP ${response.status}): ${detail}`);
  }
  return payload;
}

function isRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function sanitizeMetadataValue(value, depth = 0) {
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : undefined;
  if (typeof value === "string") return value.slice(0, 4096);
  if (depth >= 3) return undefined;
  if (Array.isArray(value)) {
    return value.slice(0, 50)
      .map((item) => sanitizeMetadataValue(item, depth + 1))
      .filter((item) => item !== undefined);
  }
  if (!isRecord(value)) return undefined;
  const result = {};
  for (const [key, item] of Object.entries(value).slice(0, 50)) {
    const safe = sanitizeMetadataValue(item, depth + 1);
    if (safe !== undefined) result[String(key).slice(0, 128)] = safe;
  }
  return result;
}

function safeExperienceMetadata(...sources) {
  const metadata = {};
  for (const source of sources) {
    if (!isRecord(source)) continue;
    if (typeof source.status === "string" && source.status.trim()) {
      metadata.status = source.status.trim().toLowerCase().slice(0, 64);
    }
    const version = Number(source.version);
    if (Number.isSafeInteger(version) && version >= 0) metadata.version = version;
    if (typeof source.curated_at === "string" && source.curated_at.trim()) {
      metadata.curated_at = source.curated_at.trim().slice(0, 512);
    }
    const curatedFrom = source.curated_from ?? source.from;
    if (curatedFrom !== undefined) {
      const safe = sanitizeMetadataValue(curatedFrom);
      if (safe !== undefined) metadata.curated_from = safe;
    }
  }
  return metadata;
}

function parseExperienceDocument(rawContent, ...metadataSources) {
  const raw = String(rawContent || "");
  const match = /<!--\s*MEMORY_FIELDS\b([\s\S]*?)\s*-->\s*$/i.exec(raw);
  let fields = {};
  let content = raw;
  let metadataValid = true;
  if (match) {
    content = raw.slice(0, match.index).trimEnd();
    try {
      const parsed = JSON.parse(match[1].trim());
      if (isRecord(parsed)) fields = parsed;
      else metadataValid = false;
    } catch {
      metadataValid = false;
    }
  }
  return {
    content,
    metadata: safeExperienceMetadata(...metadataSources, fields),
    metadataValid,
  };
}

function documentFromReadPayload(payload) {
  const value = payload?.result;
  if (!isRecord(value)) return parseExperienceDocument(value);
  const content = value.raw_content ?? value.raw ?? value.content ?? "";
  return parseExperienceDocument(content, value.attrs, value.metadata, value);
}

function withMetadata(payload, metadata) {
  return Object.keys(metadata).length > 0 ? { ...payload, metadata } : payload;
}

async function fetchExperienceDocument(uri, config, fetchImpl) {
  const url = new URL(`${normalizedBaseUrl(config)}/api/v1/content/read`);
  url.searchParams.set("uri", uri);
  url.searchParams.set("raw", "true");
  const response = await fetchImpl(url.toString(), {
    method: "GET",
    headers: requestHeaders(config),
    signal: requestSignal(config),
  });
  return documentFromReadPayload(await readJsonResponse(response));
}

async function searchExperience(args, config, fetchImpl) {
  const query = String(args?.query || "").trim();
  if (!query) return errorResult("search_experience requires a non-empty query");
  try {
    const response = await fetchImpl(`${normalizedBaseUrl(config)}/api/v1/search/find`, {
      method: "POST",
      headers: requestHeaders(config, true),
      signal: requestSignal(config),
      body: JSON.stringify({
        query,
        target_uri: EXPERIENCE_TARGET_URI,
        limit: clampLimit(args?.limit),
      }),
    });
    const payload = await readJsonResponse(response);
    const memories = Array.isArray(payload?.result?.memories) ? payload.result.memories : [];
    const candidates = memories.filter((item) => isExperienceUri(item?.uri, config?.user));
    const hydrated = await Promise.all(candidates.map(async (item) => {
      const directMetadata = safeExperienceMetadata(item?.attrs, item?.metadata, item);
      try {
        const document = await fetchExperienceDocument(item.uri, config, fetchImpl);
        if (!document.metadataValid) return null;
        return { item, metadata: { ...directMetadata, ...document.metadata } };
      } catch {
        // Lifecycle state is authoritative only in the raw memory document.
        // A missing/failed hydration must not turn an archived or deprecated
        // Experience back into executable guidance.
        return null;
      }
    }));
    const results = hydrated
      .filter(Boolean)
      .filter(({ metadata }) => !EXCLUDED_EXPERIENCE_STATUSES.has(metadata.status))
      .map(({ item, metadata }) => withMetadata({
        uri: item.uri,
        title: titleFromUri(item.uri),
        score: Number.isFinite(Number(item.score)) ? Number(item.score) : 0,
        snippet: String(item.abstract || item.overview || ""),
      }, metadata));
    return result({ results });
  } catch (error) {
    return errorResult(error instanceof Error ? error.message : error);
  }
}

async function readExperience(args, config, fetchImpl) {
  const uri = String(args?.uri || "").trim();
  if (!isExperienceUri(uri, config?.user)) {
    return errorResult("read_experience requires an Experience URI owned by the current user");
  }
  try {
    const document = await fetchExperienceDocument(uri, config, fetchImpl);
    if (!document.metadataValid) {
      return errorResult("read_experience refuses malformed Experience lifecycle metadata");
    }
    if (EXCLUDED_EXPERIENCE_STATUSES.has(document.metadata.status)) {
      return errorResult(
        `read_experience refuses Experience status=${document.metadata.status}`,
      );
    }
    return result(withMetadata({ uri, content: document.content }, document.metadata));
  } catch (error) {
    return errorResult(error instanceof Error ? error.message : error);
  }
}

export function createExperienceToolProvider({ fetchImpl = globalThis.fetch } = {}) {
  if (typeof fetchImpl !== "function") throw new Error("fetchImpl must be a function");
  return {
    listTools() {
      return EXPERIENCE_TOOL_DEFINITIONS.map((tool) => structuredClone(tool));
    },
    async callTool(params, { config } = {}) {
      if (params?.name === "search_experience") {
        return searchExperience(params.arguments, config, fetchImpl);
      }
      if (params?.name === "read_experience") {
        return readExperience(params.arguments, config, fetchImpl);
      }
      return null;
    },
  };
}
