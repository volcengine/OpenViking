import { fileURLToPath } from 'node:url';
import { defineConfig } from '@rspress/core';
import { pluginPreview } from '@rspress/plugin-preview';
import { pluginWorkspaceDev } from 'rsbuild-plugin-workspace-dev';
import { createSidebar } from './sidebar';

export default defineConfig({
  root: fileURLToPath(new URL('./docs', import.meta.url)),
  base: '/ui/',
  // 默认语言是中文：开发时看到的就是中文页面，地址不带语言前缀；英文移到
  // `/ui/en/` 下。
  //
  // The default language is Chinese, so development shows the Chinese pages
  // without a language prefix; English moves to `/ui/en/`.
  lang: 'zh',
  title: 'OpenViking UI',
  description: 'React components for OpenViking applications.',
  // Brand assets mirror docs/images/{nav-logo-light,nav-logo-dark,favicon}.svg.
  icon: '/favicon.svg',
  logo: { light: '/logo.svg', dark: '/logo-dark.svg' },
  logoText: 'OpenViking UI',
  globalStyles: fileURLToPath(new URL('./styles.css', import.meta.url)),
  plugins: [pluginPreview()],
  builderConfig: {
    plugins: [pluginWorkspaceDev()],
  },
  themeConfig: {
    socialLinks: [
      {
        icon: 'github',
        mode: 'link',
        content: 'https://github.com/volcengine/OpenViking',
      },
    ],
    locales: [
      {
        lang: 'zh',
        label: '简体中文',
        title: 'OpenViking UI',
        description: '面向 OpenViking 应用的 React 组件库。',
        nav: [
          { text: '指南', link: '/guide/getting-started' },
          { text: '业务组件', link: '/components/' },
          { text: '区块', link: '/modules/' },
          { text: 'OpenViking 文档', link: 'https://docs.openviking.ai/' },
        ],
        sidebar: createSidebar('zh'),
      },
      {
        lang: 'en',
        label: 'English',
        title: 'OpenViking UI',
        description: 'React components for OpenViking applications.',
        nav: [
          { text: 'Guide', link: '/en/guide/getting-started' },
          { text: 'Business components', link: '/en/components/' },
          { text: 'Blocks', link: '/en/modules/' },
          { text: 'OpenViking docs', link: 'https://docs.openviking.ai/' },
        ],
        sidebar: createSidebar('en'),
      },
    ],
    footer: {
      message: 'OpenViking UI',
    },
  },
});
