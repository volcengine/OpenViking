/**
 * VS Code adapter for the OpenViking Copilot extension.
 *
 * Stays as thin as possible — every piece of logic lives in
 * `extension-core.ts` so it can be unit-tested without VS Code. This
 * file's job is:
 *   - read `openviking.*` workspace/user settings into hostOverrides
 *   - call buildActivationHandle
 *   - register `runDeactivate` as a disposable
 *
 * The chat participant (#16/#17), LM tools (#22/#26), and SecretStorage-
 * backed `Set API Key` command (#19) hook into this handle in later
 * issues.
 */

import * as vscode from "vscode";
import {
  buildActivationHandle,
  runDeactivate,
  type ActivationHandle,
  type PluginConfig,
} from "./extension-core";

let handle: ActivationHandle | null = null;

export function activate(context: vscode.ExtensionContext): void {
  handle = buildActivationHandle({
    hostOverrides: readWorkspaceOverrides(),
  });

  if (!handle) {
    // Plugin is disabled (no config files, no env force-enable). Stay
    // out of the way silently — re-running activation is cheap, so
    // `OPENVIKING_MEMORY_ENABLED=1` after a reload picks us back up.
    return;
  }

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
 * Map `openviking.*` workspace/user settings to PluginConfig
 * overrides. The full ~20-field schema lives in #19; this minimal
 * subset is enough to drive activation + recall + capture defaults.
 */
function readWorkspaceOverrides(): Partial<PluginConfig> {
  const cfg = vscode.workspace.getConfiguration("openviking");
  const overrides: Partial<PluginConfig> = {};

  const url = cfg.get<string>("url");
  if (typeof url === "string" && url.trim()) overrides.baseUrl = url.trim().replace(/\/+$/, "");

  const apiKey = cfg.get<string>("apiKey");
  if (typeof apiKey === "string" && apiKey.trim()) overrides.apiKey = apiKey.trim();

  const account = cfg.get<string>("account");
  if (typeof account === "string" && account.trim()) overrides.accountId = account.trim();

  const user = cfg.get<string>("user");
  if (typeof user === "string" && user.trim()) overrides.userId = user.trim();

  const agentId = cfg.get<string>("agentId");
  if (typeof agentId === "string" && agentId.trim()) overrides.agentId = agentId.trim();

  const autoRecall = cfg.get<boolean>("autoRecall");
  if (typeof autoRecall === "boolean") overrides.autoRecall = autoRecall;

  const autoCapture = cfg.get<boolean>("autoCapture");
  if (typeof autoCapture === "boolean") overrides.autoCapture = autoCapture;

  const debug = cfg.get<boolean>("debug");
  if (typeof debug === "boolean") overrides.debug = debug;

  return overrides;
}
