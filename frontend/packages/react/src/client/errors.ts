/**
 * 请求失败的统一错误。区分 HTTP 失败与原生响应里的 `status: 'error'`，
 * 保留服务端错误码、状态与请求标识。
 * The single error a failed request raises. It distinguishes an HTTP failure
 * from a native `status: 'error'` response and keeps the server error code,
 * status and request identifier.
 */
export class OvClientError extends Error {
  /** 服务端错误码，HTTP 失败时为 `HTTP_<status>` 或 `NETWORK_ERROR`。 */
  readonly code: string;
  /** HTTP 状态码；请求未到达服务端时省略。 */
  readonly statusCode?: number;
  /** 服务端返回的附加信息。 */
  readonly details?: unknown;
  /** 服务端请求标识，便于与服务端日志对应。 */
  readonly requestId?: string;
  /** 原始响应体，便于诊断无法归一的响应。 */
  readonly responseBody?: unknown;

  constructor(options: {
    code: string;
    message: string;
    statusCode?: number;
    details?: unknown;
    requestId?: string;
    responseBody?: unknown;
    cause?: unknown;
  }) {
    super(
      options.message,
      options.cause === undefined ? undefined : { cause: options.cause },
    );
    this.name = 'OvClientError';
    this.code = options.code;
    this.statusCode = options.statusCode;
    this.details = options.details;
    this.requestId = options.requestId;
    this.responseBody = options.responseBody;
  }
}

/** 判断一个值是否为 {@link OvClientError}。 */
export function isOvClientError(value: unknown): value is OvClientError {
  return value instanceof OvClientError;
}
