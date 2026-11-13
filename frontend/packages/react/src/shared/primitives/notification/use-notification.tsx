/** Adapted from Arco Design Notification (MIT), commit
 * 4e087a0fe7d5ae2f222fbce4a9518ba047b01772. See LICENSE.arco-design.
 * Keeps instance-local notices, ID updates, removal and the context holder.
 * Omits the global singleton, configuration, count limit and other positions.
 */
import { useCallback, useId, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useLocale } from '../../locale-context.js';
import { Notification } from './notification.js';
import type {
  NotificationApi,
  NotificationConfig,
  NotificationItem,
  NotificationOptions,
  NotificationType,
} from './types.js';

export function useNotification(
  options: NotificationOptions = {},
): readonly [NotificationApi, ReactNode] {
  const locale = useLocale(options.locale);
  const prefix = useId();
  const sequence = useRef(0);
  const [notices, setNotices] = useState<NotificationItem[]>([]);
  const remove = useCallback((id: string) => {
    setNotices((items) => items.filter((item) => item.id !== id));
  }, []);
  const api = useMemo<NotificationApi>(() => {
    const add = (type: NotificationType) => (config: NotificationConfig) => {
      const id = config.id ?? `${prefix}-${++sequence.current}`;
      const next = {
        ...config,
        duration: config.duration ?? (type === 'loading' ? 0 : 3000),
        id,
        type,
      };
      setNotices((items) =>
        items.some((item) => item.id === id)
          ? items.map((item) => (item.id === id ? next : item))
          : [...items, next],
      );
      return id;
    };
    return {
      info: add('info'),
      success: add('success'),
      warning: add('warning'),
      error: add('error'),
      normal: add('normal'),
      loading: add('loading'),
      remove,
      clear: () => setNotices([]),
    };
  }, [prefix, remove]);
  return [
    api,
    <Notification
      key="notifications"
      notices={notices}
      onRemove={remove}
      locale={locale}
      labels={options.labels}
    />,
  ];
}
