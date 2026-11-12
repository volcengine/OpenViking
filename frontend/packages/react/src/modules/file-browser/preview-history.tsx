import { useCallback, useState } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { Button } from '../../shared/primitives/button/button.js';
import { readHistoricalText, readPreviewHistory } from './preview-native.js';
import { useDashboardQuery } from './use-dashboard-query.js';
import { PreviewQueryState } from './preview-query-state.js';
import type {
  PreviewHistoryEntry,
  PreviewWorkflowProps,
} from './preview-workflow-types.js';

export function PreviewHistory({
  uri,
  versioned,
  showContent,
  text,
  locale,
  requestOptions,
  loadingText,
  retryText,
}: PreviewWorkflowProps & {
  versioned: boolean;
  showContent: boolean;
  loadingText: string;
  retryText: string;
}) {
  const query = useDashboardQuery(
    useCallback(
      (client: OvClient, options: OvRequestOptions) =>
        readPreviewHistory(client, uri, versioned, options),
      [uri, versioned],
    ),
    requestOptions,
  );
  const [selected, setSelected] = useState<PreviewHistoryEntry>();
  const content = useDashboardQuery(
    useCallback(
      (client: OvClient, options: OvRequestOptions) =>
        readHistoricalText(client, selected!, options),
      [selected],
    ),
    requestOptions,
    Boolean(selected),
  );
  const statusText = { ...text, loading: loadingText, retry: retryText };
  const dates = new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
  return (
    <div className="ov-file-browser-workflow__history">
      <PreviewQueryState query={query} text={statusText} />
      {query.data?.length === 0 ? <p>{text.historyEmpty}</p> : null}
      <ol className="ov-file-browser-workflow__versions">
        {query.data?.map((entry) => (
          <li key={entry.id}>
            {showContent ? (
              <Button
                size="small"
                aria-pressed={selected?.id === entry.id}
                onClick={() => setSelected(entry)}
              >
                {versioned
                  ? `${text.version} ${entry.title}`
                  : entry.title || entry.id.slice(0, 12)}
              </Button>
            ) : (
              <span>{entry.title || entry.id.slice(0, 12)}</span>
            )}
            {entry.timestamp && Number.isFinite(entry.timestamp) ? (
              <time dateTime={new Date(entry.timestamp * 1000).toISOString()}>
                {dates.format(entry.timestamp * 1000)}
              </time>
            ) : null}
          </li>
        ))}
      </ol>
      {!versioned && query.data?.length === 500 ? (
        <p>{text.historyLimit}</p>
      ) : null}
      {selected ? (
        <div className="ov-file-browser-workflow__revision">
          <PreviewQueryState query={content} text={statusText} />
          {content.data !== undefined ? <pre>{content.data}</pre> : null}
        </div>
      ) : null}
    </div>
  );
}
