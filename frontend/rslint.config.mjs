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
    // 文档示例以数据为主：一棵示例树、它的渲染、演示用的产品图标和少量样式
    // 都放在同一个文件里，而 rspress 的虚拟 demo 只打包入口文件、无法解析
    // 相对导入的兄弟模块。上限放宽到 1000 行，仍保留限制以免示例无限膨胀。
    files: ['docs/docs/_demos/**/*.tsx'],
    rules: {
      'max-lines': [
        'error',
        { max: 1000, skipBlankLines: true, skipComments: true },
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
    // 可拖拽的分隔条是 WAI-ARIA 的 window splitter：`role="separator"` 在可聚
    // 焦时就属于可交互角色，`tabIndex` 与方向键处理都是规范要求的。默认配置按
    // 「装饰性分隔线」判定，这里只对这一个组件放开，不放松其他文件。
    //
    // A draggable divider is the WAI-ARIA window splitter: a focusable
    // `role="separator"` is an interactive role, and both `tabIndex` and the arrow
    // keys are required by the pattern. The default config judges it as a
    // decorative rule; the exception is narrowed to this one component.
    files: ['packages/react/src/shared/primitives/resizable/*.tsx'],
    rules: {
      'jsx-a11y/no-noninteractive-tabindex': [
        'error',
        {
          tags: [],
          roles: ['tabpanel', 'separator'],
          allowExpressionValues: true,
        },
      ],
      'jsx-a11y/no-noninteractive-element-interactions': 'off',
    },
  },
  {
    files: ['**/*.{jsx,tsx}'],
    rules: {
      // TypeScript props and the automatic JSX runtime replace these checks.
      'react/prop-types': 'off',
      'react/react-in-jsx-scope': 'off',
    },
  },
]);
