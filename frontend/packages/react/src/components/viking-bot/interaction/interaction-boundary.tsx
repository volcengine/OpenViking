import { useRenderErrors } from '../labels.js';
import { ErrorBoundary } from '../../../shared/error-boundary/error-boundary.js';
import { VikingBotInteractionView } from './viking-bot-interaction.js';

import type { VikingBotInteractionProps } from './types.js';

export function VikingBotInteraction(props: VikingBotInteractionProps) {
  const { slots, ...contentProps } = props;
  return (
    <ErrorBoundary
      fallback={slots?.errorFallback}
      message={useRenderErrors(props.locale).interaction}
      locale={props.locale}
      resetKeys={[props.requestKey, props.request.kind, props.index]}
    >
      <VikingBotInteractionView {...contentProps} />
    </ErrorBoundary>
  );
}
