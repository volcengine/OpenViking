/**
 * VS Code adapter for the OpenViking Copilot extension.
 *
 * Stays as thin as possible — every piece of logic lives in
 * `extension-core.ts`, `participant-core.ts`, `commands-core.ts`,
 * and `mcp/manifest.ts` so it can be unit-tested without VS Code.
 *
 * Activation is async because we read `apiKey` from VS Code
 * SecretStorage before building the activation handle. SecretStorage
 * has the highest priority for `apiKey` — above settings.json — so
 * users who run `OpenViking: Set API Key` never need to touch
 * settings.json.
 */

import * as vscode from "vscode";
import {
  buildActivationHandle,
  runDeactivate,
  type ActivationHandle,
  type PluginConfig,
} from "./extension-core";
import { registerOpenVikingCommands } from "./commands";
import { registerOpenVikingMcpProvider } from "./mcp/register";
import { registerOpenVikingParticipant } from "./participant";
import { OPENVIKING_SETTINGS, SECRETS_API_KEY } from "./settings-schema";

let handle: ActivationHandle | null = null;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  // Register commands first so the user can reach `Set API Key` even
  // when buildActivationHandle returns null (plugin disabled).
  for (const disposable of registerOpenVikingCommands(context)) {
    context.subscriptions.push(disposable);
  }

  const secretApiKey = await context.secrets.get(SECRETS_API_KEY);
  const overrides = readWorkspaceOverrides();
  if (secretApiKey && secretApiKey.trim()) {
    overrides.apiKey = secretApiKey.trim();
  }

  handle = buildActivationHandle({ hostOverrides: overrides });

  if (!handle) {
    return;
  }

  context.subscriptions.push(registerOpenVikingParticipant(context, handle));
  context.subscriptions.push(registerOpenVikingMcpProvider(handle));

  context.subscriptions.push(
    new vscode.Disposable(() => {
      void runDeactivate(handle);
      handle = null;
    }),
  );
}

export async function deactivate(): Promise<void> {
  await runDeactivate(handle);
  handle = null;
}

/**
 * Map every `openviking.*` workspace/user setting from the typed
 * catalogue (`settings-schema.ts`) into a `Partial<PluginConfig>`.
 * The same descriptor list drives the manifest schema, so adding a
 * new setting only requires touching `settings-schema.ts` + the
 * manifest — this loop picks it up automatically as long as the
 * descriptor declares a `cfgField`.
 */
function readWorkspaceOverrides(): Partial<PluginConfig> {
  const cfg = vscode.workspace.getConfiguration("openviking");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const out: Record<string, any> = {};

  for (const desc of OPENVIKING_SETTINGS) {
    if (!desc.cfgField) continue;
    const settingName = desc.key.replace(/^openviking\./, "");

    switch (desc.type) {
      case "string":
      case "enum": {
        const v = cfg.get<string>(settingName);
        if (typeof v === "string" && v.trim()) {
          out[desc.cfgField] =
            desc.cfgField === "baseUrl" ? v.trim().replace(/\/+$/, "") : v.trim();
        }
        break;
      }
      case "boolean": {
        const v = cfg.get<boolean>(settingName);
        if (typeof v === "boolean") out[desc.cfgField] = v;
        break;
      }
      case "number": {
        const v = cfg.get<number>(settingName);
        if (typeof v === "number" && Number.isFinite(v)) out[desc.cfgField] = v;
        break;
      }
      case "string-array": {
        const v = cfg.get<string[]>(settingName);
        if (Array.isArray(v)) {
          const arr = v.filter((s): s is string => typeof s === "string" && s.trim().length > 0);
          if (arr.length > 0) out[desc.cfgField] = arr;
        }
        break;
      }
    }
  }

  return out as Partial<PluginConfig>;
}
