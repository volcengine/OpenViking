import { useState } from 'react';

import type { InteractionLabels } from '../labels.js';
import type { VikingBotInteractionAnswer } from '../types.js';

type RepairRequest = {
  options: readonly { id: string; label: string; description?: string }[];
  initialValue?: string;
};

/** 修复候选字段。Repair-candidate field. */
export function RepairField({
  request,
  disabled,
  labels,
  onSubmit,
}: {
  request: RepairRequest;
  disabled?: boolean;
  labels: InteractionLabels;
  onSubmit: (answer: VikingBotInteractionAnswer) => void;
}) {
  const [selected, setSelected] = useState(request.initialValue ?? '');

  return (
    <>
      <ul className="ov-viking-bot__interaction-options">
        {request.options.map((option) => (
          <li key={option.id}>
            <label className="ov-viking-bot__interaction-option">
              <input
                type="radio"
                name="ov-viking-bot-repair"
                value={option.id}
                checked={selected === option.id}
                disabled={disabled}
                onChange={() => setSelected(option.id)}
              />
              <span>{option.label}</span>
              {option.description ? <small>{option.description}</small> : null}
            </label>
          </li>
        ))}
      </ul>
      <button
        type="button"
        className="ov-viking-bot__interaction-submit"
        disabled={disabled || !selected}
        onClick={() => onSubmit({ kind: 'repair', value: selected })}
      >
        {labels.submit}
      </button>
    </>
  );
}
