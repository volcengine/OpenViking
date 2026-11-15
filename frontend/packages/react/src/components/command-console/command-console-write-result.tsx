import type { CommandConsoleWriteResultContent } from './types.js';
import { CommandConsoleUriText } from './command-console-uri-text.js';

export interface CommandConsoleWriteResultProps {
  content: CommandConsoleWriteResultContent;
  onOpenUri?: (uri: string) => void;
}

/** 写入结果。Write result. */
export function CommandConsoleWriteResult({
  content,
  onOpenUri,
}: CommandConsoleWriteResultProps) {
  return (
    <div className="ov-command-console__write">
      {content.title ? (
        <div className="ov-command-console__card-title">
          <CommandConsoleUriText
            text={content.title}
            mode="prose"
            onOpenUri={onOpenUri}
          />
        </div>
      ) : null}
      <div className="ov-command-console__write-lines">
        {content.lines.map((line, index) => (
          // 结果行由宿主提供，可能重复，用序号区分。
          <div key={index} className="ov-command-console__write-line">
            {typeof line === 'string' ? (
              <CommandConsoleUriText
                text={line}
                mode="prose"
                onOpenUri={onOpenUri}
              />
            ) : (
              line
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
