import { resolveCommandConsoleSkeleton } from './command-catalog.js';
import type { CommandConsoleCommandDefinition } from './command-catalog.js';
import { CommandConsoleCommandHelp } from './command-console-command-help.js';
import { CommandConsoleCodeBlock } from './command-console-code-block.js';
import type { ResolvedCommandConsoleLabels } from './command-console-labels.js';
import { CommandConsoleMarkdown } from './command-console-markdown.js';
import { CommandConsoleResourcePreview } from './command-console-resource-preview.js';
import { CommandConsoleResourceTree } from './command-console-resource-tree.js';
import {
  getCommandConsoleResultText,
  getVisibleCommandConsoleResultTitle,
  isCommandConsoleJson,
} from './command-console-result-text.js';
import { CommandConsoleSearchList } from './command-console-search-list.js';
import { CommandConsoleSkeleton } from './command-console-skeleton.js';
import { CommandConsoleStatusCard } from './command-console-status-card.js';
import { formatCommandConsoleJson } from './command-console-text.js';
import type { CommandConsoleRecord } from './types.js';
import { CommandConsoleUriText } from './command-console-uri-text.js';
import { CommandConsoleWriteResult } from './command-console-write-result.js';

export interface CommandConsoleRecordProps {
  record: CommandConsoleRecord;
  command?: CommandConsoleCommandDefinition;
  labels: ResolvedCommandConsoleLabels;
  onOpenUri?: (uri: string) => void;
  onCopy?: (text: string) => void;
  onPickCommand?: (command: string) => void;
}

export function CommandConsoleRecordBody({
  record,
  command,
  labels,
  onOpenUri,
  onCopy,
  onPickCommand,
}: CommandConsoleRecordProps) {
  const { content } = record;

  if (record.status === 'pending') {
    return (
      <CommandConsoleSkeleton kind={resolveCommandConsoleSkeleton(command)} />
    );
  }

  if (!content) {
    return (
      <div className="ov-command-console__muted">
        {record.status === 'error' ? labels.error : labels.noResult}
      </div>
    );
  }

  switch (content.variant) {
    case 'search-list':
      return (
        <CommandConsoleSearchList
          content={content}
          onOpenUri={onOpenUri}
          noResultLabel={labels.noResult}
        />
      );
    case 'resource-preview':
      return (
        <CommandConsoleResourcePreview
          content={{
            ...content,
            title:
              getVisibleCommandConsoleResultTitle(content.title, command) ?? '',
          }}
          labels={labels}
          onCopy={onCopy}
          onOpenUri={onOpenUri}
        />
      );
    case 'resource-tree':
      return (
        <CommandConsoleResourceTree
          content={{
            ...content,
            title: getVisibleCommandConsoleResultTitle(content.title, command),
          }}
          onOpenUri={onOpenUri}
          resourceLabel={labels.resource}
          loadMoreLabel={labels.loadMore}
          noResultLabel={labels.noResult}
        />
      );
    case 'status':
      return (
        <CommandConsoleStatusCard
          content={{
            ...content,
            title: getVisibleCommandConsoleResultTitle(content.title, command),
          }}
          onOpenUri={onOpenUri}
        />
      );
    case 'write-result':
      return (
        <CommandConsoleWriteResult
          content={{
            ...content,
            title: getVisibleCommandConsoleResultTitle(content.title, command),
          }}
          onOpenUri={onOpenUri}
        />
      );
    case 'command-help':
      return (
        <CommandConsoleCommandHelp
          content={content}
          onPick={onPickCommand}
          labels={labels}
        />
      );
    default: {
      const text = getCommandConsoleResultText(
        formatCommandConsoleJson(content.text),
        command,
      );
      return (
        <div className="ov-command-console__text">
          {content.format === 'markdown' ? (
            <CommandConsoleMarkdown
              text={text}
              labels={labels}
              onCopy={onCopy}
              onOpenUri={onOpenUri}
            />
          ) : isCommandConsoleJson(text) ? (
            <CommandConsoleCodeBlock
              text={text}
              labels={labels}
              onCopy={onCopy}
              onOpenUri={onOpenUri}
            />
          ) : (
            <pre className="ov-command-console__plain">
              <CommandConsoleUriText
                text={text}
                mode="prose"
                onOpenUri={onOpenUri}
              />
            </pre>
          )}
        </div>
      );
    }
  }
}
