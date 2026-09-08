export interface OvHttpConfig {
  baseUrl?: string;
  endpoint?: string;
  apiKey?: string;
  account?: string;
  user?: string;
  sendIdentityHeaders?: boolean;
  userAgent?: string;
  [key: string]: unknown;
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
  },
): (
  path: string,
  init?: RequestInit,
  options?: { timeoutMs?: number; actorPeerId?: string },
) => Promise<OvHttpEnvelope>;
