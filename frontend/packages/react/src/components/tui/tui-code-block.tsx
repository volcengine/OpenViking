import { TuiCopyButton } from './tui-copy-button.js';
import type { ResolvedTuiLabels } from './tui-labels.js';
import { formatTuiJson } from './tui-text.js';
import { TuiUriText } from './tui-uri-text.js';

/** 文本代码块共用复制与资源链接行为。 */
export function TuiCodeBlock({
  text,
  labels,
  onCopy,
  onOpenUri,
}: {
  text: string;
  labels: ResolvedTuiLabels;
  onCopy?: (text: string) => void;
  onOpenUri?: (uri: string) => void;
}) {
  return (
    <div className="ov-tui__code-block">
      <TuiCopyButton
        className="ov-tui__copy--inline"
        text={text}
        label={labels.copy}
        copiedLabel={labels.copied}
        onCopy={onCopy}
      />
      <pre className="ov-tui__code-content">
        <TuiUriText
          text={formatTuiJson(text)}
          mode="literal"
          onOpenUri={onOpenUri}
        />
      </pre>
    </div>
  );
}
