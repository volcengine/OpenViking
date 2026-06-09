import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import {
  DEFAULT_ACCOUNT_ID,
  DEFAULT_USER_ID,
  sortedAccountIds,
  uniqueOptions,
} from '#/lib/admin-options'
import type { AdminAccount, AdminUser } from '#/lib/admin'

export function AccountSelect({
  accounts,
  disabled,
  label,
  onChange,
  value,
}: {
  accounts: readonly AdminAccount[]
  disabled?: boolean
  label: string
  onChange: (value: string) => void
  value: string
}) {
  const options = sortedAccountIds(
    accounts.map((account) => account.accountId),
    value || DEFAULT_ACCOUNT_ID,
  )

  return (
    <Select
      value={value || DEFAULT_ACCOUNT_ID}
      onValueChange={(next) => {
        if (next) {
          onChange(next)
        }
      }}
    >
      <SelectTrigger
        aria-label={label}
        className="h-9 w-full"
        disabled={disabled}
      >
        <SelectValue>{value || DEFAULT_ACCOUNT_ID}</SelectValue>
      </SelectTrigger>
      <SelectContent alignItemWithTrigger>
        {options.map((item) => (
          <SelectItem key={item} value={item}>
            {item}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

export function UserSelect({
  disabled,
  label,
  onChange,
  users,
  value,
}: {
  disabled?: boolean
  label: string
  onChange: (value: string) => void
  users: readonly AdminUser[]
  value: string
}) {
  const options = uniqueOptions(
    users.map((user) => user.userId),
    value || DEFAULT_USER_ID,
  )

  return (
    <Select
      value={value || DEFAULT_USER_ID}
      onValueChange={(next) => {
        if (next) {
          onChange(next)
        }
      }}
    >
      <SelectTrigger
        aria-label={label}
        className="h-9 w-full"
        disabled={disabled}
      >
        <SelectValue>{value || DEFAULT_USER_ID}</SelectValue>
      </SelectTrigger>
      <SelectContent alignItemWithTrigger>
        {options.map((item) => (
          <SelectItem key={item} value={item}>
            {item}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
