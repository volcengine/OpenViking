import { createContext, useContext, useState } from 'react';
import { createQuickInputTracker } from './quick-input-tracker.js';
import type { VikingBotQuickInput } from './capabilities.js';

export const QuickInputScope = createContext<ReturnType<
  typeof createQuickInputTracker
> | null>(null);

/** Track only the prompt this instance inserted, leaving custom text intact. */
export function useQuickInput(
  value: string,
  onChange: (text: string) => void,
  onSelect?: (input: VikingBotQuickInput) => void,
  ownsScope = false,
) {
  const inherited = useContext(QuickInputScope);
  const [local, setLocal] = useState(() => ({
    value,
    tracker: createQuickInputTracker(value),
  }));
  // Record each rendered value, including clear/restore with a custom composer.
  // Fork metadata instead of mutating it during render: abandoned renders must
  // not release the committed prompt. Editor events have already mapped their
  // changes, so copying that snapshot preserves their caret-based ownership.
  if (local.value !== value)
    setLocal({
      value,
      tracker: createQuickInputTracker(value, local.tracker.snapshot()),
    });
  const tracker = ownsScope ? local.tracker : (inherited ?? local.tracker);
  return {
    tracker,
    select(input: VikingBotQuickInput) {
      if (onSelect) {
        if (tracker === local.tracker) tracker.release();
        onSelect(input);
      } else onChange(tracker.insert(value, input.prompt));
    },
  };
}
