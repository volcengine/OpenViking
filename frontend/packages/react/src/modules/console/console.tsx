import { withMonitoring } from '../../shared/monitoring/with-monitoring.js';
import { NotificationScope } from '../../shared/feedback/notification-scope.js';
import { useRenderErrors } from './labels.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { TaskTrackingProvider } from '../../shared/task-tracking/context.js';
import type { ConsoleProps } from './types.js';
import { ConsoleView } from './console-view.js';

function ConsoleMonitoredView(props: ConsoleProps) {
  return (
    <ErrorBoundary
      fallback={props.slots?.errorFallback}
      message={useRenderErrors(props.locale).console}
      locale={props.locale}
      resetKeys={[props.scopeKey]}
    >
      <TaskTrackingProvider service={props.taskService}>
        <NotificationScope locale={props.locale} isolateRequests>
          <ConsoleView {...props} />
        </NotificationScope>
      </TaskTrackingProvider>
    </ErrorBoundary>
  );
}

export const Console = withMonitoring(ConsoleMonitoredView, 'Console');
