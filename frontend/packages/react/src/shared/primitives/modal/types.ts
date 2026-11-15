import type { ReactNode, RefObject } from 'react';
import type { Locale } from '../../locale.js';

/** A controlled modal with the same overlay capabilities as the internal Drawer. */
export interface ModalProps {
  open: boolean;
  children: ReactNode;
  /** Accessible name; render a visible heading inside children. */
  title: string;
  /** Theme anchor; focus returns to the element active when the modal opened. */
  triggerRef: RefObject<HTMLElement | null>;
  id?: string;
  className?: string;
  locale?: Locale;
  /** Overrides the localized close button name, including an explicit empty string. */
  closeLabel?: string;
  closeIcon?: ReactNode;
  /** Allow pointer clicks on the mask to close the modal. Defaults to true. */
  maskClosable?: boolean;
  /** Close intent from the close button, Escape or the mask when enabled. */
  onClose?: () => void;
  /** Runs once the exit transition has finished and the panel is gone. */
  afterClose?: () => void;
}
