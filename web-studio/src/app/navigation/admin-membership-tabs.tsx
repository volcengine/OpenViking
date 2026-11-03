import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { fetchAdminGroups, fetchAdminUsersPage } from '#/shared/api/admin'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { UserRoundIcon, UsersRoundIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

const tabs = [
  { to: '/admin/users', label: 'groups.userLabel', icon: UserRoundIcon },
  {
    to: '/admin/users/groups',
    label: 'groups.groupLabel',
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
    queryKey: ['managed-users', baseUrl, apiKey, accountId, 1, 20, ''],
    queryFn: () =>
      fetchAdminUsersPage(adminConnection, accountId, {
        page: 1,
        pageSize: 20,
        search: '',
      }),
    select: (page) => page.accountTotal,
    retry: false,
  })
  const groups = useQuery({
    enabled,
    queryKey: ['managed-groups', baseUrl, accountId, apiKey],
    queryFn: () => fetchAdminGroups(adminConnection),
    select: (items) => items.length,
    retry: false,
  })
  return (
    <nav
      aria-label={t('appShell:adminNavigation.members')}
      className="inline-flex w-fit gap-0.5 rounded-lg bg-muted p-0.5"
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
