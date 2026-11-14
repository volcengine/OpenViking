import { createContext } from 'react';
import type { SelectValue } from './types.js';

export const SelectContext = createContext<{
  values: readonly SelectValue[];
  disabled: boolean;
  choose: (value: SelectValue) => void;
} | null>(null);
