/** Adapted from Arco Design Message/useMessage (MIT), commit
 * 4e087a0fe7d5ae2f222fbce4a9518ba047b01772. See LICENSE.arco-design.
 * Keeps instance-local notices, ID updates, removal and the context holder.
 * Omits the global singleton, configuration, count limit and other positions.
 */
import type { ReactNode } from 'react';
import { useLocale } from '../../locale-context.js';
import { useNotices } from '../notice/use-notices.js';
import { Toast } from './toast.js';
import type { ToastApi, ToastOptions } from './types.js';

export function useToast(
  options: ToastOptions = {},
): readonly [ToastApi, ReactNode] {
  const locale = useLocale(options.locale);
  const [api, notices, remove] = useNotices();
  return [
    api,
    <Toast
      key="toasts"
      notices={notices}
      onRemove={remove}
      locale={locale}
      labels={options.labels}
    />,
  ];
}
