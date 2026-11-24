import type { NoticeConfig, NoticeItem, NoticeType } from '../notice/types.js';

export type {
  NoticeType as ToastType,
  NoticeLabels as ToastLabels,
  NoticeOptions as ToastOptions,
} from '../notice/types.js';

/** Compact feedback has content, without notification titles or action areas. */
export type ToastConfig = Omit<NoticeConfig, 'title' | 'btn'>;
export type ToastItem = Omit<NoticeItem, 'title' | 'btn'>;
export type ToastApi = Record<NoticeType, (config: ToastConfig) => string> & {
  remove: (id: string) => void;
  clear: () => void;
};
