import type { TuiStatusContent } from './types.js';
import { TuiUriText } from './tui-uri-text.js';

export interface TuiStatusCardProps {
  content: TuiStatusContent;
  onOpenUri?: (uri: string) => void;
}

/** 键值状态卡片。Key/value status card. */
export function TuiStatusCard({ content, onOpenUri }: TuiStatusCardProps) {
  return (
    <div className="ov-tui__status">
      {content.title ? (
        <div className="ov-tui__card-title">
          <TuiUriText text={content.title} mode="prose" onOpenUri={onOpenUri} />
        </div>
      ) : null}
      <dl className="ov-tui__status-rows">
        {content.rows.map((row) => (
          <div
            key={row.label}
            className="ov-tui__status-row"
            data-variant={row.variant ?? 'inline'}
          >
            <dt className="ov-tui__status-label">{row.label}</dt>
            <dd className="ov-tui__status-value">
              {typeof row.value === 'string' ? (
                <TuiUriText
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
