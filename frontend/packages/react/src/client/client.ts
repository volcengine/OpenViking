import { OvClientError } from './errors.js';
import {
  buildQuery,
  joinUrl,
  mergeIdentityLayers,
  resolveClientOptions,
  resolveIdentityLayer,
  resolveRequestOptions,
} from './options.js';
import type {
  OvClient,
  OvClientOptions,
  OvErrorEnvelope,
  OvRequestSpec,
  OvSuccessEnvelope,
} from './types.js';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readEnvelope(value: unknown): OvErrorEnvelope & OvSuccessEnvelope {
  return isRecord(value) ? value : {};
}

/** 请求被调用方主动取消时保留 `AbortError` 语义，便于调用方区分。 */
function isAbortError(error: unknown): boolean {
  return (
    isRecord(error) &&
    (error.name === 'AbortError' || error.code === 'ABORT_ERR')
  );
}

/**
 * 把传输层的拒绝归一成 {@link OvClientError}。调用方主动取消时保留原始错误，
 * 以便按 `AbortError` 判断；其余网络失败带上原始错误作为 cause。
 *
 * Normalises a transport rejection into {@link OvClientError}. A caller-requested
 * abort keeps the original error so it can be recognised as an `AbortError`;
 * every other network failure carries the original error as its cause.
 */
function normalizeTransportError(error: unknown, signal?: AbortSignal): never {
  if (isAbortError(error) || signal?.aborted) {
    throw error;
  }
  throw new OvClientError({
    code: 'NETWORK_ERROR',
    message: error instanceof Error ? error.message : 'Network request failed',
    cause: error,
  });
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

  const client: OvClient = {
    get baseURL() {
      return defaults.baseURL ?? '';
    },

    async request<TResult>(spec: OvRequestSpec): Promise<TResult> {
      // 默认身份层每次请求只求值一次，父层配置与请求覆盖共用这一份结果。
      const baseLayer = await resolveIdentityLayer(defaults);
      const localLayer = await resolveIdentityLayer(spec.options);
      const parent = resolveClientOptions(defaults, baseLayer);
      const effective = resolveRequestOptions(
        spec.options,
        parent,
        mergeIdentityLayers(baseLayer, localLayer),
      );

      const url =
        joinUrl(effective.baseURL, spec.path) + buildQuery(spec.query);

      const hasBody = spec.body !== undefined;
      const headers = hasBody
        ? { 'content-type': 'application/json', ...effective.headers }
        : effective.headers;

      let response: Response;
      try {
        response = await fetchImpl(url, {
          method: spec.method,
          headers,
          body: hasBody ? JSON.stringify(spec.body) : undefined,
          credentials: effective.credentials,
          signal: effective.signal,
        });
      } catch (error) {
        normalizeTransportError(error, effective.signal);
      }

      // 先读文本再解析：读取失败是传输失败（含取消），必须上报；只有解析失败
      // 才是「响应不是 JSON」，此时按无正文处理。
      let raw: string;
      try {
        raw = await response.text();
      } catch (error) {
        normalizeTransportError(error, effective.signal);
      }

      let body: unknown;
      if (raw.trim()) {
        try {
          body = JSON.parse(raw);
        } catch {
          // 不是 JSON 就按原文返回，不把二进制或纯文本响应强转成对象。
          body = raw;
        }
      }

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
