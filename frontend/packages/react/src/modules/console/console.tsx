import { withMonitoring } from '../../shared/monitoring/with-monitoring.js';
import { NotificationScope } from '../../shared/feedback/notification-scope.js';
import { resolveConsoleLocale, useRenderErrors } from './labels.js';
import { useLocale } from '../../shared/locale-context.js';
import { ConsoleSkeleton } from './console-skeleton.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { TaskTrackingProvider } from '../../shared/task-tracking/context.js';
import type { ConsoleProps } from './types.js';
import { ConsoleView, defaultModes } from './console-view.js';
import { useOvSignal } from '../../client/provider.js';
import { useBotActivation } from './use-bot-activation.js';

function ConsoleMonitoredView(props: ConsoleProps) {
  const { modes = defaultModes, chatOptions, scopeKey, requestOptions } = props;
  const inheritedSignal = useOvSignal();
  const activation = useBotActivation({
    activation: chatOptions?.activation,
    enabled: modes.includes('conversation'),
    scopeKey: chatOptions?.scopeKey ?? scopeKey,
    signal:
      (chatOptions?.requestOptions ?? requestOptions)?.signal ??
      inheritedSignal,
  });
  const locale = useLocale(props.locale);
  return (
    <ErrorBoundary
      fallback={props.slots?.errorFallback}
      message={useRenderErrors(props.locale).console}
      locale={props.locale}
      resetKeys={[props.scopeKey]}
    >
      <TaskTrackingProvider service={props.taskService}>
        <NotificationScope locale={props.locale} isolateRequests>
          {activation.state.status === 'initializing' ? (
            <div
              id={props.id}
              className={['ov-console', props.className]
                .filter(Boolean)
                .join(' ')}
              style={props.style}
              hidden={props.hidden}
            >
              <ConsoleSkeleton
                aria-label={resolveConsoleLocale(locale).initialLoading}
              />
            </div>
          ) : (
            <ConsoleView {...props} activation={activation} />
          )}
        </NotificationScope>
      </TaskTrackingProvider>
    </ErrorBoundary>
  );
}

export const Console = withMonitoring(ConsoleMonitoredView, 'Console');
