import {
  Navigate,
  createFileRoute,
  useRouterState,
} from '@tanstack/react-router'

export const Route = createFileRoute('/users')({ component: LegacyUsers })
function LegacyUsers() {
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  const to = pathname.endsWith('/groups')
    ? '/admin/users/groups'
    : pathname.endsWith('/permissions')
      ? '/admin/users/permissions'
      : pathname.endsWith('/memory-templates')
        ? '/admin/users/memory-templates'
        : '/admin/users'
  return <Navigate replace to={to} />
}
