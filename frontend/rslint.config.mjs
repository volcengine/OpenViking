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

import propsOrder from './rslint-plugins/props-order.mjs';

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
  {
    // 公开 Props 按约定放在各目录的 types.ts；内部子组件的 Props 定义在
    // 各自的 .tsx 里，不属于公开接口。
    files: ['packages/react/src/**/types.ts'],
    plugins: { 'ov-ui': propsOrder },
    rules: { 'ov-ui/props-order': 'error' },
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
