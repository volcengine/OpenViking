/**
 * 一个身份字段的取值：字符串，或每次请求重新求值的函数。
 * Value of one identity field: a string, or a function re-evaluated per request.
 */
export type OvIdentityValue = string | (() => string | Promise<string>);

/**
 * 一次请求的身份与作用域。字段省略表示继承上层配置；显式传空字符串表示移除
 * 该字段继承到的值，请求里不再发送对应请求头。
 *
 * 这些字段一一对应 OpenViking 的请求头，宿主传值即可，不需要自己组装 header：
 * `apiKey` → `X-API-Key`，`accountId` → `X-OpenViking-Account`，`userId` →
 * `X-OpenViking-User`，`resourceId` → `V-Resource-Id`。
 *
 * Identity and scope of one request. An omitted field inherits from the layer
 * above; an explicit empty string removes the inherited value so the header is
 * not sent.
 *
 * The fields map one to one onto OpenViking's request headers, so a host passes
 * values instead of assembling headers: `apiKey` → `X-API-Key`, `accountId` →
 * `X-OpenViking-Account`, `userId` → `X-OpenViking-User`, `resourceId` →
 * `V-Resource-Id`.
 */
export interface OvRequestOptions {
  /**
   * 服务地址及可选网关前缀，不含原生接口的 `/api/v1`。省略时继承上层配置，
   * 空字符串表示同源请求。
   *
   * Service address with an optional gateway prefix, excluding the native
   * `/api/v1`. Omitted inherits the layer above; an empty string means a
   * same-origin request.
   */
  baseURL?: string;
  /**
   * 凭证。开源版为 `X-API-Key` 的取值；商业版由浏览器 Cookie 承载，无需传入。
   * 函数形式每次请求重新求值，可读取轮换后的凭证。
   *
   * Credential. In the open-source edition it becomes `X-API-Key`; the
   * commercial edition carries it in the browser cookie, so it is omitted
   * there. A function is re-evaluated on every request, so it can read a rotated
   * credential.
   */
  apiKey?: OvIdentityValue;
  /**
   * OpenViking 账号 ID，对应数据空间。
   * OpenViking account ID, which corresponds to the data space.
   */
  accountId?: OvIdentityValue;
  /**
   * 账号内的用户 ID。
   * User ID within the account.
   */
  userId?: OvIdentityValue;
  /**
   * 当前库 ID。数据面接口需要它确定请求落在哪个库。
   * Current library ID. Data-plane requests need it to know which library they
   * address.
   */
  resourceId?: OvIdentityValue;
  /**
   * 调用方中止请求的信号。
   * Signal the caller uses to abort the request.
   */
  signal?: AbortSignal;
}

/**
 * 创建客户端时的配置。在 {@link OvRequestOptions} 之上增加 Cookie 策略与传输层
 * 设置。
 * Configuration when creating a client: {@link OvRequestOptions} plus the cookie
 * policy and transport settings.
 */
export interface OvClientOptions extends OvRequestOptions {
  /**
   * 是否携带浏览器 Cookie。默认 `include`，跨源请求也允许携带 Cookie；
   * 可显式覆盖为 `same-origin` 或 `omit`。仍遵循服务端 CORS 与浏览器 Cookie 策略。
   *
   * Whether browser cookies are sent. Defaults to `include`, including for
   * cross-origin requests. Override with `same-origin` or `omit` as needed.
   * Server CORS and browser cookie policies still apply.
   */
  credentials?: RequestCredentials;
  /**
   * 自定义 fetch 实现，用于测试或在没有全局 fetch 的环境运行。
   * A custom fetch implementation, for tests or environments without a global
   * fetch.
   */
  fetch?: typeof globalThis.fetch;
}

/**
 * 配置逐字段解析后的结果，所有字段均已确定。
 * The result of resolving configuration field by field, with every field
 * decided.
 */
export interface OvResolvedOptions {
  baseURL: string;
  credentials: RequestCredentials;
  headers: Readonly<Record<string, string>>;
  signal?: AbortSignal;
}

/**
 * 原生接口的请求描述。Client 只按 method、path、query 与 body 发请求，不解析
 * 业务含义。
 * A native API request. The Client sends it by method, path, query and body
 * without interpreting the payload.
 */
export interface OvRequestSpec {
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD';
  /**
   * 以 `/` 开头的接口路径，不含服务地址与网关前缀。
   * API path starting with `/`, without the service address or gateway prefix.
   */
  path: string;
  query?: Readonly<Record<string, OvQueryValue>>;
  /** JSON data or FormData for native multipart uploads; FormData sets its own boundary. */
  body?: unknown;
  /**
   * 响应体的读取方式。省略时为 `json`：解析原生外壳并返回 `result`。需要原始
   * 字节时传 `blob`，此时 `TResult` 应声明为 `Blob`。
   *
   * How the response body is read. Omitted means `json`: the native envelope is
   * parsed and its `result` returned. Pass `blob` for raw bytes, declaring
   * `TResult` as `Blob`.
   */
  responseType?: OvResponseType;
  /**
   * 本次请求的身份、作用域与服务地址覆盖，例如切换到另一个库或另一个网关。
   * Identity, scope and service-address override for this request, for example
   * switching to another library or another gateway.
   */
  options?: OvRequestOptions;
}

/**
 * 响应体的读取方式：`json` 解析外壳后返回 `result`，`blob` 返回原始字节。
 * How the response body is read: `json` parses the envelope and returns
 * `result`, `blob` returns the raw bytes.
 */
