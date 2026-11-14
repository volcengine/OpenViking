import type { SetStateAction } from 'react';
import { useEffect, useMemo, useRef } from 'react';
import { useVikingBotChat } from './chat/use-viking-bot-chat.js';
import { useVikingBotSessions } from './chat/use-viking-bot-sessions.js';
import { useAttachmentUpload } from './chat/use-attachment-upload.js';
import type { VikingBotProps } from '../../components/viking-bot/types.js';
import type { ConsoleProps } from './types.js';

/** Console owns chat requests and sessions. */
export function useConversation(
  {
    conversation,
    chatOptions,
    scopeKey,
    requestOptions,
    locale,
    modes,
  }: ConsoleProps,
  setDraft: (value: SetStateAction<string>) => void,
): Partial<VikingBotProps> {
  const enabled =
    (modes === undefined || modes.includes('conversation')) &&
    conversation?.messages === undefined &&
    conversation?.onSend === undefined;
  const options = {
    ...chatOptions,
    locale: chatOptions?.locale ?? locale,
    enabled,
    scopeKey: chatOptions?.scopeKey ?? scopeKey,
    requestOptions: chatOptions?.requestOptions ?? requestOptions,
  };
  const chat = useVikingBotChat(options);
  const uploads = useAttachmentUpload(
    enabled &&
      !conversation?.onFilesAdded &&
      conversation?.attachments === undefined
      ? chatOptions?.service?.attachmentUpload
      : undefined,
    chat.send,
    chat.signal,
  );
  const clearUploads = uploads.clear;
  const { sessionId, reset, selectSession } = chat;
  const selection = useMemo(
    () => ({
      prop: undefined as string | undefined,
      notified: undefined as string | undefined,
      pending: false,
      select: selectSession,
    }),
    [selectSession],
  );
  const sessions = useVikingBotSessions({
    ...options,
    enabled: enabled && !conversation?.sessions,
  });
  const submission = useRef(0);
  const owner = useMemo(() => ({ send: chat.send, active: true }), [chat.send]);
  // Draft callbacks belong to the same controller as the request, including unmount.
  useEffect(() => {
    owner.active = true;
    return () => {
      owner.active = false;
    };
  }, [owner]);
  const selectedId = conversation?.sessions?.currentId;
  // Follow the host's URL-selected remote session, including Back/Forward.
  // A key would also tear down the stream when the URL echoes a newly created ID.
  // The controller cancels old requests on selection changes and unmount.
  useEffect(() => {
    if (!enabled || selection.prop === selectedId) return;
    selection.prop = selectedId;
    selection.pending = sessionId !== selectedId;
    if (!selection.pending) return;
    submission.current++;
    if (selectedId) selection.select(selectedId);
    else reset();
    clearUploads();
    setDraft('');
  }, [
    enabled,
    selection,
    selectedId,
    sessionId,
    reset,
    clearUploads,
    setDraft,
  ]);
  const onSelect = conversation?.sessions?.onSelect;
  // Report asynchronously created remote IDs to the host router while streaming,
  // before send() settles. No event handler receives that intermediate ID.
  // This starts no resources; notified suppresses duplicate router callbacks.
  useEffect(() => {
    if (!enabled || (selection.pending && sessionId !== selection.prop)) return;
    selection.pending = false;
    if (selection.notified === sessionId) return;
    selection.notified = sessionId;
    if (sessionId && sessionId !== selectedId) onSelect?.(sessionId);
  }, [enabled, selection, selectedId, sessionId, onSelect]);
  if (!enabled) return conversation ?? {};
  const error = chat.messages.some(
    (message) => message.clientError === chat.error,
  )
    ? chat.persistenceError
    : (chat.error ?? chat.persistenceError);
  return {
    ...conversation,
    ...uploads.props,
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
      if (!followingUp && uploads.props) uploads.removeSent(draft.attachments);
      setDraft((current) =>
        current.trim() === draft.text.trim() ? '' : current,
      );
      void owner.send(draft).then((success) => {
        if (!owner.active) return;
        if (!success) {
          if (followingUp && token === submission.current)
            setDraft((current) => (current === '' ? draft.text : current));
          return;
        }
        if (followingUp && uploads.props) uploads.removeSent(draft.attachments);
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
      ...conversation?.sessions,
      items: conversation?.sessions?.items ?? sessions.sessions,
      currentId: chat.sessionId,
      isLoading: conversation?.sessions?.isLoading ?? sessions.isLoading,
      error: conversation?.sessions?.error ?? sessions.error?.message,
      onOpenHistory: () => {
        conversation?.sessions?.onOpenHistory?.();
        void sessions.refresh();
      },
      onSelect: (id) => {
        submission.current++;
        selection.notified = id;
        chat.selectSession(id);
        clearUploads();
        setDraft('');
        onSelect?.(id);
      },
      onNew: () => {
        submission.current++;
        chat.reset();
        clearUploads();
        setDraft('');
        conversation?.sessions?.onNew?.();
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
