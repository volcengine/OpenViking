import { defineValaxyConfig } from 'valaxy'

export default defineValaxyConfig({
  siteConfig: {
    title: 'OpenViking Blog',
    subtitle: 'Engineering notes for context-aware AI agents',
    description: 'Product notes, architecture essays, and release stories from the OpenViking project.',
    author: {
      name: 'OpenViking contributors',
      link: 'https://github.com/volcengine/OpenViking',
    },
    url: 'https://blog.openviking.dev',
    lang: 'en-US',
    favicon: '/favicon.ico',
  },
  theme: 'press',
  themeConfig: {
    logo: '/ov-logo.png',
    nav: [
      { text: 'Blog', link: '/' },
      { text: 'Docs', link: 'https://docs.openviking.dev/' },
      { text: 'GitHub', link: 'https://github.com/volcengine/OpenViking' },
    ],
    social: [
      {
        name: 'GitHub',
        link: 'https://github.com/volcengine/OpenViking',
        icon: 'i-ri-github-line',
      },
    ],
    footer: {
      message: 'Released under the Apache-2.0 License.',
      copyright: 'Copyright OpenViking contributors',
    },
  },
})
