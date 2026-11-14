/** Adapted from Arco Design Message (MIT), commit
 * 4e087a0fe7d5ae2f222fbce4a9518ba047b01772. See LICENSE.arco-design.
 */
import { useLocale } from '../../locale-context.js';
import { resolveLocale } from '../../locale.js';
import { NoticeStack } from '../notice/notice-stack.js';
import type { ToastItem, ToastOptions } from './types.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

export function Toast({
  locale: localeProp,
  labels,
  ...props
}: ToastOptions & {
  notices: ToastItem[];
  onRemove: (id: string) => void;
}) {
  const locale = useLocale(localeProp);
  const text = resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale, labels);
  return <NoticeStack {...props} kind="toast" locale={locale} labels={text} />;
}
