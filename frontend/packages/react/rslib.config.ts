import { pluginReact } from '@rsbuild/plugin-react';
import { pluginSvgr } from '@rsbuild/plugin-svgr';
import { defineConfig } from '@rslib/core';

export default defineConfig({
  lib: [
    {
      id: 'javascript',
      format: 'esm',
      dts: true,
      source: {
        entry: {
          index: './src/index.ts',
          'components/directory-tour':
            './src/components/directory-tour/index.ts',
          icons: './src/icons/index.tsx',
          client: './src/client/index.ts',
          'components/auth/invitation-confirmation':
            './src/components/auth/invitation-confirmation/index.ts',
          'components/auth/login-method-selector':
            './src/components/auth/login-method-selector/index.ts',
          'components/auth/password-setup-form':
            './src/components/auth/password-setup-form/index.ts',
          'components/auth/password-reset-request-form':
            './src/components/auth/password-reset-request-form/index.ts',
          'components/auth/api-key-login-form':
            './src/components/auth/api-key-login-form/index.ts',
          'components/auth/email-login-form':
            './src/components/auth/email-login-form/index.ts',
          'modules/scope-cascade': './src/modules/scope-cascade/index.ts',
          'components/file-tree': './src/components/file-tree/index.ts',
          'components/file-preview': './src/components/file-preview/index.ts',
          'components/file-browser-action-bar':
            './src/components/file-browser-action-bar/index.ts',
          'components/command-console':
            './src/components/command-console/index.ts',
          'components/scope-switcher':
            './src/components/scope-switcher/index.ts',
          'components/agent-connect': './src/components/agent-connect/index.ts',
          'components/viking-bot': './src/components/viking-bot/index.ts',
          'components/viking-bot/interaction':
            './src/components/viking-bot/interaction/index.ts',
          'modules/file-browser': './src/modules/file-browser/index.ts',
          'components/wiki-dashboard':
            './src/components/wiki-dashboard/index.ts',
          'components/experience-dashboard':
            './src/components/experience-dashboard/index.ts',
          'modules/console': './src/modules/console/index.ts',
        },
      },
      // 位图内联成 data URI，产物不落独立的资源文件：库的 JS 入口要能在没有
      // 打包器的环境（Node、SSR）直接 import，外部 .png 在那里解析不了。
      //
      // Inline bitmaps as data URIs instead of emitting separate asset files: the
      // package's JS entries must stay importable without a bundler (Node, SSR),
      // where an external .png cannot be resolved.
      output: {
        emitCss: false,
        dataUriLimit: { image: 64 * 1024 },
        // Normalize this CommonJS default during bundling for direct Node/SSR use.
        // React and ReactDOM remain external peer dependencies.
        autoExternal: { exclude: ['react-focus-lock'] },
      },
      tools: {
        rspack: {
          optimization: { splitChunks: { chunks: 'all', minSize: 0 } },
        },
      },
    },
    // Docs chrome is private and must not become a shared entry of the SDK graph.
    {
      id: 'docs-controls',
      format: 'esm',
      dts: { distPath: './dist/docs' },
      source: {
        entry: {
          'shared/primitives/button/button':
            './src/shared/primitives/button/button.tsx',
          'shared/primitives/notification/index':
            './src/shared/primitives/notification/index.ts',
        },
      },
      output: { distPath: { root: './dist/docs' }, emitCss: false },
    },
    // Build each stylesheet independently so every public CSS entry is complete.
    ...Object.entries({
      style: './src/style.css',
      'docs/notification/style':
        './src/shared/primitives/notification/style.css',
      'components/directory-tour/style':
        './src/components/directory-tour/style.css',

      'components/auth/invitation-confirmation/style':
        './src/components/auth/invitation-confirmation/style.css',
      'components/auth/login-method-selector/style':
        './src/components/auth/login-method-selector/style.css',
      'components/auth/password-setup-form/style':
        './src/components/auth/password-setup-form/style.css',
      'components/auth/password-reset-request-form/style':
        './src/components/auth/password-reset-request-form/style.css',
      'components/auth/api-key-login-form/style':
        './src/components/auth/api-key-login-form/style.css',
      'components/auth/email-login-form/style':
        './src/components/auth/email-login-form/style.css',
      'modules/scope-cascade/style': './src/modules/scope-cascade/style.css',
      'components/style': './src/components/style.css',
      'components/file-tree/style': './src/components/file-tree/style.css',
      'components/file-preview/style':
        './src/components/file-preview/style.css',
      'components/file-browser-action-bar/style':
        './src/components/file-browser-action-bar/style.css',
      'components/command-console/style':
        './src/components/command-console/style.css',
      'components/scope-switcher/style':
        './src/components/scope-switcher/style.css',
      'components/agent-connect/style':
        './src/components/agent-connect/style.css',
      'components/viking-bot/style': './src/components/viking-bot/style.css',
      'components/viking-bot/interaction/style':
        './src/components/viking-bot/viking-bot-interaction.css',
      'modules/style': './src/modules/style.css',
      'modules/file-browser/style': './src/modules/file-browser/style.css',
      'components/wiki-dashboard/style':
        './src/components/wiki-dashboard/style.css',
      'components/experience-dashboard/style':
        './src/components/experience-dashboard/style.css',
      'modules/console/style': './src/modules/console/style.css',
    }).map(([name, entry]) => ({
      id: name.replaceAll('/', '-'),
      format: 'esm' as const,
      dts: false,
      source: { entry: { [name]: entry } },
    })),
  ],
  output: {
    target: 'web',
    // 默认的 `'auto'` 会在每次构建前清空 `dist/`，重建期间产物短暂消失。文档站的
    // dev server 正盯着这个目录，一旦在那几毫秒里解析模块就会报「找不到模块」，
    // 且文件回来后不会自动恢复。产物文件名稳定、就地覆盖，因此这里不清空；需要
    // 干净构建时用 `npm run clean:lib`。
    //
    // The default `'auto'` empties `dist/` before every build, so the output is
    // briefly absent while it rebuilds. The docs dev server watches that
    // directory and fails with "module not found" if it resolves during those
    // milliseconds, without recovering once the file returns. Output names are
    // stable and overwritten in place, so skip the clean; use `npm run clean:lib`
    // when a from-scratch build is needed.
    cleanDistPath: false,
  },
  plugins: [pluginReact(), pluginSvgr()],
});
