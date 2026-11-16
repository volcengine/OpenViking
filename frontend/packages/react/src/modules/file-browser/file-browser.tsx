import { withMonitoring } from '../../shared/monitoring/with-monitoring.js';
import { NotificationScope } from '../../shared/feedback/notification-scope.js';
import { useRenderErrors } from './file-browser-labels.js';
import { forwardRef } from 'react';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { FileBrowserContent } from './file-browser-content.js';
import type {
  FileBrowserComponent,
  FileBrowserHandle,
  FileBrowserProps,
} from './types.js';

const FileBrowserView: FileBrowserComponent = forwardRef<
  FileBrowserHandle,
  FileBrowserProps
>(function FileBrowser(props, ref) {
  const { slots, ...contentProps } = props;
  return (
    <ErrorBoundary
      fallback={slots?.errorFallback}
      message={useRenderErrors(props.locale).browser}
      locale={props.locale}
      resetKeys={[props.rootUri, props.uri]}
    >
      <NotificationScope locale={props.locale} isolateRequests>
        <FileBrowserContent {...contentProps} ref={ref} />
      </NotificationScope>
    </ErrorBoundary>
  );
});

export const FileBrowser = withMonitoring(FileBrowserView, 'FileBrowser');
