// @vitest-environment jsdom
import { StudioManagementNavigation } from '#/app/navigation/studio-management-navigation'
import '#/i18n'
import { SidebarProvider } from '#/shared/ui/sidebar'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

vi.mock('#/shared/hooks/use-mobile', () => ({ useIsMobile: () => false }))
vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    children,
    ...props
  }: {
    to: string
    children?: React.ReactNode
  }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}))
afterEach(cleanup)
it.each([false, true])(
  'shows real management destinations with Root configuration access=%s',
  (isRoot) => {
    render(
      <SidebarProvider>
        <StudioManagementNavigation
          pathname="/admin/users/groups"
          isRoot={isRoot}
        />
      </SidebarProvider>,
    )
    const links = screen.getAllByRole('link')
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '/admin/dashboard',
      '/admin/users',
      '/admin/users/permissions',
      '/admin/users/memory-templates',
      ...(isRoot ? ['/admin/vikingbot'] : []),
      '/admin/tasks',
      '/admin/request-logs',
      ...(isRoot
        ? ['/admin/configuration', '/admin/configuration/server']
        : []),
    ])
    expect(links[1].hasAttribute('data-active')).toBe(true)
  },
)

it('highlights only server configuration in the Root group', () => {
  render(
    <SidebarProvider>
      <StudioManagementNavigation
        pathname="/admin/configuration/server"
        isRoot
      />
    </SidebarProvider>,
  )
  const server = screen.getByRole('link', { name: 'Server configuration' })
  const configuration = screen.getByRole('link', {
    name: 'Configuration',
  })
  expect(server.hasAttribute('data-active')).toBe(true)
  expect(configuration.hasAttribute('data-active')).toBe(false)
  expect(server.closest('[data-slot="sidebar-group"]')).toBe(
    screen
      .getByRole('link', { name: 'Configuration' })
      .closest('[data-slot="sidebar-group"]'),
  )
})

it('groups gateway management with Root configuration and highlights every gateway subpage', () => {
  render(
    <SidebarProvider>
      <StudioManagementNavigation
        pathname="/admin/gateway/profiles/p1"
        isRoot
        showGateway
      />
    </SidebarProvider>,
  )
  const gateway = screen.getByRole('link', { name: 'Gateway' })
  const configuration = screen.getByRole('link', {
    name: 'Configuration',
  })
  expect(gateway.getAttribute('href')).toBe('/admin/gateway')
  expect(gateway.hasAttribute('data-active')).toBe(true)
  expect(gateway.closest('[data-slot="sidebar-group"]')).toBe(
    configuration.closest('[data-slot="sidebar-group"]'),
  )
})

it('keeps membership and Root credential entries out of unauthenticated development', () => {
  render(
    <SidebarProvider>
      <StudioManagementNavigation
        pathname="/admin/configuration/server"
        isRoot
        showRootConfiguration={false}
        showGateway
        developmentMode
      />
    </SidebarProvider>,
  )
  expect(
    screen.queryByRole('link', { name: 'Server configuration' }),
  ).toBeNull()
  expect(screen.queryByRole('link', { name: 'Gateway' })).toBeNull()
  expect(screen.queryByRole('link', { name: 'User & API Keys' })).toBeNull()
})

it('does not expose Gateway to an Account Admin even when the service supports it', () => {
  render(
    <SidebarProvider>
      <StudioManagementNavigation
        pathname="/admin/users"
        isRoot={false}
        showGateway
      />
    </SidebarProvider>,
  )
  expect(screen.queryByRole('link', { name: 'Gateway' })).toBeNull()
})
