import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import type { VikingBotReference } from '../../../components/viking-bot/index.js';
import type { ConsoleChatOptions } from '../types.js';

const emptyReferences: readonly VikingBotReference[] = [];

export function useReferenceSelection(
  scope: object,
  selection: ConsoleChatOptions['referenceSelection'],
) {
  const [selected, setSelected] = useState<{
    scope: typeof scope;
    items: readonly VikingBotReference[];
  }>();
  const onChange = useCallback(
    (items: readonly VikingBotReference[]) => setSelected({ scope, items }),
    [scope],
  );
  const onSelectionChange = selection?.onChange;
  const latestSelection = useRef({ scope, selection, onSelectionChange });
  useLayoutEffect(() => {
    latestSelection.current = { scope, selection, onSelectionChange };
  }, [scope, selection, onSelectionChange]);
  const clearReferences = useCallback(
    (sent?: readonly VikingBotReference[]) => {
      const current = latestSelection.current;
      if (current.scope !== scope) return;
      if (current.onSelectionChange) {
        const items = current.selection?.selected ?? emptyReferences;
        const remaining = sent
          ? items.filter((item) => !sent.includes(item))
          : [];
        if (!sent || remaining.length !== items.length)
          current.onSelectionChange(remaining);
        return;
      }
      setSelected((current) => {
        if (current?.scope !== scope) return current;
        if (!sent) return undefined;
        const items = current.items.filter((item) => !sent.includes(item));
        return items.length === current.items.length
          ? current
          : { scope, items };
      });
    },
    [scope],
  );
  return {
    selected:
      selection?.selected ??
      (selected?.scope === scope ? selected.items : emptyReferences),
    onChange: onSelectionChange ?? onChange,
    clearReferences,
  };
}
