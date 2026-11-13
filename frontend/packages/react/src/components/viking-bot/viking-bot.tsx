import { useRenderErrors } from './labels.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { VikingBotContent } from './viking-bot-content.js';
import type { VikingBotProps } from './types.js';

export function VikingBot(props: VikingBotProps) {
  const { slots, ...contentProps } = props;
  return (
    <ErrorBoundary
      fallback={slots?.errorFallback}
      message={useRenderErrors(props.locale).bot}
      locale={props.locale}
      resetKeys={[props.listKey]}
    >
      <VikingBotContent {...contentProps} />
    </ErrorBoundary>
  );
}
