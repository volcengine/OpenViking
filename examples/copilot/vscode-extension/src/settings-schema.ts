/**
 * Canonical list of every `openviking.*` VS Code setting we expose.
 *
 * VS Code reads `package.json#contributes.configuration` directly,
 * so the manifest is the runtime source of truth — but PLAN.md §8.2
 * specifies all 25 settings in one place. This module mirrors that
 * spec as a typed object so:
 *
 *   1. `extension.ts` can drive `readWorkspaceOverrides()` from a
 *      single iterable (no risk of forgetting to map a new field).
 *   2. `settings-schema.test.ts` can assert the manifest matches
 *      the spec — drift between the two breaks the build.
 *
 * Adding a new setting: bump the entry here, mirror the JSON schema
 * in `package.json`, and add the workspace-override mapping in
 * `extension.ts`. The drift-detection test enforces step #2.
 */

import type { PluginConfig } from "@openviking/copilot-shared";

export type SettingDescriptorType =
  | "string"
  | "boolean"
  | "number"
  | "string-array"
  | "enum";

export interface SettingDescriptor<TKey extends string = string> {
  /** Full setting key as it appears in `settings.json` (e.g. `openviking.url`). */
  key: TKey;
  /** Logical type, mapped to a JSON-schema fragment in the manifest. */
  type: SettingDescriptorType;
  /** Default value the manifest declares. */
  default: unknown;
  /** For `enum` settings, the allowed values. */
  enumValues?: readonly string[];
  /** PluginConfig field this setting overrides (when applicable). */
  cfgField?: keyof PluginConfig;
  /**
   * True for settings the user should NOT inline in settings.json.
   * Currently only `apiKey` — the manifest description nudges users
   * toward the `OpenViking: Set API Key` command, which writes to
   * SecretStorage.
   */
  secret?: boolean;
}

/**
 * The full settings catalogue. Order doesn't matter for the manifest
 * but is grouped by feature here for readability.
 */
export const OPENVIKING_SETTINGS: readonly SettingDescriptor[] = [
  // -------- connection --------
  { key: "openviking.url", type: "string", default: "", cfgField: "baseUrl" },
  { key: "openviking.apiKey", type: "string", default: "", cfgField: "apiKey", secret: true },
  { key: "openviking.account", type: "string", default: "", cfgField: "accountId" },
  { key: "openviking.user", type: "string", default: "", cfgField: "userId" },
  { key: "openviking.agentId", type: "string", default: "", cfgField: "agentId" },

  // -------- recall --------
  { key: "openviking.autoRecall", type: "boolean", default: true, cfgField: "autoRecall" },
  { key: "openviking.recallLimit", type: "number", default: 6, cfgField: "recallLimit" },
  { key: "openviking.recallTokenBudget", type: "number", default: 2000, cfgField: "recallTokenBudget" },
  { key: "openviking.recallMaxContentChars", type: "number", default: 500, cfgField: "recallMaxContentChars" },
  { key: "openviking.recallPreferAbstract", type: "boolean", default: true, cfgField: "recallPreferAbstract" },
  { key: "openviking.scoreThreshold", type: "number", default: 0.35, cfgField: "scoreThreshold" },
  { key: "openviking.minQueryLength", type: "number", default: 3, cfgField: "minQueryLength" },

  // -------- capture --------
  { key: "openviking.autoCapture", type: "boolean", default: true, cfgField: "autoCapture" },
  {
    key: "openviking.captureMode",
    type: "enum",
    default: "semantic",
    enumValues: ["semantic", "keyword"] as const,
    cfgField: "captureMode",
  },
  { key: "openviking.captureMaxLength", type: "number", default: 24000, cfgField: "captureMaxLength" },
  { key: "openviking.captureAssistantTurns", type: "boolean", default: true, cfgField: "captureAssistantTurns" },
  { key: "openviking.commitTokenThreshold", type: "number", default: 20000, cfgField: "commitTokenThreshold" },
  { key: "openviking.resumeContextBudget", type: "number", default: 32000, cfgField: "resumeContextBudget" },

  // -------- timeouts / async --------
  { key: "openviking.timeoutMs", type: "number", default: 15000, cfgField: "timeoutMs" },
  { key: "openviking.captureTimeoutMs", type: "number", default: 30000, cfgField: "captureTimeoutMs" },
  { key: "openviking.writePathAsync", type: "boolean", default: true, cfgField: "writePathAsync" },

  // -------- bypass --------
  { key: "openviking.bypassSession", type: "boolean", default: false, cfgField: "bypassSession" },
  { key: "openviking.bypassSessionPatterns", type: "string-array", default: [], cfgField: "bypassSessionPatterns" },

  // -------- debug --------
  { key: "openviking.debug", type: "boolean", default: false, cfgField: "debug" },
  { key: "openviking.debugLogPath", type: "string", default: "", cfgField: "debugLogPath" },
];

/** SecretStorage key for the apiKey command. */
export const SECRETS_API_KEY = "openviking.apiKey" as const;

/** Command id surfaced from the manifest. */
export const SET_API_KEY_COMMAND = "openviking.setApiKey" as const;
