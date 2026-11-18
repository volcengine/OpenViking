import { useLocalDraftValidation } from './use-local-draft-validation.js';
import { renderConversationFeedback } from './conversation-feedback.js';
import { renderConversationHistory } from './conversation-history.js';
import { useLocale } from '../../shared/locale-context.js';
import type { ReactNode, SetStateAction } from 'react';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useVikingBotChat } from './chat/use-viking-bot-chat.js';
import { useMemoryCommit } from './chat/use-memory-commit.js';
import { useVikingBotSessions } from './chat/use-viking-bot-sessions.js';
import { useHitl } from './hitl/use-hitl.js';
import { useAttachmentUpload } from './chat/use-attachment-upload.js';
import { compileQuickInputProps } from './compile-quick-inputs.js';
import { useConversationReferences } from './use-conversation-references.js';
import {
  conversationSessions,
  useSessionActionScope,
  useSessionSelection,
} from './conversation-sessions.js';
import type { VikingBotProps } from '../../components/viking-bot/types.js';
import type { ConsoleProps } from './types.js';

/** Console owns chat requests and sessions. */
export function useConversation(
  props: ConsoleProps,
  setDraft: (value: SetStateAction<string>) => void,
  managedDraft = '',
): Partial<VikingBotProps> & { feedback?: ReactNode } {
  const { conversation, chatOptions, scopeKey, requestOptions, locale, modes } =
    props;
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
    selection: chatOptions?.referenceSelection,
  });
  const hitl = useHitl(
    chat,
    enabled && conversation?.interaction === undefined,
    useLocale(options.locale),
    chatOptions?.hitl,
    {
      requestOptions: {
        ...options.requestOptions,
        signal: chat.signal ?? options.requestOptions?.signal,
      },
      scopeKey: options.scopeKey,
      onOpenUri: conversation?.onOpenUri,
      customChat: Boolean(chatOptions?.service),
    },
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
  const hostSessions = conversation?.sessions;
  const sessions = useVikingBotSessions({
    ...options,
    enabled: enabled && !hostSessions,
  });
  const sessionScope = useSessionActionScope(sessions.refresh, hostSessions);
  const submission = useRef(0);
  const resetInput = useCallback(() => {
    submission.current++;
    clearUploads();
    clearReferences();
    setDraft('');
  }, [clearUploads, clearReferences, setDraft]);
  const owner = useMemo(() => ({ send: chat.send, active: true }), [chat.send]);
  // Draft callbacks belong to the same controller as the request, including unmount.
  useEffect(() => {
    owner.active = true;
    return () => {
      owner.active = false;
    };
  }, [owner]);
  const { activeId, select, switching } = useSessionSelection({
    chat,
    host: hostSessions,
    enabled,
    resetInput,
  });
  const memoryCommit = useMemoryCommit({
    chat,
    messages: hitl.messages,
    enabled: enabled && chatOptions?.memoryCommit !== false,
    switching,
    locale: conversationLocale,
    options,
  });
  if (!enabled) {
    const scoped = hostSessions && { ...hostSessions, scopeKey: sessionScope };
    return scoped
      ? { ...conversation, sessions: scoped }
      : (conversation ?? {});
  }
  const isSending =
    switching || chat.status === 'streaming' || chat.status === 'loading';
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
    messages: memoryCommit.messages,
    onMessageAction: (id, key) => {
      if (!memoryCommit.onMessageAction(id, key))
        conversation?.onMessageAction?.(id, key);
    },
    messageList:
      conversation?.messageList === undefined
        ? renderConversationHistory(chat, switching, conversationLocale)
        : conversation.messageList,
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
    sessions: conversationSessions({
      host: hostSessions,
      sessions,
      scopeKey: sessionScope,
      activeId,
      select,
      reset: chat.reset,
      resetInput,
    }),
    feedback: (
      <>
        {renderConversationFeedback(chat, validation.feedback, {
          locale: feedbackLocale,
          scopeKey,
        })}
        {memoryCommit.feedback}
      </>
    ),
  };
}
