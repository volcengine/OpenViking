import * as vscode from "vscode";
import {
  OPENVIKING_RECALL_TOOL_NAME,
  RecallCache,
  deriveSessionId,
} from "@openviking/copilot-shared";
import type { ActivationHandle } from "../extension-core";
import { buildRecallContext, type ParticipantRecallState } from "../participant-core";

export interface OpenVikingRecallToolInput {
  query: string;
  sessionId?: string;
}

export function registerOpenVikingRecallTool(
  _context: vscode.ExtensionContext,
  handle: ActivationHandle | null,
): vscode.Disposable {
  if (!handle) return new vscode.Disposable(() => {});
  const state = buildRecallToolState(handle);
  return vscode.lm.registerTool(OPENVIKING_RECALL_TOOL_NAME, new OpenVikingRecallTool(state));
}

class OpenVikingRecallTool implements vscode.LanguageModelTool<OpenVikingRecallToolInput> {
  constructor(private readonly state: ParticipantRecallState) {}

  async invoke(
    options: vscode.LanguageModelToolInvocationOptions<OpenVikingRecallToolInput>,
    token: vscode.CancellationToken,
  ): Promise<vscode.LanguageModelToolResult> {
    if (token.isCancellationRequested) {
      return new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart(emptyRecallBlock("Recall was cancelled."))]);
    }

    const query = options.input.query.trim();
    if (!query) {
      return new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart(emptyRecallBlock("No recall query was provided."))]);
    }

    const state = options.input.sessionId?.trim()
      ? { ...this.state, sessionId: options.input.sessionId.trim() }
      : this.state;
    const result = await buildRecallContext(state, query);
    return new vscode.LanguageModelToolResult([
      new vscode.LanguageModelTextPart(result.block ?? emptyRecallBlock("No relevant OpenViking context found.")),
    ]);
  }

  prepareInvocation(
    options: vscode.LanguageModelToolInvocationPrepareOptions<OpenVikingRecallToolInput>,
  ): vscode.ProviderResult<vscode.PreparedToolInvocation> {
    const query = options.input.query.trim();
    return {
      invocationMessage: query ? `Searching OpenViking memory for “${query}”` : "Searching OpenViking memory",
    };
  }
}

function buildRecallToolState(handle: ActivationHandle): ParticipantRecallState {
  const hostSessionId = vscode.workspace.workspaceFolders?.[0]?.uri.toString() ?? process.cwd();
  const sessionId = deriveSessionId("copilot-vscode", hostSessionId);
  handle.logger.log("recall_tool_state_built", { sessionId, hostSessionId });
  return {
    cfg: handle.cfg,
    client: handle.client,
    cache: new RecallCache(),
    sessionId,
    logger: handle.logger,
  };
}

function emptyRecallBlock(message: string): string {
  return `<openviking-context>\n${message}\n</openviking-context>`;
}
