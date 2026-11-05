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
   * 是否携带浏览器 Cookie。商业版的数据面与控制面接口都依赖 Cookie 鉴权，
   * 跨源调用时需要设为 `include`。
   *
   * Whether browser cookies are sent. The commercial edition authenticates both
   * data-plane and control-plane requests with a cookie, so a cross-origin call
   * needs `include`.
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
  body?: unknown;
  /**
   * 本次请求的身份、作用域与服务地址覆盖，例如切换到另一个库或另一个网关。
   * Identity, scope and service-address override for this request, for example
   * switching to another library or another gateway.
   */
  options?: OvRequestOptions;
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
   * 当前生效的服务地址，用于缓存键与订阅隔离。
   * The effective service address, used to isolate caches and subscriptions.
   */
  readonly baseURL: string;
}
