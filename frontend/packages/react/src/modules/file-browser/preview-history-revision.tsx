import { useId, type ReactNode } from 'react';
import CopyIcon from '../../../../icons/svg/file-preview/copy.svg?react';
import { Button } from '../../shared/primitives/button/button.js';
import { Empty } from '../../shared/primitives/empty/empty.js';
import { LazyContent } from '../../shared/lazy-content.js';
import { PreviewLoading, PreviewQueryState } from './preview-query-state.js';
import { PreviewHistoryTabs } from './preview-history-tabs.js';
import type { FileBrowserWorkflowLabels } from './preview-workflow-types.js';
import type { DashboardQuery } from './use-dashboard-query.js';
import type { DiffLine } from './preview-diff.js';
import type { Locale } from '../../shared/locale.js';
const load = async () => ({
  default: (await import('./preview-history-core.js')).PreviewHistoryCore,
});
export function PreviewHistoryRevision({
  content,
  current,
  selectedPath,
  addedFile,
  view,
  compare,
  changes,
  identical,
  copy,
  copied,
  text,
  setView,
  onCopy,
  extraActions,
  controls,
  comparison,
  locale,
  loading = false,
  queryState,
}: {
  content: DashboardQuery<string | null>;
  loading?: boolean;
  queryState?: ReactNode;
  current: DashboardQuery<unknown>;
  selectedPath?: string;
  addedFile?: boolean;
  view: 'preview' | 'source';
  compare: boolean;
  changes?: DiffLine[];
  identical: boolean;
  copy: { pending: boolean; error?: unknown };
  copied: boolean;
  text: FileBrowserWorkflowLabels & { loading: string; retry: string };
  setView: (view: 'preview' | 'source') => void;
  onCopy: () => void;
  extraActions?: ReactNode;
  controls?: ReactNode;
  comparison?: ReactNode;
  locale: Locale;
}) {
  const id = useId();
  const deleted = content.data === null;
  const displayedChanges = compare && !identical ? changes : undefined;
  const ready =
    !loading &&
    content.data !== undefined &&
    !content.loading &&
    !content.error &&
    (!compare || (!current.loading && !current.error));
  return (
    <section className="ov-file-history__content-panel">
      <div className="ov-file-history__content-card">
        <div className="ov-file-history__corner-marks" aria-hidden="true">
          <span />
          <span />
          <span />
          <span />
        </div>
        <div className="ov-file-history__toolbar">
          {controls}
          <PreviewHistoryTabs
            id={id}
            value={view}
            onChange={setView}
            text={text}
          />
          <div className="ov-file-history__toolbar-actions">
            {extraActions}
            <Button
              size="small"
              type="outline"
              icon={<CopyIcon aria-hidden="true" />}
              disabled={typeof content.data !== 'string'}
              loading={copy.pending}
              onClick={onCopy}
            >
              {copied ? text.copied : text.copyOriginal}
            </Button>
          </div>
        </div>
        {comparison ? (
          <div className="ov-file-history__directory-comparison">
            {comparison}
          </div>
        ) : null}
        <div
          className="ov-file-history__viewer"
          id={`${id}-panel`}
          role="tabpanel"
          aria-labelledby={`${id}-${view}`}
          tabIndex={0}
        >
          <PreviewQueryState query={content} text={text} showLoading={false} />
          {queryState}
          {loading || content.loading || (compare && current.loading) ? (
            <PreviewLoading label={text.loading} />
          ) : null}
          {compare ? (
            <PreviewQueryState
              query={current}
              text={text}
              showLoading={false}
            />
          ) : null}
          {compare && identical && !deleted ? (
            <p className="ov-file-history__no-diff-notice" role="status">
              {text.noChanges}
            </p>
          ) : null}
          {ready && deleted && !displayedChanges?.length ? (
            <Empty
              size="compact"
              illustration="tray"
              title={text.deletedVersion}
            />
          ) : ready ? (
            <LazyContent
              load={load}
              contentProps={{
                content: content.data ?? '',
                changes: displayedChanges,
                view,
                locale,
                path: selectedPath,
                addedFile,
                deletedFile: deleted,
              }}
              fallback={<PreviewLoading label={text.loading} />}
            />
          ) : null}
        </div>
        {displayedChanges?.length ? (
          <div className="ov-file-history__legend">
            <span className="ov-file-history__removed-legend">
              <i />
              {text.removedLines}
            </span>
            <span className="ov-file-history__added-legend">
              <i />
              {text.addedLines}
            </span>
          </div>
        ) : null}
      </div>
    </section>
  );
}
