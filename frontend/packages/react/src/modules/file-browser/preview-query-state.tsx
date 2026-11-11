import type { DashboardQuery } from './use-dashboard-query.js';
import type { FileBrowserWorkflowLabels } from './preview-workflow-types.js';
import { Button } from '../../shared/components/button/button.js';

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
  text,
}: {
  error?: unknown;
  text: FileBrowserWorkflowLabels;
}) {
  return error === undefined ? null : (
    <p className="ov-file-browser-workflow__error" role="alert">
      {text.failed}{' '}
      {error instanceof Error
        ? error.message
        : typeof error === 'string'
          ? error
          : ''}
    </p>
  );
}
