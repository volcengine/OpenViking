export function buildUserAgent(harness: string, version?: string): string;

export function readManifestVersion(manifest: string | URL): string;

export function resolveOpenVikingCredentials(env?: Record<string, string | undefined>): {
  credentialSource: string;
  baseUrl: string;
  mcpUrl: string;
  apiKey: string;
  account: string;
  user: string;
  peerId: string;
  hasApiKey: boolean;
};
