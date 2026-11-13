import type { ReactElement, ReactNode } from 'react';

/** Command metadata; execution state and errors belong to the caller. */
export interface DropdownItem {
  key: string;
  label: ReactNode;
  icon?: ReactNode;
  disabled?: boolean;
  className?: string;
  onClick: () => unknown;
}

/** Internal command menu. No business operations or public package entry. */
export interface DropdownProps {
  children: ReactElement;
  groups: readonly (readonly DropdownItem[])[];
  disabled?: boolean;
  /** Hover also allows click and keyboard input; click omits pointer preview. */
  trigger?: 'hover' | 'click';
  className?: string;
  menuClassName?: string;
}
