import type { FilePreviewLevel } from './types.js';
import { useTabIndicator } from '../../shared/primitives/tabs/use-tab-indicator.js';

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
  const { ref, indicator } = useTabIndicator(
    '[aria-pressed="true"]',
    activeKey,
    levels?.length ?? 0,
  );
  if (!levels?.length) return null;
  return (
    <div
      className="ov-tabs ov-file-preview__levels ov-file-preview__levels--file"
      role="group"
      ref={ref}
    >
      {levels.map((level) => (
        <button
          key={level.key}
          type="button"
          className="ov-tabs__tab ov-file-preview__level"
          aria-pressed={level.key === activeKey}
          disabled={disabled || level.disabled}
          onClick={() => onSelect(level.key)}
        >
          {level.label}
        </button>
      ))}
      <span
        aria-hidden="true"
        className="ov-tabs__indicator ov-file-preview__level-indicator"
        style={{
          transform: `translateX(${indicator.left}px)`,
          width: indicator.width,
        }}
      />
    </div>
  );
}
