import { useTrack } from '../../../shared/analytics/use-track.js';
import { useLocale } from '../../../shared/locale-context.js';
import { TargetInteraction } from './target-interaction.js';
import { InteractionForm } from './interaction-form.js';
import { resolveInteractionLabels } from './labels.js';
import type {
  VikingBotInteractionProps,
  VikingBotInteractionAnswer,
} from './types.js';

/** A host-owned question rendered with the library's form controls. */
export function VikingBotInteractionView({
  request,
  requestKey,
  className,
  index = 0,
  total = 1,
  locale: localeProp,
  labels: overrides,
  ...props
}: VikingBotInteractionProps) {
  const track = useTrack();
  const events = {
    onSubmit: (answer: VikingBotInteractionAnswer) => {
      track('viking_bot_interaction_submit');
      return props.onSubmit?.(answer);
    },
    onCancel: props.onCancel
      ? () => {
          track('viking_bot_interaction_cancel');
          props.onCancel?.();
        }
      : undefined,
  };
  const locale = useLocale(localeProp);
  const labels = resolveInteractionLabels(locale, overrides);
  const blocked = Boolean(props.disabled || props.submitting);
  return (
    <section
      className={['ov-viking-bot__interaction', className]
        .filter(Boolean)
        .join(' ')}
      aria-label={labels.title}
      aria-busy={props.submitting || undefined}
    >
      {total > 1 ? (
        <p className="ov-viking-bot__interaction-progress">
          {labels.progress(index, total)}
        </p>
      ) : null}
      {request.kind === 'custom' ? (
        <>
          {props.error ? (
            <p role="alert" className="ov-viking-bot__interaction-error">
              {props.error}
            </p>
          ) : null}
          {request.render({
            disabled: blocked,
            submitting: props.submitting,
            submit: (value) => {
              if (!blocked) return events.onSubmit({ kind: 'custom', value });
            },
            cancel: () => {
              if (!blocked) events.onCancel?.();
            },
          })}
        </>
      ) : request.kind === 'target' ? (
        <TargetInteraction
          key={JSON.stringify([request.kind, requestKey ?? index])}
          {...props}
          {...events}
          request={request}
          labels={labels}
          locale={locale}
        />
      ) : (
        <InteractionForm
          key={JSON.stringify([request.kind, requestKey ?? index])}
          {...props}
          {...events}
          request={request}
          locale={locale}
          labels={labels}
        />
      )}
    </section>
  );
}
