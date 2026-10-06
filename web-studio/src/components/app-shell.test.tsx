// @vitest-environment jsdom

import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

import { AppShell, ConnectionScopedRouteContent } from './app-shell'

const mocks = vi.hoisted(() => ({
  role: 'root' as 'admin' | 'root',
}))

vi.mock('#/hooks/use-app-connection', () => ({
  AppConnectionProvider: ({ children }: { children: ReactNode }) => children,
  useAppConnection: () => ({
    connection: {
      accountId: 'acme',
      adminApiKey: 'control-key',
      apiKey: '',
      baseUrl: 'http://localhost:1933',
      userId: 'root',
    },
    connectionRole: mocks.role,
    identityScopeKey: `acme:${mocks.role}`,
    isConnectionRoleLoading: false,
    serverMode: 'api_key',
  }),
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: ReactNode }) => children,
  useNavigate: () => vi.fn(),
  useRouterState: () => '/home',
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: {
      changeLanguage: vi.fn(),
      language: 'en',
      resolvedLanguage: 'en',
    },
    t: (key: string) => key,
  }),
}))
vi.mock('next-themes', () => ({
  useTheme: () => ({ resolvedTheme: 'light', setTheme: vi.fn() }),
}))

vi.mock('#/components/ui/collapsible', () => {
  const Wrapper = ({ children }: { children?: ReactNode }) => children
  return {
    Collapsible: Wrapper,
    CollapsibleContent: Wrapper,
    CollapsibleTrigger: Wrapper,
  }
})
vi.mock('#/components/ui/scroll-area', () => ({
  ScrollArea: ({ children }: { children?: ReactNode }) => children,
}))
vi.mock('#/components/ui/sidebar', () => {
  const Wrapper = ({ children }: { children?: ReactNode }) => children
  return {
    Sidebar: Wrapper,
    SidebarContent: Wrapper,
    SidebarGroup: Wrapper,
    SidebarGroupContent: Wrapper,
    SidebarGroupLabel: Wrapper,
    SidebarHeader: Wrapper,
    SidebarInset: Wrapper,
    SidebarMenu: Wrapper,
    SidebarMenuButton: Wrapper,
    SidebarMenuItem: Wrapper,
    SidebarMenuSub: Wrapper,
    SidebarMenuSubButton: Wrapper,
    SidebarMenuSubItem: Wrapper,
    SidebarProvider: Wrapper,
    SidebarTrigger: Wrapper,
  }
})
vi.mock('#/components/account-switcher', () => ({
  AccountSwitcher: () => null,
}))
vi.mock('#/components/cross-device-verify-dialog', () => ({
  CrossDeviceVerifyDialog: () => null,
}))
vi.mock('#/components/current-user-menu', () => ({
  CurrentUserMenu: () => null,
}))
vi.mock('#/components/generated-credential-dialog', () => ({
  GeneratedCredentialDialog: () => null,
}))

beforeEach(() => {
  mocks.role = 'root'
})
afterEach(cleanup)

describe('ConnectionScopedRouteContent', () => {
  it('does not mount route content while the server mode is unresolved', () => {
    render(
      <ConnectionScopedRouteContent serverMode="checking">
        <span data-testid="identity-scoped-route" />
      </ConnectionScopedRouteContent>,
    )

    expect(screen.queryByTestId('identity-scoped-route')).toBeNull()
  })

  it.each(['api_key', 'trusted', 'dev', 'oidc', 'ldap', 'offline'] as const)(
    'mounts route content after resolving %s mode',
    (serverMode) => {
      render(
        <ConnectionScopedRouteContent serverMode={serverMode}>
          <span data-testid="identity-scoped-route" />
        </ConnectionScopedRouteContent>,
      )

      expect(screen.getByTestId('identity-scoped-route')).toBeTruthy()
    },
  )
})

it('shows Account Models navigation only to Root', () => {
  const { rerender } = render(<AppShell>{null}</AppShell>)

  expect(screen.getByText('footer.models')).toBeTruthy()

  mocks.role = 'admin'
  rerender(<AppShell>{null}</AppShell>)

  expect(screen.queryByText('footer.models')).toBeNull()
})
