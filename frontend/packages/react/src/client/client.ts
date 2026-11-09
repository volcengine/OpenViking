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
  isAbortError,
  normalizeTransportError,
  parseJson,
  readBlob,
  readText,
  readEnvelope,
  throwIfErrorEnvelope,
  toClientError,
} from './response.js';
import { decodeEventStream } from './sse.js';
import type {
  OvClient,
  OvClientOptions,
  OvRequestOptions,
  OvRequestSpec,
  OvResolvedOptions,
  OvStream,
  OvStreamEvent,
  OvStreamSpec,
} from './types.js';

/** 流式响应要求的 content-type。The content type a streamed response must carry. */
const EVENT_STREAM = 'text/event-stream';

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
  return {
    url: joinUrl(effective.baseURL, spec.path) + buildQuery(spec.query),
    init: {
      method: spec.method,
      headers: hasBody
        ? { 'content-type': 'application/json', ...headers }
        : headers,
      body: hasBody ? JSON.stringify(spec.body) : undefined,
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
  ): Promise<{ response: Response; effective: OvResolvedOptions }> {
    const effective = await resolveFor(defaults, spec.options);
    const { url, init } = buildRequest(effective, spec, {
      ...headers,
      ...effective.headers,
    });
    const active = signal ?? effective.signal;
    try {
      return {
        response: await fetchImpl(url, { ...init, signal: active }),
        effective,
      };
    } catch (error) {
      normalizeTransportError(error, active);
    }
  }

  const client: OvClient = {
    get baseURL() {
      return defaults.baseURL ?? '';
    },

    async request<TResult>(spec: OvRequestSpec): Promise<TResult> {
      const { response, effective } = await send(spec);

      if (spec.responseType === 'blob') {
        if (!response.ok) {
          // 失败响应仍是 JSON 外壳，先解析才能保留服务端的错误码。
          const raw = await readText(response, effective.signal);
          throw toClientError(response, parseJson(raw) ?? raw);
        }
        return (await readBlob(response, effective.signal)) as TResult;
      }

      // 先读文本再解析：读取失败是传输失败（含取消），必须上报；只有解析失败
      // 才是「响应不是 JSON」，此时按原文返回。
      const raw = await readText(response, effective.signal);
      const body = parseJson(raw) ?? (raw.trim() ? raw : undefined);

      if (!response.ok) throw toClientError(response, body);
      throwIfErrorEnvelope(response, body);

      const envelope = readEnvelope(body);
      return ('result' in envelope ? envelope.result : body) as TResult;
    },

    async stream(spec: OvStreamSpec): Promise<OvStream> {
      // 自己的控制器与调用方的 signal 联动：`abort()` 与调用方取消都能停掉
      // 连接，任一方触发即中止。
      const controller = new AbortController();
      // 取消时唤醒正在等待的读取：底层读取不一定随 signal 结束，靠它自己返回
      // 可能永远等下去。
      let releaseAbort: (() => void) | undefined;
      const aborted = new Promise<'aborted'>((resolve) => {
        releaseAbort = () => resolve('aborted');
      });

      const stop = () => {
        releaseAbort?.();
        controller.abort();
      };
      const callerSignal = spec.options?.signal;
      const forward = () => stop();
      if (callerSignal?.aborted) forward();
      else callerSignal?.addEventListener('abort', forward, { once: true });

      const { response } = await send(
        spec,
        { accept: EVENT_STREAM, ...spec.headers },
        controller.signal,
      );

      if (!response.ok) {
        const raw = await readText(response, controller.signal);
        throw toClientError(response, parseJson(raw) ?? raw);
      }

      const contentType = response.headers.get('content-type') ?? '';
      if (!contentType.includes(EVENT_STREAM) || !response.body) {
        // 服务端可能用 JSON 外壳报告失败，例如代理层在建立流之前就拒绝了请求。
        const body = parseJson(await readText(response, controller.signal));
        if (body !== undefined) throwIfErrorEnvelope(response, body);
        throw new OvClientError({
          code: 'UNEXPECTED_CONTENT_TYPE',
          message: `Expected ${EVENT_STREAM}, got ${contentType || 'no content type'}`,
          statusCode: response.status,
          requestId: response.headers.get('x-request-id') ?? undefined,
          responseBody: body,
        });
      }

      const reader = decodeEventStream(response.body)[Symbol.asyncIterator]();

      // 取消是调用方的意图，不是失败：迭代安静结束，调用点不必再吞一次
      // AbortError。连接已断时的读取错误也在此时一并忽略。
      async function* iterate(): AsyncGenerator<OvStreamEvent> {
        try {
          for (;;) {
            const next = await Promise.race([reader.next(), aborted]);
            if (next === 'aborted') return;
            if (next.done) return;
            yield next.value;
          }
        } catch (error) {
          if (isAbortError(error)) return;
          throw error;
        }
      }

      return {
        abort: () => {
          stop();
          void Promise.resolve(reader.return?.(undefined)).catch(() => {});
        },
        [Symbol.asyncIterator]: iterate,
      };
    },
  };

  return client;
}
