import { useCallback, useMemo, useState } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { readFileContent } from './fs-read-content.js';
import { readHistoricalText, readPreviewHistory } from './preview-native.js';
import { diffLines } from './preview-diff.js';
import { useDashboardQuery } from './use-dashboard-query.js';
import { PreviewHistoryList } from './preview-history-list.js';
import { PreviewHistoryRevision } from './preview-history-revision.js';
import { usePreviewMutation } from './use-preview-mutation.js';
import type { PreviewWorkflowProps } from './preview-workflow-types.js';

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
  const [selectedId, setSelectedId] = useState<string>();
  const [compare, setCompare] = useState(false);
  const [view, setView] = useState<'preview' | 'source'>('preview');
  const [copiedId, setCopiedId] = useState<string>();
  const entries = query.data;
  const selected = showContent
    ? (entries?.find((entry) => entry.id === selectedId) ?? entries?.[0])
    : undefined;
  const content = useDashboardQuery(
    useCallback(
      (client: OvClient, options: OvRequestOptions) =>
        readHistoricalText(client, selected!, options, versioned),
      [selected, versioned],
    ),
    requestOptions,
    Boolean(selected),
  );
  const current = useDashboardQuery(
    useCallback(
      (client: OvClient, options: OvRequestOptions) =>
        readFileContent(client, uri, options, -1, versioned),
      [uri, versioned],
    ),
    requestOptions,
    Boolean(selected) && compare,
  );
  const copy = usePreviewMutation(uri, requestOptions);
  const statusText = { ...text, loading: loadingText, retry: retryText };
  const changes = useMemo(
    () =>
      compare && content.data !== undefined && current.data !== undefined
        ? diffLines(content.data, current.data.content)
        : undefined,
    [compare, content.data, current.data],
  );
  const identical =
    content.data !== undefined && content.data === current.data?.content;
  return (
    <div className="ov-file-history__layout">
      <PreviewHistoryList
        query={query}
        showContent={showContent}
        selectedId={selected?.id}
        compare={compare}
        text={statusText}
        locale={locale}
        onCompare={() => setCompare(!compare)}
        onSelect={(id) => {
          setSelectedId(id);
          setCopiedId(undefined);
          copy.reset();
        }}
      />
      {showContent ? (
        <PreviewHistoryRevision
          content={content}
          current={current}
          selectedPath={selected?.path}
          view={view}
          compare={compare}
          changes={changes}
          identical={identical}
          copy={copy}
          copied={Boolean(copiedId && copiedId === selected?.id)}
          text={statusText}
          setView={setView}
          onCopy={() => {
            const body = content.data;
            if (body !== undefined && selected)
              void copy.run(
                async () => navigator.clipboard.writeText(body),
                () => setCopiedId(selected.id),
              );
          }}
        />
      ) : null}
    </div>
  );
}
