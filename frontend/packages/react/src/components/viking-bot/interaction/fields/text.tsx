import { useState } from 'react';

import type { InteractionLabels } from '../labels.js';
import type { VikingBotInteractionAnswer } from '../types.js';

/** 文本字段。Free-text field. */
export function TextField({
  initialValue,
  description,
  multiline,
  disabled,
  labels,
  onSubmit,
}: {
  initialValue?: string;
  description?: React.ReactNode;
  multiline?: boolean;
  disabled?: boolean;
  labels: InteractionLabels;
  onSubmit: (answer: VikingBotInteractionAnswer) => void;
}) {
  const [value, setValue] = useState(initialValue ?? '');

  return (
    <>
      {description ? <p>{description}</p> : null}
      {multiline ? (
        <textarea
          className="ov-viking-bot__interaction-input"
          value={value}
          disabled={disabled}
          onChange={(event) => setValue(event.target.value)}
        />
      ) : (
        <input
          type="text"
          className="ov-viking-bot__interaction-input"
          value={value}
          disabled={disabled}
          onChange={(event) => setValue(event.target.value)}
        />
      )}
      <button
        type="button"
        className="ov-viking-bot__interaction-submit"
        disabled={disabled}
        onClick={() => onSubmit({ kind: 'text', value })}
      >
        {labels.submit}
      </button>
    </>
  );
}
