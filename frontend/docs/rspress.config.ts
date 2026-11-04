import { fileURLToPath } from 'node:url';
import { defineConfig } from '@rspress/core';
import { pluginPreview } from '@rspress/plugin-preview';
import { pluginWorkspaceDev } from 'rsbuild-plugin-workspace-dev';

export default defineConfig({
  root: fileURLToPath(new URL('./docs', import.meta.url)),
  base: '/ui/',
  lang: 'en',
  title: 'OpenViking UI',
  description: 'React UI modules for OpenViking applications.',
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
        lang: 'en',
        label: 'English',
        title: 'OpenViking UI',
        description: 'React UI modules for OpenViking applications.',
        nav: [
          { text: 'Guide', link: '/guide/getting-started' },
          { text: 'Components', link: '/components/' },
          { text: 'OpenViking docs', link: 'https://docs.openviking.ai/' },
        ],
        sidebar: {
          '/guide/': [
            {
              text: 'Guide',
              items: [
                { text: 'Getting started', link: '/guide/getting-started' },
              ],
            },
          ],
          '/components/': [
            {
              text: 'Components',
              items: [
                { text: 'Overview', link: '/components/' },
                { text: 'Icons', link: '/components/icons' },
                { text: 'FileTree', link: '/components/file-tree' },
                { text: 'FilePreview', link: '/components/file-preview' },
              ],
            },
          ],
        },
      },
      {
        lang: 'zh',
        label: '简体中文',
        title: 'OpenViking UI',
        description: '面向 OpenViking 应用的 React UI 模块。',
        nav: [
          { text: '指南', link: '/zh/guide/getting-started' },
          { text: '组件', link: '/zh/components/' },
          { text: 'OpenViking 文档', link: 'https://docs.openviking.ai/' },
        ],
        sidebar: {
          '/zh/guide/': [
            {
              text: '指南',
              items: [{ text: '快速开始', link: '/zh/guide/getting-started' }],
            },
          ],
          '/zh/components/': [
            {
              text: '组件',
              items: [
                { text: '概览', link: '/zh/components/' },
                { text: '图标', link: '/zh/components/icons' },
                { text: 'FileTree 文件树', link: '/zh/components/file-tree' },
                {
                  text: 'FilePreview 文件预览',
                  link: '/zh/components/file-preview',
                },
              ],
            },
          ],
        },
      },
    ],
    footer: {
      message: 'OpenViking UI',
    },
  },
});
