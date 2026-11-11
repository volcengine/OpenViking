// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import i18n from '#/i18n'
import { RetrievalControls } from './retrieval-controls'
import type { RetrievalRequestOptions } from '../types/retrieval'

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('en')
})

it.each(['zh-CN', 'en'])(
  'translates both selected time fields in %s',
  async (language) => {
    await i18n.changeLanguage(language)
    const options: RetrievalRequestOptions = {
      contextTypes: [],
      customPathInput: '',
      ignoreCase: false,
      includeProvenance: false,
      levels: [],
      resultCount: 10,
      scope: 'all',
      tags: [],
      timeField: 'updated_at',
    }
    const props = {
      mode: 'find' as const,
      onModeChange: vi.fn(),
      onOptionsChange: vi.fn(),
      t: i18n.getFixedT(language, 'retrieval'),
    }
    const view = render(<RetrievalControls {...props} options={options} />)
    fireEvent.click(
      screen.getByRole('button', { name: i18n.t('retrieval:advanced.title') }),
    )
    expect(
      screen
        .getByRole('combobox', {
          name: i18n.t('retrieval:advanced.timeField'),
        })
        .querySelector('[data-slot="select-value"]')?.textContent,
    ).toBe(i18n.t('retrieval:advanced.updatedAt'))
    view.rerender(
      <RetrievalControls
        {...props}
        options={{ ...options, timeField: 'created_at' }}
      />,
    )
    expect(
      screen
        .getByRole('combobox', {
          name: i18n.t('retrieval:advanced.timeField'),
        })
        .querySelector('[data-slot="select-value"]')?.textContent,
    ).toBe(i18n.t('retrieval:advanced.createdAt'))
  },
)
