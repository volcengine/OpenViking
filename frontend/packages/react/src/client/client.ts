import { OvClientError } from './errors.js';
import {
  buildQuery,
  joinUrl,
  resolveClientOptions,
  resolveIdentityHeaders,
  resolveOptions,
} from './options.js';
import type {
  OvClient,
  OvClientOptions,
  OvErrorEnvelope,
  OvRequestSpec,
  OvResolvedOptions,
  OvSuccessEnvelope,
} from './types.js';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readEnvelope(value: unknown): OvErrorEnvelope & OvSuccessEnvelope {
  return isRecord(value) ? value : {};
}

/**
 * 用 `fetch` 实现 {@link OvClient}。这是库自带的默认传输层，调用方只需给服务
 * 地址和身份字段；要换传输方式（例如复用宿主已有的 axios 实例）时才需要自己
 * 实现 {@link OvClient}。
 *
 * Implements {@link OvClient} with `fetch`. This is the library's default
 * transport, so a caller supplies only the service address and identity fields.
 * Implement {@link OvClient} yourself only to replace the transport, for example
 * to reuse the host's existing axios instance.
 */
export function createOvClient(options: OvClientOptions = {}): OvClient {
  const fetchImpl = options.fetch ?? globalThis.fetch;
  if (!fetchImpl) {
    throw new Error(
      'createOvClient needs a fetch implementation. Pass `fetch` on platforms without a global one.',
    );
  }

  // 只记录原始配置：函数形式的身份字段每次请求都要重新求值，因此不能缓存求值
  // 结果。
  const defaults = options;
  let resolved: OvResolvedOptions | undefined;

  const client: OvClient = {
    get baseURL() {
      return defaults.baseURL ?? '';
    },

    async request<TResult>(spec: OvRequestSpec): Promise<TResult> {
      // 客户端级配置求值一次即可复用，除非身份字段是函数。
      resolved ??= await resolveClientOptions(defaults);
      const parent: OvResolvedOptions =
        typeof defaults.apiKey === 'function'
          ? await resolveClientOptions(defaults)
          : resolved;

      const localHeaders = await resolveIdentityHeaders(spec.options);
      const effective = resolveOptions(spec.options, parent, localHeaders);

      const url =
        joinUrl(effective.baseURL, spec.path) + buildQuery(spec.query);

      const response = await fetchImpl(url, {
        method: spec.method,
        headers: effective.headers,
        body: spec.body === undefined ? undefined : JSON.stringify(spec.body),
        credentials: effective.credentials,
        signal: effective.signal,
      });

      const body: unknown = await response.json().catch(() => undefined);
      const envelope = readEnvelope(body);
      const requestId = response.headers.get('x-request-id') ?? undefined;

      if (!response.ok) {
        throw new OvClientError({
          code: envelope.error?.code ?? `HTTP_${response.status}`,
          message: envelope.error?.message ?? response.statusText,
          statusCode: response.status,
          details: envelope.error?.details,
          requestId,
          responseBody: body,
        });
      }

      // 原生接口在 HTTP 200 里用 status: 'error' 表达业务失败。
      if (envelope.status === 'error') {
        throw new OvClientError({
          code: envelope.error?.code ?? 'UNKNOWN',
          message: envelope.error?.message ?? 'Request failed',
          statusCode: response.status,
          details: envelope.error?.details,
          requestId,
          responseBody: body,
        });
      }

      return ('result' in envelope ? envelope.result : body) as TResult;
    },
  };

  return client;
}
