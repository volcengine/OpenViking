import type { FilePreviewLevel } from './types.js';

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
  if (!levels?.length) return null;
  return (
    <div
      className="ov-file-preview__levels ov-file-preview__levels--file"
      role="group"
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
    </div>
  );
}
