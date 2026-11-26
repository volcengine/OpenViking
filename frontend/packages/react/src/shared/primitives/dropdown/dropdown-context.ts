import { createContext, type RefObject } from 'react';

export const DropdownContext = createContext<{
  close: () => void;
  menu: RefObject<HTMLDivElement | null>;
} | null>(null);
