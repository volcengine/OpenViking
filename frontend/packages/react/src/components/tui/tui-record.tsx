import { resolveTuiSkeleton } from './command-catalog.js';
import type { TuiCommandDefinition } from './command-catalog.js';
import { TuiCommandHelp } from './tui-command-help.js';
import { TuiCopyButton } from './tui-copy-button.js';
import type { ResolvedTuiLabels } from './tui-labels.js';
import { TuiMarkdown } from './tui-markdown.js';
import { TuiResourcePreview } from './tui-resource-preview.js';
import { TuiResourceTree } from './tui-resource-tree.js';
import { TuiSearchList } from './tui-search-list.js';
import { TuiSkeleton } from './tui-skeleton.js';
import { TuiStatusCard } from './tui-status-card.js';
import { formatTuiJson } from './tui-text.js';
import type { TuiRecord } from './types.js';
import { TuiUriText } from './tui-uri-text.js';
import { TuiWriteResult } from './tui-write-result.js';

export interface TuiRecordProps {
  record: TuiRecord;
  command?: TuiCommandDefinition;
  labels: ResolvedTuiLabels;
  onOpenUri?: (uri: string) => void;
  onCopy?: (text: string) => void;
  onPickCommand: (command: string) => void;
}

function RecordBody({
  record,
  command,
  labels,
  onOpenUri,
  onCopy,
  onPickCommand,
}: TuiRecordProps) {
  const { content } = record;

  if (record.status === 'pending') {
    return <TuiSkeleton kind={resolveTuiSkeleton(command)} />;
  }

  if (!content) {
    return (
      <div className="ov-tui__muted">
        {record.status === 'error' ? labels.error : labels.success}
      </div>
    );
  }

  switch (content.variant) {
    case 'search-list':
      return <TuiSearchList content={content} onOpenUri={onOpenUri} />;
    case 'resource-preview':
      return <TuiResourcePreview content={content} onOpenUri={onOpenUri} />;
    case 'resource-tree':
      return (
        <TuiResourceTree
          content={content}
          onOpenUri={onOpenUri}
          loadMoreLabel={labels.loadMore}
        />
      );
    case 'status':
      return <TuiStatusCard content={content} />;
    case 'write-result':
      return <TuiWriteResult content={content} />;
    case 'command-help':
      return <TuiCommandHelp content={content} onPick={onPickCommand} />;
    default: {
      const text = formatTuiJson(content.text);
      return (
        <div className="ov-tui__text">
          {content.format === 'markdown' ? (
            <TuiMarkdown text={text} onOpenUri={onOpenUri} />
          ) : (
            <pre className="ov-tui__plain">
              <TuiUriText text={text} onOpenUri={onOpenUri} />
            </pre>
          )}
          <TuiCopyButton
            text={content.text}
            label={labels.copy}
            copiedLabel={labels.copied}
            onCopy={onCopy}
            className="ov-tui__copy--inline"
          />
        </div>
      );
    }
  }
}

const STATUS_TEXT = {
  pending: 'pending',
  success: 'success',
  error: 'error',
} as const;

/** 一条执行记录：命令回显、状态与结果。 */
export function TuiRecordItem(props: TuiRecordProps) {
  const { record, labels } = props;
  const statusLabel = labels[STATUS_TEXT[record.status]];

  return (
    <div className="ov-tui__record" data-status={record.status}>
      <div className="ov-tui__bubble">
        <code className="ov-tui__bubble-text">{record.command}</code>
        {record.createdAt ? (
          <span className="ov-tui__bubble-time">{record.createdAt}</span>
        ) : null}
      </div>
      <div className="ov-tui__result">
        <div className="ov-tui__result-head">
          <span className="ov-tui__status" data-status={record.status}>
            <span className="ov-tui__status-dot" aria-hidden="true" />
            {statusLabel}
          </span>
          <TuiCopyButton
            text={record.command}
            label={labels.copy}
            copiedLabel={labels.copied}
            onCopy={props.onCopy}
          />
        </div>
        <div className="ov-tui__result-body">
          <RecordBody {...props} />
        </div>
      </div>
    </div>
  );
}
