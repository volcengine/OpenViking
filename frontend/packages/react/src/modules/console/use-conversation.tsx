import { useLocalDraftValidation } from './use-local-draft-validation.js';
import { renderConversationFeedback } from './conversation-feedback.js';
import { useLocale } from '../../shared/locale-context.js';
import type { ReactNode, SetStateAction } from 'react';
import { useEffect, useMemo, useRef } from 'react';
import { useVikingBotChat } from './chat/use-viking-bot-chat.js';
import { useVikingBotSessions } from './chat/use-viking-bot-sessions.js';
import { useHitl } from './hitl/use-hitl.js';
import { useAttachmentUpload } from './chat/use-attachment-upload.js';
import { compileQuickInputProps } from './compile-quick-inputs.js';
import { useConversationReferences } from './use-conversation-references.js';
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
  managedDraft = '',
): Partial<VikingBotProps> & { feedback?: ReactNode } {
  const conversationLocale = useLocale(conversation?.locale ?? locale);
  const feedbackLocale = useLocale(chatOptions?.locale ?? locale);
  const validation = useLocalDraftValidation(
    !chatOptions?.service,
    feedbackLocale,
  );
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
  const { references, clearReferences } = useConversationReferences({
    enabled: enabled && !conversation?.references,
    requestOptions: options.requestOptions,
    scopeKey: options.scopeKey,
  });
  const hitl = useHitl(
    chat,
    enabled && conversation?.interaction === undefined,
    useLocale(options.locale),
    chatOptions?.hitl,
  );
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
    clearReferences();
    setDraft('');
  }, [
    enabled,
    selection,
    selectedId,
    sessionId,
    reset,
    clearUploads,
    clearReferences,
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
  const isSending = chat.status === 'streaming' || chat.status === 'loading';
  return {
    ...conversation,
    ...uploads.props,
    ...compileQuickInputProps({
      conversation,
      locale: conversationLocale,
      managedDraft,
      busy: isSending || chat.isSubmitting,
      setDraft,
    }),
    references: conversation?.references ?? references,
    messages: hitl.messages,
    interaction: conversation?.interaction ?? hitl.interaction,
    streaming: chat.streaming,
    listKey: `${scopeKey ?? ''}:${chat.sessionId ?? 'new'}`,
    isSending,
    canSendWhileStreaming:
      chat.canSteer && chat.status === 'streaming' && Boolean(chat.sessionId),
    isSubmitting: chat.isSubmitting,
    onSend: (draft) => {
      if (!validation.validate(draft)) return;
      const token = ++submission.current;
      const followingUp = chat.status === 'streaming';
      if (!followingUp) {
        if (uploads.props) uploads.removeSent(draft.attachments);
        clearReferences(draft.references ?? []);
      }
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
        if (followingUp) clearReferences(draft.references ?? []);
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
        clearReferences();
        setDraft('');
        onSelect?.(id);
      },
      onNew: () => {
        submission.current++;
        chat.reset();
        clearUploads();
        clearReferences();
        setDraft('');
        conversation?.sessions?.onNew?.();
      },
    },
    feedback: renderConversationFeedback(chat, validation.feedback, {
      locale: feedbackLocale,
      scopeKey,
    }),
  };
}
