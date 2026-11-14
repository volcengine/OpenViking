import { useEffect, useRef, useState } from 'react';
import type { VikingBotInteractionProps } from './types.js';
import type { InteractionLabels } from './labels.js';
import { ErrorNotification } from '../../../shared/feedback/error-notification.js';
import { interactionError } from './directory-state.js';

export function CustomInteraction({
  request,
  disabled,
  submitting,
  error,
  onSubmit,
  onCancel,
  labels,
  locale,
  onPrevious,
}: Omit<VikingBotInteractionProps, 'labels'> & {
  labels: InteractionLabels;
  onPrevious?: () => void;
}) {
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<unknown>();
  const lifetime = useRef(true);
  const locked = useRef(false);
  useEffect(() => {
    lifetime.current = true;
    return () => {
      lifetime.current = false;
    };
  }, []);
  if (request.kind !== 'custom') return null;
  const busy = Boolean(submitting || pending);
  const blocked = Boolean(disabled || busy);
  const message =
    error ??
    (failure ? interactionError(failure, labels, 'submitFailed') : undefined);
  return (
    <>
      {message ? (
        <ErrorNotification
          title={labels.submitFailed}
          error={failure}
          identity={error ?? failure}
          locale={locale}
        >
          {message}
        </ErrorNotification>
      ) : null}
      {request.render({
        previous: onPrevious,
        disabled: blocked,
        submitting: busy,
        clearError: () => setFailure(undefined),
        submit: async (value) => {
          if (blocked || locked.current || !lifetime.current) return;
          locked.current = true;
          setPending(true);
          setFailure(undefined);
          try {
            await onSubmit?.({ kind: 'custom', value });
          } catch (cause) {
            if (lifetime.current) setFailure(cause);
          } finally {
            locked.current = false;
            if (lifetime.current) setPending(false);
          }
        },
        cancel: () => {
          if (!blocked) onCancel?.();
        },
      })}
    </>
  );
}
