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
  const items =
    selection?.selected ??
    (selected?.scope === scope ? selected.items : emptyReferences);
  const onSelectionChange = selection?.onChange;
  const latest = useRef({
    scope,
    items,
    onSelectionChange,
    identities: new Map<string, symbol>(),
  });
  // A URI keeps its identity until it leaves the committed selection.
  useLayoutEffect(() => {
    const previous = latest.current;
    const identities = new Map(
      items.map(({ uri }) => [
        uri,
        (previous.scope === scope ? previous.identities.get(uri) : undefined) ??
          Symbol(uri),
      ]),
    );
    latest.current = { scope, items, onSelectionChange, identities };
  }, [scope, items, onSelectionChange]);

  const clearReferences = useCallback(() => {
    const current = latest.current;
    if (current.scope !== scope) return;
    if (current.onSelectionChange) current.onSelectionChange([]);
    else setSelected(undefined);
  }, [scope]);

  const prepareReferenceClear = (sent: readonly VikingBotReference[]) => {
    const submitted = new Map(
      sent.map(({ uri }) => [uri, latest.current.identities.get(uri)]),
    );
    return () => {
      const current = latest.current;
      if (current.scope !== scope) return;
      const remaining = current.items.filter(({ uri }) => {
        const identity = submitted.get(uri);
        return (
          identity === undefined || current.identities.get(uri) !== identity
        );
      });
      if (remaining.length === current.items.length) return;
      if (current.onSelectionChange) current.onSelectionChange(remaining);
      else setSelected({ scope, items: remaining });
    };
  };

  return {
    selected: items,
    onChange: onSelectionChange ?? onChange,
    clearReferences,
    prepareReferenceClear,
  };
}
