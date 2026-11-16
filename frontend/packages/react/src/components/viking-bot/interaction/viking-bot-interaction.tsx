import { useTrack } from '../../../shared/analytics/use-track.js';
import { LocaleProvider, useLocale } from '../../../shared/locale-context.js';
import { TargetInteraction } from './target-interaction.js';
import { CustomInteraction } from './custom-interaction.js';
import { InteractionSteps } from './interaction-steps.js';
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
}: VikingBotInteractionProps & { onPrevious?: () => void }) {
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
  if (request.kind === 'steps')
    return (
      <LocaleProvider locale={locale}>
        <InteractionSteps
          key={JSON.stringify([request.kind, requestKey])}
          {...props}
          request={request}
          className={className}
          locale={locale}
          labels={labels}
        />
      </LocaleProvider>
    );
  return (
    <LocaleProvider locale={locale}>
      <section
        className={['ov-viking-bot__interaction', className]
          .filter(Boolean)
          .join(' ')}
        aria-label={labels.title}
        aria-busy={props.submitting || undefined}
      >
        {total > 1 ? (
          <p
            className="ov-viking-bot__interaction-progress"
            aria-current="step"
          >
            {labels.progress(index, total)}
          </p>
        ) : null}
        {request.kind === 'custom' ? (
          <CustomInteraction
            key={JSON.stringify([request.kind, requestKey ?? index])}
            {...props}
            {...events}
            request={request}
            labels={labels}
            locale={locale}
          />
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
    </LocaleProvider>
  );
}
