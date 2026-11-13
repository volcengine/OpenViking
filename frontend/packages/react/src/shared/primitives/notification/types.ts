import type { CSSProperties, ReactNode } from 'react';
import type { Locale } from '../../locale.js';

export type NotificationType =
  'info' | 'success' | 'warning' | 'error' | 'normal' | 'loading';

export interface NotificationConfig {
  content: ReactNode;
  id?: string;
  title?: ReactNode;
  icon?: ReactNode;
  showIcon?: boolean;
  duration?: number;
  closable?: boolean;
  closeIcon?: ReactNode;
  className?: string;
  style?: CSSProperties;
  /** Called immediately when the close button or auto-dismiss timer removes the notice. */
  onDismiss?: () => void;
  /** Called after the exit animation, including programmatic removal and clearing. */
  onClose?: () => void;
  btn?: ReactNode;
}

export interface NotificationItem extends NotificationConfig {
  id: string;
  type: NotificationType;
}

export interface NotificationLabels {
  region: string;
  close: string;
}

export interface NotificationOptions {
  locale?: Locale;
  labels?: Partial<NotificationLabels>;
}

export type NotificationApi = Record<
  NotificationType,
  (config: NotificationConfig) => string
> & {
  remove: (id: string) => void;
  clear: () => void;
};
