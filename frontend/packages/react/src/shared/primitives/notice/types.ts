import type { CSSProperties, ReactNode } from 'react';
import type { Locale } from '../../locale.js';

export type NoticeType =
  'info' | 'success' | 'warning' | 'error' | 'normal' | 'loading';

export interface NoticeConfig {
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

export interface NoticeItem extends NoticeConfig {
  id: string;
  type: NoticeType;
}

export interface NoticeLabels {
  region: string;
  close: string;
}

export interface NoticeOptions {
  locale?: Locale;
  labels?: Partial<NoticeLabels>;
}

export type NoticeApi = Record<NoticeType, (config: NoticeConfig) => string> & {
  remove: (id: string) => void;
  clear: () => void;
};
