// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { expect, it, vi } from 'vitest'
import type { OpenVikingProviderProps } from '@openviking/react/client'
import { StudioUiProvider } from './studio-ui-provider'

const state = vi.hoisted(() => ({
  language: 'zh-CN',
  identityScopeKey: 'first-account',
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ i18n: { language: state.language } }),
}))
vi.mock('../hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: state.identityScopeKey }),
}))
vi.mock('@openviking/react/client', () => ({
  OpenVikingProvider: ({ locale, children }: OpenVikingProviderProps) => (
    <div lang={locale}>{children}</div>
  ),
}))

it('changes account and shared language without remounting the Studio shell', () => {
  function Filter() {
    const [value, setValue] = useState('')
    return (
      <input
        aria-label="Filter"
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
    )
  }
  const tree = () => (
    <StudioUiProvider>
      <Filter />
    </StudioUiProvider>
  )
  const { rerender } = render(tree())
  const input = screen.getByRole<HTMLInputElement>('textbox', {
    name: 'Filter',
  })
  fireEvent.change(input, { target: { value: 'kept' } })
  state.language = 'en-US'
  state.identityScopeKey = 'second-account'
  rerender(tree())
  expect(input.closest('[lang]')?.getAttribute('lang')).toBe('en-US')
  expect(input.value).toBe('kept')
})
