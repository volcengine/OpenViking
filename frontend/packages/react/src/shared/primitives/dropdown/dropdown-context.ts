import { createContext } from 'react';

export const DropdownContext = createContext<(() => void) | undefined>(
  undefined,
);
