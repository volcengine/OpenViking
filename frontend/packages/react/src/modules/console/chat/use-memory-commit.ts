import { useEffect, useMemo, useRef, useState } from 'react';
import { isAbortError } from '../../../client/response.js';
import type { VikingBotMessage } from '../../../components/viking-bot/model.js';
import type { Locale } from '../../../shared/locale.js';
import { useMonitoringBinding } from '../../../shared/monitoring/context.js';
import { readData, tagData } from '../../../shared/monitoring/data.js';
import { useNotification } from '../../../shared/primitives/notification/index.js';
import { useTaskTracking } from '../../../shared/task-tracking/context.js';
import { isTaskTerminal } from '../../../shared/task-tracking/types.js';
import { resolveConsoleLocale } from '../labels.js';
import type { UseVikingBotChatOptions, VikingBotChatResult } from './types.js';
import {
  memoryCommitAction,
  memoryCommitActionKey,
} from './memory-commit-action.js';

import {
  latestCommitMessage,
  useMemoryCommitHistory,
  type Submission,
} from './memory-commit-history.js';

export function useMemoryCommit({
  chat,
  messages,
  enabled,
  switching,
  locale,
  options,
}: {
  chat: VikingBotChatResult;
  messages: readonly VikingBotMessage[];
  enabled: boolean;
  switching: boolean;
  locale: Locale;
  options: UseVikingBotChatOptions;
}) {
  const monitoring = useMonitoringBinding();
  const commit = enabled ? chat.commitSession : undefined;
  const { sessionId } = chat;
  const { tasks, track } = useTaskTracking({
    ...options,
    origin: 'memory-commit',
  });
  const [notification, feedback] = useNotification({ locale });
  const copy = resolveConsoleLocale(locale).memoryCommit;
  const currentCopy = useRef(copy);
  currentCopy.current = copy;
  const owner = useMemo(
    () => ({ commit, track, requests: new Map<string, AbortController>() }),
    [commit, track],
  );
  const [submissions, setSubmissions] = useState<{
    owner: typeof owner;
    bySession: Map<string, Submission>;
  }>();
  // The requests belong to the current client and identity. Cleanup aborts
  // their waits on owner replacement or unmount, which submit handlers cannot observe.
  useEffect(
    () => () => {
      owner.requests.forEach((request) => request.abort());
      owner.requests.clear();
    },
    [owner],
  );
  const submission =
    submissions?.owner === owner && sessionId
      ? submissions.bySession.get(sessionId)
      : undefined;
  const messageId =
    commit &&
    sessionId &&
    !switching &&
    chat.status === 'idle' &&
    chat.responseComplete !== false &&
    !chat.signal?.aborted
      ? latestCommitMessage(messages)
      : undefined;
  const loadedHistory = chat.historyKey && chat.responseComplete === undefined;
  const matchingSubmission =
    !loadedHistory || submission?.messageId === messageId
      ? submission
      : undefined;
  const history = useMemoryCommitHistory(
    chat,
    owner,
    messageId,
    track,
    Boolean(matchingSubmission),
  );
  const { restoring } = history;
  const currentSubmission = matchingSubmission ?? history.submission;
  const task = currentSubmission?.taskKey
    ? tasks.find((item) => item.key === currentSubmission.taskKey)
    : undefined;
  const status: 'idle' | Submission['status'] =
    task?.status === 'completed'
      ? 'success'
      : isTaskTerminal(task?.status)
        ? 'idle'
        : (currentSubmission?.status ?? 'idle');
  const busy = status === 'pending' || status === 'submitted';
  const actionStatus =
    busy || currentSubmission?.messageId === messageId ? status : 'idle';

  function run(message: string) {
    const submit = owner.commit;
    if (
      !submit ||
      !sessionId ||
      message !== messageId ||
      restoring ||
      busy ||
      actionStatus === 'success'
    )
      return;
    if (owner.requests.has(sessionId)) return;
    const request = new AbortController();
    owner.requests.set(sessionId, request);
    const signal = chat.signal
      ? AbortSignal.any([request.signal, chat.signal])
      : request.signal;
    const update = (next?: Submission) =>
      setSubmissions((previous) => {
        const bySession = new Map(
          previous?.owner === owner ? previous.bySession : undefined,
        );
        if (next) bySession.set(sessionId, next);
        else bySession.delete(sessionId);
        return { owner, bySession };
      });
    update({ messageId: message, status: 'pending' });
    void (async () => {
      try {
        const result = await submit(sessionId, signal);
        if (signal.aborted) return;
        if (result.status === 'skipped') {
          const empty = result.reason === 'no_messages';
          update(empty ? { messageId: message, status: 'success' } : undefined);
          notification.info({
            content: empty
              ? currentCopy.current.empty
              : currentCopy.current.skipped,
          });
        } else {
          if (typeof result.taskId !== 'string' || !result.taskId.trim())
            throw new Error('Invalid session commit task ID');
          const taskKey = owner.track({ id: result.taskId, kind: 'memory' });
          update({ messageId: message, status: 'submitted', taskKey });
        }
      } catch (error) {
        if (signal.aborted) return;
        update();
        if (!isAbortError(error))
          notification.error({
            title: currentCopy.current.failed,
            content: error instanceof Error ? error.message : String(error),
          });
      } finally {
        if (owner.requests.get(sessionId) === request)
          owner.requests.delete(sessionId);
      }
    })();
  }

  const projected =
    messageId && !restoring
      ? messages.map((message) => {
          if (message.id !== messageId) return message;
          const actions =
            message.actions ??
            (message.action ? [{ messageId, action: message.action }] : []);
          return {
            ...message,
            actions: [
              ...actions,
              {
                messageId,
                action: memoryCommitAction(copy[actionStatus], actionStatus),
              },
            ],
          };
        })
      : messages;
  tagData(monitoring, projected, readData(monitoring, [messages]));
  return {
    messages: projected,
    onMessageAction: (message: string, key: string) => {
      if (key !== memoryCommitActionKey || !messageId) return false;
      run(message);
      return true;
    },
    feedback,
  };
}
