import type { TuiSkeletonKind } from './command-catalog.js';

export interface TuiSkeletonProps {
  kind: TuiSkeletonKind;
}

function Line({ width }: { width: string }) {
  return <span className="ov-tui__skeleton-line" style={{ width }} />;
}

/** 等待中的骨架；按命令选择形态，未知命令使用通用骨架。 */
export function TuiSkeleton({ kind }: TuiSkeletonProps) {
  if (kind === 'search') {
    return (
      <div className="ov-tui__skeleton" aria-hidden="true">
        <div className="ov-tui__skeleton-summary">
          <Line width="100%" />
          <Line width="56%" />
        </div>
        {[0, 1, 2].map((index) => (
          <div key={index} className="ov-tui__skeleton-item">
            <span className="ov-tui__skeleton-icon" />
            <span className="ov-tui__skeleton-lines">
              <Line width="72%" />
              <Line width="44%" />
            </span>
          </div>
        ))}
      </div>
    );
  }

  if (kind === 'tree') {
    return (
      <div className="ov-tui__skeleton" aria-hidden="true">
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className="ov-tui__skeleton-row">
            <Line width={index % 2 ? '52%' : '68%'} />
          </div>
        ))}
      </div>
    );
  }

  if (kind === 'status') {
    return (
      <div className="ov-tui__skeleton" aria-hidden="true">
        {[0, 1, 2].map((index) => (
          <div key={index} className="ov-tui__skeleton-row">
            <Line width="28%" />
            <Line width="46%" />
          </div>
        ))}
      </div>
    );
  }

  if (kind === 'resource') {
    return (
      <div className="ov-tui__skeleton" aria-hidden="true">
        <Line width="38%" />
        <div className="ov-tui__skeleton-block">
          <Line width="100%" />
          <Line width="92%" />
          <Line width="76%" />
        </div>
      </div>
    );
  }

  if (kind === 'write') {
    return (
      <div className="ov-tui__skeleton" aria-hidden="true">
        <div className="ov-tui__skeleton-row">
          <Line width="64%" />
        </div>
        <div className="ov-tui__skeleton-row">
          <Line width="42%" />
        </div>
      </div>
    );
  }

  return (
    <div className="ov-tui__skeleton" aria-hidden="true">
      <div className="ov-tui__skeleton-summary">
        <Line width="100%" />
        <Line width="64%" />
      </div>
    </div>
  );
}
