import type { CommandConsoleStatusContent } from './types.js';
import { CommandConsoleUriText } from './command-console-uri-text.js';

export interface CommandConsoleStatusCardProps {
  content: CommandConsoleStatusContent;
  onOpenUri?: (uri: string) => void;
}

/** 键值状态卡片。Key/value status card. */
export function CommandConsoleStatusCard({
  content,
  onOpenUri,
}: CommandConsoleStatusCardProps) {
  return (
    <div className="ov-command-console__status">
      {content.title ? (
        <div className="ov-command-console__card-title">
          <CommandConsoleUriText
            text={content.title}
            mode="prose"
            onOpenUri={onOpenUri}
          />
        </div>
      ) : null}
      <dl className="ov-command-console__status-rows">
        {content.rows.map((row) => (
          <div
            key={row.label}
            className="ov-command-console__status-row"
            data-variant={row.variant ?? 'inline'}
          >
            <dt className="ov-command-console__status-label">{row.label}</dt>
            <dd className="ov-command-console__status-value">
              {typeof row.value === 'string' ? (
                <CommandConsoleUriText
                  text={row.value}
                  mode="literal"
                  onOpenUri={onOpenUri}
                />
              ) : (
                row.value
              )}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
