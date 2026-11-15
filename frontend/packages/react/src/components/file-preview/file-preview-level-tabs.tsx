import type { FilePreviewLevel } from './types.js';
import { useLevelIndicator } from './use-level-indicator.js';

export function FilePreviewLevelTabs({
  levels,
  activeKey,
  disabled,
  onSelect,
}: {
  levels?: readonly FilePreviewLevel[];
  activeKey?: string;
  disabled: boolean;
  onSelect: (key: string) => void;
}) {
  const { ref, indicator } = useLevelIndicator(
    '[aria-pressed="true"]',
    activeKey,
    levels?.length ?? 0,
  );
  if (!levels?.length) return null;
  return (
    <div
      className="ov-file-preview__levels ov-file-preview__levels--file"
      role="group"
      ref={ref}
    >
      {levels.map((level) => (
        <button
          key={level.key}
          type="button"
          className="ov-file-preview__level"
          aria-pressed={level.key === activeKey}
          disabled={disabled || level.disabled}
          onClick={() => onSelect(level.key)}
        >
          {level.label}
        </button>
      ))}
      <span
        aria-hidden="true"
        className="ov-file-preview__level-indicator"
        style={{
          transform: `translateX(${indicator.left}px)`,
          width: indicator.width,
        }}
      />
    </div>
  );
}
