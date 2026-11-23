import type { CommandConsoleSkeletonKind } from './command-catalog.js';

export interface CommandConsoleSkeletonProps {
  kind: CommandConsoleSkeletonKind;
}

function Line({ width }: { width: string }) {
  return (
    <span className="ov-command-console__skeleton-line" style={{ width }} />
  );
}

/** 等待中的骨架；按命令选择形态，未知命令使用通用骨架。 */
export function CommandConsoleSkeleton({ kind }: CommandConsoleSkeletonProps) {
  if (kind === 'search') {
    return (
      <div className="ov-command-console__skeleton" aria-hidden="true">
        <div className="ov-command-console__skeleton-summary">
          <Line width="100%" />
          <Line width="56%" />
        </div>
        {[0, 1, 2].map((index) => (
          <div key={index} className="ov-command-console__skeleton-item">
            <span className="ov-command-console__skeleton-icon" />
            <span className="ov-command-console__skeleton-lines">
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
      <div className="ov-command-console__skeleton" aria-hidden="true">
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className="ov-command-console__skeleton-row">
            <Line width={index % 2 ? '52%' : '68%'} />
          </div>
        ))}
      </div>
    );
  }

  if (kind === 'status') {
    return (
      <div className="ov-command-console__skeleton" aria-hidden="true">
        {[0, 1, 2].map((index) => (
          <div key={index} className="ov-command-console__skeleton-row">
            <Line width="28%" />
            <Line width="46%" />
          </div>
        ))}
      </div>
    );
  }

  if (kind === 'resource') {
    return (
      <div className="ov-command-console__skeleton" aria-hidden="true">
        <Line width="38%" />
        <div className="ov-command-console__skeleton-block">
          <Line width="100%" />
          <Line width="92%" />
          <Line width="76%" />
        </div>
      </div>
    );
  }

  if (kind === 'write') {
    return (
      <div className="ov-command-console__skeleton" aria-hidden="true">
        <div className="ov-command-console__skeleton-row">
          <Line width="64%" />
        </div>
        <div className="ov-command-console__skeleton-row">
          <Line width="42%" />
        </div>
      </div>
    );
  }

  return (
    <div className="ov-command-console__skeleton" aria-hidden="true">
      <div className="ov-command-console__skeleton-summary">
        <Line width="100%" />
        <Line width="64%" />
      </div>
    </div>
  );
}
