import { useTranslation } from 'react-i18next'

import { AccountSelect, UserSelect } from '#/components/identity-select'
import { Field, FieldContent, FieldLabel } from '#/components/ui/field'
import { Input } from '#/components/ui/input'
import { RadioGroup, RadioGroupItem } from '#/components/ui/radio-group'
import type { AdminAccount, AdminUser } from '#/lib/admin'

export type IdentityPickerValue =
  | { mode: 'current' }
  | { mode: 'custom'; apiKey: string }
  | { mode: 'select'; accountId: string; userId: string; apiKey: string }

/**
 * Account/user directory used by the optional "select a specific identity"
 * mode. Presentational: the parent (via {@link useIdentityDirectory}) owns the
 * selected account/user state and the underlying admin queries; the picker only
 * renders the dropdowns and forwards selection changes.
 */
export type IdentityDirectory = {
  available: boolean
  isRoot: boolean
  accounts: AdminAccount[]
  users: AdminUser[]
  selectedAccountId: string
  selectedUserId: string
  onSelectAccount: (accountId: string) => void
  onSelectUser: (userId: string) => void
  accountsLoading: boolean
  usersLoading: boolean
}

type IdentityPickerProps = {
  value: IdentityPickerValue
  onChange: (value: IdentityPickerValue) => void
  currentApiKey: string
  currentIdentityLabel: string
  customKeyId?: string
  disabled?: boolean
  directory?: IdentityDirectory
}

export function resolveEffectiveApiKey(
  value: IdentityPickerValue,
  currentApiKey: string,
): string {
  // 'custom' and 'select' both carry a concrete key; 'current' resolves to the
  // Studio session key.
  return value.mode === 'current' ? currentApiKey : value.apiKey
}

export function IdentityPicker({
  value,
  onChange,
  currentApiKey,
  currentIdentityLabel,
  customKeyId = 'identity-picker-custom-key',
  disabled = false,
  directory,
}: IdentityPickerProps) {
  const { t } = useTranslation(['oauth', 'common'])
  const hasCurrentKey = Boolean(currentApiKey)
  const canSelect = Boolean(directory?.available)

  return (
    <RadioGroup
      value={value.mode}
      onValueChange={(next) => {
        if (next === 'current') {
          onChange({ mode: 'current' })
        } else if (next === 'select') {
          onChange({
            mode: 'select',
            accountId: directory?.selectedAccountId ?? '',
            userId: directory?.selectedUserId ?? '',
            apiKey: value.mode === 'select' ? value.apiKey : '',
          })
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

      {canSelect && directory ? (
        <label className="flex items-start gap-3 cursor-pointer">
          <RadioGroupItem
            value="select"
            disabled={disabled}
            className="mt-0.5"
          />
          <div className="flex flex-1 flex-col gap-2">
            <span className="text-sm font-medium leading-none">
              {t('identityPicker.useSelect', { ns: 'oauth' })}
            </span>
            {value.mode === 'select' ? (
              <div className="flex flex-col gap-2">
                <div className="grid gap-1">
                  <span className="text-xs font-medium text-muted-foreground">
                    {t('identityPicker.selectAccountLabel', { ns: 'oauth' })}
                  </span>
                  <AccountSelect
                    accounts={directory.accounts}
                    value={directory.selectedAccountId}
                    label={t('identityPicker.selectAccountLabel', {
                      ns: 'oauth',
                    })}
                    disabled={
                      disabled || !directory.isRoot || directory.accountsLoading
                    }
                    onChange={directory.onSelectAccount}
                  />
                </div>
                <div className="grid gap-1">
                  <span className="text-xs font-medium text-muted-foreground">
                    {t('identityPicker.selectUserLabel', { ns: 'oauth' })}
                  </span>
                  <UserSelect
                    users={directory.users}
                    value={directory.selectedUserId}
                    label={t('identityPicker.selectUserLabel', { ns: 'oauth' })}
                    disabled={disabled || directory.usersLoading}
                    onChange={directory.onSelectUser}
                  />
                </div>
                {!directory.isRoot ? (
                  <span className="text-xs text-muted-foreground">
                    {t('identityPicker.selectAccountAdminHint', {
                      ns: 'oauth',
                    })}
                  </span>
                ) : null}
                {!value.apiKey && !directory.usersLoading ? (
                  <span className="text-xs text-destructive">
                    {t('identityPicker.selectNoKey', { ns: 'oauth' })}
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>
        </label>
      ) : null}

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
