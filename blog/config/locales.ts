export interface BlogLocale {
  key: string
  label: string
  lang: string
  link: string
  postsPrefix: string
  nav: Array<{ text: string, link: string }>
  hero: {
    kicker: string
    title: string
    intro: string
    label: string
    fallbackCategory: string
    dateLocale: string
  }
  sidebar: {
    text: string
    items: Array<{ text: string, link: string }>
  }
  editLinkText: string
  footer: {
    message: string
    copyright: string
  }
}

export const blogLocales: BlogLocale[] = [
  {
    key: 'root',
    label: 'English',
    lang: 'en',
    link: '/',
    postsPrefix: '/posts/',
    nav: [
      { text: 'Blog', link: '/' },
      { text: 'Docs', link: 'https://docs.openviking.dev/' },
    ],
    hero: {
      kicker: 'OpenViking Blog',
      title: 'Notes from the context layer',
      intro: 'Product notes, architecture essays, and release stories for people building context-aware agents.',
      label: 'Blog posts',
      fallbackCategory: 'OpenViking',
      dateLocale: 'en-US',
    },
    sidebar: {
      text: 'Blog',
      items: [
        { text: 'Why OpenViking Needs a Blog', link: '/posts/getting-started' },
        { text: 'Building a Context Layer for Agents', link: '/posts/context-layer-for-agents' },
      ],
    },
    editLinkText: 'Edit this page',
    footer: {
      message: 'Released under the Apache-2.0 License.',
      copyright: 'Copyright OpenViking contributors',
    },
  },
  {
    key: 'zh',
    label: '简体中文',
    lang: 'zh-CN',
    link: '/zh/',
    postsPrefix: '/zh/posts/',
    nav: [
      { text: '博客', link: '/zh/' },
      { text: '文档', link: 'https://docs.openviking.dev/zh/' },
    ],
    hero: {
      kicker: 'OpenViking 博客',
      title: '来自上下文层的技术笔记',
      intro: '面向构建上下文感知 Agent 的团队，记录产品思考、架构实践和发布故事。',
      label: '博客文章',
      fallbackCategory: 'OpenViking',
      dateLocale: 'zh-CN',
    },
    sidebar: {
      text: '博客',
      items: [
        { text: '为什么 OpenViking 需要博客', link: '/zh/posts/getting-started' },
        { text: '为 Agent 构建上下文层', link: '/zh/posts/context-layer-for-agents' },
      ],
    },
    editLinkText: '编辑此页',
    footer: {
      message: '基于 Apache-2.0 License 发布。',
      copyright: 'Copyright OpenViking contributors',
    },
  },
]

export const defaultBlogLocale = blogLocales[0]

export function resolveBlogLocale(path: string) {
  const matched = blogLocales
    .filter(locale => locale.key !== 'root')
    .find(locale => path.startsWith(locale.link))

  return matched || defaultBlogLocale
}
