export type SetupNetworkTransport = (url: string, init: RequestInit) => Promise<Response>;

export type SetupVersionCompatibility = "compatible" | "server_too_old" | "server_too_new" | "unknown";

export type SetupHealthResult = {
  ok: boolean;
  version: string;
  error: string;
  compatibility: SetupVersionCompatibility;
  pluginVersion: string;
  compatRange: string;
};

export type SetupApiKeyProbeResult = {
  keyType: "user_key" | "root_key" | "no_key" | "unknown";
  needsAccountId: boolean;
  needsUserId: boolean;
  detail: string;
};

export type SetupNetworkProbeOptions = {
  pluginVersion: string;
  compatRange: string;
  checkVersionCompatibility: (serverVersion: string) => SetupVersionCompatibility;
  transport?: SetupNetworkTransport;
  timeoutMs?: number;
};

export type SetupNetworkProbes = {
  probeApiKeyType: (baseUrl: string, apiKey?: string) => Promise<SetupApiKeyProbeResult>;
  checkServiceHealth: (baseUrl: string, apiKey?: string) => Promise<SetupHealthResult>;
};

export const defaultSetupNetworkTransport: SetupNetworkTransport = (url, init) => fetch(url, init);

export function createSetupNetworkProbes({
  pluginVersion,
  compatRange,
  checkVersionCompatibility,
  transport = defaultSetupNetworkTransport,
  timeoutMs = 10_000,
}: SetupNetworkProbeOptions): SetupNetworkProbes {
  return {
    async probeApiKeyType(baseUrl: string, apiKey?: string): Promise<SetupApiKeyProbeResult> {
      if (!apiKey) {
        return { keyType: "no_key", needsAccountId: false, needsUserId: false, detail: "No API key configured" };
      }

      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
      const sessionsUrl = `${baseUrl.replace(/\/+$/, "")}/api/v1/sessions?limit=1`;
      try {
        const headers: Record<string, string> = { "X-API-Key": apiKey };
        const response = await transport(sessionsUrl, {
          headers,
          signal: controller.signal,
        });

        if (response.ok) {
          return { keyType: "user_key", needsAccountId: false, needsUserId: false, detail: "API key has full user context" };
        }

        if ([400, 401, 403, 422].includes(response.status)) {
          let body = "";
          try {
            body = await response.text();
          } catch { /* ignore parse errors */ }
          const lower = body.toLowerCase();
          const needsAccount = /x-openviking-account|account[_ ]?id|account context|tenant/.test(lower);
          const needsUser = /x-openviking-user|user[_ ]?id|user context|user key/.test(lower);
          if (needsAccount || needsUser) {
            return {
              keyType: "root_key",
              needsAccountId: needsAccount,
              needsUserId: needsUser,
              detail: body.slice(0, 200),
            };
          }

          try {
            const probeHeaders: Record<string, string> = {
              "X-API-Key": apiKey,
              "X-OpenViking-Account": "__probe__",
              "X-OpenViking-User": "__probe__",
            };
            const probe2 = await transport(sessionsUrl, {
              headers: probeHeaders,
              signal: controller.signal,
            });
            if (probe2.status !== response.status) {
              return {
                keyType: "root_key",
                needsAccountId: true,
                needsUserId: true,
                detail: body.slice(0, 200) || `HTTP ${response.status} -> ${probe2.status} after adding tenant headers`,
              };
            }
          } catch { /* ignore probe errors, fall through to unknown */ }

          if (response.status === 401 || response.status === 403) {
            return { keyType: "unknown", needsAccountId: false, needsUserId: false, detail: `HTTP ${response.status} - authentication failed, verify your API key` };
          }
          return { keyType: "unknown", needsAccountId: false, needsUserId: false, detail: `HTTP ${response.status}${body ? ` - ${body.slice(0, 160)}` : ""}` };
        }

        return { keyType: "unknown", needsAccountId: false, needsUserId: false, detail: `HTTP ${response.status}` };
      } catch (err) {
        return { keyType: "unknown", needsAccountId: false, needsUserId: false, detail: String(err instanceof Error ? err.message : err) };
      } finally {
        clearTimeout(timeoutId);
      }
    },

    async checkServiceHealth(baseUrl: string, apiKey?: string): Promise<SetupHealthResult> {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const headers: Record<string, string> = {};
        if (apiKey) headers["X-API-Key"] = apiKey;
        const response = await transport(`${baseUrl.replace(/\/+$/, "")}/health`, {
          headers,
          signal: controller.signal,
        });
        if (response.ok) {
          try {
            const data = await response.json() as Record<string, unknown>;
            const result = (data.result ?? data) as Record<string, unknown>;
            const version = String(result.version ?? data.version ?? "");
            const compatibility = checkVersionCompatibility(version);
            return { ok: true, version, error: "", compatibility, pluginVersion, compatRange };
          } catch {
            return { ok: true, version: "", error: "", compatibility: "unknown", pluginVersion, compatRange };
          }
        }
        return { ok: false, version: "", error: `HTTP ${response.status}`, compatibility: "unknown", pluginVersion, compatRange };
      } catch (err) {
        return { ok: false, version: "", error: String(err instanceof Error ? err.message : err), compatibility: "unknown", pluginVersion, compatRange };
      } finally {
        clearTimeout(timeoutId);
      }
    },
  };
}
