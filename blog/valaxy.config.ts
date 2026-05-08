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
    llms: {
      enable: true,
      files: true,
      fullText: false,
    },
  },
  theme: 'starter',
  themeConfig: {
    colors: {
      primary: '#0f766e',
    },
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
      since: 2026,
      icon: {
        name: 'i-ri-github-line',
        animated: false,
        color: 'var(--va-c-primary)',
        url: 'https://github.com/volcengine/OpenViking',
        title: 'OpenViking on GitHub',
      },
      powered: false,
      message: 'Released under the Apache-2.0 License.',
      copyright: 'Copyright OpenViking contributors',
    },
  },
})
