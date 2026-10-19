//  @ts-check

import { tanstackConfig } from '@tanstack/eslint-config'
import i18next from 'eslint-plugin-i18next'
import studioI18n from './script/eslint-i18n.mjs'

const LINT_FILES = ['src/**/*.{ts,tsx}']
const GENERATED_IGNORES = ['src/gen/**', 'src/routeTree.gen.ts']
const I18N_IGNORES = [
  ...GENERATED_IGNORES,
  'src/**/*.test.{ts,tsx}',
  'src/test/**',
  'src/i18n/locales/**',
]

export default [
  ...tanstackConfig.map((config) => ({
    ...config,
    files: LINT_FILES,
    ignores: [
      ...(config.ignores ?? []),
      ...GENERATED_IGNORES,
      'src/components/ui/**',
    ],
  })),
  {
    files: LINT_FILES,
    ignores: I18N_IGNORES,
    languageOptions: tanstackConfig.find(
      (config) => config.name === 'tanstack/javascript',
    ).languageOptions,
    plugins: {
      i18next,
      'studio-i18n': studioI18n,
    },
    rules: {
      'studio-i18n/no-literal-message': 'error',
      'studio-i18n/explicit-locale': 'error',
      'i18next/no-literal-string': [
        'error',
        {
          framework: 'react',
          mode: 'jsx-only',
          'jsx-components': {
            exclude: ['^Trans$'],
          },
          'jsx-attributes': {
            include: [
              '^(title|placeholder|label|tooltip|description|alt|aria-label|aria-description|ariaLabel|helperText|emptyText)$',
            ],
          },
        },
      ],
    },
  },
  {
    rules: {
      'import/no-cycle': 'off',
      'import/order': 'off',
      'sort-imports': 'off',
      '@typescript-eslint/array-type': 'off',
      '@typescript-eslint/require-await': 'off',
      'pnpm/json-enforce-catalog': 'off',
    },
  },
  {
    ignores: ['eslint.config.js', 'prettier.config.js', ...GENERATED_IGNORES],
  },
]
