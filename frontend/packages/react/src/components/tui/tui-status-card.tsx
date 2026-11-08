import type { TuiStatusContent } from './types.js';

export interface TuiStatusCardProps {
  content: TuiStatusContent;
}

/** 键值状态卡片。Key/value status card. */
export function TuiStatusCard({ content }: TuiStatusCardProps) {
  return (
    <div className="ov-tui__status">
      {content.title ? (
        <div className="ov-tui__card-title">{content.title}</div>
      ) : null}
      <dl className="ov-tui__status-rows">
        {content.rows.map((row) => (
          <div
            key={row.label}
            className="ov-tui__status-row"
            data-variant={row.variant ?? 'inline'}
          >
            <dt className="ov-tui__status-label">{row.label}</dt>
            <dd className="ov-tui__status-value">{row.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
