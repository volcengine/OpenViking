import { OpenVikingError } from "./errors.js";
import {
  nodeImagePathToDataURI,
  nodePathToBlob,
  packOutputPath,
  writeResponseToFile,
} from "./node-files.js";
import { OpenVikingTransport, type TransportOptions } from "./transport.js";
import type {
  AddResourceOptions,
  BatchAddMessagesOptions,
  BatchWriteOperation,
  BatchWriteOptions,
  ClientConfig,
  CommitSessionOptions,
  CreateSessionOptions,
  ExperienceOutcomeOptions,
  ExperienceTrajectoryOptions,
  FindOptions,
  FindResult,
  GitBlob,
  GitCommitOptions,
  GitRestoreOptions,
  JsonObject,
  ListOptions,
  GetSkillOptions,
  GrepOptions,
  ImportPackOptions,
  Message,
  PreflightAssetOptions,
  ReindexOptions,
  RequestOptions,
  ResolveAssetsOptions,
  SearchContextOptions,
  SearchContextResult,
  SearchOptions,
  SetTagsOptions,
  TaskListOptions,
  TreeOptions,
  UpdateSessionConfigOptions,
  UpdateWatchOptions,
  WaitOptions,
  WriteOptions,
} from "./types.js";

const compact = (value: JsonObject): JsonObject =>
  Object.fromEntries(
    Object.entries(value).filter(
      ([, item]) => item !== undefined && item !== null,
    ),
  );
const mergeExtra = (
  body: JsonObject,
  extra: JsonObject | undefined,
  protectedKeys: readonly string[] = [],
): JsonObject => {
  const protectedFields = new Set(protectedKeys);
  for (const [key, value] of Object.entries(extra ?? {})) {
    if (key in body || protectedFields.has(key))
      throw new TypeError(`OpenViking: extra cannot override ${key}`);
    if (value !== undefined && value !== null) body[key] = value;
  }
  return body;
};
const pathPart = (value: string): string => encodeURIComponent(value);

/** Normalize a short OpenViking URI to the canonical `viking://` form. */
export const normalizeURI = (uri: string): string =>
  uri.startsWith("viking://") ? uri : `viking://${uri.replace(/^\/+/, "")}`;

/** HTTP client for an existing OpenViking server. */
export class OpenVikingClient {
  readonly baseUrl: string;
  private readonly transport: OpenVikingTransport;

  /** Create a client with explicit connection and identity configuration. */
  constructor(config: ClientConfig) {
    this.transport = new OpenVikingTransport(config);
    this.baseUrl = this.transport.baseUrl;
  }

  private request<T>(
    method: string,
    path: string,
    options: TransportOptions = {},
  ): Promise<T> {
    return this.transport.request<T>(method, path, options);
  }

