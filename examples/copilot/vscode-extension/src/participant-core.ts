/**
 * VS Code-free helpers for the @openviking chat participant.
 *
 * The participant adapter (`participant.ts`) imports `vscode` and
 * dispatches request.command + writes to the chat stream. The core
 * helpers below are pure-async functions that take a ParticipantState
 * and the user's input — easy to unit-test under Vitest without
 * needing @vscode/test-electron.
 */

import {
  CommitQueue,
  type BuildRecallContextOptions,
  type BuildRecallContextResult,
  type DebugLogger,
  type OVClient,
  type PluginConfig,
  type RecallContextState,
  RecallCache,
  buildRecallContextBlock,
} from "@openviking/copilot-shared";

export interface ParticipantRecallState extends RecallContextState {
  cfg: PluginConfig;
  client: OVClient;
  cache: RecallCache;
  logger: DebugLogger;
}

export interface ParticipantState extends ParticipantRecallState {
  queue: CommitQueue;
}

/**
 * Run a recall against OV, rank + dedupe, format the
 * `<openviking-context>` block. Cached by (query, sessionId) so
 * back-to-back calls from the participant + LM tool round-trip OV
 * only once per turn.
 */
export async function buildRecallContext(
  state: ParticipantRecallState,
  query: string,
  opts: BuildRecallContextOptions = {},
): Promise<BuildRecallContextResult> {
  return buildRecallContextBlock(state, query, opts);
}

export interface RunResult {
  ok: boolean;
  message: string;
}

/**
 * Append a user-provided memory text into the OV session and force a
 * commit so it lands as an archived memory immediately. Used by the
 * `/store` slash command.
 */
export async function runStore(
  state: ParticipantState,
  text: string,
): Promise<RunResult> {
  const trimmed = text.trim();
  if (!trimmed) return { ok: false, message: "Nothing to store — message body was empty." };

  const enqueueRes = await state.queue.enqueue([{ role: "user", content: trimmed }]);
  if (enqueueRes.appended === 0) {
    state.logger.log("store_failed_append");
    return { ok: false, message: "Failed to append the message to OpenViking." };
  }

  // Force a flush regardless of token threshold so the memory is
  // immediately archivable on the server side.
  await state.queue.flush();
  state.logger.log("store_committed", { chars: trimmed.length });
  return {
    ok: true,
    message: `Stored ${trimmed.length} character${trimmed.length === 1 ? "" : "s"} into OpenViking memory.`,
  };
}

/**
 * Delete a viking:// URI. Used by the `/forget` slash command.
 * Returns a user-facing message; never throws so the participant
 * stream can render the result inline.
 */
export async function runForget(
  state: ParticipantState,
  rawUri: string,
): Promise<RunResult> {
  const uri = rawUri.trim();
  if (!uri) return { ok: false, message: "Usage: `/forget <viking://...>`" };
  if (!uri.startsWith("viking://")) {
    return { ok: false, message: "URI must start with `viking://`." };
  }

  const res = await state.client.forget(uri);
  if (!res.ok) {
    state.logger.log("forget_failed", { uri, message: res.error.message });
    return { ok: false, message: `Failed to delete ${uri}: ${res.error.message}` };
  }
  state.logger.log("forget_ok", { uri });
  return { ok: true, message: `Deleted ${uri}.` };
}
