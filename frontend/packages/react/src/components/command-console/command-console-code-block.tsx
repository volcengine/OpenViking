import { CommandConsoleCopyButton } from './command-console-copy-button.js';
import type { ResolvedCommandConsoleLabels } from './command-console-labels.js';
import { isCommandConsoleJson } from './command-console-result-text.js';
import { formatCommandConsoleJson } from './command-console-text.js';
import { CommandConsoleUriText } from './command-console-uri-text.js';

/** 文本代码块共用复制与资源链接行为。 */
export function CommandConsoleCodeBlock({
  text,
  labels,
  onCopy,
  onOpenUri,
}: {
  text: string;
  labels: ResolvedCommandConsoleLabels;
  onCopy?: (text: string) => void;
  onOpenUri?: (uri: string) => void;
}) {
  return (
    <div className="ov-command-console__code-block">
      <CommandConsoleCopyButton
        className="ov-command-console__copy--inline"
        text={text}
        label={labels.copy}
        copiedLabel={labels.copied}
        onCopy={onCopy}
      />
      {/* eslint-disable jsx-a11y/no-noninteractive-tabindex -- Labelled code output must support keyboard horizontal scrolling. */}
      <pre
        className="ov-command-console__code-content"
        role="region"
        aria-label={labels.codeOutput}
        tabIndex={0}
      >
        <CommandConsoleUriText
          text={formatCommandConsoleJson(text)}
          mode={isCommandConsoleJson(text) ? 'json' : 'literal'}
          onOpenUri={onOpenUri}
        />
      </pre>
      {/* eslint-enable jsx-a11y/no-noninteractive-tabindex */}
    </div>
  );
}
