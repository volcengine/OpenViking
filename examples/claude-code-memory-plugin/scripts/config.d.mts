/**
 * Type surface of `scripts/config.mjs` for the TypeScript hooks modules in
 * `mods/`, which import it but run outside `tsc`'s JavaScript checks.
 */

export declare function isPluginEnabled(): boolean;

export declare function usageCardsEnabled(
  cwd?: string,
  options?: { env?: Record<string, string | undefined> },
): boolean;

export declare function loadConfig(
  cwd?: string,
  options?: { env?: Record<string, string | undefined> },
): Record<string, any> & { configPath: string | null; credentialPath: string | null };
