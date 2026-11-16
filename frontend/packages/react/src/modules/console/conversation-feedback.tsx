import { useId, useMemo, useState } from 'react';
import { OvClientError } from '../../client/errors.js';
import { ErrorNotification } from '../../shared/feedback/error-notification.js';
import type { Locale } from '../../shared/locale.js';
import { Toast } from '../../shared/primitives/toast/toast.js';

export function ConversationFeedback({
  error,
  title,
  locale,
}: {
  error: Error;
  title: string;
  locale: Locale;
}) {
  const id = useId();
  const identity = error.cause ?? error;
  const [dismissed, setDismissed] = useState<{ identity: unknown }>();
  const notices = useMemo(
    () =>
      Object.is(dismissed?.identity, identity)
        ? []
        : [{ id, type: 'warning' as const, content: error.message }],
    [dismissed, identity, id, error.message],
  );
  // This code is produced by local attachment validation before the request.
  if (
    error instanceof OvClientError &&
    error.code === 'BOT_INVALID_ATTACHMENT'
  ) {
    return (
      <Toast
        locale={locale}
        notices={notices}
        onRemove={() => setDismissed({ identity })}
      />
    );
  }
  return (
    <ErrorNotification
      title={title}
      identity={identity}
      error={error}
      locale={locale}
    >
      {error.message}
    </ErrorNotification>
  );
}
