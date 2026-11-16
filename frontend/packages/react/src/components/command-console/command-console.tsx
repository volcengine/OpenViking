import { withMonitoring } from '../../shared/monitoring/with-monitoring.js';
import { useRenderErrors } from './command-console-labels.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { CommandConsoleContent } from './command-console-content.js';
import type { CommandConsoleProps } from './types.js';

function CommandConsoleView(props: CommandConsoleProps) {
  const { slots, ...contentProps } = props;
  return (
    <ErrorBoundary
      monitoringRegion="record"
      fallback={slots?.errorFallback}
      message={useRenderErrors(props.locale).console}
      locale={props.locale}
    >
      <CommandConsoleContent {...contentProps} />
    </ErrorBoundary>
  );
}

export const CommandConsole = withMonitoring(
  CommandConsoleView,
  'CommandConsole',
);
