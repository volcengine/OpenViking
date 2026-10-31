export interface OvHttpConfig {
  baseUrl?: string;
  endpoint?: string;
  apiKey?: string;
  account?: string;
  user?: string;
  sendIdentityHeaders?: boolean;
  userAgent?: string;
  authorization?: string;
  extraHeaders?: Record<string, string>;
  [key: string]: unknown;
}

export interface OvHttpRequestOptions {
  timeoutMs?: number;
  actorPeerId?: string;
  signal?: AbortSignal;
}

export interface OvHttpEnvelope {
  ok: boolean;
  status: number;
  result: any;
  error?: any;
  traceId?: string;
}

export function buildOvHeaders(
  cfg?: OvHttpConfig,
  options?: {
    actorPeerId?: string;
    identityHeaders?: boolean;
    extraHeaders?: Record<string, string>;
  },
): Record<string, string>;

export function createOvHttp(
  cfg?: OvHttpConfig,
  options?: {
    defaultTimeoutMs?: number;
    resolveActorPeerId?: () => string;
    identityHeaders?: boolean;
    extraHeaders?: Record<string, string>;
    requireJsonBody?: boolean;
    requireResultEnvelope?: boolean;
    minTimeoutMs?: number;
  },
): (
  path: string,
  init?: RequestInit,
  options?: OvHttpRequestOptions,
) => Promise<OvHttpEnvelope>;

export class OvRequestError extends Error {
  code: string;
  status: number;
  traceId?: string;
  constructor(code: string, status?: number, traceId?: string);
}
export function requireOvResult(response: OvHttpEnvelope): any;
export function requestOvResult(cfg: OvHttpConfig & { timeoutMs?: number }, path: string,
  options?: { body?: any; signal?: AbortSignal; timeoutMs?: number }): Promise<any>;
