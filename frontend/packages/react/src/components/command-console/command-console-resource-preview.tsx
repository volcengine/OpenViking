import type { CommandConsoleResourcePreviewContent } from './types.js';
import {
  CommandConsoleUriLink,
  CommandConsoleUriText,
} from './command-console-uri-text.js';
import { CommandConsoleCodeBlock } from './command-console-code-block.js';
import type { ResolvedCommandConsoleLabels } from './command-console-labels.js';
import { isCommandConsoleJson } from './command-console-result-text.js';

export interface CommandConsoleResourcePreviewProps {
  content: CommandConsoleResourcePreviewContent;
  onOpenUri?: (uri: string) => void;
  onCopy?: (text: string) => void;
  labels: ResolvedCommandConsoleLabels;
}

/** 单个资源正文预览。A single resource body preview. */
export function CommandConsoleResourcePreview({
  content,
  onOpenUri,
  onCopy,
  labels,
}: CommandConsoleResourcePreviewProps) {
  const metaLabel =
    content.meta?.filter(Boolean).join(' · ') || content.description;
  const target = content.navigationUri ?? content.uri;

  return (
    <div className="ov-command-console__preview">
      {metaLabel ? (
        <div className="ov-command-console__result-meta">
          <CommandConsoleUriText
            text={metaLabel}
            mode="prose"
            onOpenUri={onOpenUri}
          />
        </div>
      ) : null}
      {content.title ? (
        <div className="ov-command-console__card-title">
          <CommandConsoleUriText
            text={content.title}
            mode="literal"
            onOpenUri={onOpenUri}
          />
        </div>
      ) : null}
      <div className="ov-command-console__preview-body">
        {content.uri ? (
          <CommandConsoleUriLink
            uri={target}
            onOpenUri={onOpenUri}
            className="ov-command-console__link ov-command-console__preview-uri"
          >
            {content.uri}
          </CommandConsoleUriLink>
        ) : null}
        {isCommandConsoleJson(content.content) ? (
          <CommandConsoleCodeBlock
            text={content.content}
            labels={labels}
            onCopy={onCopy}
            onOpenUri={onOpenUri}
          />
        ) : (
          <pre className="ov-command-console__preview-content">
            <CommandConsoleUriText
              text={content.content}
              mode="literal"
              onOpenUri={onOpenUri}
            />
          </pre>
        )}
      </div>
    </div>
  );
}
