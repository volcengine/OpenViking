import { fileURLToPath } from 'node:url';
import { defineConfig } from '@rspress/core';
import { pluginPreview } from '@rspress/plugin-preview';

export default defineConfig({
  root: fileURLToPath(new URL('./docs', import.meta.url)),
  base: '/ui/',
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
    nav: [
      { text: 'Guide', link: '/guide/getting-started' },
      { text: 'Components', link: '/components/' },
      { text: 'OpenViking docs', link: 'https://docs.openviking.ai/' },
    ],
    sidebar: {
      '/guide/': [
        {
          text: 'Guide',
          items: [{ text: 'Getting started', link: '/guide/getting-started' }],
        },
      ],
      '/components/': [
        {
          text: 'Components',
          items: [{ text: 'StatusBadge', link: '/components/status-badge' }],
        },
      ],
    },
    footer: {
      message: 'OpenViking UI · React modules for context applications',
    },
  },
});
