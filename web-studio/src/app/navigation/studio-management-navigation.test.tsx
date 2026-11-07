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
      '/admin/users',
      '/admin/users/groups',
      '/admin/users/permissions',
      '/admin/users/memory-templates',
      '/admin/configuration',
      ...(isRoot ? ['/admin/configuration/server', '/admin/vikingbot'] : []),
    ])
    expect(links[1].hasAttribute('data-active')).toBe(true)
    expect(links[0].hasAttribute('data-active')).toBe(false)
  },
)

it('highlights only server configuration in the service management group', () => {
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
    exact: true,
  })
  expect(server.hasAttribute('data-active')).toBe(true)
  expect(configuration.hasAttribute('data-active')).toBe(false)
  expect(server.closest('[data-slot="sidebar-group"]')).toBe(
    screen
      .getByRole('link', { name: 'VikingBot management' })
      .closest('[data-slot="sidebar-group"]'),
  )
})

it('groups gateway management with bot management and highlights every gateway subpage', () => {
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
  const botManagement = screen.getByRole('link', {
    name: 'VikingBot management',
  })
  expect(gateway.getAttribute('href')).toBe('/admin/gateway')
  expect(gateway.hasAttribute('data-active')).toBe(true)
  expect(gateway.closest('[data-slot="sidebar-group"]')).toBe(
    botManagement.closest('[data-slot="sidebar-group"]'),
  )
})

it('keeps unavailable membership pages out of local development while exposing service configuration', () => {
  render(
    <SidebarProvider>
      <StudioManagementNavigation
        pathname="/admin/configuration/server"
        isRoot
        developmentMode
      />
    </SidebarProvider>,
  )
  expect(
    screen.getByRole('link', { name: 'Server configuration' }),
  ).toBeTruthy()
  expect(screen.queryByRole('link', { name: /^Users$/ })).toBeNull()
  expect(screen.queryByRole('link', { name: /^User groups$/ })).toBeNull()
})
