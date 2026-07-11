import { OpenVikingError } from "./errors.js";
import { zipSync } from "fflate";
import type {
  AddResourceOptions,
  ClientConfig,
  CreateSessionOptions,
  FindResult,
  JsonObject,
  ListOptions,
  GetSkillOptions,
  GrepOptions,
  ImportPackOptions,
  Message,
  RequestOptions,
  ResponseEnvelope,
  SearchOptions,
  TaskListOptions,
  TreeOptions,
  UpdateWatchOptions,
  WaitOptions,
} from "./types.js";

const compact = (value: JsonObject): JsonObject =>
  Object.fromEntries(
    Object.entries(value).filter(
      ([, item]) => item !== undefined && item !== null,
    ),
  );
const pathPart = (value: string): string => encodeURIComponent(value);
const bytesToDataURI = (bytes: Uint8Array, mimeType: string): string => {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return `data:${mimeType};base64,${btoa(binary)}`;
};

const imageMimeType = (path: string): string => {
  const extension = path.toLowerCase().match(/\.[^.\\/]+$/)?.[0];
  const mimeTypes: Record<string, string> = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
  };
  return (extension && mimeTypes[extension]) || "image/png";
};

async function nodeImagePathToDataURI(
  path: string,
): Promise<string | undefined> {
  if (typeof process === "undefined" || !process.versions?.node)
    return undefined;
  const fsSpecifier = "node:fs/promises";
  const fs = await import(fsSpecifier);
  try {
    const stat = await fs.stat(path);
    if (!stat.isFile()) return undefined;
    return bytesToDataURI(await fs.readFile(path), imageMimeType(path));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  }
}

async function nodePathToBlob(
  path: string,
): Promise<{ blob: Blob; filename: string; sourceName?: string } | undefined> {
  if (typeof process === "undefined" || !process.versions?.node)
    return undefined;
  // Keep built-in specifiers dynamic so the bundled ESM and CommonJS outputs
  // can share this implementation without eager filesystem initialization.
  const fsSpecifier = "node:fs/promises";
  const pathSpecifier = "node:path";
  const fs = await import(fsSpecifier);
  const nodePath = await import(pathSpecifier);
  let stat;
  try {
    stat = await fs.stat(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  }
  if (stat.isFile())
    return {
      blob: new Blob([await fs.readFile(path)]),
      filename: nodePath.basename(path),
      sourceName: nodePath.basename(path),
    };
  if (!stat.isDirectory()) return undefined;
  const files: Record<string, Uint8Array> = {};
  const walk = async (directory: string, prefix = ""): Promise<void> => {
    for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
      if (entry.isSymbolicLink()) continue;
      const fullPath = nodePath.join(directory, entry.name);
      const archivePath = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (entry.isDirectory()) await walk(fullPath, archivePath);
      else if (entry.isFile()) files[archivePath] = await fs.readFile(fullPath);
    }
  };
  await walk(path);
  return {
    blob: new Blob([zipSync(files)]),
    filename: `${nodePath.basename(path)}.zip`,
    sourceName: nodePath.basename(path),
  };
}

/** Normalize a short OpenViking URI to the canonical `viking://` form. */
export const normalizeURI = (uri: string): string =>
  uri.startsWith("viking://") ? uri : `viking://${uri.replace(/^\/+/, "")}`;

/** HTTP client for an existing OpenViking server. */
export class OpenVikingClient {
  readonly baseUrl: string;
  private readonly fetcher: typeof globalThis.fetch;
  private readonly headers: Headers;
  private readonly timeout: number;
  private readonly profile: boolean;
  private readonly uploadMode: string | undefined;

