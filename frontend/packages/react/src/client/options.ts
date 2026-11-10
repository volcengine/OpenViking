import type {
  OvClientOptions,
  OvIdentityValue,
  OvQueryValue,
  OvRequestOptions,
  OvResolvedOptions,
} from './types.js';

/**
 * 身份字段到请求头的映射。宿主传字段值，Client 负责组装，不暴露 header 名称。
 * Mapping from identity fields to request headers. A host passes field values and
 * the Client assembles the headers, so header names stay internal.
 */
export const IDENTITY_HEADERS = {
  apiKey: 'X-API-Key',
  accountId: 'X-OpenViking-Account',
  userId: 'X-OpenViking-User',
  resourceId: 'V-Resource-Id',
} as const;

export type OvIdentityField = keyof typeof IDENTITY_HEADERS;

/** 求值一个身份字段，兼容函数形式。 */
async function evaluate(
  value: string | (() => string | Promise<string>) | undefined,
): Promise<string | undefined> {
  if (value === undefined) return undefined;
  return typeof value === 'function' ? await value() : value;
}

/**
 * 求值一层身份字段，保留空字符串，以便区分「未提供」与「显式清除」。
 * Evaluates one identity layer, keeping empty strings so that "absent" and
 * "explicitly cleared" stay distinguishable.
 */
export async function resolveIdentityLayer(
  options: OvRequestOptions | undefined,
): Promise<OvIdentityLayer> {
  const layer: OvIdentityLayer = {};
  for (const field of Object.keys(IDENTITY_HEADERS) as OvIdentityField[]) {
    const value = await evaluate(options?.[field]);
    if (value !== undefined) layer[field] = value;
  }
  return layer;
}

/**
 * 合并两层身份字段：近的一层覆盖远的一层，近层显式传空字符串即清除该字段。
 * Merges two identity layers: the nearer one wins, and an explicit empty string
 * in it clears the field.
 */
export function mergeIdentityLayers(
  far: OvIdentityLayer,
  near: OvIdentityLayer,
): OvIdentityLayer {
  return { ...far, ...near };
}

/**
 * 把身份字段转成请求头。空字符串表示清除，不发送对应请求头。
 * Turns identity fields into headers. An empty string means cleared, so that
 * header is not sent.
 */
export function toHeaders(layer: OvIdentityLayer): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of Object.keys(IDENTITY_HEADERS) as OvIdentityField[]) {
    const value = layer[field]?.trim();
    if (value) out[IDENTITY_HEADERS[field].toLowerCase()] = value;
  }
  return out;
}

/**
 * 解析客户端级配置。`baseURL` 缺省为空字符串，表示浏览器同源请求。
 *
 * 身份层由调用方求值后传入：函数形式的身份字段每次请求只应求值一次，父层配置
 * 与请求覆盖必须复用同一份结果，否则会重复刷新凭证并可能发出旧值。
 *
 * Resolves client-level configuration. A missing `baseURL` is an empty string,
 * meaning a same-origin browser request.
 *
 * The caller passes an already-evaluated identity layer: a function-form
 * identity field must be evaluated once per request, and both the parent
 * configuration and the request override must reuse that one result. Evaluating
 * twice would refresh a credential twice and could send the older value.
 */
export function resolveClientOptions(
  options: OvClientOptions,
  identity: OvIdentityLayer,
): OvResolvedOptions {
  return {
    baseURL: options.baseURL ?? '',
    credentials: options.credentials ?? 'include',
    headers: toHeaders(identity),
    signal: options.signal,
  };
}

/**
 * 在已解析的客户端配置上叠加一次请求的覆盖。`baseURL` 传空字符串表示同源，
 * 只有 `undefined` 才继承。
 *
 * Applies one request's override on top of resolved client configuration. An
 * empty `baseURL` means same origin; only `undefined` inherits.
 */
