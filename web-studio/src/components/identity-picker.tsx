import * as React from 'react'
import { useTranslation } from 'react-i18next'

import { Field, FieldContent, FieldLabel } from '#/components/ui/field'
import { Input } from '#/components/ui/input'
import { RadioGroup, RadioGroupItem } from '#/components/ui/radio-group'

export type IdentityPickerValue =
  | { mode: 'current' }
  | { mode: 'custom'; apiKey: string }

type IdentityPickerProps = {
  value: IdentityPickerValue
  onChange: (value: IdentityPickerValue) => void
  currentApiKey: string
  currentIdentityLabel: string
  customKeyId?: string
  disabled?: boolean
}

export function resolveEffectiveApiKey(
  value: IdentityPickerValue,
  currentApiKey: string,
): string {
  return value.mode === 'current' ? currentApiKey : value.apiKey
}

export function IdentityPicker({
  value,
  onChange,
  currentApiKey,
  currentIdentityLabel,
  customKeyId = 'identity-picker-custom-key',
  disabled = false,
}: IdentityPickerProps) {
  const { t } = useTranslation(['oauth', 'common'])
  const hasCurrentKey = Boolean(currentApiKey)

  React.useEffect(() => {
    if (!hasCurrentKey && value.mode === 'current') {
      onChange({ mode: 'custom', apiKey: '' })
    }
  }, [hasCurrentKey, value, onChange])

  return (
    <RadioGroup
      value={value.mode}
      onValueChange={(next) => {
        if (next === 'current') {
          onChange({ mode: 'current' })
        } else {
          onChange({
            mode: 'custom',
            apiKey: value.mode === 'custom' ? value.apiKey : '',
          })
        }
      }}
      disabled={disabled}
    >
      <label className="flex items-start gap-3 cursor-pointer">
        <RadioGroupItem
          value="current"
          disabled={disabled || !hasCurrentKey}
          className="mt-0.5"
        />
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-medium leading-none">
            {t('identityPicker.useCurrent', { ns: 'oauth' })}
          </span>
          <span className="text-xs text-muted-foreground">
            {hasCurrentKey
              ? currentIdentityLabel
              : t('identityPicker.noCurrent', { ns: 'oauth' })}
          </span>
        </div>
      </label>

      <label className="flex items-start gap-3 cursor-pointer">
        <RadioGroupItem value="custom" disabled={disabled} className="mt-0.5" />
        <div className="flex flex-1 flex-col gap-2">
          <span className="text-sm font-medium leading-none">
            {t('identityPicker.useCustom', { ns: 'oauth' })}
          </span>
          {value.mode === 'custom' ? (
            <Field>
              <FieldLabel htmlFor={customKeyId} className="sr-only">
                {t('identityPicker.customKeyLabel', { ns: 'oauth' })}
              </FieldLabel>
              <FieldContent>
                <Input
                  id={customKeyId}
                  type="password"
                  autoComplete="off"
                  placeholder={t('identityPicker.customKeyPlaceholder', {
                    ns: 'oauth',
                  })}
                  value={value.apiKey}
                  onChange={(event) =>
                    onChange({ mode: 'custom', apiKey: event.target.value })
                  }
                  disabled={disabled}
                />
              </FieldContent>
            </Field>
          ) : null}
        </div>
      </label>
    </RadioGroup>
  )
}
