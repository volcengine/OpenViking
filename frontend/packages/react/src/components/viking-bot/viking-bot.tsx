import { withMonitoring } from '../../shared/monitoring/with-monitoring.js';
import { useRenderErrors } from './labels.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { VikingBotContent } from './viking-bot-content.js';
import type { VikingBotProps } from './types.js';

function VikingBotView(props: VikingBotProps) {
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

export const VikingBot = withMonitoring(VikingBotView, 'VikingBot');
