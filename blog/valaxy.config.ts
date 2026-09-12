import { defineValaxyConfig } from 'valaxy'
import { blogLocales, defaultBlogLocale } from './config/locales'

const localeConfig = Object.fromEntries(
  blogLocales.map(locale => [
    locale.key,
    {
      label: locale.label,
      lang: locale.lang,
      link: locale.link,
      ...(locale.key === 'root'
        ? {}
        : {
            themeConfig: {
              nav: locale.nav,
              sidebar: {
                [locale.postsPrefix]: [
                  {
                    text: locale.sidebar.text,
                    items: locale.sidebar.items,
                  },
                ],
              },
              editLink: {
                pattern: 'https://github.com/volcengine/OpenViking/edit/main/blog/:path',
                text: locale.editLinkText,
              },
              footer: locale.footer,
            },
          }),
    },
  ]),
)

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
    lang: defaultBlogLocale.lang,
    languages: blogLocales.map(locale => locale.lang),
    favicon: '/favicon.ico',
    lastUpdated: true,
    llms: {
      enable: true,
      files: true,
      fullText: false,
    },
  },
  theme: 'press',
  themeConfig: {
    colors: {
      primary: '#0f766e',
    },
    logo: '/ov-logo.png',
    nav: defaultBlogLocale.nav,
    sidebar: {
      [defaultBlogLocale.postsPrefix]: [
        {
          text: defaultBlogLocale.sidebar.text,
          items: defaultBlogLocale.sidebar.items,
        },
      ],
    },
    i18nRouting: true,
    locales: localeConfig,
    editLink: {
      pattern: 'https://github.com/volcengine/OpenViking/edit/main/blog/:path',
      text: defaultBlogLocale.editLinkText,
    },
    socialLinks: [
      {
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
      message: defaultBlogLocale.footer.message,
      copyright: defaultBlogLocale.footer.copyright,
    },
  },
})
