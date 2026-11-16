import { useId, useState } from 'react';
import { Radio } from '../../../shared/primitives/radio/radio.js';
import { CustomChoiceInput } from './custom-choice-input.js';
import type { VikingBotInteractionFieldContext } from './field-types.js';
import type { Locale } from '../../../shared/locale.js';
import type { InteractionLabels } from './labels.js';
import type { VikingBotInteractionRequest } from './types.js';

interface Props {
  controlProps?: VikingBotInteractionFieldContext<string>['controlProps'];
  request: Extract<VikingBotInteractionRequest, { kind: 'choice' | 'repair' }>;
  value?: string;
  labels: InteractionLabels;
  locale: Locale;
  disabled: boolean;
  invalid: boolean;
  errorId: string;
  onChange: (value: string) => void;
}

export function ChoiceInput({
  request,
  controlProps,
  value,
  labels,
  locale,
  disabled,
  invalid,
  errorId,
  onChange,
}: Props) {
  const id = useId();
  const [custom, setCustom] = useState(false);
  const [customText, setCustomText] = useState('');
  const options = request.options.map((option) => ({
    ...option,
    value: 'value' in option ? option.value : option.id,
  }));
  const allowCustom = request.kind === 'choice' && request.allowCustomInput;
  return (
    <div
      {...controlProps}
      className="ov-viking-bot__interaction-choices"
      role="radiogroup"
      aria-label={
        typeof request.question === 'string' ? request.question : labels.answer
      }
      aria-invalid={invalid || undefined}
      aria-describedby={
        controlProps?.['aria-describedby'] ?? (invalid ? errorId : undefined)
      }
    >
      {options.map((option, index) => (
        <Radio
          key={option.value}
          name={id}
          value={option.value}
          checked={!custom && value === option.value}
          disabled={disabled}
          className="ov-viking-bot__interaction-option"
          onChange={() => {
            setCustom(false);
            onChange(option.value);
          }}
        >
          <span className="ov-viking-bot__interaction-index" aria-hidden="true">
            {index + 1}
          </span>
          <span className="ov-viking-bot__interaction-option-content">
            <span>{option.label}</span>
            {option.description ? (
              <span className="ov-viking-bot__interaction-description">
                {option.description}
              </span>
            ) : null}
            {'code' in option && option.code ? (
              <code>{option.code}</code>
            ) : null}
          </span>
        </Radio>
      ))}
      {allowCustom ? (
        <CustomChoiceInput
          key={String(disabled)}
          name={id}
          selected={custom}
          value={customText}
          labels={labels}
          locale={locale}
          disabled={disabled}
          invalid={invalid}
          errorId={errorId}
          config={typeof allowCustom === 'object' ? allowCustom : undefined}
          onChoose={(text) => {
            setCustom(true);
            setCustomText(text);
            onChange(text);
          }}
        />
      ) : null}
    </div>
  );
}
