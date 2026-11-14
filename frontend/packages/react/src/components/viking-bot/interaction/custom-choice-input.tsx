import { useCallback, useRef, useState } from 'react';
import { Radio } from '../../../shared/primitives/radio/radio.js';
import { Input } from '../../../shared/primitives/input/input.js';
import { Button } from '../../../shared/primitives/button/button.js';
import EditIcon from '../../../../../icons/svg/viking-bot/interaction-edit.svg?react';
import { DirectoryPicker } from './directory-picker.js';
import type { VikingBotInteractionCustomInputContext } from './types.js';
import type { Locale } from '../../../shared/locale.js';
import type { InteractionLabels } from './labels.js';
import type { VikingBotInteractionDirectoryPicker } from './directory-picker-types.js';

export function CustomChoiceInput({
  name,
  selected,
  value,
  config,
  labels,
  locale,
  disabled,
  invalid,
  errorId,
  onChoose,
}: {
  name: string;
  selected: boolean;
  value: string;
  config?: {
    placeholder?: string;
    open?: boolean;
    onOpen?: (context: VikingBotInteractionCustomInputContext) => void;
    directoryPicker?: VikingBotInteractionDirectoryPicker;
  };
  labels: InteractionLabels;
  locale: Locale;
  disabled: boolean;
  invalid: boolean;
  errorId: string;
  onChoose: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const focusInput = useCallback(
    (node: HTMLInputElement | null) => node?.focus(),
    [],
  );
  const current = useRef({ disabled, onChoose });
  current.current = { disabled, onChoose };
  const icon = (
    <span className="ov-viking-bot__interaction-index" aria-hidden="true">
      <EditIcon className="ov-viking-bot__interaction-edit" />
    </span>
  );
  const className = `ov-viking-bot__interaction-custom${selected ? ' ov-viking-bot__interaction-custom--selected' : ''}`;
  if (config?.onOpen || config?.directoryPicker)
    return (
      <>
        <Button
          ref={triggerRef}
          className={`${className} ov-viking-bot__interaction-picker-trigger`}
          aria-haspopup="dialog"
          aria-expanded={disabled ? false : config.onOpen ? config.open : open}
          aria-pressed={selected}
          aria-label={selected ? `${labels.other}: ${value}` : labels.other}
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? errorId : undefined}
          disabled={disabled}
          onClick={() =>
            config.onOpen
              ? config.onOpen({
                  value,
                  select: (next) => {
                    if (triggerRef.current && !current.current.disabled)
                      current.current.onChoose(next);
                  },
                })
              : (config.directoryPicker?.onQueryChange?.(''), setOpen(true))
          }
        >
          {icon}
          <span className="ov-viking-bot__interaction-picker-value">
            {selected ? value : labels.other}
          </span>
        </Button>
        {open && !disabled && config.directoryPicker ? (
          <DirectoryPicker
            picker={config.directoryPicker}
            value={value}
            labels={labels}
            locale={locale}
            triggerRef={triggerRef}
            onClose={() => setOpen(false)}
            onConfirm={(uri) => {
              setOpen(false);
              config.directoryPicker?.onSelectionChange?.(uri);
              onChoose(uri);
            }}
          />
        ) : null}
      </>
    );
  return (
    <div className={className}>
      <Radio
        name={name}
        checked={selected}
        disabled={disabled}
        className="ov-viking-bot__interaction-custom-choice"
        onChange={() => onChoose(value)}
      >
        {icon}
        <span
          className={selected ? 'ov-viking-bot__interaction-sr' : undefined}
        >
          {labels.other}
        </span>
      </Radio>
      {selected ? (
        <Input
          ref={focusInput}
          name="custom-answer"
          aria-label={labels.other}
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? errorId : undefined}
          className="ov-viking-bot__interaction-custom-input"
          value={value}
          placeholder={config?.placeholder ?? labels.other}
          disabled={disabled}
          onChange={(event) => onChoose(event.target.value)}
        />
      ) : null}
    </div>
  );
}
