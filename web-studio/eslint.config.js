//  @ts-check

import { tanstackConfig } from '@tanstack/eslint-config'
import i18next from 'eslint-plugin-i18next'

const LINT_FILES = ['src/**/*.{ts,tsx}']
const GENERATED_IGNORES = ['src/gen/**', 'src/routeTree.gen.ts', 'src/components/ui/**']

export default [
  ...tanstackConfig.map((config) => ({
    ...config,
    files: LINT_FILES,
    ignores: [...(config.ignores ?? []), ...GENERATED_IGNORES],
  })),
  {
    files: ['src/components/**/*.tsx', 'src/routes/**/*.tsx'],
    ignores: GENERATED_IGNORES,
    plugins: {
      i18next,
    },
    rules: {
      'i18next/no-literal-string': ['warn', {
        framework: 'react',
        mode: 'jsx-only',
        'jsx-components': {
          exclude: ['^Trans$'],
        },
        'jsx-attributes': {
          include: ['^(title|placeholder|label|tooltip|description|aria-label|aria-description)$'],
        },
      }],
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
