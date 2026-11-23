// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import i18n from '#/i18n'
import { AdditionalResourceOptions } from './additional-resource-options'
import type { AdditionalResourceOptionsValue } from './additional-resource-options'

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('en')
})

const defaults: AdditionalResourceOptionsValue = {
  parseMode: 'default',
  processingMode: 'semantic_and_vectors',
  tagMode: 'replace',
  sourceName: '',
  tags: '',
  timeout: '',
  wait: false,
}

it.each(['zh-CN', 'en'])(
  'shows translated closed select labels for every enum in %s',
  async (language) => {
    await i18n.changeLanguage(language)
    const props = {
      disabled: false,
      isRemote: true,
      onChange: vi.fn(),
      t: i18n.getFixedT(language, 'addResource'),
    }
    const view = render(
      <AdditionalResourceOptions {...props} value={defaults} />,
    )
    const labels = () =>
      screen
        .getAllByRole('combobox')
        .map(
          (node) =>
            node.querySelector('[data-slot="select-value"]')?.textContent,
        )
    expect(labels()).toEqual(
      [
        'parseMode.default',
        'processingMode.semantic',
        'preserveStructure.serverDefault',
        'tagMode.replace',
      ].map((key) => i18n.t(`addResource:${key}`)),
    )
    for (const [patch, keys] of [
      [
        {
          parseMode: 'no_split',
          processingMode: 'vectors_only',
          preserveStructure: true,
          tagMode: 'append',
        },
        [
          'parseMode.noSplit',
          'processingMode.vectorsOnly',
          'preserveStructure.preserve',
          'tagMode.append',
        ],
      ],
      [
        { preserveStructure: false, tagMode: 'clear' },
        [
          'parseMode.default',
          'processingMode.semantic',
          'preserveStructure.flatten',
          'tagMode.clear',
        ],
      ],
    ] as const) {
      view.rerender(
        <AdditionalResourceOptions
          {...props}
          value={{ ...defaults, ...patch }}
        />,
      )
      expect(labels()).toEqual(keys.map((key) => i18n.t(`addResource:${key}`)))
    }
    // Switching language updates the selected text without opening any dropdown.
    const nextLanguage = language === 'en' ? 'zh-CN' : 'en'
    await i18n.changeLanguage(nextLanguage)
    view.rerender(
      <AdditionalResourceOptions
        {...props}
        t={i18n.getFixedT(nextLanguage, 'addResource')}
        value={defaults}
      />,
    )
    expect(labels()[2]).toBe(
      i18n.t('addResource:preserveStructure.serverDefault'),
    )
  },
)
