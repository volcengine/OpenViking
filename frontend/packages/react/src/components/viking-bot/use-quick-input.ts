import { useRef } from 'react';
import { quickInputText } from './capabilities.js';
import type { VikingBotQuickInput } from './capabilities.js';

/** Track only the prompt this instance inserted, leaving custom text intact. */
export function useQuickInput(
  value: string,
  onChange: (text: string) => void,
  onSelect?: (input: VikingBotQuickInput) => void,
) {
  const previous = useRef<string | undefined>(undefined);
  return (input: VikingBotQuickInput) => {
    if (onSelect) onSelect(input);
    else {
      onChange(quickInputText(value, input, previous.current));
      previous.current = input.prompt;
    }
  };
}
