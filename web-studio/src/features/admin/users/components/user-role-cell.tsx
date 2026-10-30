import type { AdminUserRole } from '#/shared/api/admin'
import { Badge } from '#/shared/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/shared/ui/select'
import { useTranslation } from 'react-i18next'

const ASSIGNABLE_ROLES = ['user', 'admin'] as const
function isAssignableRole(
  role: string,
): role is (typeof ASSIGNABLE_ROLES)[number] {
  return role === 'user' || role === 'admin'
}

export function UserRoleCell({
  userId,
  role,
  editable,
  pending,
  onChange,
}: {
  userId: string
  role: string
  editable: boolean
  pending: boolean
  onChange: (role: AdminUserRole) => void
}) {
  const { t } = useTranslation('settings')
  if (!editable || !isAssignableRole(role))
    return (
      <Badge variant={role === 'admin' ? 'secondary' : 'outline'}>
        {t(`roles.${role}`, { defaultValue: role })}
      </Badge>
    )
  return (
    <Select
      value={role}
      disabled={pending}
      onValueChange={(next) => {
        if (next && isAssignableRole(next) && next !== role) onChange(next)
      }}
    >
      <SelectTrigger
        className="h-8 w-28"
        aria-label={t('actions.changeRole', { user: userId })}
      >
        <SelectValue>{t(`roles.${role}`)}</SelectValue>
      </SelectTrigger>
      <SelectContent>
        {ASSIGNABLE_ROLES.map((value) => (
          <SelectItem key={value} value={value}>
            {t(`roles.${value}`)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