export function resolveRequestOptions(
  local: OvRequestOptions | undefined,
  parent: OvResolvedOptions,
  mergedLayer: OvIdentityLayer,
): OvResolvedOptions {
  const headers: Record<string, string> = { ...parent.headers };

  // 逐字段写入或删除：显式空字符串要移除父层继承来的请求头，否则清空凭据这类
  // 操作不会生效。
  for (const field of Object.keys(IDENTITY_HEADERS) as OvIdentityField[]) {
    const value = mergedLayer[field];
    if (value === undefined) continue;
    const name = IDENTITY_HEADERS[field].toLowerCase();
    if (value.trim()) headers[name] = value;
    else delete headers[name];
  }

  return {
    baseURL: local?.baseURL ?? parent.baseURL,
    credentials: parent.credentials,
    headers,
    signal: local?.signal ?? parent.signal,
  };
}

/**
 * 拼接服务地址与接口路径。拼接只在这里做：`/gateway/ov` 加 `/api/v1/fs/ls`
 * 得到 `/gateway/ov/api/v1/fs/ls`，接口路径以 `/` 开头也不会丢掉前缀。
 *
 * Joins the service address and the API path. Joining happens only here:
 * `/gateway/ov` plus `/api/v1/fs/ls` yields `/gateway/ov/api/v1/fs/ls`, and a
 * leading `/` in the path never drops the prefix.
 */
export function joinUrl(baseURL: string, path: string): string {
  const prefix = baseURL.replace(/\/+$/, '');
  return `${prefix}${path.startsWith('/') ? path : `/${path}`}`;
}

/**
 * 把查询参数序列化为查询串，跳过 `undefined` 与 `null`，数组按键重复。
 * Serialises query parameters, skipping `undefined` and `null`; arrays repeat
 * the key.
 */
export function buildQuery(
  query: Readonly<Record<string, OvQueryValue>> | undefined,
): string {
  if (!query) return '';
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, String(item));
      continue;
    }
    params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : '';
}

/**
 * 一层已求值的身份字段：`undefined` 表示未提供，空字符串表示显式清除，其余
 * 为要发送的值。
 * One evaluated identity layer: `undefined` means absent, an empty string means
 * explicitly cleared, anything else is the value to send.
 */
export type OvIdentityLayer = Partial<Record<OvIdentityField, string>>;

/**
 * 一层未求值的身份字段，供 Provider 逐层合并。函数形式原样保留，由客户端在
 * 每次请求时求值。
 * One unevaluated identity layer for Provider merging. Function values are kept
 * as-is so the client evaluates them per request.
 */
export type OvIdentityConfig = Partial<
  Record<OvIdentityField, OvIdentityValue>
>;

/** Provider 已解析的配置，供嵌套 Provider 继续继承。 */
export interface OvProviderBase {
  baseURL?: string;
  credentials?: RequestCredentials;
  signal?: AbortSignal;
  /** 传输层覆盖。内层未声明时继承外层，否则内层会丢掉外层的自定义 fetch。 */
  fetch?: typeof globalThis.fetch;
  identity: OvIdentityConfig;
}

/**
 * 把本层配置叠加到父层之上。只覆盖本层显式声明的字段：`undefined` 表示继承，
 * 空字符串表示清除继承到的值。
 *
 * Applies one layer of configuration on top of its parent. Only fields this
 * layer declares are overridden: `undefined` inherits, and an empty string
 * clears the inherited value.
 */
export function mergeProviderConfig(
  inherited: OvProviderBase | undefined,
  local: OvClientOptions,
): OvProviderBase {
  const declared: OvIdentityConfig = {};
  if (local.apiKey !== undefined) declared.apiKey = local.apiKey;
  if (local.accountId !== undefined) declared.accountId = local.accountId;
  if (local.userId !== undefined) declared.userId = local.userId;
  if (local.resourceId !== undefined) declared.resourceId = local.resourceId;

  return {
    baseURL: local.baseURL ?? inherited?.baseURL,
    credentials: local.credentials ?? inherited?.credentials,
    signal: local.signal ?? inherited?.signal,
    fetch: local.fetch ?? inherited?.fetch,
    identity: { ...inherited?.identity, ...declared },
  };
}
