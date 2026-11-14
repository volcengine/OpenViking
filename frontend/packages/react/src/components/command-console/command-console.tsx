import { useRenderErrors } from './command-console-labels.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { CommandConsoleContent } from './command-console-content.js';
import type { CommandConsoleProps } from './types.js';

export function CommandConsole(props: CommandConsoleProps) {
  const { slots, ...contentProps } = props;
  return (
    <ErrorBoundary
      fallback={slots?.errorFallback}
      message={useRenderErrors(props.locale).console}
      locale={props.locale}
    >
      <CommandConsoleContent {...contentProps} />
    </ErrorBoundary>
  );
}
