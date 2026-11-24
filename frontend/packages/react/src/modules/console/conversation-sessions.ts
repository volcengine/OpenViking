import { useEffect, useMemo, useRef, type RefObject } from 'react';
import type { VikingBotSessions } from '../../components/viking-bot/capabilities.js';
import type {
  VikingBotChatResult,
  VikingBotSessionsResult,
} from './chat/types.js';

export function useSessionActionScope(
  refresh: VikingBotSessionsResult['refresh'],
  host: VikingBotSessions | undefined,
) {
  const scopeKey = host?.scopeKey;
  const rename = scopeKey === undefined ? host?.onRename : undefined;
  const remove = scopeKey === undefined ? host?.onRemove : undefined;
  return useMemo(
    () => ({ refresh, scopeKey, rename, remove }),
    [refresh, scopeKey, rename, remove],
  );
}

export function useSessionSelection({
  chat,
  host,
  enabled,
  resetInput,
}: {
  chat: Pick<VikingBotChatResult, 'sessionId' | 'reset' | 'selectSession'>;
  host?: VikingBotSessions;
  enabled: boolean;
  resetInput: () => void;
}) {
  const { sessionId, reset, selectSession } = chat;
  const activeId = useRef(sessionId);
  activeId.current = sessionId;
  const selection = useMemo(
    () => ({
      prop: undefined as string | undefined,
      notified: undefined as string | undefined,
      pending: false,
      select: selectSession,
    }),
    [selectSession],
  );
  const selectedId = host?.currentId;
  // Follow the host's URL-selected remote session, including Back/Forward.
  // A key would also tear down the stream when the URL echoes a newly created ID.
  // The controller cancels old requests on selection changes and unmount.
  useEffect(() => {
    if (!enabled || selection.prop === selectedId) return;
    selection.prop = selectedId;
    selection.pending = sessionId !== selectedId;
    if (!selection.pending) return;
    resetInput();
    if (selectedId) selection.select(selectedId);
    else reset();
  }, [enabled, selection, selectedId, sessionId, reset, resetInput]);
  const onSelect = host?.onSelect;
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
  const switching = Boolean(
    selectedId && selection.prop !== selectedId && sessionId !== selectedId,
  );
  return {
    activeId,
    switching,
    select: (id: string) => {
      selection.notified = id;
      selection.select(id);
    },
  };
}

export function conversationSessions({
  host,
  sessions,
  scopeKey,
  activeId,
  select,
  reset,
  resetInput,
}: {
  host: VikingBotSessions | undefined;
  sessions: VikingBotSessionsResult;
  scopeKey: VikingBotSessions['scopeKey'];
  activeId: RefObject<string | undefined>;
  select: (id: string) => void;
  reset: () => void;
  resetInput: () => void;
}): VikingBotSessions {
  const onNew = () => {
    activeId.current = undefined;
    resetInput();
    reset();
    host?.onNew();
  };
  return {
    ...host,
    scopeKey,
    items: host?.items ?? sessions.sessions,
    currentId: activeId.current,
    isLoading: host?.isLoading ?? sessions.isLoading,
    error: host?.error ?? sessions.error?.message,
    onOpenHistory: () => {
      host?.onOpenHistory?.();
      void sessions.refresh();
    },
    onSelect: (id) => {
      activeId.current = id;
      resetInput();
      select(id);
      host?.onSelect(id);
    },
    onNew,
    onRename: host ? host.onRename : sessions.rename,
    onRemove: host
      ? host.onRemove
      : async (id) => {
          if (!(await sessions.remove(id))) return false;
          if (activeId.current === id) onNew();
          return true;
        },
  };
}
