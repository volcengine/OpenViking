import type { VikingBotQuickInput } from './capabilities.js';

export function QuickInputs({
  items,
  onSelect,
  disabled,
}: {
  items?: readonly VikingBotQuickInput[];
  onSelect: (item: VikingBotQuickInput) => void;
  disabled?: boolean;
}) {
  if (!items?.length) return null;
  return (
    <div className="ov-viking-bot__quick-inputs">
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          disabled={disabled}
          onClick={() => onSelect(item)}
        >
          {item.icon}
          {item.label}
        </button>
      ))}
    </div>
  );
}
