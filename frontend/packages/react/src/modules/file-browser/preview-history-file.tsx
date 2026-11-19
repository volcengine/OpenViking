import { PreviewQueryState } from './preview-query-state.js';
import { Empty } from '../../shared/primitives/empty/empty.js';
import { useState } from 'react';
import { useToast } from '../../shared/primitives/toast/use-toast.js';
import {
  PreviewHistoryControls,
  PreviewHistoryPath,
} from './preview-history-controls.js';
import { PreviewHistoryList } from './preview-history-list.js';
import { PreviewHistoryRevision } from './preview-history-revision.js';
import { PreviewHistorySnapshot } from './preview-history-snapshot.js';
import { PreviewHistoryRestore } from './preview-history-restore.js';
import { usePreviewMutation } from './use-preview-mutation.js';
import { usePreviewHistory } from './use-preview-history.js';
import type { PreviewHistoryProps } from './preview-history.js';

export function PreviewHistoryFile(props: PreviewHistoryProps) {
  const history = usePreviewHistory(props);
  const [toast, toastHolder] = useToast({ locale: props.locale });
  const copyFailure = () =>
    toast.error({ id: 'copy-history', content: props.text.copyFailed });
  const [view, setView] = useState<'preview' | 'source'>('preview');
  const [copiedId, setCopiedId] = useState<string>();
  const copy = usePreviewMutation(props.uri, props.requestOptions);
  const text = {
    ...props.text,
    loading: props.loadingText,
    retry: props.retryText,
  };
  const selected = history.selected;
  const deleted = history.content.data === null;
  const comparison =
    history.compare && (history.query.data?.length ?? 0) > 1 ? (
      <PreviewHistoryControls
        history={history}
        text={text}
        locale={props.locale}
        comparison
      />
    ) : undefined;
  return (
    <>
      {toastHolder}
      {props.canSaveSnapshot ? (
        <PreviewHistorySnapshot
          uri={props.uri}
          text={text}
          requestOptions={props.requestOptions}
          onSaved={history.query.retry}
        />
      ) : null}
      <div
        className={`ov-file-history__body${props.directory ? ' ov-file-history__single-pane ov-file-history__directory' : ''}`}
      >
        {props.directoryHeader}
        {!props.directory ? (
          <PreviewHistoryList
            query={history.query}
            showContent={props.showContent}
            selectedId={selected?.id}
            deletedIds={history.deletedIds}
            compare={history.requestedCompare}
            canCompare={history.canCompare}
            text={text}
            locale={props.locale}
            comparison={comparison}
            onCompare={() => history.setCompare(!history.requestedCompare)}
            onSelect={(id) => {
              history.select(id);
              setCopiedId(undefined);
              copy.reset();
            }}
          />
        ) : null}
        {props.showContent ? (
          <PreviewHistoryRevision
            loading={history.query.loading}
            queryState={
              props.directory ? (
                <>
                  <PreviewQueryState
                    query={history.query}
                    text={text}
                    showLoading={false}
                  />
                  {history.query.data?.length === 0 ? (
                    <Empty
                      size="compact"
                      illustration="tray"
                      title={text.historyEmpty}
                    />
                  ) : null}
                </>
              ) : undefined
            }
            controls={
              props.directory ? (
                <>
                  {props.directoryFileSelector}
                  <PreviewHistoryControls
                    history={history}
                    text={text}
                    locale={props.locale}
                  />
                </>
              ) : (
                <PreviewHistoryPath
                  path={props.uri}
                  text={text}
                  onCopy={() => {
                    void copy.run(
                      async () => navigator.clipboard.writeText(props.uri),
                      () => {},
                      undefined,
                      copyFailure,
                    );
                  }}
                />
              )
            }
            comparison={props.directory ? comparison : undefined}
            locale={props.locale}
            content={history.content}
            current={history.current}
            selectedPath={selected?.path}
            view={view}
            compare={history.compare}
            changes={history.changes}
            addedFile={
              !deleted && (!history.base || history.current.data === null)
            }
            identical={history.identical}
            copy={copy}
            copied={Boolean(copiedId && copiedId === selected?.id)}
            text={text}
            setView={setView}
            extraActions={
              // Retain the confirmation and its error scope while versions refresh.
              props.canWrite && props.versioned ? (
                <PreviewHistoryRestore
                  {...props}
                  selected={selected}
                  current={history.query.data?.[0]}
                  disabled={
                    deleted ||
                    history.content.loading ||
                    Boolean(history.content.error)
                  }
                  onRestored={(unchanged) => {
                    toast.success({
                      content: unchanged
                        ? text.rollbackUnchanged
                        : text.rollbackSaved,
                    });
                    history.query.retry();
                    props.onUpdated();
                  }}
                  onConflict={history.query.retry}
                />
              ) : null
            }
            onCopy={() => {
              const body = history.content.data;
              if (typeof body === 'string' && selected)
                void copy.run(
                  async () => navigator.clipboard.writeText(body),
                  () => setCopiedId(selected.id),
                  undefined,
                  copyFailure,
                );
            }}
          />
        ) : null}
      </div>
    </>
  );
}
