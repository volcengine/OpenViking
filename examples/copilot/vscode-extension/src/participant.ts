/**
 * `@openviking` chat participant — VS Code adapter.
 *
 * Stays thin: dispatches `request.command` to the participant-core
 * helpers, writes their results to the chat stream, and on the
 * default (no-command) path prepends the recall block before
 * delegating to the request's selected language model.
 */

import * as vscode from "vscode";
import {
  CommitQueue,
  RecallCache,
  canonicaliseTranscript,
  deriveSessionId,
} from "@openviking/copilot-shared";
import type { ActivationHandle } from "./extension-core";
import {
  buildRecallContext,
  runForget,
  runStore,
  type ParticipantState,
} from "./participant-core";

const PARTICIPANT_ID = "openviking.memory";

/**
 * Register the chat participant against the activation handle. Returns a
 * disposable that the caller (extension.ts) pushes onto
 * `context.subscriptions`. When `handle` is null (plugin disabled) this
 * is a no-op and returns a noop disposable.
 */
export function registerOpenVikingParticipant(
  _context: vscode.ExtensionContext,
  handle: ActivationHandle | null,
): vscode.Disposable {
  if (!handle) return new vscode.Disposable(() => {});

  const state = buildParticipantState(handle);
  handle.registerCommitQueue(state.queue);

  const participant = vscode.chat.createChatParticipant(
    PARTICIPANT_ID,
    (request, _ctx, stream, token) => handleRequest(state, request, stream, token),
  );
  return participant;
}

function buildParticipantState(handle: ActivationHandle): ParticipantState {
  const hostSessionId =
    vscode.workspace.workspaceFolders?.[0]?.uri.toString() ??
    process.cwd();
  const sessionId = deriveSessionId("copilot-vscode", hostSessionId);

  const cache = new RecallCache();
  const queue = new CommitQueue({
    sessionId,
    client: handle.client,
    threshold: handle.cfg.commitTokenThreshold,
    async: handle.cfg.writePathAsync,
    logger: handle.logger,
  });

  handle.logger.log("participant_state_built", { sessionId, hostSessionId });

  return {
    cfg: handle.cfg,
    client: handle.client,
    cache,
    queue,
    sessionId,
    logger: handle.logger,
  };
}

async function handleRequest(
  state: ParticipantState,
  request: vscode.ChatRequest,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
): Promise<vscode.ChatResult | undefined> {
  switch (request.command) {
    case "recall":
      return handleRecallCommand(state, request, stream);
    case "store":
      return handleStoreCommand(state, request, stream);
    case "forget":
      return handleForgetCommand(state, request, stream);
    default:
      return handleDefaultRequest(state, request, stream, token);
  }
}

async function handleRecallCommand(
  state: ParticipantState,
  request: vscode.ChatRequest,
  stream: vscode.ChatResponseStream,
): Promise<vscode.ChatResult> {
  const query = request.prompt.trim();
  if (!query) {
    stream.markdown("Usage: `/recall <query>` — searches OpenViking and shows the matched memories.");
    return {};
  }
  const result = await buildRecallContext(state, query);
  if (!result.block) {
    stream.markdown("_No relevant memories found._");
    return {};
  }
  stream.markdown(`\`\`\`\n${result.block}\n\`\`\``);
  return {};
}

async function handleStoreCommand(
  state: ParticipantState,
  request: vscode.ChatRequest,
  stream: vscode.ChatResponseStream,
): Promise<vscode.ChatResult> {
  const result = await runStore(state, request.prompt);
  stream.markdown(result.ok ? `✓ ${result.message}` : `⚠️ ${result.message}`);
  return {};
}

async function handleForgetCommand(
  state: ParticipantState,
  request: vscode.ChatRequest,
  stream: vscode.ChatResponseStream,
): Promise<vscode.ChatResult> {
  const result = await runForget(state, request.prompt);
  stream.markdown(result.ok ? `✓ ${result.message}` : `⚠️ ${result.message}`);
  return {};
}

async function handleDefaultRequest(
  state: ParticipantState,
  request: vscode.ChatRequest,
  stream: vscode.ChatResponseStream,
  token: vscode.CancellationToken,
): Promise<vscode.ChatResult | undefined> {
  // 1. Build the recall block and send it as the first stream chunk so
  //    the model sees recalled context before generating its reply.
  const recall = await buildRecallContext(state, request.prompt);
  if (recall.block) {
    stream.markdown(`${recall.block}\n\n`);
  }

  // 2. Delegate to the LM the user has selected for this chat.
  const messages: vscode.LanguageModelChatMessage[] = [];
  if (recall.block) {
    messages.push(
      vscode.LanguageModelChatMessage.User(
        `${recall.block}\n\nUse the memories above where they help. Cite \`viking://\` URIs when relevant.`,
      ),
    );
  }
  messages.push(vscode.LanguageModelChatMessage.User(request.prompt));

  let assistantText = "";
  try {
    const lmRes = await request.model.sendRequest(messages, {}, token);
    for await (const part of lmRes.text) {
      assistantText += part;
      stream.markdown(part);
    }
  } catch (err) {
    state.logger.log("lm_send_failed", {
      message: err instanceof Error ? err.message : String(err),
    });
    return { errorDetails: { message: "Language model request failed." } };
  }

  // 3. Capture the turn after the stream completes. Sanitise + filter
  //    via the shared transcript canonicaliser so the recall block we
  //    just injected never lands back in the captured user message.
  await captureTurn(state, request.prompt, assistantText);
  return {};
}

async function captureTurn(
  state: ParticipantState,
  userText: string,
  assistantText: string,
): Promise<void> {
  if (!state.cfg.autoCapture) return;
  const turns = canonicaliseTranscript(
    [
      { role: "user", text: userText },
      { role: "assistant", text: assistantText },
    ],
    {
      captureAssistantTurns: state.cfg.captureAssistantTurns,
      captureMaxLength: state.cfg.captureMaxLength,
    },
  );
  if (turns.length === 0) return;
  await state.queue.enqueue(turns);
}
