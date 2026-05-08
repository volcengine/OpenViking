/**
 * VS Code adapter for the `openviking.setApiKey` command.
 *
 * Stays a thin shim: forwards to `runSetApiKeyCommand` with adapters
 * over `vscode.window.showInputBox` + `vscode.window.showInformationMessage`
 * + `context.secrets`. All real logic lives in `commands-core.ts`.
 */

import * as vscode from "vscode";
import { SET_API_KEY_COMMAND, SECRETS_API_KEY } from "./settings-schema";
import {
  runSetApiKeyCommand,
  type InputProvider,
  type SecretStorageLike,
} from "./commands-core";

export function registerOpenVikingCommands(context: vscode.ExtensionContext): vscode.Disposable[] {
  const secrets: SecretStorageLike = {
    get: (k) => Promise.resolve(context.secrets.get(k)).then((v) => v ?? undefined),
    store: (k, v) => Promise.resolve(context.secrets.store(k, v)),
    delete: (k) => Promise.resolve(context.secrets.delete(k)),
  };

  const input: InputProvider = {
    prompt: async (opts) => {
      const showInputBoxOpts: vscode.InputBoxOptions = {
        password: opts.password,
        prompt: opts.prompt,
        ignoreFocusOut: true,
      };
      if (opts.placeholder !== undefined) showInputBoxOpts.placeHolder = opts.placeholder;
      if (opts.validate) {
        showInputBoxOpts.validateInput = (v) => opts.validate!(v) ?? undefined;
      }
      return vscode.window.showInputBox(showInputBoxOpts);
    },
    showInfo: async (message) => {
      await vscode.window.showInformationMessage(message);
    },
  };

  return [
    vscode.commands.registerCommand(SET_API_KEY_COMMAND, async () => {
      await runSetApiKeyCommand(secrets, input, { secretKey: SECRETS_API_KEY });
    }),
  ];
}
