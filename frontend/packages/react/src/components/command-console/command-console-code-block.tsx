import { CommandConsoleCopyButton } from './command-console-copy-button.js';
import type { ResolvedCommandConsoleLabels } from './command-console-labels.js';
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
      <pre className="ov-command-console__code-content">
        <CommandConsoleUriText
          text={formatCommandConsoleJson(text)}
          mode="literal"
          onOpenUri={onOpenUri}
        />
      </pre>
    </div>
  );
}
