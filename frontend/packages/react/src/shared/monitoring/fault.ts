import { isOvClientError } from '../../client/errors.js';
import type { ErrorKind, Fault } from '../../client/monitoring/fault.js';

const hint = Symbol('OpenViking.fault');
export function markFault<T extends Error>(
  error: T,
  errorKind: ErrorKind,
  code?: string,
) {
  Object.defineProperty(error, hint, {
    value: { errorKind, code: code ?? codes[errorKind] },
  });
  return error;
}

const codes = {
  render: 'RENDER_ERROR',
  network: 'NETWORK_ERROR',
  http: 'HTTP_ERROR',
  business: 'BUSINESS_ERROR',
  configuration: 'REQUEST_CONFIG_ERROR',
  'response-read': 'RESPONSE_READ_ERROR',
  protocol: 'STREAM_PROTOCOL_ERROR',
  'resource-load': 'RESOURCE_LOAD_ERROR',
  renderer: 'RENDERER_ERROR',
  'host-service': 'HOST_SERVICE_ERROR',
  storage: 'STORAGE_ERROR',
  unknown: 'UNKNOWN_ERROR',
} as const;

export function classify(
  error: unknown,
  fallback: ErrorKind = 'unknown',
): Pick<Fault<ErrorKind>, 'errorKind' | 'code'> {
  const marked =
    error !== null && typeof error === 'object'
      ? (error as { [hint]?: ReturnType<typeof classify> })[hint]
      : undefined;
  if (marked) return marked;
  const explicit = fallback === 'configuration' || fallback === 'response-read';
  const kind = explicit
    ? fallback
    : isOvClientError(error)
      ? error.statusCode && error.statusCode >= 400
        ? 'http'
        : error.statusCode && error.code !== 'UNEXPECTED_CONTENT_TYPE'
          ? 'business'
          : error.code === 'NETWORK_ERROR'
            ? 'network'
            : [
                  'BOT_INVALID_EVENT',
                  'BOT_STREAM_TRUNCATED',
                  'UNEXPECTED_CONTENT_TYPE',
                ].includes(error.code)
              ? 'protocol'
              : fallback
      : fallback;
  return { errorKind: kind, code: codes[kind] };
}

export const cancelled = (error: unknown, signal?: AbortSignal) =>
  signal?.aborted ||
  (typeof error === 'object' &&
    error !== null &&
    (('name' in error && error.name === 'AbortError') ||
      ('code' in error && error.code === 'ABORT_ERR')));
