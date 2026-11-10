import type { TuiWriteResultContent } from './types.js';
import { TuiUriText } from './tui-uri-text.js';

export interface TuiWriteResultProps {
  content: TuiWriteResultContent;
  onOpenUri?: (uri: string) => void;
}

/** 写入结果。Write result. */
export function TuiWriteResult({ content, onOpenUri }: TuiWriteResultProps) {
  return (
    <div className="ov-tui__write">
      {content.title ? (
        <div className="ov-tui__card-title">
          <TuiUriText text={content.title} mode="prose" onOpenUri={onOpenUri} />
        </div>
      ) : null}
      <div className="ov-tui__write-lines">
        {content.lines.map((line, index) => (
          // 结果行由宿主提供，可能重复，用序号区分。
          <div key={index} className="ov-tui__write-line">
            {typeof line === 'string' ? (
              <TuiUriText text={line} mode="prose" onOpenUri={onOpenUri} />
            ) : (
              line
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
