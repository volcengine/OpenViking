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
  const [local] = useState(() => createQuickInputTracker(value));
  const tracker = ownsScope ? local : (inherited ?? local);
  return {
    tracker,
    select(input: VikingBotQuickInput) {
      if (onSelect) {
        if (tracker === local) tracker.release();
        onSelect(input);
      } else onChange(tracker.insert(value, input.prompt));
    },
  };
}
