/** Arbitrary JSON object returned by APIs without a dedicated result type. */
export type JsonObject = Record<string, unknown>;
/** One target URI or multiple target scopes. */
export type TargetURI = string | string[];
/** Header values accepted without requiring the DOM-only `HeadersInit` alias. */
export type ClientHeaders =
  Headers | Record<string, string> | [string, string][];
/** Temporary upload storage mode supported by the OpenViking server. */
export type UploadMode = "local" | "shared";
/** Conflict policy accepted when importing an OVPack. */
export type PackConflictPolicy = "fail" | "overwrite" | "skip";
/** Vector handling strategy accepted when importing an OVPack. */
export type PackVectorMode = "auto" | "recompute" | "require";

/** Connection, identity and transport configuration. */
export interface ClientConfig {
  baseUrl: string;
  apiKey?: string;
  account?: string;
  user?: string;
  actorPeerId?: string;
  timeout?: number;
  headers?: ClientHeaders;
  fetch?: typeof globalThis.fetch;
  profile?: boolean;
  uploadMode?: UploadMode;
}

/** A Node.js local path or remote URL accepted by resource APIs. */
export type UploadSource = string;

/** Options shared by OVPack import and restore operations. */
export interface ImportPackOptions {
  onConflict?: PackConflictPolicy;
  vectorMode?: PackVectorMode;
}

/** Options for creating a filesystem snapshot. */
export interface GitCommitOptions {
  message: string;
  paths?: string[];
  branch?: string;
  authorName?: string;
  authorEmail?: string;
}
/** Options for restoring a filesystem snapshot. */
export interface GitRestoreOptions {
  sourceCommit: string;
  projectDir?: string;
  branch?: string;
  dryRun?: boolean;
  message?: string;
  authorName?: string;
  authorEmail?: string;
}
/** Raw file returned by a snapshot lookup. */
export interface GitBlob {
  oid: string;
  size: number;
  bytes: Uint8Array;
}

/** Per-request cancellation options. */
export interface RequestOptions {
  signal?: AbortSignal;
}
/** Options shared by asynchronous processing APIs. */
export interface WaitOptions {
  wait?: boolean;
  timeout?: number;
  telemetry?: unknown;
}
/** Resource import options. */
export interface AddResourceOptions extends WaitOptions {
  to?: string;
  parent?: string;
  reason?: string;
  instruction?: string;
  strict?: boolean;
  ignoreDirs?: string;
  include?: string;
  exclude?: string;
  directlyUploadMedia?: boolean;
  preserveStructure?: boolean;
  watchInterval?: number;
  args?: JsonObject;
}
/** Semantic retrieval options. */
export interface SearchOptions {
  targetUri?: TargetURI;
  image?: string;
  sessionId?: string;
  limit?: number;
  nodeLimit?: number;
  scoreThreshold?: number;
  filter?: JsonObject;
  contextType?: unknown;
  telemetry?: unknown;
  since?: string;
  until?: string;
  timeField?: string;
  level?: number[];
  tags?: string[];
  includeProvenance?: boolean;
}
/** Content grep options. */
export interface GrepOptions {
  caseInsensitive?: boolean;
  nodeLimit?: number;
  levelLimit?: number;
  excludeUri?: string;
}
/** Directory listing options. */
export interface ListOptions {
  simple?: boolean;
  recursive?: boolean;
  output?: string;
  absLimit?: number;
  showAllHidden?: boolean;
  nodeLimit?: number;
  sortBy?: "name" | "mtime";
  sortOrder?: "asc" | "desc";
}
/** Directory tree options. */
export interface TreeOptions {
  output?: string;
  absLimit?: number;
  showAllHidden?: boolean;
  nodeLimit?: number;
}
/** Session message payload. */
export interface Message {
  role: string;
  content?: string;
  parts?: JsonObject[];
  createdAt?: string;
  peerId?: string;
  telemetry?: unknown;
}
/** Session creation options. */
export interface CreateSessionOptions {
  sessionId?: string;
  memoryPolicy?: JsonObject;
  telemetry?: unknown;
}
/** Background task filters. */
export interface TaskListOptions {
  taskType?: string;
  status?: string;
  resourceId?: string;
  limit?: number;
}
/** Options for retrieving an installed skill. */
export interface GetSkillOptions {
  includeContent?: boolean;
  includeFiles?: boolean;
  includeSource?: boolean;
  level?: number;
  targetUri?: string;
}
/** Fields that can be changed on a watch task. */
export interface UpdateWatchOptions {
  watchInterval?: number;
  isActive?: boolean;
  reason?: string;
  instruction?: string;
}
/** Grouped semantic retrieval results. */
export interface FindResult {
  memories?: unknown[];
  resources?: unknown[];
  skills?: unknown[];
  provenance?: JsonObject[];
  [key: string]: unknown;
}
/** Error payload returned by OpenViking. */
export interface APIErrorInfo {
  code?: string;
  message?: string;
  details?: JsonObject;
}
/** Standard OpenViking HTTP response envelope. */
export interface ResponseEnvelope<T> {
  status?: string;
  result?: T;
  error?: APIErrorInfo;
  telemetry?: unknown;
  profile?: string[];
  detail?: unknown;
}
