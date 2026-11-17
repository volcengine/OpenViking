import type { RefObject } from 'react';
import type { VikingBotSessions } from '../../components/viking-bot/capabilities.js';
import type { VikingBotSessionsResult } from './chat/types.js';

export function conversationSessions({
  host,
  sessions,
  activeId,
  select,
  reset,
  resetInput,
}: {
  host: VikingBotSessions | undefined;
  sessions: VikingBotSessionsResult;
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