  /** Create a client with explicit connection and identity configuration. */
  constructor(config: ClientConfig) {
    if (!config.baseUrl?.trim())
      throw new TypeError("OpenViking: baseUrl is required");
    const url = new URL(config.baseUrl);
    if (!/^https?:$/.test(url.protocol))
      throw new TypeError("OpenViking: baseUrl must use http or https");
    this.baseUrl = config.baseUrl.replace(/\/+$/, "");
    this.fetcher = config.fetch ?? globalThis.fetch;
    if (!this.fetcher)
      throw new TypeError("OpenViking: fetch is not available");
    this.timeout = config.timeout ?? 60_000;
    this.profile = config.profile ?? false;
    this.uploadMode = config.uploadMode;
    this.headers = new Headers(config.headers);
    if (config.apiKey) this.headers.set("X-API-Key", config.apiKey);
    if (config.account)
      this.headers.set("X-OpenViking-Account", config.account);
    if (config.user) this.headers.set("X-OpenViking-User", config.user);
    if (config.actorPeerId)
      this.headers.set("X-OpenViking-Actor-Peer", config.actorPeerId);
  }

  private async request<T>(
    method: string,
    path: string,
    options: {
      query?: JsonObject;
      body?: unknown;
      form?: FormData;
      signal?: AbortSignal;
    } = {},
  ): Promise<T> {
    const url = new URL(
      `${this.baseUrl}${path.startsWith("/") ? path : `/${path}`}`,
    );
    for (const [key, value] of Object.entries(options.query ?? {})) {
      if (value !== undefined && value !== null && value !== "")
        url.searchParams.set(key, String(value));
    }
    if (this.profile) url.searchParams.set("profile", "1");
    const headers = new Headers(this.headers);
    let body: string | FormData | undefined;
    if (options.form) body = options.form;
    else if (options.body !== undefined) {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(options.body);
    }
    const controller = new AbortController();
    const abort = () => controller.abort(options.signal?.reason);
    options.signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(
      () =>
        controller.abort(new DOMException("Request timed out", "TimeoutError")),
      this.timeout,
    );
    try {
      const init: RequestInit = { method, headers, signal: controller.signal };
      if (body !== undefined) init.body = body;
      const response = await this.fetcher(url, init);
      const text = await response.text();
      let envelope: ResponseEnvelope<T> = {};
      if (text) {
        try {
          envelope = JSON.parse(text) as ResponseEnvelope<T>;
        } catch (cause) {
          throw new OpenVikingError(`HTTP ${response.status}: ${text}`, {
            statusCode: response.status,
            cause,
          });
        }
      }
      if (envelope.error || envelope.status === "error" || !response.ok) {
        const info = envelope.error;
        throw new OpenVikingError(
          info?.message ?? String(envelope.detail ?? `HTTP ${response.status}`),
          compact({
            code: info?.code,
            details: info?.details,
            statusCode: response.status,
          }) as { code?: string; details?: JsonObject; statusCode?: number },
        );
      }
      return envelope.result as T;
    } catch (error) {
      if (error instanceof OpenVikingError) throw error;
      if (controller.signal.aborted)
        throw new OpenVikingError("Request timed out or was aborted", {
          code: "DEADLINE_EXCEEDED",
          cause: error,
        });
      throw new OpenVikingError(
        error instanceof Error ? error.message : "Network request failed",
        { code: "UNAVAILABLE", cause: error },
      );
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", abort);
    }
  }

  private async upload(file: Blob, filename = "upload"): Promise<string> {
    const form = new FormData();
    form.set("file", file, filename);
    if (this.uploadMode) form.set("upload_mode", this.uploadMode);
    const result = await this.request<{ temp_file_id: string }>(
      "POST",
      "/api/v1/resources/temp_upload",
      { form },
    );
    if (!result?.temp_file_id)
      throw new OpenVikingError(
        "Upload response did not include temp_file_id",
        { code: "INTERNAL" },
      );
    return result.temp_file_id;
  }

