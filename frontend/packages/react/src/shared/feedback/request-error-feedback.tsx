import { useCallback, useEffect, useSyncExternalStore } from 'react';
import type {
  RequestErrors,
  RequestFailure,
} from '../../client/error-events.js';
import { useLocale } from '../locale-context.js';
import { ErrorNotification, errorMessage } from './error-notification.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

export function RequestErrorFeedback({ errors }: { errors: RequestErrors }) {
  const failures = useSyncExternalStore(errors.subscribe, errors.getSnapshot);
  return failures.map((failure) => (
    <RequestError key={failure.id} failure={failure} errors={errors} />
  ));
}

function RequestError({
  failure,
  errors,
}: {
  failure: RequestFailure;
  errors: RequestErrors;
}) {
  const locale = useLocale();
  const text = { 'en-US': enUS, 'zh-CN': zhCN }[locale];
  const dismiss = useCallback(
    () => errors.dismiss(failure.id),
    [errors, failure.id],
  );
  // Effect necessity: the owning request's AbortSignal may change independently
  // of UI events (navigation, account switch, unmount). Remove its stale feedback
  // on abort and detach the listener when this feedback expires or is dismissed.
  useEffect(() => {
    const signal = failure.signal;
    if (signal?.aborted) dismiss();
    else signal?.addEventListener('abort', dismiss, { once: true });
    return () => signal?.removeEventListener('abort', dismiss);
  }, [dismiss, failure]);
  return (
    <ErrorNotification
      title={text.requestFailed}
      error={failure.error}
      identity={failure}
      fromRequest
      onDismiss={dismiss}
    >
      {errorMessage(failure.error)}
    </ErrorNotification>
  );
}
