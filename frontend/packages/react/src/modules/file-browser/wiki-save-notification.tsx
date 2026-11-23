import { useCallback, useContext, useId, useMemo, useState } from 'react';
import { NotificationScopeContext } from '../../shared/feedback/notification-context.js';
import type { Locale } from '../../shared/locale.js';
import { Notification } from '../../shared/primitives/notification/notification.js';
import type { NotificationItem } from '../../shared/primitives/notification/types.js';
import enUS from './locales/wiki-save.en-US.js';
import zhCN from './locales/wiki-save.zh-CN.js';
import type { WikiSaveFeedback } from './use-wiki-save-feedback.js';

export function WikiSaveNotification({
  feedback,
  locale,
}: {
  feedback?: WikiSaveFeedback;
  locale: Locale;
}) {
  const owner = useContext(NotificationScopeContext);
  const id = useId();
  const [dismissed, setDismissed] = useState<WikiSaveFeedback>();
  const text = { 'en-US': enUS, 'zh-CN': zhCN }[locale];
  const notices = useMemo<NotificationItem[]>(() => {
    if (!feedback || feedback === dismissed) return [];
    return feedback.status === 'saving'
      ? [{ id, type: 'loading', duration: 0, closable: false, ...text.saving }]
      : [{ id, type: 'success', ...text.success }];
  }, [dismissed, feedback, id, text]);
  const dismiss = useCallback(() => setDismissed(feedback), [feedback]);
  if (!owner?.active) return null;
  return (
    <Notification
      locale={locale}
      container={owner.container}
      notices={notices}
      onRemove={dismiss}
    />
  );
}
