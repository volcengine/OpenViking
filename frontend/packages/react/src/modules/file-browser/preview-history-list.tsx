import { useTrack } from '../../shared/analytics/use-track.js';
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
  compare,
  text,
  locale,
  onCompare,
  onSelect,
}: {
  query: DashboardQuery<PreviewHistoryEntry[]>;
  showContent: boolean;
  selectedId?: string;
  compare: boolean;
  text: FileBrowserWorkflowLabels & { loading: string; retry: string };
  locale: PreviewWorkflowProps['locale'];
  onCompare: () => void;
  onSelect: (id: string) => void;
}) {
  const track = useTrack();
  const entries = query.data;
  const dates = new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
  const statusText = text;
  return (
    <aside className="ov-file-history__sidebar">
      {showContent ? (
        <div className="ov-file-history__compare">
          {text.compareChanges}
          <button
            type="button"
            role="switch"
            aria-label={text.compareChanges}
            aria-checked={compare}
            disabled={!selectedId}
            onClick={onCompare}
          />
        </div>
      ) : null}
      <div className="ov-file-history__entries">
        <PreviewQueryState query={query} text={statusText} />
        {entries?.length === 0 ? <p>{text.historyEmpty}</p> : null}
        <ol>
          {entries?.map((entry) => {
            const title = entry.numbered
              ? `${text.version} ${entry.title}`
              : entry.title;
            const time =
              entry.timestamp && Number.isFinite(entry.timestamp)
                ? new Date(entry.timestamp * 1000)
                : undefined;
            const label = (
              <>
                {time ? (
                  <time dateTime={time.toISOString()}>
                    {dates.format(time)}
                  </time>
                ) : null}
                {entry.numbered ? <strong>{title}</strong> : null}
                {entry.note ? <span>{entry.note}</span> : null}
                {!time && !entry.numbered && !entry.note
                  ? entry.id.slice(0, 12)
                  : null}
              </>
            );
            return (
              <li key={entry.id}>
                {showContent ? (
                  <button
                    type="button"
                    aria-label={entry.numbered ? title : undefined}
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
              </li>
            );
          })}
        </ol>
        {entries?.length === 500 ? <p>{text.historyLimit}</p> : null}
      </div>
    </aside>
  );
}
