import type { ButtonHTMLAttributes, ReactElement, ReactNode } from 'react';
import type { PopoverPlacement } from '../popover/types.js';

export interface DropdownProps {
  /** A button, or a component forwarding its ref and events to a button. */
  trigger: ReactElement;
  /** Menu items, separators, fragments and wrappers such as Tooltip. */
  children: ReactNode;
  triggerMode?: 'hover' | 'click';
  disabled?: boolean;
  placement?: PopoverPlacement;
  alignment?: 'center' | 'end';
  open?: boolean;
  className?: string;
  menuClassName?: string;
  onOpenChange?: (open: boolean) => void;
}

export type DropdownItemProps = Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  'role' | 'type' | 'tabIndex'
> & {
  role?: 'menuitem' | 'menuitemradio' | 'menuitemcheckbox';
  closeOnClick?: boolean;
};
