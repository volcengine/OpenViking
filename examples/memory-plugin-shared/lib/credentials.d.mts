export function buildUserAgent(harness: string, version?: string): string;

export function readManifestVersion(manifest: string | URL): string;

export const CREDENTIAL_ENV_VARS: readonly string[];

export const CONNECTION_ENV_VARS: readonly string[];

export interface CredentialFiles {
  cliFile: Record<string, unknown>;
  cliPath: string;
  cliPathCandidate: string;
  ovFile: Record<string, unknown>;
  ovPath: string;
  connectionFile: string;
}

export function loadCredentialFiles(
  env?: Record<string, string | undefined>,
  options?: { connectionFile?: string },
): CredentialFiles;

export interface ConnectionHostInput {
  apiKey?: string;
  account?: string;
  user?: string;
  baseUrl?: string;
  authMode?: string;
  peerId?: string;
}

export interface Connection extends CredentialFiles {
  harness: string;
  credentialSource: string;
  baseUrl: string;
  mcpUrl: string;
  apiKey: string;
  account: string;
  user: string;
  peerId: string;
  authMode: string;
  sendIdentityHeaders: boolean;
  hasApiKey: boolean;
  apiKeySource: string;
  credentialPath: string;
}

export function resolveConnection(
  harness: string,
  options?: {
    env?: Record<string, string | undefined>;
    files?: CredentialFiles | null;
    hostInput?: ConnectionHostInput;
    rootKeyFallback?: boolean;
  },
): Connection;

export function resolveConnectionFileForCwd(
  cwd: string,
  env?: Record<string, string | undefined>,
): string;

export function buildProxyConnection(
  harness: string,
  options?: {
    env?: Record<string, string | undefined>;
    manifestUrl?: string | URL;
    version?: string;
    cwd?: string;
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
  cliPath: string;
  ovPath: string;
  connectionFile: string;
  timeoutMs: number;
  debug: boolean;
  debugLogPath: string;
};
