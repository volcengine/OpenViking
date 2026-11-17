import { useMemo, type RefObject } from 'react';
import type { VikingBotSessions } from '../../components/viking-bot/capabilities.js';
import type { VikingBotSessionsResult } from './chat/types.js';

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
