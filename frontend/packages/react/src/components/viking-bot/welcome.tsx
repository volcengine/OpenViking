import { VikingBotIcon } from '../../icons/viking-bot-icons.js';
import type { VikingBotQuickInput } from './capabilities.js';
import type { VikingBotLabels } from './model.js';
import { DEFAULT_LABELS } from './labels.js';
import { QuickInputs } from './quick-inputs.js';

export function VikingBotWelcome({
  labels: overrides,
  quickInputs,
  onSelect,
  disabled,
}: {
  labels?: VikingBotLabels;
  quickInputs?: readonly VikingBotQuickInput[];
  onSelect: (input: VikingBotQuickInput) => void;
  disabled?: boolean;
}) {
  const labels = { ...DEFAULT_LABELS, ...overrides };
  return (
    <div className="ov-viking-bot__home">
      <div className="ov-viking-bot__home-intro">
        <div className="ov-viking-bot__home-illustration" aria-hidden="true">
          <span className="ov-viking-bot__home-bulb">
            <VikingBotIcon name="bulb" width={24} height={24} fill="none" />
          </span>
          <span className="ov-viking-bot__home-book">
            <VikingBotIcon
              name="book"
              width={24}
              height={24}
              viewBox="-2.833333 -3.5 20 19.2"
            />
          </span>
          <span className="ov-viking-bot__home-video">
            <VikingBotIcon name="video" width={24} height={24} />
          </span>
        </div>
        <div className="ov-viking-bot__home-text">
          <h2>{labels.welcomeTitle}</h2>
          <p>{labels.welcomeDescription}</p>
        </div>
      </div>
      <QuickInputs
        items={quickInputs}
        onSelect={onSelect}
        disabled={disabled}
      />
    </div>
  );
}
