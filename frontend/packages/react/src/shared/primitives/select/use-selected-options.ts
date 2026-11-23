import { useState, type ReactNode } from 'react';
import { collectOptions } from './collection.js';
import type { SelectSelectedOption, SelectValue } from './types.js';

/** Retain only selected metadata when a remote result page replaces children. */
export function useSelectedOptions<V extends SelectValue>(
  children: ReactNode,
  values: readonly V[],
) {
  const [previous, setPrevious] = useState<SelectSelectedOption<V>[]>([]);
  const options = collectOptions(children);
  const selected = values.map(
    (value) =>
      options.find((option) => option.value === value) ??
      previous.find((option) => option.value === value) ?? {
        value,
        label: String(value),
        disabled: false,
      },
  ) as SelectSelectedOption<V>[];
  // Adjust this component's state during render rather than mutating a ref or
  // mirroring props in an Effect; abandoned renders cannot leak cached labels.
  if (
    selected.length !== previous.length ||
    selected.some((option, index) => {
      const old = previous[index];
      return (
        option.value !== old?.value ||
        option.label !== old.label ||
        option.disabled !== old.disabled
      );
    })
  )
    setPrevious(selected);
  return selected;
}
