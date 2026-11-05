import type {
  OvClientOptions,
  OvQueryValue,
  OvRequestOptions,
  OvResolvedOptions,
} from './types.js';

/**
 * 身份字段到请求头的映射。宿主传字段值，Client 负责组装，不暴露 header 名称。
 * Mapping from identity fields to request headers. A host passes field values and
 * the Client assembles the headers, so header names stay internal.
 */
const IDENTITY_HEADERS = {
  apiKey: 'X-API-Key',
  accountId: 'X-OpenViking-Account',
  userId: 'X-OpenViking-User',
  resourceId: 'V-Resource-Id',
} as const;

/** 求值一个身份字段，兼容函数形式。 */
async function evaluate(
  value: string | (() => string | Promise<string>) | undefined,
): Promise<string | undefined> {
  if (value === undefined) return undefined;
  const resolved = typeof value === 'function' ? await value() : value;
  return resolved.trim() ? resolved : undefined;
}

/**
 * 把一层身份字段求值成请求头。空字符串视为未提供，不发送该请求头。
 * Evaluates one layer of identity fields into headers. An empty string counts as
 * absent and the header is not sent.
 */
export async function resolveIdentityHeaders(
  options: OvRequestOptions | undefined,
): Promise<Record<string, string>> {
  const out: Record<string, string> = {};
  for (const [field, header] of Object.entries(IDENTITY_HEADERS)) {
    const value = await evaluate(
      options?.[field as keyof typeof IDENTITY_HEADERS],
    );
    if (value !== undefined) out[header.toLowerCase()] = value;
  }
  return out;
}

/**
 * 逐字段解析配置：本层 → 上层 → 默认值。`baseURL: ''` 是明确取值，只有
 * `undefined` 才表示继承。
 *
 * Resolves configuration field by field: this layer, then the layer above, then
 * the default. `baseURL: ''` is an explicit value; only `undefined` inherits.
 */
export function resolveOptions(
  local: OvRequestOptions | undefined,
  parent: OvResolvedOptions,
  localHeaders: Record<string, string>,
): OvResolvedOptions {
  return {
    baseURL: parent.baseURL,
    credentials: parent.credentials,
    headers: { ...parent.headers, ...localHeaders },
    signal: local?.signal ?? parent.signal,
  };
}

/**
 * 解析客户端级配置。`baseURL` 缺省为空字符串，表示浏览器同源请求。
 * Resolves client-level configuration. A missing `baseURL` is an empty string,
 * meaning a same-origin browser request.
 */
export async function resolveClientOptions(
  options: OvClientOptions,
): Promise<OvResolvedOptions> {
  return {
    baseURL: options.baseURL ?? '',
    credentials: options.credentials ?? 'same-origin',
    headers: await resolveIdentityHeaders(options),
    signal: options.signal,
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
