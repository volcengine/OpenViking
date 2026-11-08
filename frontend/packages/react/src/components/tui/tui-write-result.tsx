import type { TuiWriteResultContent } from './types.js';

export interface TuiWriteResultProps {
  content: TuiWriteResultContent;
}

/** 写入结果。Write result. */
export function TuiWriteResult({ content }: TuiWriteResultProps) {
  return (
    <div className="ov-tui__write">
      {content.title ? (
        <div className="ov-tui__card-title">{content.title}</div>
      ) : null}
      <div className="ov-tui__write-lines">
        {content.lines.map((line, index) => (
          // 结果行由宿主提供，可能重复，用序号区分。
          <div key={index} className="ov-tui__write-line">
            {line}
          </div>
        ))}
      </div>
    </div>
  );
}
