import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import CopyIcon from '../../../../icons/svg/file-preview/copy.svg?react';
import RestoreIcon from '../../../../icons/svg/file-preview/restore.svg?react';
import { Button } from '../../shared/primitives/button/button.js';
import {
  PreviewMutationError,
  PreviewQueryState,
} from './preview-query-state.js';
import type { FileBrowserWorkflowLabels } from './preview-workflow-types.js';
import type { DashboardQuery } from './use-dashboard-query.js';
import type { DiffLine } from './preview-diff.js';

export function PreviewHistoryRevision({
  content,
  current,
  selectedPath,
  view,
  compare,
  writable,
  changes,
  identical,
  canRestore,
  restore,
  copy,
  copied,
  confirm,
  text,
  setView,
  onStartRestore,
  onCancelRestore,
  onRestore,
  onCopy,
}: {
  content: DashboardQuery<string>;
  current: DashboardQuery<unknown>;
  selectedPath?: string;
  view: 'preview' | 'source';
  compare: boolean;
  writable: boolean;
  changes?: DiffLine[];
  identical: boolean;
  canRestore: boolean;
  restore: { pending: boolean; error?: unknown };
  copy: { pending: boolean; error?: unknown };
  copied: boolean;
  confirm: boolean;
  text: FileBrowserWorkflowLabels & { loading: string; retry: string };
  setView: (view: 'preview' | 'source') => void;
  onStartRestore: () => void;
  onCancelRestore: () => void;
  onRestore: () => void;
  onCopy: () => void;
}) {
  const statusText = text;
  return (
    <div className="ov-file-history__main">
      <div className="ov-file-history__revision">
        <div className="ov-file-history__toolbar">
          <div
            className="ov-file-preview__switch"
            role="group"
            aria-label={text.historyPreview}
          >
            {(['preview', 'source'] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                className="ov-file-preview__switch-item"
                aria-pressed={view === mode}
                disabled={!selectedPath || compare}
                onClick={() => setView(mode)}
              >
                {mode === 'preview'
                  ? text.historyPreview
                  : text.historyMarkdown}
              </button>
            ))}
          </div>
          <div className="ov-file-history__commands">
            {writable ? (
              <Button
                size="small"
                icon={<RestoreIcon aria-hidden="true" />}
                disabled={!canRestore || restore.pending}
                onClick={onStartRestore}
              >
                {text.restoreVersion}
              </Button>
            ) : null}
            <Button
              size="small"
              type="outline"
              icon={<CopyIcon aria-hidden="true" />}
              disabled={content.data === undefined || restore.pending}
              loading={copy.pending}
              onClick={onCopy}
            >
              {copied ? text.copied : text.copyOriginal}
            </Button>
          </div>
        </div>
        <PreviewMutationError error={copy.error} text={text} />
        {confirm ? (
          <div className="ov-file-history__confirm">
            <p>{text.confirmRestore}</p>
            <div className="ov-file-history__commands">
              <Button
                size="small"
                disabled={restore.pending}
                onClick={onCancelRestore}
              >
                {text.cancel}
              </Button>
              <Button
                size="small"
                type="primary"
                loading={restore.pending}
                onClick={onRestore}
              >
                {text.restoreVersion}
              </Button>
            </div>
            <PreviewMutationError error={restore.error} text={text} />
          </div>
        ) : null}
        <div className="ov-file-history__content">
          <PreviewQueryState query={content} text={statusText} />
          {compare || (writable && current.error) ? (
            <PreviewQueryState query={current} text={statusText} />
          ) : null}
          {compare ? (
            identical ? (
              <p>{text.noChanges}</p>
            ) : changes ? (
              <ol className="ov-file-history__diff">
                {changes.map(({ kind, text: line }, index) => (
                  <li key={index} data-change={kind}>
                    <span aria-hidden="true">{index + 1}</span>
                    <code>
                      {kind === 'added'
                        ? '+ '
                        : kind === 'removed'
                          ? '− '
                          : '  '}
                      {line.trimEnd() || '\u00a0'}
                    </code>
                  </li>
                ))}
              </ol>
            ) : null
          ) : content.data !== undefined ? (
            view === 'preview' && /\.md$/i.test(selectedPath ?? '') ? (
              <div className="ov-file-history__markdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
                  {content.data}
                </ReactMarkdown>
              </div>
            ) : (
              <pre>{content.data}</pre>
            )
          ) : null}
        </div>
        {compare && changes && !identical ? (
          <div className="ov-file-history__legend">
            <span data-change="removed">{text.removedLines}</span>
            <span data-change="added">{text.addedLines}</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
