import type { SetStateAction } from 'react';
import { useEffect, useMemo, useRef } from 'react';
import { useVikingBotChat, useVikingBotSessions } from '../viking-bot/index.js';
import type { VikingBotProps } from '../../components/viking-bot/types.js';
import type { ConsoleProps } from './types.js';

/** Both Console and standalone hosts consume the same public headless hooks. */
export function useConversation(
  { conversation, chatOptions, scopeKey, requestOptions, locale }: ConsoleProps,
  setDraft: (value: SetStateAction<string>) => void,
): Partial<VikingBotProps> {
  const enabled =
    conversation?.messages === undefined && conversation?.onSend === undefined;
  const options = {
    ...chatOptions,
    locale: chatOptions?.locale ?? locale,
    enabled,
    scopeKey: chatOptions?.scopeKey ?? scopeKey,
    requestOptions: chatOptions?.requestOptions ?? requestOptions,
  };
  const chat = useVikingBotChat(options);
  const sessions = useVikingBotSessions(options);
  const submission = useRef(0);
  const owner = useMemo(() => ({ send: chat.send, active: true }), [chat.send]);
  // Draft callbacks belong to the same controller as the request, including unmount.
  useEffect(() => {
    owner.active = true;
    return () => {
      owner.active = false;
    };
  }, [owner]);
  if (!enabled) return conversation ?? {};
  const error = chat.error ?? chat.persistenceError;
  return {
    ...conversation,
    messages: chat.messages,
    streaming: chat.streaming,
    listKey: `${scopeKey ?? ''}:${chat.sessionId ?? 'new'}`,
    isSending: chat.status === 'streaming' || chat.status === 'loading',
    canSendWhileStreaming:
      chat.canSteer && chat.status === 'streaming' && Boolean(chat.sessionId),
    isSubmitting: chat.isSubmitting,
    onSend: (draft) => {
      const token = ++submission.current;
      const followingUp = chat.status === 'streaming';
      if (chat.canSteer)
        setDraft((current) =>
          current.trim() === draft.text.trim() ? '' : current,
        );
      void owner.send(draft).then((success) => {
        if (!owner.active) return;
        if (!success) {
          if (chat.canSteer && token === submission.current)
            setDraft((current) => (current === '' ? draft.text : current));
          return;
        }
        if (!chat.canSteer)
          setDraft((current) =>
            current.trim() === draft.text.trim() ? '' : current,
          );
        if (!followingUp) void sessions.refresh();
      });
    },
    onCancel:
      chat.canInterrupt && chat.status === 'streaming'
        ? () => {
            void chat.abort();
          }
        : undefined,
    sessions: {
      items: sessions.sessions,
      currentId: chat.sessionId,
      isLoading: sessions.isLoading,
      error: sessions.error?.message,
      onOpenHistory: () => {
        void sessions.refresh();
      },
      onSelect: (id) => {
        submission.current++;
        chat.selectSession(id);
        setDraft('');
      },
      onNew: () => {
        submission.current++;
        chat.reset();
        setDraft('');
      },
    },
    footer:
      error || conversation?.footer ? (
        <>
          {error ? (
            <p className="ov-console__error" role="alert">
              {error.message}
            </p>
          ) : null}
          {conversation?.footer}
        </>
      ) : undefined,
  };
}
