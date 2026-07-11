/** Arbitrary JSON object returned by APIs without a dedicated result type. */
export type JsonObject = Record<string, unknown>;
/** One target URI or multiple target scopes. */
export type TargetURI = string | string[];

/** Connection, identity and transport configuration. */
export interface ClientConfig {
  baseUrl: string;
  apiKey?: string;
  account?: string;
  user?: string;
  actorPeerId?: string;
  timeout?: number;
  headers?: HeadersInit;
  fetch?: typeof globalThis.fetch;
  profile?: boolean;
}

/** A browser file/blob, a Node.js local path, a remote URL, or inline skill data. */
export type UploadSource = string | Blob;

/** Options shared by OVPack import and restore operations. */
export interface ImportPackOptions { onConflict?: string; vectorMode?: string; }

/** Per-request cancellation options. */
export interface RequestOptions { signal?: AbortSignal; }
/** Options shared by asynchronous processing APIs. */
export interface WaitOptions { wait?: boolean; timeout?: number; telemetry?: unknown; }
/** Resource import options. */
export interface AddResourceOptions extends WaitOptions {
  to?: string; parent?: string; reason?: string; instruction?: string; strict?: boolean;
  ignoreDirs?: string; include?: string; exclude?: string; directlyUploadMedia?: boolean;
  preserveStructure?: boolean; watchInterval?: number; args?: JsonObject;
}
/** Semantic retrieval options. */
export interface SearchOptions {
  targetUri?: TargetURI; image?: string | Blob; sessionId?: string; limit?: number;
  nodeLimit?: number; scoreThreshold?: number; filter?: JsonObject; contextType?: unknown;
  telemetry?: unknown; since?: string; until?: string; timeField?: string; level?: number[]; tags?: string[];
}
/** Content grep options. */
export interface GrepOptions { caseInsensitive?: boolean; nodeLimit?: number; levelLimit?: number; excludeUri?: string; }
/** Directory listing options. */
export interface ListOptions { simple?: boolean; recursive?: boolean; output?: string; absLimit?: number; showAllHidden?: boolean; nodeLimit?: number; }
/** Directory tree options. */
export interface TreeOptions { output?: string; absLimit?: number; showAllHidden?: boolean; nodeLimit?: number; }
/** Session message payload. */
export interface Message { role: string; content?: string; parts?: JsonObject[]; createdAt?: string; peerId?: string; telemetry?: unknown; }
/** Session creation options. */
export interface CreateSessionOptions { sessionId?: string; memoryPolicy?: JsonObject; telemetry?: unknown; }
/** Background task filters. */
export interface TaskListOptions { taskType?: string; status?: string; resourceId?: string; limit?: number; }
/** Grouped semantic retrieval results. */
export interface FindResult { memories?: unknown[]; resources?: unknown[]; skills?: unknown[]; [key: string]: unknown; }
/** Error payload returned by OpenViking. */
export interface APIErrorInfo { code?: string; message?: string; details?: JsonObject; }
/** Standard OpenViking HTTP response envelope. */
export interface ResponseEnvelope<T> { status?: string; result?: T; error?: APIErrorInfo; telemetry?: unknown; profile?: string[]; detail?: unknown; }