  private async download(path: string, body: JsonObject): Promise<Blob> {
    const controller = new AbortController();
    const timer = setTimeout(
      () =>
        controller.abort(new DOMException("Request timed out", "TimeoutError")),
      this.timeout,
    );
    try {
      const headers = new Headers(this.headers);
      headers.set("Content-Type", "application/json");
      const response = await this.fetcher(`${this.baseUrl}${path}`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) {
        const text = await response.text();
        try {
          const envelope = JSON.parse(text) as ResponseEnvelope<never>;
          throw new OpenVikingError(
            envelope.error?.message ??
              String(envelope.detail ?? `HTTP ${response.status}`),
            compact({
              code: envelope.error?.code,
              details: envelope.error?.details,
              statusCode: response.status,
            }) as { code?: string; details?: JsonObject; statusCode?: number },
          );
        } catch (error) {
          if (error instanceof OpenVikingError) throw error;
          throw new OpenVikingError(`HTTP ${response.status}: ${text}`, {
            statusCode: response.status,
            cause: error,
          });
        }
      }
      return response.blob();
    } catch (error) {
      if (error instanceof OpenVikingError) throw error;
      if (controller.signal.aborted)
        throw new OpenVikingError("Request timed out", {
          code: "DEADLINE_EXCEEDED",
          cause: error,
        });
      throw new OpenVikingError(
        error instanceof Error ? error.message : "Network request failed",
        { code: "UNAVAILABLE", cause: error },
      );
    } finally {
      clearTimeout(timer);
    }
  }

  /** Add a remote URL or Node.js local file/directory as a resource. */
  async addResource(
    source: string,
    options: AddResourceOptions = {},
  ): Promise<JsonObject> {
    if (options.to && options.parent)
      throw new TypeError("OpenViking: cannot specify both to and parent");
    const body: JsonObject = compact({
      to: options.to,
      parent: options.parent,
      reason: options.reason,
      instruction: options.instruction,
      wait: options.wait ?? false,
      timeout: options.timeout,
      strict: options.strict ?? false,
      ignore_dirs: options.ignoreDirs,
      include: options.include,
      exclude: options.exclude,
      directly_upload_media: options.directlyUploadMedia ?? true,
      preserve_structure: options.preserveStructure,
      watch_interval: options.watchInterval ?? 0,
      args:
        options.args && Object.keys(options.args).length
          ? options.args
          : undefined,
      telemetry: options.telemetry,
    });
    const local = await nodePathToBlob(source);
    if (local) {
      body.temp_file_id = await this.upload(local.blob, local.filename);
      body.source_name = local.sourceName;
    } else body.path = source;
    return this.request("POST", "/api/v1/resources", { body });
  }