export type OvResponseType = 'json' | 'blob';

/**
 * 一次 JSON 请求的外壳与 `result`。列表接口把 `has_more` 放在 `result` 之外，
 * 需要分页时用 {@link OvClient.requestEnvelope} 才能读到它。
 * The envelope and `result` of one JSON request. A listing puts `has_more`
 * outside `result`, so paging reads it through {@link OvClient.requestEnvelope}.
 */
export interface OvResponseEnvelope<TResult = unknown> {
  /** 原生响应的 `result`。The native response's `result`. */
  result: TResult;
  /** 是否还有下一页；接口不返回该字段时为 `false`。 */
  hasMore: boolean;
}

/** 查询参数可接受的值；`undefined` 与 `null` 表示不发送。 */
export type OvQueryValue =
  | string
  | number
  | boolean
  | null
  | undefined
  | readonly (string | number | boolean)[];

/**
 * 原生接口的成功响应外壳。`result` 承载业务数据，其余字段是外层元数据。
 * The success envelope of a native response. `result` carries the payload and
 * the remaining fields are outer metadata.
 */
export interface OvSuccessEnvelope<TResult = unknown> {
  result?: TResult;
  status?: string;
  [key: string]: unknown;
}

/** 原生接口的错误响应外壳。The error envelope of a native response. */
export interface OvErrorEnvelope {
  status?: string;
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
  };
}

/**
 * 一个 SSE 事件。字段按 SSE 规范解析，`data` 保持原文——OpenViking 把事件类型
 * 与事件 id 放在 `data` 的 JSON 里，不占 SSE 的 `event` / `id` 字段，调用方需要
 * 自己解析 `data` 才能读到它们。
 *
 * One SSE event. Fields follow the SSE specification and `data` stays raw:
 * OpenViking puts the event type and event id inside the `data` JSON rather than
 * in the SSE `event` / `id` fields, so a caller parses `data` to read them.
 */
export interface OvStreamEvent {
  /**
   * `data:` 字段，多行按换行拼接，原样保留不做 JSON 解析。
   * The `data:` field, with multiple lines joined by newlines and kept verbatim.
   */
  data: string;
  /**
   * `event:` 字段，服务端未发送时省略。
   * The `event:` field, omitted when the server sends none.
   */
  event?: string;
  /**
   * `id:` 字段，服务端未发送时省略。
   * The `id:` field, omitted when the server sends none.
   */
  id?: string;
  /**
   * `retry:` 字段，单位毫秒，服务端未发送时省略。
   * The `retry:` field in milliseconds, omitted when the server sends none.
   */
  retry?: number;
}

/**
 * 一次流式请求。除 `headers` 外与 {@link OvRequestSpec} 相同。
 * A streaming request. It matches {@link OvRequestSpec} apart from `headers`.
 */
export interface OvStreamSpec extends Omit<OvRequestSpec, 'responseType'> {
  /**
   * 追加的请求头。身份字段仍由 Client 组装并在同名时胜出，调用方无法误改凭证。
   * `accept: text/event-stream` 默认发送，可在此覆盖。
   *
   * Extra request headers. Identity headers are still assembled by the Client
   * and win on a name clash, so a caller cannot accidentally replace a
   * credential. `accept: text/event-stream` is sent by default and may be
   * overridden here.
   */
  headers?: Readonly<Record<string, string>>;
}

/**
 * 已建立的流。异步迭代得到事件，`abort` 中止请求后迭代器正常结束。
 * 响应体边读边消费，因此只能迭代一次。
 *
 * An open stream. Iterating yields events; `abort` stops the request and the
 * iterator then ends without an error. The body is consumed as it is read, so
 * it can be iterated only once.
 */
export interface OvStream extends AsyncIterable<OvStreamEvent> {
  /** 中止请求并释放连接。Aborts the request and releases the connection. */
  abort: () => void;
}

/**
 * 框架无关的请求能力。库自带基于 `fetch` 的实现，宿主也可以自行实现以复用
 * 已有请求层；模块只依赖本接口。
 * Framework-neutral request capability. The library ships a `fetch`-based
 * implementation, and a host may implement it to reuse an existing request
 * layer; modules depend only on this interface.
 */
export interface OvClient {
  /**
   * 发一次请求并返回原生响应的 `result`。
   * Sends one request and returns the native response's `result`.
   */
  request<TResult>(spec: OvRequestSpec): Promise<TResult>;
  /**
   * 与 `request` 相同，但同时返回 `result` 之外的分页标记。列表接口用它判断
   * 是否还有下一页；`responseType: 'blob'` 不适用。
   *
   * Same as `request`, but also returns the pagination flag that sits outside
   * `result`. A listing uses it to decide whether a next page exists; not
   * applicable to `responseType: 'blob'`.
   */
  requestEnvelope<TResult>(
    spec: OvRequestSpec,
  ): Promise<OvResponseEnvelope<TResult>>;
  /**
   * 建立一条 SSE 流。响应到达后即返回，事件随后逐个产出；HTTP 失败、非事件流
   * 响应与外壳里的 `status: 'error'` 都在返回前抛出。
   *
   * Opens an SSE stream. It resolves once the response arrives and yields events
   * afterwards; an HTTP failure, a non-event-stream response and a
   * `status: 'error'` envelope all throw before it resolves.
   */
  stream(spec: OvStreamSpec): Promise<OvStream>;
  /**
   * 当前生效的服务地址，用于缓存键与订阅隔离。
   * The effective service address, used to isolate caches and subscriptions.
   */
  readonly baseURL: string;
}
