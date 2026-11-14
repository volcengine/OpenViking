import {
  ErrorNotification,
  errorMessage,
} from '../../shared/feedback/error-notification.js';
import type { Locale } from '../../shared/locale.js';
import type { DashboardQuery } from './use-dashboard-query.js';
import type { FileBrowserWorkflowLabels } from './preview-workflow-types.js';
import { Button } from '../../shared/primitives/button/button.js';

export function PreviewQueryState<T>({
  query,
  text,
}: {
  query: DashboardQuery<T>;
  text: { loading: string; retry: string } & Pick<
    FileBrowserWorkflowLabels,
    'failed'
  >;
}) {
  return query.error ? (
    <p role="alert">
      {text.failed} {query.error.message}{' '}
      <Button size="small" onClick={query.retry}>
        {text.retry}
      </Button>
    </p>
  ) : query.loading ? (
    <p>{text.loading}</p>
  ) : null;
}
export function PreviewMutationError({
  error,
  title,
  locale,
}: {
  error?: unknown;
  title: string;
  locale?: Locale;
}) {
  return error === undefined ? null : (
    <ErrorNotification
      title={title}
      identity={error}
      error={error}
      locale={locale}
    >
      {errorMessage(error)}
    </ErrorNotification>
  );
}