  /** Install a skill from inline data or an existing Node.js path. */
  async addSkill(
    source: unknown,
    options: WaitOptions & { targetUri?: string } = {},
  ): Promise<JsonObject> {
    const body: JsonObject = compact({
      wait: options.wait ?? false,
      timeout: options.timeout,
      telemetry: options.telemetry,
      target_uri: options.targetUri,
    });
    const local =
      typeof source === "string" ? await nodePathToBlob(source) : undefined;
    if (local)
      body.temp_file_id = await this.upload(local.blob, local.filename);
    else body.data = source;
    return this.request("POST", "/api/v1/skills", { body });
  }
  /** List installed skills. */
  listSkills(
    options: { nodeLimit?: number; targetUri?: string } = {},
  ): Promise<JsonObject> {
    return this.request("GET", "/api/v1/skills", {
      query: {
        node_limit: options.nodeLimit ?? 1000,
        target_uri: options.targetUri,
      },
    });
  }
  /** Search installed skills semantically. */
  findSkills(
    query: string,
    options: {
      limit?: number;
      scoreThreshold?: number;
      level?: number[];
      targetUri?: string;
      telemetry?: unknown;
    } = {},
  ): Promise<JsonObject> {
    return this.request("POST", "/api/v1/skills/find", {
      body: compact({
        query,
        limit: options.limit ?? 10,
        score_threshold: options.scoreThreshold,
        level: options.level,
        target_uri: options.targetUri,
        telemetry: options.telemetry,
      }),
    });
  }
  /** Validate skill data without installing it. */
  validateSkill(
    data: unknown,
    options: {
      strict?: boolean;
      sourcePath?: string;
      skillDirName?: string;
      targetUri?: string;
    } = {},
  ): Promise<JsonObject> {
    return this.request("POST", "/api/v1/skills/validate", {
      body: compact({
        data,
        strict: options.strict ?? false,
        source_path: options.sourcePath,
        skill_dir_name: options.skillDirName,
        target_uri: options.targetUri,
      }),
    });
  }
  /** Get an installed skill. */
  getSkill(name: string, options: GetSkillOptions = {}): Promise<JsonObject> {
    return this.request("GET", `/api/v1/skills/${pathPart(name)}`, {
      query: {
        include_content: options.includeContent,
        include_files: options.includeFiles ?? true,
        include_source: options.includeSource ?? false,
        level: options.level,
        target_uri: options.targetUri,
      },
    });
  }
  /** Replace an installed skill. */
  async updateSkill(
    name: string,
    source: unknown,
    options: WaitOptions & {
      sourceMetadata?: JsonObject;
      targetUri?: string;
    } = {},
  ): Promise<JsonObject> {
    const body: JsonObject = compact({
      wait: options.wait ?? false,
      timeout: options.timeout,
      source_metadata: options.sourceMetadata,
      target_uri: options.targetUri,
      telemetry: options.telemetry,
    });
    const local =
      typeof source === "string" ? await nodePathToBlob(source) : undefined;
    if (local)
      body.temp_file_id = await this.upload(local.blob, local.filename);
    else body.data = source;
    return this.request("PUT", `/api/v1/skills/${pathPart(name)}`, { body });
  }
  /** Delete an installed skill. */
  deleteSkill(name: string, targetUri?: string): Promise<JsonObject> {
    return this.request("DELETE", `/api/v1/skills/${pathPart(name)}`, {
      query: { target_uri: targetUri },
    });
  }
  /** List resource watches. */
  listWatches(
    options: { activeOnly?: boolean; toUri?: string } = {},
  ): Promise<JsonObject> {
    return this.request("GET", "/api/v1/watches", {
      query: {
        active_only: options.activeOnly ?? false,
        to_uri: options.toUri ? normalizeURI(options.toUri) : undefined,
      },
    });
  }
  /** Get a watch by task ID. */
  getWatch(taskId: string, toUri?: string): Promise<JsonObject> {
    return this.request("GET", `/api/v1/watches/${pathPart(taskId)}`, {
      query: { to_uri: toUri ? normalizeURI(toUri) : undefined },
    });
  }
  /** Partially update a watch. */
  updateWatch(
    ref: { taskId?: string; toUri?: string },
    changes: UpdateWatchOptions,
  ): Promise<JsonObject> {
    if (!ref.taskId && !ref.toUri) {
      throw new TypeError("OpenViking: watch reference is required");
    }
    return this.request(
      "PATCH",
      ref.taskId
        ? `/api/v1/watches/${pathPart(ref.taskId)}`
        : "/api/v1/watches",
      {
        query: { to_uri: ref.toUri ? normalizeURI(ref.toUri) : undefined },
        body: compact({
          watch_interval: changes.watchInterval,
          is_active: changes.isActive,
          reason: changes.reason,
          instruction: changes.instruction,
        }),
      },
    );
  }
  /** Delete a watch. */
  deleteWatch(ref: { taskId?: string; toUri?: string }): Promise<JsonObject> {
    if (!ref.taskId && !ref.toUri)
      throw new TypeError("OpenViking: watch reference is required");
    return this.request(
      "DELETE",
      ref.taskId
        ? `/api/v1/watches/${pathPart(ref.taskId)}`
        : "/api/v1/watches",
      { query: { to_uri: ref.toUri ? normalizeURI(ref.toUri) : undefined } },
    );
  }
  /** Trigger a watch immediately. */
  triggerWatch(ref: { taskId?: string; toUri?: string }): Promise<JsonObject> {
    if (!ref.taskId && !ref.toUri)
      throw new TypeError("OpenViking: watch reference is required");
    return this.request(
      "POST",
      ref.taskId
        ? `/api/v1/watches/${pathPart(ref.taskId)}/trigger`
        : "/api/v1/watches/trigger",
      { query: { to_uri: ref.toUri ? normalizeURI(ref.toUri) : undefined } },
    );
  }

