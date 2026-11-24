import { Switch } from '../../shared/primitives/switch/switch.js';
import { Fragment, type ReactNode } from 'react';
import {
  historyVersionBadge,
  historyVersionLabel,
} from './preview-history-controls.js';
import { useTrack } from '../../shared/analytics/use-track.js';
import { Empty } from '../../shared/primitives/empty/empty.js';
import { PreviewQueryState } from './preview-query-state.js';
import type {
  FileBrowserWorkflowLabels,
  PreviewHistoryEntry,
  PreviewWorkflowProps,
} from './preview-workflow-types.js';
import type { DashboardQuery } from './use-dashboard-query.js';

export function PreviewHistoryList({
  query,
  showContent,
  selectedId,
  deletedIds,
  compare,
  canCompare,
  comparison,
  text,
  locale,
  onCompare,
  onSelect,
}: {
  query: DashboardQuery<PreviewHistoryEntry[]>;
  showContent: boolean;
  selectedId?: string;
  deletedIds: ReadonlySet<string>;
  compare: boolean;
  canCompare: boolean;
  comparison?: ReactNode;
  text: FileBrowserWorkflowLabels & { loading: string; retry: string };
  locale: PreviewWorkflowProps['locale'];
  onCompare: () => void;
  onSelect: (id: string) => void;
}) {
  const track = useTrack();
  const entries = query.data;
  const statusText = text;
  return (
    <aside className="ov-file-history__sidebar">
      {showContent ? (
        <div className="ov-file-history__switch-row">
          {text.compareChanges}
          <Switch
            size="small"
            aria-label={text.compareChanges}
            checked={compare}
            disabled={!selectedId || !canCompare}
            onChange={onCompare}
          />
        </div>
      ) : null}
      {comparison}
      <div className="ov-file-history__commit-list">
        <PreviewQueryState query={query} text={statusText} />
        {entries?.length === 0 ? (
          <Empty size="compact" illustration="tray" title={text.historyEmpty} />
        ) : null}
        <>
          {entries?.map((entry, index) => {
            const title = entry.numbered
              ? `${text.version} ${entry.title}`
              : entry.title;
            const versionLabel = historyVersionLabel(entry, locale);
            const badge = historyVersionBadge(
              entry,
              deletedIds.has(entry.id),
              text,
            );
            const label = (
              <>
                <time
                  className="ov-file-history__version-name"
                  title={versionLabel}
                  dateTime={
                    entry.timestamp
                      ? new Date(entry.timestamp * 1000).toISOString()
                      : undefined
                  }
                >
                  {versionLabel}
                </time>
                {index === 0 ? <i>{text.latest}</i> : null}
                {badge ? (
                  <span className="ov-file-history__version-badge">
                    {badge}
                  </span>
                ) : null}
              </>
            );
            return (
              <Fragment key={entry.id}>
                {showContent ? (
                  <button
                    type="button"
                    className={
                      selectedId === entry.id
                        ? 'ov-file-history__commit-active'
                        : undefined
                    }
                    aria-label={
                      entry.numbered && badge
                        ? `${title} · ${badge}`
                        : entry.numbered
                          ? title
                          : undefined
                    }
                    aria-pressed={selectedId === entry.id}
                    onClick={() => {
                      track('file_browser_history_revision_select');
                      onSelect(entry.id);
                    }}
                  >
                    {label}
                  </button>
                ) : (
                  <div>{label}</div>
                )}
              </Fragment>
            );
          })}
        </>
        {entries?.length === 500 && !entries[0]?.numbered ? (
          <p>{text.historyLimit}</p>
        ) : null}
      </div>
    </aside>
  );
}
