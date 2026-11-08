import { useState } from 'react';

import type { InteractionLabels } from '../labels.js';
import type { VikingBotInteractionAnswer } from '../types.js';

type ChoiceRequest = {
  options: readonly {
    value: string;
    label: React.ReactNode;
    description?: React.ReactNode;
    code?: string;
  }[];
  allowCustomInput?: boolean | { placeholder?: string };
};

const CUSTOM = '__custom__';

/** 单选字段。Single-choice field. */
export function ChoiceField({
  request,
  disabled,
  labels,
  onSubmit,
}: {
  request: ChoiceRequest;
  disabled?: boolean;
  labels: InteractionLabels;
  onSubmit: (answer: VikingBotInteractionAnswer) => void;
}) {
  const [selected, setSelected] = useState('');
  const [custom, setCustom] = useState('');
  const customEnabled = Boolean(request.allowCustomInput);
  const customPlaceholder =
    typeof request.allowCustomInput === 'object'
      ? (request.allowCustomInput.placeholder ?? labels.other)
      : labels.other;

  return (
    <>
      <ul className="ov-viking-bot__interaction-options">
        {request.options.map((option) => (
          <li key={option.value}>
            <label className="ov-viking-bot__interaction-option">
              <input
                type="radio"
                name="ov-viking-bot-interaction"
                value={option.value}
                checked={selected === option.value}
                disabled={disabled}
                onChange={() => setSelected(option.value)}
              />
              <span>{option.label}</span>
              {option.code ? <code>{option.code}</code> : null}
              {option.description ? <small>{option.description}</small> : null}
            </label>
          </li>
        ))}
        {customEnabled ? (
          <li>
            <label className="ov-viking-bot__interaction-option">
              <input
                type="radio"
                name="ov-viking-bot-interaction"
                value={CUSTOM}
                checked={selected === CUSTOM}
                disabled={disabled}
                onChange={() => setSelected(CUSTOM)}
              />
              <input
                type="text"
                value={custom}
                placeholder={customPlaceholder}
                aria-label={customPlaceholder}
                disabled={disabled || selected !== CUSTOM}
                onChange={(event) => setCustom(event.target.value)}
              />
            </label>
          </li>
        ) : null}
      </ul>
      <button
        type="button"
        className="ov-viking-bot__interaction-submit"
        disabled={disabled || !selected}
        onClick={() =>
          onSubmit({
            kind: 'choice',
            value: selected === CUSTOM ? custom : selected,
          })
        }
      >
        {labels.submit}
      </button>
    </>
  );
}
