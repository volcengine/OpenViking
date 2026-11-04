import {
  defineConfig,
  globalIgnores,
  globals,
  js,
  jsxA11yPlugin,
  reactHooksPlugin,
  reactPlugin,
  ts,
} from '@rslint/core';

export default defineConfig([
  globalIgnores([
    '**/dist/**',
    '**/doc_build/**',
    '**/.rspress/**',
    '**/.rspress-cache/**',
  ]),
  js.configs.recommended,
  ...ts.configs.recommendedTypeChecked,
  {
    rules: {
      'max-lines': [
        'error',
        { max: 200, skipBlankLines: true, skipComments: true },
      ],
    },
  },
  {
    files: ['**/*.{ts,tsx,mts,cts}'],
    languageOptions: {
      parserOptions: {
        project: [
          './packages/react/tsconfig.json',
          './packages/react/tsconfig.config.json',
          './docs/tsconfig.json',
        ],
      },
    },
  },
  {
    files: ['**/*.{js,mjs}'],
    languageOptions: { globals: globals.node },
  },
  reactPlugin.configs.recommended,
  reactHooksPlugin.configs.recommended,
  jsxA11yPlugin.configs.recommended,
  {
    files: ['**/*.{jsx,tsx}'],
    rules: {
      // TypeScript props and the automatic JSX runtime replace these checks.
      'react/prop-types': 'off',
      'react/react-in-jsx-scope': 'off',
    },
  },
]);
