// @vitest-environment jsdom
import { StudioVersion } from '#/features/connection/components/studio-version'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({ version: '0.5.4' }))

vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({ version: state.version }),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: { version?: string }) =>
      key === 'footer.version' ? `v${values?.version ?? ''}` : key,
  }),
}))

afterEach(() => {
  cleanup()
  state.version = '0.5.4'
})

it('shows the version of the connected OpenViking server', () => {
  render(<StudioVersion />)
  expect(screen.getByText('v0.5.4')).toBeTruthy()
})

it('keeps a single version prefix', () => {
  state.version = 'v0.4.16'
  render(<StudioVersion />)
  expect(screen.getByText('v0.4.16')).toBeTruthy()
})

it('renders nothing when the server reports no version', () => {
  state.version = ''
  const { container } = render(<StudioVersion />)
  expect(container.innerHTML).toBe('')
})