  /** Find relevant content without session context. */
  async find(query: string, options: SearchOptions = {}): Promise<FindResult> {
    return this.searchRequest("find", query, options);
  }
  /** Search relevant content with optional session context. */
  async search(
    query: string,
    options: SearchOptions = {},
  ): Promise<FindResult> {
    return this.searchRequest("search", query, options);
  }
  private async searchRequest(
    kind: "find" | "search",
    query: string,
    options: SearchOptions,
  ): Promise<FindResult> {
    let imageUrl: string | undefined;
    if (typeof options.image === "string") {
      imageUrl = (await nodeImagePathToDataURI(options.image)) ?? options.image;
    }
    return this.request("POST", `/api/v1/search/${kind}`, {
      body: compact({
        query,
        target_uri: options.targetUri ?? "viking://",
        image_url: imageUrl,
        session_id: kind === "search" ? options.sessionId : undefined,
        limit: options.nodeLimit ?? options.limit ?? 10,
        score_threshold: options.scoreThreshold,
        filter: options.filter,
        context_type: options.contextType,
        telemetry: options.telemetry,
        since: options.since,
        until: options.until,
        time_field: options.timeField,
        level: options.level,
        tags: options.tags,
      }),
    });
  }
  /** Search file contents by pattern. */
  grep(
    uri: string,
    pattern: string,
    options: GrepOptions = {},
  ): Promise<JsonObject> {
    return this.request("POST", "/api/v1/search/grep", {
      body: compact({
        uri: normalizeURI(uri),
        pattern,
        case_insensitive: options.caseInsensitive ?? false,
        node_limit: options.nodeLimit ?? 256,
        level_limit: options.levelLimit,
        exclude_uri: options.excludeUri
          ? normalizeURI(options.excludeUri)
          : undefined,
      }),
    });
  }
  /** Find files by glob pattern. */
  glob(
    pattern: string,
    uri = "viking://",
    nodeLimit = 256,
  ): Promise<JsonObject> {
    return this.request("POST", "/api/v1/search/glob", {
      body: { pattern, uri: normalizeURI(uri), node_limit: nodeLimit },
    });
  }

  /** List directory contents. */
  list(uri: string, options: ListOptions = {}): Promise<unknown[]> {
    return this.request("GET", "/api/v1/fs/ls", {
      query: {
        uri: normalizeURI(uri),
        simple: options.simple ?? false,
        recursive: options.recursive ?? false,
        output: options.output ?? "original",
        abs_limit: options.absLimit ?? 256,
        show_all_hidden: options.showAllHidden ?? false,
        node_limit: options.nodeLimit ?? 1000,
      },
    });
  }
  /** Return a directory tree. */
  tree(uri: string, options: TreeOptions = {}): Promise<JsonObject[]> {
    return this.request("GET", "/api/v1/fs/tree", {
      query: {
        uri: normalizeURI(uri),
        output: options.output ?? "original",
        abs_limit: options.absLimit ?? 128,
        show_all_hidden: options.showAllHidden ?? false,
        node_limit: options.nodeLimit ?? 1000,
      },
    });
  }
  /** Return URI metadata. */
  stat(uri: string): Promise<JsonObject> {
    return this.request("GET", "/api/v1/fs/stat", {
      query: { uri: normalizeURI(uri) },
    });
  }
  /** Return URI logical attributes. */
  attrs(uri: string): Promise<JsonObject> {
    return this.request("GET", "/api/v1/fs/attrs", {
      query: { uri: normalizeURI(uri) },
    });
  }
  /** Create a directory. */
  mkdir(uri: string, description?: string): Promise<void> {
    return this.request("POST", "/api/v1/fs/mkdir", {
      body: compact({ uri: normalizeURI(uri), description }),
    });
  }
  /** Remove a resource or directory. */
  remove(
    uri: string,
    options: { recursive?: boolean; wait?: boolean; timeout?: number } = {},
  ): Promise<void> {
    return this.request("DELETE", "/api/v1/fs", {
      query: {
        uri: normalizeURI(uri),
        recursive: options.recursive ?? false,
        wait: options.wait ?? false,
        timeout: options.timeout,
      },
    });
  }
  /** Move a URI. */
  move(fromUri: string, toUri: string): Promise<void> {
    return this.request("POST", "/api/v1/fs/mv", {
      body: { from_uri: normalizeURI(fromUri), to_uri: normalizeURI(toUri) },
    });
  }
  /** Read text content. */
  read(uri: string, offset = 0, limit = -1): Promise<string> {
    return this.request("GET", "/api/v1/content/read", {
      query: { uri: normalizeURI(uri), offset, limit },
    });
  }
  /** Read L0 abstract content. */
  abstract(uri: string): Promise<string> {
    return this.request("GET", "/api/v1/content/abstract", {
      query: { uri: normalizeURI(uri) },
    });
  }
  /** Read L1 overview content. */
  overview(uri: string): Promise<string> {
    return this.request("GET", "/api/v1/content/overview", {
      query: { uri: normalizeURI(uri) },
    });
  }
  /** Write text content, including an empty string used to clear a file. */
  write(
    uri: string,
    content: string,
    options: WaitOptions & { mode?: string } = {},
  ): Promise<JsonObject> {
    return this.request("POST", "/api/v1/content/write", {
      body: compact({
        uri: normalizeURI(uri),
        content,
        mode: options.mode ?? "replace",
        wait: options.wait ?? false,
        timeout: options.timeout,
        telemetry: options.telemetry,
      }),
    });
  }
  /** Set retrieval tags. */
  setTags(
    uri: string,
    tags: string[],
    options: { mode?: string; recursive?: boolean; telemetry?: unknown } = {},
  ): Promise<JsonObject> {
    return this.request("POST", "/api/v1/fs/attrs/set_tags", {
      body: compact({
        uri: normalizeURI(uri),
        tags,
        mode: options.mode ?? "replace",
        recursive: options.recursive ?? false,
        telemetry: options.telemetry,
      }),
    });
  }
  /** Rebuild indexes for a URI. */
  reindex(
    uri: string,
    options: { mode?: string; wait?: boolean } = {},
  ): Promise<JsonObject> {
    return this.request("POST", "/api/v1/content/reindex", {
      body: {
        uri: normalizeURI(uri),
        mode: options.mode ?? "vectors_only",
        wait: options.wait ?? true,
      },
    });
  }

