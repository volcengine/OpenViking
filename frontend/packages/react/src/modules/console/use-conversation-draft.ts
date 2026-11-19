import { useCallback, useMemo, useState, type SetStateAction } from 'react';

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
