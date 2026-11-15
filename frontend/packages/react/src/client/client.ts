import {
  observation,
  type ObservedSpec,
} from '../shared/monitoring/request-observer.js';
import { OvClientError } from './errors.js';
import {
  buildQuery,
  joinUrl,
  mergeIdentityLayers,
  resolveClientOptions,
  resolveIdentityLayer,
  resolveRequestOptions,
} from './options.js';
import {
  normalizeTransportError,
  parseJson,
  readBlob,
  readText,
  readEnvelope,
  throwIfErrorEnvelope,
  toClientError,
} from './response.js';
import { openStream } from './stream.js';
import type {
  OvClient,
  OvClientOptions,
  OvRequestOptions,
  OvRequestSpec,
  OvResolvedOptions,
  OvResponseEnvelope,
  OvStream,
  OvStreamSpec,
} from './types.js';

/**
 * 逐字段解析本次请求生效的配置。身份层每次请求只求值一次，父层与请求覆盖共用
 * 同一份结果，避免重复刷新凭证。
 *
 * Resolves the configuration in effect for one request field by field. The
 * identity layer is evaluated once per request and shared by the parent and the
 * override, so a credential is not refreshed twice.
 */
async function resolveFor(
  defaults: OvClientOptions,
  override: OvRequestOptions | undefined,
): Promise<OvResolvedOptions> {
  const baseLayer = await resolveIdentityLayer(defaults);
  const localLayer = await resolveIdentityLayer(override);
  const parent = resolveClientOptions(defaults, baseLayer);
  return resolveRequestOptions(
    override,
    parent,
    mergeIdentityLayers(baseLayer, localLayer),
  );
}

/** 组装请求地址与初始化参数。Builds the request URL and init. */
function buildRequest(
  effective: OvResolvedOptions,
  spec: Pick<OvRequestSpec, 'method' | 'path' | 'query' | 'body'>,
  headers: Record<string, string>,
): { url: string; init: RequestInit } {
  const hasBody = spec.body !== undefined;
  const multipart = spec.body instanceof FormData;
  const requestHeaders = { ...headers };
  if (multipart) {
    for (const key of Object.keys(requestHeaders))
      if (key.toLowerCase() === 'content-type') delete requestHeaders[key];
  }
  return {
    url: joinUrl(effective.baseURL, spec.path) + buildQuery(spec.query),
    init: {
      method: spec.method,
      headers:
        hasBody && !multipart
          ? { 'content-type': 'application/json', ...requestHeaders }
          : requestHeaders,
      body: multipart
        ? (spec.body as FormData)
        : hasBody
          ? JSON.stringify(spec.body)
          : undefined,
      credentials: effective.credentials,
    },
  };
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

  async function send(
    spec: OvRequestSpec | OvStreamSpec,
    headers?: Record<string, string>,
    signal?: AbortSignal,
    /**
     * 已经解析好的配置。流式请求要先解析一次用于接上调用方 signal，再交给这里发
     * 送；不复用就会把身份字段求值两遍——函数形式的凭证因此可能被刷新两次，第二
     * 次甚至可能失败。
     *
     * Configuration already resolved. A streaming request resolves it once to link
     * the caller's signal and then hands it here; without reuse the identity fields
     * are evaluated twice, so a function-valued credential may refresh twice and the
     * second evaluation can even fail.
     */
    resolved?: OvResolvedOptions,
  ): Promise<{ response: Response; effective: OvResolvedOptions }> {
    const observer = observation(spec as ObservedSpec);
    const effective = resolved ?? (await resolveFor(defaults, spec.options));
    const { url, init } = buildRequest(effective, spec, {
      ...headers,
      ...effective.headers,
    });
    const active = signal ?? effective.signal;
    if (observer) {
      observer.phase = 'send';
      observer.signal = active;
    }
    try {
      const response = await fetchImpl(url, { ...init, signal: active });
      observer?.response(response);
      return { response, effective };
    } catch (error) {
      normalizeTransportError(error, active);
    }
  }

  /** 发一次 JSON 请求，返回 `result` 与外壳里的分页标记。 */
  async function requestJson<TResult>(
    spec: OvRequestSpec,
  ): Promise<OvResponseEnvelope<TResult>> {
    const { response, effective } = await send(spec);

    // 先读文本再解析：读取失败是传输失败（含取消），必须上报；只有解析失败
    // 才是「响应不是 JSON」，此时按原文返回。
    //
    // `parseJson` 用 `undefined` 表示「不是 JSON」，合法的 `null` 要保留：
    // 否则响应体为 `null` 时会被替换成字符串 `"null"`，调用方拿到的类型就变了。
    //
    // Read the text before parsing: a read failure is a transport failure (including
    // an abort) and must surface, while only a parse failure means "not JSON", in
    // which case the raw text is returned.
    //
    // `parseJson` reports "not JSON" with `undefined`, so a legitimate `null` is
    // kept: otherwise a `null` body becomes the string `"null"` and the caller's
    // type is silently different.
    const observer = observation(spec as ObservedSpec);
    if (observer) observer.phase = 'read-body';
    const raw = await readText(response, effective.signal);
    observer?.capture(raw);
    const parsed = parseJson(raw);
    const body = parsed !== undefined ? parsed : raw.trim() ? raw : undefined;

    if (observer) observer.phase = 'business-envelope';
    if (!response.ok) throw toClientError(response, body);
    throwIfErrorEnvelope(response, body, observer?.business);

    const envelope = readEnvelope(body);
    return {
      result: ('result' in envelope ? envelope.result : body) as TResult,
      hasMore: envelope.has_more === true,
    };
  }

  const client: OvClient = {
    get baseURL() {
      return defaults.baseURL ?? '';
    },

    async request<TResult>(spec: OvRequestSpec): Promise<TResult> {
      if (spec.responseType === 'blob') {
        const { response, effective } = await send(spec);
        const observer = observation(spec as ObservedSpec);
        if (observer) observer.phase = 'read-body';
        if (!response.ok) {
          // 失败响应仍是 JSON 外壳，先解析才能保留服务端的错误码。
          const raw = await readText(response, effective.signal);
          observer?.capture(raw);
          if (observer) observer.phase = 'business-envelope';
          throw toClientError(response, parseJson(raw) ?? raw);
        }
        return (await readBlob(response, effective.signal)) as TResult;
      }

      const { result } = await requestJson<TResult>(spec);
      return result;
    },

    requestEnvelope<TResult>(
      spec: OvRequestSpec,
    ): Promise<OvResponseEnvelope<TResult>> {
      if (spec.responseType === 'blob') {
        return Promise.reject(
          new OvClientError({
            code: 'INVALID_REQUEST',
            message: 'requestEnvelope does not support responseType: blob',
          }),
        );
      }
      return requestJson<TResult>(spec);
    },

    stream(spec: OvStreamSpec): Promise<OvStream> {
      return openStream(spec, { send, resolveFor, defaults });
    },
  };

  return client;
}