  /** Create a session. */
  createSession(options: CreateSessionOptions = {}): Promise<JsonObject> {
    return this.request("POST", "/api/v1/sessions", {
      body: compact({
        session_id: options.sessionId,
        memory_policy: options.memoryPolicy,
        telemetry: options.telemetry,
      }),
    });
  }
  /** List sessions visible to the caller. */
  listSessions(): Promise<unknown[]> {
    return this.request("GET", "/api/v1/sessions");
  }
  /** Get one session. */
  getSession(sessionId: string, autoCreate = false): Promise<JsonObject> {
    return this.request("GET", `/api/v1/sessions/${pathPart(sessionId)}`, {
      query: { auto_create: autoCreate || undefined },
    });
  }
  /** Test whether a session exists. */
  async sessionExists(sessionId: string): Promise<boolean> {
    try {
      await this.getSession(sessionId);
      return true;
    } catch (error) {
      if (error instanceof OpenVikingError && error.code === "NOT_FOUND")
        return false;
      throw error;
    }
  }
  /** Delete a session. */
  deleteSession(sessionId: string): Promise<void> {
    return this.request("DELETE", `/api/v1/sessions/${pathPart(sessionId)}`);
  }
  /** Assemble session context within a token budget. */
  getSessionContext(
    sessionId: string,
    tokenBudget = 128_000,
  ): Promise<JsonObject> {
    return this.request(
      "GET",
      `/api/v1/sessions/${pathPart(sessionId)}/context`,
      { query: { token_budget: tokenBudget } },
    );
  }
  /** Get a committed session archive. */
  getSessionArchive(sessionId: string, archiveId: string): Promise<JsonObject> {
    return this.request(
      "GET",
      `/api/v1/sessions/${pathPart(sessionId)}/archives/${pathPart(archiveId)}`,
    );
  }
  /** Append one message to a session. */
  addMessage(sessionId: string, message: Message): Promise<JsonObject> {
    if (message.content === undefined && !message.parts?.length) {
      throw new TypeError("OpenViking: message requires content or parts");
    }
    const content = message.parts?.length ? undefined : message.content;
    return this.request(
      "POST",
      `/api/v1/sessions/${pathPart(sessionId)}/messages`,
      {
        body: compact({
          role: message.role,
          content,
          parts: message.parts?.length ? message.parts : undefined,
          created_at: message.createdAt,
          peer_id: message.peerId,
          telemetry: message.telemetry,
        }),
      },
    );
  }
  /** Append multiple messages to a session. */
  batchAddMessages(
    sessionId: string,
    messages: Message[],
    telemetry?: unknown,
  ): Promise<JsonObject> {
    return this.request(
      "POST",
      `/api/v1/sessions/${pathPart(sessionId)}/messages/batch`,
      {
        body: compact({
          messages: messages.map((message) => {
            if (message.content === undefined && !message.parts?.length) {
              throw new TypeError(
                "OpenViking: each message requires content or parts",
              );
            }
            const parts = message.parts?.length ? message.parts : undefined;
            return compact({
              role: message.role,
              content: parts ? undefined : message.content,
              parts,
              created_at: message.createdAt,
              peer_id: message.peerId,
            });
          }),
          telemetry,
        }),
      },
    );
  }
  /** Commit a session and extract memories. */
  commitSession(
    sessionId: string,
    keepRecentCount = 0,
    telemetry?: unknown,
  ): Promise<JsonObject> {
    return this.request(
      "POST",
      `/api/v1/sessions/${pathPart(sessionId)}/commit`,
      { body: compact({ keep_recent_count: keepRecentCount, telemetry }) },
    );
  }
  /** Export a resource subtree as an OVPack blob. */
  exportOVPack(uri: string, includeVectors = false): Promise<Blob> {
    return this.download("/api/v1/pack/export", {
      uri: normalizeURI(uri),
      include_vectors: includeVectors,
    });
  }
  /** Back up public scopes as a restore-only OVPack blob. */
  backupOVPack(includeVectors = false): Promise<Blob> {
    return this.download("/api/v1/pack/backup", {
      include_vectors: includeVectors,
    });
  }
  /** Import a local OVPack file under a parent URI. */
  async importOVPack(
    source: string,
    parent: string,
    options: ImportPackOptions = {},
  ): Promise<string> {
    const local = await nodePathToBlob(source);
    if (!local)
      throw new TypeError(
        "OpenViking: importOVPack requires an existing Node.js local file",
      );
    const result = await this.request<{ uri: string }>(
      "POST",
      "/api/v1/pack/import",
      {
        body: compact({
          parent: normalizeURI(parent),
          temp_file_id: await this.upload(local.blob, local.filename),
          on_conflict: options.onConflict,
          vector_mode: options.vectorMode,
        }),
      },
    );
    return result.uri;
  }
  /** Restore a local OVPack backup file. */
  async restoreOVPack(
    source: string,
    options: ImportPackOptions = {},
  ): Promise<string> {
    const local = await nodePathToBlob(source);
    if (!local)
      throw new TypeError(
        "OpenViking: restoreOVPack requires an existing Node.js local file",
      );
    const result = await this.request<{ uri: string }>(
      "POST",
      "/api/v1/pack/restore",
      {
        body: compact({
          temp_file_id: await this.upload(local.blob, local.filename),
          on_conflict: options.onConflict,
          vector_mode: options.vectorMode,
        }),
      },
    );
    return result.uri;
  }
  /** Get a background task. */
  async getTask(taskId: string): Promise<JsonObject | null> {
    try {
      return await this.request("GET", `/api/v1/tasks/${pathPart(taskId)}`);
    } catch (error) {
      if (
        error instanceof OpenVikingError &&
        (error.code === "NOT_FOUND" || error.statusCode === 404)
      ) {
        return null;
      }
      throw error;
    }
  }
  /** List background tasks. */
  listTasks(options: TaskListOptions = {}): Promise<unknown[]> {
    return this.request("GET", "/api/v1/tasks", {
      query: {
        task_type: options.taskType,
        status: options.status,
        resource_id: options.resourceId,
        limit: options.limit,
      },
    });
  }
  /** Wait for queued processing to finish. */
  waitProcessed(timeout?: number): Promise<JsonObject> {
    return this.request("POST", "/api/v1/system/wait", {
      body: compact({ timeout }),
    });
  }
  /** Check the raw server health endpoint. */
  async health(options: RequestOptions = {}): Promise<boolean> {
    const controller = new AbortController();
    const abort = () => controller.abort(options.signal?.reason);
    options.signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(() => controller.abort(), this.timeout);
    try {
      const response = await this.fetcher(`${this.baseUrl}/health`, {
        headers: this.headers,
        signal: controller.signal,
      });
      if (!response.ok) return false;
      return ((await response.json()) as { status?: string }).status === "ok";
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", abort);
    }
  }
  /** Check filesystem/index consistency. */
  checkConsistency(uri: string): Promise<JsonObject> {
    return this.request("POST", "/api/v1/system/consistency", {
      body: { uri: normalizeURI(uri) },
    });
  }
  /** Return aggregate observer status. */
  getStatus(): Promise<JsonObject> {
    return this.request("GET", "/api/v1/observer/system");
  }
  /** Return queue observer status. */
  queueStatus(): Promise<JsonObject> {
    return this.request("GET", "/api/v1/observer/queue");
  }
  /** Return VikingDB observer status. */
  vikingDBStatus(): Promise<JsonObject> {
    return this.request("GET", "/api/v1/observer/vikingdb");
  }
  /** Return model observer status. */
  modelsStatus(): Promise<JsonObject> {
    return this.request("GET", "/api/v1/observer/models");
  }
  /** Create a tenant account and its first administrator. */
  adminCreateAccount(
    accountId: string,
    adminUserId: string,
    options: { userConfig?: JsonObject; seed?: string } = {},
  ): Promise<JsonObject> {
    return this.request("POST", "/api/v1/admin/accounts", {
      body: compact({
        account_id: accountId,
        admin_user_id: adminUserId,
        user_config: options.userConfig,
        seed: options.seed,
      }),
    });
  }
  /** List tenant accounts. */
  adminListAccounts(): Promise<unknown[]> {
    return this.request("GET", "/api/v1/admin/accounts");
  }
  /** Delete a tenant account. */
  adminDeleteAccount(accountId: string): Promise<JsonObject> {
    return this.request(
      "DELETE",
      `/api/v1/admin/accounts/${pathPart(accountId)}`,
    );
  }
  /** Register a user in an account. */
  adminRegisterUser(
    accountId: string,
    userId: string,
    role: string,
    options: { userConfig?: JsonObject; seed?: string } = {},
  ): Promise<JsonObject> {
    return this.request(
      "POST",
      `/api/v1/admin/accounts/${pathPart(accountId)}/users`,
      {
        body: compact({
          user_id: userId,
          role,
          user_config: options.userConfig,
          seed: options.seed,
        }),
      },
    );
  }
  /** List users in an account. */
  adminListUsers(accountId: string): Promise<unknown[]> {
    return this.request(
      "GET",
      `/api/v1/admin/accounts/${pathPart(accountId)}/users`,
    );
  }
  /** Remove a user from an account. */
  adminRemoveUser(accountId: string, userId: string): Promise<JsonObject> {
    return this.request(
      "DELETE",
      `/api/v1/admin/accounts/${pathPart(accountId)}/users/${pathPart(userId)}`,
    );
  }
  /** Change a user's role. */
  adminSetRole(
    accountId: string,
    userId: string,
    role: string,
  ): Promise<JsonObject> {
    return this.request(
      "PUT",
      `/api/v1/admin/accounts/${pathPart(accountId)}/users/${pathPart(userId)}/role`,
      { body: { role } },
    );
  }
  /** Regenerate a user's API key. */
  adminRegenerateKey(
    accountId: string,
    userId: string,
    seed?: string,
  ): Promise<JsonObject> {
    return this.request(
      "POST",
      `/api/v1/admin/accounts/${pathPart(accountId)}/users/${pathPart(userId)}/key`,
      { body: seed === undefined ? undefined : { seed } },
    );
  }
  /** Start legacy-data migration or cleanup. */
  adminMigrate(cleanup = false): Promise<JsonObject> {
    return this.request("POST", "/api/v1/admin/migrate", {
      body: { action: cleanup ? "cleanup" : "migrate" },
    });
  }
}
