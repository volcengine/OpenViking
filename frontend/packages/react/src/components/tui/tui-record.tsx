import { resolveTuiSkeleton } from './command-catalog.js';
import type { TuiCommandDefinition } from './command-catalog.js';
import {
  getTuiCommandInputText,
  matchTuiCommandInput,
} from './command-input.js';
import { TuiCommandHelp } from './tui-command-help.js';
import { TuiCommandText } from './tui-command-text.js';
import { TuiRecordTime } from './tui-record-time.js';
import { TuiCodeBlock } from './tui-code-block.js';
import type { ResolvedTuiLabels } from './tui-labels.js';
import { TuiMarkdown } from './tui-markdown.js';
import { TuiResourcePreview } from './tui-resource-preview.js';
import { TuiResourceTree } from './tui-resource-tree.js';
import { TuiResultHeader } from './tui-result-header.js';
import {
  getTuiResultText,
  getVisibleTuiResultTitle,
  isTuiJson,
} from './tui-result-text.js';
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
  onPickCommand?: (command: string) => void;
}

export function RecordBody({
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
        {record.status === 'error' ? labels.error : labels.noResult}
      </div>
    );
  }

  switch (content.variant) {
    case 'search-list':
      return (
        <TuiSearchList
          content={content}
          onOpenUri={onOpenUri}
          noResultLabel={labels.noResult}
        />
      );
    case 'resource-preview':
      return (
        <TuiResourcePreview
          content={{
            ...content,
            title: getVisibleTuiResultTitle(content.title, command) ?? '',
          }}
          labels={labels}
          onCopy={onCopy}
          onOpenUri={onOpenUri}
        />
      );
    case 'resource-tree':
      return (
        <TuiResourceTree
          content={{
            ...content,
            title: getVisibleTuiResultTitle(content.title, command),
          }}
          onOpenUri={onOpenUri}
          resourceLabel={labels.resource}
          loadMoreLabel={labels.loadMore}
          noResultLabel={labels.noResult}
        />
      );
    case 'status':
      return (
        <TuiStatusCard
          content={{
            ...content,
            title: getVisibleTuiResultTitle(content.title, command),
          }}
          onOpenUri={onOpenUri}
        />
      );
    case 'write-result':
      return (
        <TuiWriteResult
          content={{
            ...content,
            title: getVisibleTuiResultTitle(content.title, command),
          }}
          onOpenUri={onOpenUri}
        />
      );
    case 'command-help':
      return (
        <TuiCommandHelp
          content={content}
          onPick={onPickCommand}
          labels={labels}
        />
      );
    default: {
      const text = getTuiResultText(formatTuiJson(content.text), command);
      return (
        <div className="ov-tui__text">
          {content.format === 'markdown' ? (
            <TuiMarkdown
              text={text}
              labels={labels}
              onCopy={onCopy}
              onOpenUri={onOpenUri}
            />
          ) : isTuiJson(text) ? (
            <TuiCodeBlock
              text={text}
              labels={labels}
              onCopy={onCopy}
              onOpenUri={onOpenUri}
            />
          ) : (
            <pre className="ov-tui__plain">
              <TuiUriText text={text} mode="prose" onOpenUri={onOpenUri} />
            </pre>
          )}
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
  const input = props.command
    ? matchTuiCommandInput(record.command, [props.command])
    : undefined;
  const prefix = input ? getTuiCommandInputText(input.command) : '';

  return (
    <div className="ov-tui__record" data-status={record.status}>
      <div className="ov-tui__bubble">
        <code className="ov-tui__bubble-text">
          {input ? (
            <>
              {input.leadingWhitespace}
              <span className="ov-tui__bubble-command">
                <TuiCommandText text={prefix} name={input.command.name} />
              </span>
              <TuiUriText
                text={input.rest}
                mode="literal"
                onOpenUri={props.onOpenUri}
              />
            </>
          ) : (
            <TuiUriText
              text={record.command}
              mode="literal"
              onOpenUri={props.onOpenUri}
            />
          )}
        </code>
        {record.createdAt || record.createdAtMs !== undefined ? (
          <TuiRecordTime record={record} labels={labels} />
        ) : null}
      </div>
      <article className="ov-tui__result">
        <TuiResultHeader
          command={prefix || record.command}
          status={record.status}
          label={statusLabel}
        />
        <div className="ov-tui__result-body">
          <RecordBody {...props} />
        </div>
      </article>
    </div>
  );
}
