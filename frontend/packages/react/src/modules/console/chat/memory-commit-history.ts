import { useEffect, useState } from 'react';
import type { VikingBotMessage } from '../../../components/viking-bot/model.js';
import { readTaskReferences } from '../../../shared/task-tracking/read-references.js';
import {
  isTaskTerminal,
  type TaskReference,
} from '../../../shared/task-tracking/types.js';
import type { VikingBotChatResult } from './types.js';

export type Submission = {
  messageId: string;
  status: 'pending' | 'submitted' | 'success';
  taskKey?: string;
};

export function latestCommitMessage(messages: readonly VikingBotMessage[]) {
  let latest: string | undefined;
  for (let index = messages.length - 1; index >= 0; index--) {
    const message = messages[index]!;
    if (message.role === 'user') break;
    if (!latest && message.role === 'assistant' && message.interrupted) return;
    const alreadyCommitted = message.parts.some((part) => {
      if (part.type !== 'tool' && part.type !== 'tool_result') return false;
      if (part.type === 'tool' ? part.status === 'error' : part.isError)
        return false;
      return (part.tasks ?? readTaskReferences(part.name, part.output)).some(
        (task) => task.kind === 'memory' && task.status === 'completed',
      );
    });
    if (alreadyCommitted) return;
    if (
      latest === undefined &&
      message.role === 'assistant' &&
      !message.streaming &&
      !message.clientError &&
      message.parts.some((part) => part.type === 'text' && part.text.trim())
    )
      latest = message.id;
  }
  return latest;
}

export function useMemoryCommitHistory(
  chat: VikingBotChatResult,
  owner: object,
  messageId: string | undefined,
  track: (task: TaskReference) => string,
  hasSubmission: boolean,
) {
  const { sessionId, signal, getSessionCommitTask } = chat;
  const historyKey =
    chat.responseComplete === undefined ? chat.historyKey : undefined;
  const shouldRestore = Boolean(
    historyKey && messageId && getSessionCommitTask && !hasSubmission,
  );
  const [restored, setRestored] = useState<{
    owner: typeof owner;
    historyKey: object;
    messageId: string;
    submission?: Submission;
  }>();
  const historyState =
    restored?.owner === owner &&
    restored.historyKey === chat.historyKey &&
    restored.messageId === messageId
      ? restored
      : undefined;
  // History loading supplies message data. Commit state needs a separate lookup
  // for the eligible reply after Console applies its display and cache rules.
  // History can load on mount without a selection event. Abort the lookup when
  // the target reply, history scope, or component goes away.
  useEffect(() => {
    if (
      !shouldRestore ||
      !historyKey ||
      !messageId ||
      !sessionId ||
      !getSessionCommitTask
    )
      return;
    const request = new AbortController();
    const restoreSignal = signal
      ? AbortSignal.any([request.signal, signal])
      : request.signal;
    void (async () => {
      let submission: Submission | undefined;
      try {
        const task = await getSessionCommitTask(sessionId, restoreSignal);
        if (restoreSignal.aborted) return;
        if (task?.status === 'completed') {
          submission = { messageId, status: 'success' };
        } else if (task && !isTaskTerminal(task.status)) {
          submission = {
            messageId,
            status: 'submitted',
            taskKey: track({ ...task, kind: 'memory' }),
          };
        }
      } catch {
        // A restore failure still allows the user to submit from the loaded history.
      }
      if (!restoreSignal.aborted)
        setRestored({ owner, historyKey, messageId, submission });
    })();
    return () => request.abort();
  }, [
    shouldRestore,
    historyKey,
    messageId,
    sessionId,
    getSessionCommitTask,
    signal,
    owner,
    track,
  ]);
  const restoring = shouldRestore && !historyState;
  return { restoring, submission: historyState?.submission };
}
