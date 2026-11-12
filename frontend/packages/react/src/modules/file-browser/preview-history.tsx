import { useCallback, useMemo, useState } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { isExperienceFile } from './experience-data.js';
import { readFileContent } from './fs-read-content.js';
import {
  readHistoricalText,
  readPreviewHistory,
  restorePreviewVersion,
} from './preview-native.js';
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
  canWrite,
  text,
  locale,
  requestOptions,
  loadingText,
  retryText,
  onUpdated,
  onClose,
  onRestoreBusyChange,
}: PreviewWorkflowProps & {
  versioned: boolean;
  showContent: boolean;
  canWrite: boolean;
  loadingText: string;
  retryText: string;
  onRestoreBusyChange: (busy: boolean) => void;
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
  const [confirm, setConfirm] = useState(false);
  const [copiedId, setCopiedId] = useState<string>();
  const entries = query.data;
  const selected = showContent
    ? (entries?.find((entry) => entry.id === selectedId) ?? entries?.[0])
    : undefined;
  const writable = canWrite && showContent && !isExperienceFile(uri);
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
    Boolean(selected) && (compare || writable),
  );
  const restore = usePreviewMutation(uri, requestOptions);
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
  const canRestore = Boolean(
    writable &&
    selected &&
    entries?.[0] &&
    current.data !== undefined &&
    content.data !== undefined &&
    !identical,
  );

  const doRestore = () => {
    const head = entries?.[0];
    if (!canRestore || !selected || !head || current.data === undefined) return;
    const currentText = current.data.revisionText;
    onRestoreBusyChange(true);
    void restore
      .run(
        (client, options) =>
          restorePreviewVersion(
            client,
            uri,
            selected,
            head,
            currentText,
            options,
          ),
        () => {
          onClose();
          onUpdated();
        },
      )
      .finally(() => onRestoreBusyChange(false));
  };

  return (
    <div className="ov-file-history__layout">
      <PreviewHistoryList
        query={query}
        showContent={showContent}
        selectedId={selected?.id}
        compare={compare}
        pending={restore.pending}
        text={statusText}
        locale={locale}
        onCompare={() => {
          if (!compare && writable) current.retry();
          setCompare(!compare);
        }}
        onSelect={(id) => {
          setSelectedId(id);
          setConfirm(false);
          setCopiedId(undefined);
          copy.reset();
          restore.reset();
        }}
      />
      {showContent ? (
        <PreviewHistoryRevision
          content={content}
          current={current}
          selectedPath={selected?.path}
          view={view}
          compare={compare}
          writable={writable}
          changes={changes}
          identical={identical}
          canRestore={canRestore}
          restore={restore}
          copy={copy}
          copied={Boolean(copiedId && copiedId === selected?.id)}
          confirm={confirm}
          text={statusText}
          setView={setView}
          onStartRestore={() => {
            restore.reset();
            setConfirm(true);
          }}
          onCancelRestore={() => setConfirm(false)}
          onRestore={doRestore}
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
