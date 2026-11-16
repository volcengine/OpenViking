/** Adapted from Arco Design Notification (MIT), commit
 * 4e087a0fe7d5ae2f222fbce4a9518ba047b01772. See LICENSE.arco-design.
 */
import { useLocale } from '../../locale-context.js';
import type { Ref } from 'react';
import { resolveLocale } from '../../locale.js';
import { NoticeStack } from '../notice/notice-stack.js';
import type { NotificationItem, NotificationOptions } from './types.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

export function Notification({
  locale: localeProp,
  labels,
  ...props
}: NotificationOptions & {
  notices: NotificationItem[];
  onRemove: (id: string) => void;
  container?: HTMLElement | null;
  contentRef?: Ref<HTMLDivElement>;
}) {
  const locale = useLocale(localeProp);
  const text = resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale, labels);
  return (
    <NoticeStack {...props} kind="notification" locale={locale} labels={text} />
  );
}
