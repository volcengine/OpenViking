export function buildUserAgent(harness: string, version?: string): string;

export function readManifestVersion(manifest: string | URL): string;

export function resolveAuthMode(input?: {
  settings?: Record<string, unknown>;
  ovFile?: Record<string, unknown>;
  account?: string;
  user?: string;
}): { authMode: string; sendIdentityHeaders: boolean };

export function resolveOpenVikingCredentials(
  env?: Record<string, string | undefined>,
  harness?: string,
  plugin?: { apiKey?: string },
): {
  credentialSource: string;
  apiKeySource: string;
  credentialPath: string;
  cliPath: string;
  cliPathCandidate: string;
  ovPath: string;
  cliFile: Record<string, unknown>;
  ovFile: Record<string, unknown>;
  baseUrl: string;
  mcpUrl: string;
  apiKey: string;
  account: string;
  user: string;
  peerId: string;
  hasApiKey: boolean;
};

export function buildProxyConnection(
  harness: string,
  options?: {
    env?: Record<string, string | undefined>;
    manifestUrl?: string | URL;
    version?: string;
  },
): {
  harness: string;
  userAgent: string;
  baseUrl: string;
  mcpUrl: string;
  apiKey: string;
  account: string;
  user: string;
  peerId: string;
  authMode: string;
  sendIdentityHeaders: boolean;
  credentialSource: string;
  apiKeySource: string;
  credentialPath: string;
  hasApiKey: boolean;
  watchedPaths: string[];
  timeoutMs: number;
  debug: boolean;
  debugLogPath: string;
};