  private async upload(file: Blob, filename = "upload"): Promise<string> {
    const form = new FormData();
    form.set("file", file, filename);
    if (this.transport.uploadMode)
      form.set("upload_mode", this.transport.uploadMode);
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

  private async downloadToFile(
    path: string,
    body: JsonObject,
    output: string,
    signal?: AbortSignal,
  ): Promise<string> {
    return this.transport.consume(
      "POST",
      path,
      { body, ...(signal ? { signal } : {}) },
      async (response) => {
        if (
          !response.ok ||
          response.headers.get("content-type")?.includes("json")
        )
          return this.transport.parseResponse<never>(response);
        return writeResponseToFile(response, output);
      },
    );
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
      create_parent: options.createParent,
      reason: options.reason,
      instruction: options.instruction,
      wait: options.wait,
      timeout: options.timeout,
      strict: options.strict,
      ignore_dirs: options.ignoreDirs,
      include: options.include,
      exclude: options.exclude,
      directly_upload_media: options.directlyUploadMedia,
      preserve_structure: options.preserveStructure,
      watch_interval: options.watchInterval,
      processing_mode: options.processingMode,
      args:
        options.args && Object.keys(options.args).length
          ? options.args
          : undefined,
      tags: options.tags,
      tag_mode: options.tags ? options.tagMode : undefined,
      telemetry: options.telemetry,
    });
    const local = await nodePathToBlob(source);
    if (local) {
      body.temp_file_id = await this.upload(local.blob, local.filename);
      body.source_name = local.sourceName;
    } else body.path = source;
    return this.request("POST", "/api/v1/resources", {
      body: mergeExtra(body, options.extra),
    });
  }

  /** Install a skill from inline data or an existing Node.js path. */
  async addSkill(
    source: unknown,
    options: WaitOptions & { targetUri?: string; extra?: JsonObject } = {},
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
    return this.request("POST", "/api/v1/skills", {
      body: mergeExtra(body, options.extra),
    });
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
      extra?: JsonObject;
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
    return this.request("PUT", `/api/v1/skills/${pathPart(name)}`, {
      body: mergeExtra(body, options.extra),
    });
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
  async find(query: string, options: FindOptions = {}): Promise<FindResult> {
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
    options: FindOptions | SearchOptions,
  ): Promise<FindResult> {
    let imageUrl: string | undefined;
    if (typeof options.image === "string") {
      imageUrl = (await nodeImagePathToDataURI(options.image)) ?? options.image;
    }
    const body = compact({
      query,
      target_uri: options.targetUri,
      image_url: imageUrl,
      session_id:
        kind === "search" ? (options as SearchOptions).sessionId : undefined,
      limit: options.limit,
      node_limit: options.nodeLimit,
      score_threshold: options.scoreThreshold,
      filter: options.filter,
      context_type: options.contextType,
      telemetry: options.telemetry,
      since: options.since,
      until: options.until,
      time_field: options.timeField,
      level: options.level,
      tags: options.tags,
      include_provenance: options.includeProvenance,
      read_content: options.readContent,
    });
    return this.request("POST", `/api/v1/search/${kind}`, {
      body: mergeExtra(body, options.extra),
    });
  }
  /** Assemble injection-ready context on the server. */
  async searchContext(
    query: string,
    options: SearchContextOptions = {},
  ): Promise<SearchContextResult> {
    let imageUrl: string | undefined;
    if (typeof options.image === "string")
      imageUrl = (await nodeImagePathToDataURI(options.image)) ?? options.image;
    const body = compact({
      query,
      mode: "context",
      image_url: imageUrl,
      session_id: options.sessionId,
      limit: options.limit,
      node_limit: options.nodeLimit,
      score_threshold: options.scoreThreshold,
      filter: options.filter,
      context_type: options.contextType,
      include_provenance: options.includeProvenance,
      tags: options.tags,
      since: options.since,
      until: options.until,
      time_field: options.timeField,
      query_expansion: options.queryExpansion,
      max_tokens: options.maxTokens,
      quotas: options.quotas,
      purpose: options.purpose,
      detail: options.detail,
      dedup_turns: options.dedupTurns,
      exclude_uris: options.excludeUris,
      peer_scope: options.peerScope,
      other_peer_penalty: options.otherPeerPenalty,
      rewrite: options.rewrite,
      rewrite_max_bullets: options.rewriteMaxBullets,
      telemetry: options.telemetry,
    });
    return this.request("POST", "/api/v1/search/search", {
      body: mergeExtra(body, options.extra),
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
        sort_by: options.sortBy,
        sort_order: options.sortOrder,
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
        level_limit: options.levelLimit ?? 3,
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
  /** Download raw stored bytes. */
  downloadBytes(uri: string): Promise<Uint8Array> {
    return this.transport.consume(
      "GET",
      "/api/v1/content/download",
      { query: { uri: normalizeURI(uri) } },
      async (response) => {
        if (
          !response.ok ||
          response.headers.get("content-type")?.includes("json")
        )
          return this.transport.parseResponse<never>(response);
        return new Uint8Array(await response.arrayBuffer());
      },
    );
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
    options: WriteOptions = {},
  ): Promise<JsonObject> {
    const body = compact({
      uri: normalizeURI(uri),
      content,
      mode: options.mode,
      processing_mode: options.processingMode,
      wait: options.wait,
      timeout: options.timeout,
      telemetry: options.telemetry,
    });
    return this.request("POST", "/api/v1/content/write", {
      body: mergeExtra(body, options.extra),
    });
  }
  /** Apply file writes in one request and refresh their indexes once. */
  batchWrite(
    rootUri: string,
    operations: BatchWriteOperation[],
    options: BatchWriteOptions = {},
  ): Promise<JsonObject> {
    const body = compact({
      root_uri: normalizeURI(rootUri),
      operations: operations.map((operation) =>
        compact({
          uri: normalizeURI(operation.uri),
          content: operation.content,
          content_base64: operation.contentBase64,
          mode: operation.mode,
        }),
      ),
      wait: options.wait,
      timeout: options.timeout,
      telemetry: options.telemetry,
    });
    return this.request("POST", "/api/v1/content/batch-write", {
      body: mergeExtra(body, options.extra),
    });
  }
  /** Set retrieval tags. */
  setTags(
    uri: string,
    tags: string[],
    options: SetTagsOptions = {},
  ): Promise<JsonObject> {
    const body = compact({
      uri: normalizeURI(uri),
      tags,
      mode: options.mode ?? "replace",
      recursive: options.recursive ?? false,
      telemetry: options.telemetry,
    });
    return this.request("POST", "/api/v1/fs/attrs/set_tags", {
      body: mergeExtra(body, options.extra, [
        "uri",
        "tags",
        "mode",
        "recursive",
        "telemetry",
      ]),
    });
  }
  /** Rebuild indexes for a URI. */
  reindex(uri: string, options: ReindexOptions = {}): Promise<JsonObject> {
    const body = compact({
      uri: normalizeURI(uri),
      mode: options.mode ?? "vectors_only",
      wait: options.wait ?? true,
      dry_run: options.dryRun ?? false,
      recursive: options.recursive ?? true,
      tags: options.tags,
      tag_mode:
        options.tags === undefined ? undefined : (options.tagMode ?? "replace"),
    });
    return this.request("POST", "/api/v1/content/reindex", {
      body: mergeExtra(body, options.extra, ["tags", "tag_mode"]),
    });
  }

  /** Create a session. */
  createSession(options: CreateSessionOptions = {}): Promise<JsonObject> {
    const body = compact({
      session_id: options.sessionId,
      memory_policy: options.memoryPolicy,
      memory_extraction_config: options.memoryExtractionConfig,
      telemetry: options.telemetry,
    });
    if ("autoCommitPolicy" in options)
      body.auto_commit_policy = options.autoCommitPolicy ?? null;
    return this.request("POST", "/api/v1/sessions", {
      body: mergeExtra(body, options.extra),
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
  /** Update mutable session memory extraction settings. */
  updateSessionConfig(
    sessionId: string,
    options: UpdateSessionConfigOptions,
  ): Promise<JsonObject> {
    const body = compact({
      memory_extraction_config: options.memoryExtractionConfig,
      telemetry: options.telemetry,
    });
    if ("autoCommitPolicy" in options)
      body.auto_commit_policy = options.autoCommitPolicy ?? null;
    return this.request(
      "PATCH",
      `/api/v1/sessions/${pathPart(sessionId)}/config`,
      {
        body: mergeExtra(body, options.extra),
      },
    );
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
          turn_id: message.turnId,
          message_kind: message.messageKind,
          source_message_ids: message.sourceMessageIds,
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
  ): Promise<JsonObject>;
  batchAddMessages(
    sessionId: string,
    messages: Message[],
    options?: BatchAddMessagesOptions,
  ): Promise<JsonObject> {
    const normalizedOptions =
      options !== null &&
      typeof options === "object" &&
      ("telemetry" in options || "extra" in options)
        ? options
        : { telemetry: options };
    return this.request(
      "POST",
      `/api/v1/sessions/${pathPart(sessionId)}/messages/batch`,
      {
        body: mergeExtra(
          compact({
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
                turn_id: message.turnId,
                message_kind: message.messageKind,
                source_message_ids: message.sourceMessageIds,
              });
            }),
            telemetry: normalizedOptions.telemetry,
          }),
          normalizedOptions.extra,
        ),
      },
    );
  }
  /** Commit a session and extract memories. */
  commitSession(
    sessionId: string,
    keepRecentCount?: number,
    telemetry?: unknown,
    eventTags?: string[],
  ): Promise<JsonObject>;
  commitSession(
    sessionId: string,
    options?: CommitSessionOptions,
  ): Promise<JsonObject>;
  commitSession(
    sessionId: string,
    optionsOrKeepRecentCount: CommitSessionOptions | number = {},
    telemetry?: unknown,
    eventTags?: string[],
  ): Promise<JsonObject> {
    let options: CommitSessionOptions;
    if (typeof optionsOrKeepRecentCount === "number") {
      options = { keepRecentCount: optionsOrKeepRecentCount };
      if (telemetry !== undefined) options.telemetry = telemetry;
      if (eventTags !== undefined) options.eventTags = eventTags;
    } else {
      options = optionsOrKeepRecentCount;
    }
    const body = compact({
      keep_recent_count: options.keepRecentCount,
      retention_mode: options.retentionMode,
      keep_recent_turn_count: options.keepRecentTurnCount,
      retained_message_token_budget: options.retainedMessageTokenBudget,
      min_raw_tail_steps: options.minRawTailSteps,
      extraction_metadata:
        options.eventTags === undefined
          ? undefined
          : { event: { tags: options.eventTags } },
      telemetry: options.telemetry,
    });
    return this.request(
      "POST",
      `/api/v1/sessions/${pathPart(sessionId)}/commit`,
      { body: mergeExtra(body, options.extra) },
    );
  }
  /** Export a resource subtree to a local OVPack file. */
  async exportOVPack(
    uri: string,
    to: string,
    includeVectors = false,
    options: RequestOptions = {},
  ): Promise<string> {
    const output = await packOutputPath(to, uri, "export");
    return this.downloadToFile(
      "/api/v1/pack/export",
      { uri: normalizeURI(uri), include_vectors: includeVectors },
      output,
      options.signal,
    );
  }
  /** Back up public scopes to a local restore-only OVPack file. */
  async backupOVPack(
    to: string,
    includeVectors = false,
    options: RequestOptions = {},
  ): Promise<string> {
    const output = await packOutputPath(to, undefined, "openviking-backup");
    return this.downloadToFile(
      "/api/v1/pack/backup",
      { include_vectors: includeVectors },
      output,
      options.signal,
    );
  }
  /** Import a local OVPack file under a parent URI. */
  async importOVPack(
    source: string,
    parent: string,
    options: ImportPackOptions = {},
  ): Promise<string> {
    const local = await nodePathToBlob(source, { allowDirectory: false });
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
    const local = await nodePathToBlob(source, { allowDirectory: false });
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

  cancelTask(taskId: string): Promise<JsonObject> {
    return this.request(
      "POST",
      `/api/v1/tasks/${pathPart(taskId)}/cancel`,
    ) as Promise<JsonObject>;
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
    return this.transport.consume(
      "GET",
      "/health",
      options,
      async (response) =>
        response.ok &&
        ((await response.json()) as { status?: string }).status === "ok",
    );
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
  /** Return whether the observer system reports healthy. */
  async isHealthy(): Promise<boolean> {
    return (await this.getStatus()).is_healthy === true;
  }
  /** Create a filesystem snapshot. */
  gitCommit(options: GitCommitOptions): Promise<JsonObject> {
    return this.request("POST", "/api/v1/snapshot/commit", {
      body: compact({
        message: options.message,
        paths: options.paths,
        branch: options.branch ?? "main",
        author_name: options.authorName,
        author_email: options.authorEmail,
      }),
    });
  }
  /** Restore filesystem content from a previous snapshot. */
  gitRestore(options: GitRestoreOptions): Promise<JsonObject> {
    return this.request("POST", "/api/v1/snapshot/restore", {
      body: compact({
        project_dir: options.projectDir,
        source_commit: options.sourceCommit,
        branch: options.branch ?? "main",
        dry_run: options.dryRun ?? false,
        message: options.message,
        author_name: options.authorName,
        author_email: options.authorEmail,
      }),
    });
  }
  /** Return snapshot metadata or a raw file from a snapshot. */
  gitShow(targetRef: string, path?: string): Promise<JsonObject | GitBlob> {
    return this.transport.consume(
      "GET",
      "/api/v1/snapshot/show",
      { query: { target_ref: targetRef, path } },
      async (response) => {
        if (
          response.ok &&
          response.headers
            .get("content-type")
            ?.startsWith("application/octet-stream")
        ) {
          const bytes = new Uint8Array(await response.arrayBuffer());
          return {
            oid: response.headers.get("x-snapshot-oid") ?? "",
            size: Number(
              response.headers.get("x-snapshot-size") ?? bytes.length,
            ),
            bytes,
          };
        }
        return this.transport.parseResponse<JsonObject>(response);
      },
    );
  }
  /** Return snapshot history from newest to oldest. */
  gitLog(branch = "main", limit = 20, paths?: string[]): Promise<JsonObject[]> {
    return this.request("GET", "/api/v1/snapshot/log", {
      query: { branch, limit, paths },
    });
  }
  /** Compare one file between two snapshot refs. */
  gitDiff(path: string, toRef: string, fromRef?: string): Promise<JsonObject> {
    return this.request("GET", "/api/v1/snapshot/diff", {
      query: {
        path: normalizeURI(path),
        from: fromRef,
        to: toRef,
      },
    });
  }
  /** Return the account `.ovgitignore` content. */
  async gitGetIgnore(): Promise<string> {
    const result = await this.request<unknown>(
      "GET",
      "/api/v1/snapshot/ignore",
    );
    return typeof result === "string" ? result : "";
  }
  /** Set the account `.ovgitignore` content. */
  async gitSetIgnore(content: string): Promise<void> {
    await this.request("PUT", "/api/v1/snapshot/ignore", {
      body: { content },
    });
  }
  /** Delete the account `.ovgitignore` file. */
  async gitDeleteIgnore(): Promise<void> {
    await this.request("DELETE", "/api/v1/snapshot/ignore");
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
  /** Return the effective Agent Evolution switch for the caller's account. */
  adminGetAgentEvolution(): Promise<JsonObject> {
    return this.request("GET", "/api/v1/admin/agent-evolution");
  }
  /** Persist and hot-reload Agent Evolution for the caller's account. */
  adminSetAgentEvolution(enabled: boolean): Promise<JsonObject> {
    return this.request("PUT", "/api/v1/admin/agent-evolution", {
      body: { enabled },
    });
  }
  /** Return effective and explicitly overridden settings for one account. */
  adminGetAccountSettings(accountId: string): Promise<JsonObject> {
    return this.request(
      "GET",
      `/api/v1/admin/accounts/${pathPart(accountId)}/settings`,
    );
  }
  /** Update the allowlisted Agent Evolution setting for one account. */
  adminSetAccountAgentEvolution(
    accountId: string,
    enabled: boolean,
  ): Promise<JsonObject> {
    return this.request(
      "PATCH",
      `/api/v1/admin/accounts/${pathPart(accountId)}/settings`,
      { body: { agent_evolution: { enabled } } },
    );
  }
  /** List trajectories that consumed an Experience. */
  listExperienceTrajectories(
    experienceUri: string,
    options: ExperienceTrajectoryOptions = {},
  ): Promise<JsonObject> {
    return this.request(
      "GET",
      "/api/v1/agent-evolution/experiences/trajectories",
      {
        query: {
          experience_uri: normalizeURI(experienceUri),
          limit: options.limit,
          offset: options.offset,
          start_date: options.startDate,
          end_date: options.endDate,
        },
      },
    );
  }
  /** Return the outcome distribution for an Experience. */
  getExperienceOutcomes(
    experienceUri: string,
    options: ExperienceOutcomeOptions = {},
  ): Promise<JsonObject> {
    return this.request("GET", "/api/v1/agent-evolution/experiences/outcomes", {
      query: {
        experience_uri: normalizeURI(experienceUri),
        start_date: options.startDate,
        end_date: options.endDate,
      },
    });
  }
  /** Parse and validate an OpenViking Assets manifest. */
  resolveOpenVikingAssets(
    manifestYaml: string,
    options: ResolveAssetsOptions = {},
  ): Promise<JsonObject> {
    const body = compact({
      manifest_yaml: manifestYaml,
      catalog_yaml: options.catalogYaml,
      manifest_label: options.manifestLabel,
      catalog_label: options.catalogLabel,
    });
    return this.request("POST", "/api/v1/openviking-assets/resolve", {
      body: mergeExtra(body, options.extra),
    });
  }
  /** Verify read access to one Git asset. */
  preflightOpenVikingAsset(
    name: string,
    repoUrl: string,
    options: PreflightAssetOptions = {},
  ): Promise<JsonObject> {
    const body = compact({
      name,
      connector: "git",
      repo_url: repoUrl,
      branch: options.branch,
      commit: options.commit,
      auth_config: options.authConfig,
    });
    return this.request("POST", "/api/v1/openviking-assets/preflight", {
      body: mergeExtra(body, options.extra),
    });
  }
}
