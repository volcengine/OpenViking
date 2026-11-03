import { fileURLToPath } from 'node:url';
import { defineConfig } from '@rspress/core';
import { pluginPreview } from '@rspress/plugin-preview';

export default defineConfig({
  root: fileURLToPath(new URL('./docs', import.meta.url)),
  base: '/ui/',
  lang: 'en',
  title: 'OpenViking UI',
  description: 'React UI modules for OpenViking applications.',
  icon: '/logo.svg',
  logo: '/logo.svg',
  globalStyles: fileURLToPath(new URL('./styles.css', import.meta.url)),
  plugins: [pluginPreview()],
  builderConfig: {
    resolve: {
      alias:
        process.env.NODE_ENV === 'production'
          ? {}
          : {
              '@openviking/ui/style.css': fileURLToPath(
                new URL(
                  '../packages/react/src/components/status-badge/status-badge.module.css',
                  import.meta.url,
                ),
              ),
              '@openviking/ui': fileURLToPath(
                new URL('../packages/react/src/index.ts', import.meta.url),
              ),
            },
    },
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
                { text: 'StatusBadge', link: '/components/status-badge' },
              ],
            },
          ],
        },
      },
      {
        lang: 'zh',
        label: '简体中文',
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
                {
                  text: 'StatusBadge 状态徽标',
                  link: '/zh/components/status-badge',
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
