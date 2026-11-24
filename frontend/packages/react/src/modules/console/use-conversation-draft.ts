import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type SetStateAction,
} from 'react';
import type { VikingBotChatResult } from './chat/types.js';

export function useDraftRequestOwner(send: VikingBotChatResult['send']) {
  const owner = useMemo(() => ({ send, active: true }), [send]);
  // Request continuations can settle after a controller change or an unmount.
  // Invalidate the old owner on cleanup, when those continuations can no longer update this draft.
  useEffect(() => {
    owner.active = true;
    return () => {
      owner.active = false;
    };
  }, [owner]);
  return owner;
}

/** Local text follows its conversation owner without resetting command state. */
export function useConversationDraft(
  controller: object,
  identity?: object,
  scopeKey?: string,
) {
  const effectiveIdentity = identity ?? controller;
  const owner = useMemo(
    () => ({ identity: effectiveIdentity, scopeKey }),
    [effectiveIdentity, scopeKey],
  );
  const [draft, setDraft] = useState({ owner, value: '' });
  if (draft.owner !== owner) setDraft({ owner, value: '' });
  const setConversationDraft = useCallback(
    (next: SetStateAction<string>) => {
      setDraft((current) => {
        if (current.owner !== owner) return current;
        return {
          owner,
          value: typeof next === 'function' ? next(current.value) : next,
        };
      });
    },
    [owner],
  );
  return {
    conversationDraft: draft.owner === owner ? draft.value : '',
    setConversationDraft,
  };
}
