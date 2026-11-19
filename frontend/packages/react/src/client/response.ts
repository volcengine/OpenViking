import { OvClientError } from './errors.js';
import type { OvErrorEnvelope, OvSuccessEnvelope } from './types.js';

/** 任意对象判定。Record check. */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** 把响应体读成原生外壳；不是对象时按空外壳处理。 */
export function readEnvelope(
  value: unknown,
): OvErrorEnvelope & OvSuccessEnvelope {
  return isRecord(value) ? value : {};
}

/** 解析 JSON 文本；不是 JSON 时返回 `undefined`，不抛错。 */
export function parseJson(raw: string): unknown {
  if (!raw.trim()) return undefined;
  try {
    return JSON.parse(raw);
  } catch {
    return undefined;
  }
}

/** 请求被调用方主动取消时保留 `AbortError` 语义，便于调用方区分。 */
export function isAbortError(error: unknown): boolean {
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
export function normalizeTransportError(
  error: unknown,
  signal?: AbortSignal,
): never {
  if (isAbortError(error) || signal?.aborted) {
    throw error;
  }
  throw new OvClientError({
    code: 'NETWORK_ERROR',
    message: error instanceof Error ? error.message : 'Network request failed',
    cause: error,
  });
}

/** 读取文本响应体。读取失败属于传输失败（含取消），必须上报。 */
export async function readText(
  response: Response,
  signal?: AbortSignal,
): Promise<string> {
  try {
    return await response.text();
  } catch (error) {
    normalizeTransportError(error, signal);
  }
}

/** 读取二进制响应体。读取失败同样属于传输失败。 */
export async function readBlob(
  response: Response,
  signal?: AbortSignal,
): Promise<Blob> {
  try {
    return await response.blob();
  } catch (error) {
    normalizeTransportError(error, signal);
  }
}

/** 由原生错误外壳构造失败，保留错误码、状态与请求标识。 */
export function toClientError(
  response: Response,
  body: unknown,
  fallbackCode?: string,
): OvClientError {
  const envelope = readEnvelope(body);
  return new OvClientError({
    code: envelope.error?.code ?? fallbackCode ?? `HTTP_${response.status}`,
    message: envelope.error?.message ?? response.statusText,
    statusCode: response.status,
    details: envelope.error?.details,
    requestId: response.headers.get('x-request-id') ?? undefined,
    responseBody: body,
  });
}

/**
 * 原生接口在 HTTP 200 里用 `status: 'error'` 表达业务失败；命中时抛错。
 * The native API reports business failures as `status: 'error'` inside HTTP 200;
 * throw when that happens.
 */
export function throwIfErrorEnvelope(
  response: Response,
  body: unknown,
  observe?: (error: OvClientError) => void,
): void {
  const envelope = readEnvelope(body);
  if (envelope.status !== 'error') return;
  const error = toClientError(response, body, 'UNKNOWN');
  observe?.(error);
  throw error;
}
