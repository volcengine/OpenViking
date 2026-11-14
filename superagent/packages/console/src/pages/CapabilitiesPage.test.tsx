import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import fixtures from '../../test/fixtures.json'
import { CapabilitiesPage } from './CapabilitiesPage'

it('shows owner-disabled and environment-unavailable as different states', () => {
  render(<CapabilitiesPage capabilities={{ ...fixtures.capabilities, source: 'rfc' }} />)
  expect(screen.getByText('tasks.control')).toBeInTheDocument()
  expect(screen.getAllByText('Disabled by owner').length).toBeGreaterThan(1)
  expect(screen.getByText('Not supported on this platform')).toBeInTheDocument()
  expect(screen.getAllByText('Local Control Broker unavailable')).toHaveLength(4)
  expect(screen.getByText('Intentionally not provided')).toBeInTheDocument()
  expect(screen.queryByRole('switch')).not.toBeInTheDocument()
})
