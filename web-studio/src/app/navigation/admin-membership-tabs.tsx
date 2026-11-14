import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import {
  adminGroupsQuery,
  adminUsersQuery,
} from '#/features/admin/users/lib/admin-membership-queries'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { UserRoundIcon, UsersRoundIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

const tabs = [
  {
    to: '/admin/users',
    label: 'appShell:userManagement.members',
    icon: UserRoundIcon,
  },
  {
    to: '/admin/users/groups',
    label: 'appShell:userManagement.groups',
    icon: UsersRoundIcon,
  },
] as const

export function AdminMembershipTabs() {
  const { t } = useTranslation(['settings', 'appShell'])
  const { connection } = useAppConnection()
  const { accountId, baseUrl, adminApiKey: apiKey, userId } = connection
  const adminConnection = { accountId, baseUrl, apiKey, userId }
  const enabled = Boolean(accountId && apiKey)
  const users = useQuery({
    enabled,
    ...adminUsersQuery(adminConnection),
    select: (page) => page.accountTotal,
  })
  const groups = useQuery({
    enabled,
    ...adminGroupsQuery(adminConnection),
    select: (items) => items.length,
  })
  return (
    <nav
      aria-label={t('appShell:adminNavigation.members')}
      className="users-membership-tabs inline-flex w-fit gap-0.5 rounded-md bg-muted p-0.5"
    >
      {tabs.map(({ to, label, icon: Icon }, index) => (
        <Link
          key={to}
          to={to}
          activeOptions={{ exact: true }}
          className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm text-muted-foreground hover:text-foreground aria-[current=page]:bg-background aria-[current=page]:font-medium aria-[current=page]:text-foreground aria-[current=page]:shadow-xs"
        >
          <Icon className="size-3.5" />
          {t(label, { total: (index === 0 ? users.data : groups.data) ?? '–' })}
        </Link>
      ))}
    </nav>
  )
}
