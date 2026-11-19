import { withMonitoring } from '../../shared/monitoring/with-monitoring.js';
import { NotificationScope } from '../../shared/feedback/notification-scope.js';
import { useRenderErrors } from './file-preview-labels.js';
import { forwardRef } from 'react';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { FilePreviewContent } from './file-preview-content.js';
import type {
  FilePreviewComponent,
  FilePreviewHandle,
  FilePreviewProps,
} from './types.js';

const FilePreviewView: FilePreviewComponent = forwardRef<
  FilePreviewHandle,
  FilePreviewProps
>(function FilePreview(props, ref) {
  const { slots, ...contentProps } = props;
  return (
    <ErrorBoundary
      fallback={slots?.errorFallback}
      message={useRenderErrors(props.locale).preview}
      locale={props.locale}
      resetKeys={[props.activeKey, ...props.tabs.map((tab) => tab.key)]}
    >
      <NotificationScope locale={props.locale}>
        <FilePreviewContent {...contentProps} ref={ref} />
      </NotificationScope>
    </ErrorBoundary>
  );
});

export const FilePreview = withMonitoring(FilePreviewView, 'FilePreview');
