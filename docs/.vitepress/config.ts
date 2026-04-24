import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, type DefaultTheme } from 'vitepress'

const docsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repo = process.env.GITHUB_REPOSITORY || 'volcengine/OpenViking'
const base = process.env.DOCS_BASE || '/'

const sectionNames: Record<string, string> = {
  'getting-started': 'Getting Started',
  concepts: 'Concepts',
  guides: 'Guides',
  api: 'API Reference',
  faq: 'FAQ',
  about: 'About',
  design: 'Design Notes'
}

const zhSectionNames: Record<string, string> = {
  'getting-started': '快速开始',
  concepts: '核心概念',
  guides: '指南',
  api: 'API 参考',
  faq: '常见问题',
  about: '关于',
  design: '设计文档'
}

const navLabels = {
  en: {
    guide: 'Guide',
    api: 'API Reference',
    faq: 'FAQ',
    changelog: 'Changelog'
  },
  zh: {
    guide: '指南',
    api: 'API 参考',
    faq: '常见问题',
    changelog: '更新日志'
  }
}

function titleFromMarkdown(filePath: string): string {
  const content = fs.readFileSync(filePath, 'utf8')
  const heading = content.match(/^#\s+(.+)$/m)?.[1]
  const fallback = path.basename(filePath, '.md')
  return (heading || fallback).replace(/^\d+[-_]/, '').trim()
}

function linkFor(filePath: string): string {
  const relativePath = path.relative(docsRoot, filePath).replaceAll(path.sep, '/')
  return `/${relativePath.replace(/\.md$/, '')}`
}

function sidebarSection(dir: string, title: string, collapsed = true): DefaultTheme.SidebarItem {
  const absoluteDir = path.join(docsRoot, dir)
  const items = fs
    .readdirSync(absoluteDir)
    .filter((file) => file.endsWith('.md'))
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
    .map((file) => {
      const filePath = path.join(absoluteDir, file)
      return {
        text: titleFromMarkdown(filePath),
        link: linkFor(filePath)
      }
    })

  return { text: title, collapsed, items }
}

function localizedGuideSidebarItems(locale: 'en' | 'zh'): DefaultTheme.SidebarItem[] {
  const labels = locale === 'zh' ? zhSectionNames : sectionNames
  const sections = ['getting-started', 'concepts', 'guides']

  return sections.map((section, index) =>
    sidebarSection(`${locale}/${section}`, labels[section], index !== 0)
  )
}

function localizedReferenceSidebarItems(locale: 'en' | 'zh'): DefaultTheme.SidebarItem[] {
  const labels = locale === 'zh' ? zhSectionNames : sectionNames
  return [sidebarSection(`${locale}/api`, labels.api, false)]
}

function localizedAboutSidebarItems(locale: 'en' | 'zh'): DefaultTheme.SidebarItem[] {
  const labels = locale === 'zh' ? zhSectionNames : sectionNames
  return [sidebarSection(`${locale}/about`, labels.about, false)]
}

const designSidebar: DefaultTheme.SidebarItem[] = [
  sidebarSection('design', sectionNames.design, false)
]

const enNav: DefaultTheme.NavItem[] = [
  { text: navLabels.en.guide, link: '/en/getting-started/01-introduction', activeMatch: '/en/(getting-started|concepts|guides)/' },
  { text: navLabels.en.api, link: '/en/api/01-overview', activeMatch: '/en/api/' },
  { text: navLabels.en.faq, link: '/en/faq/faq', activeMatch: '/en/faq/' },
  { text: navLabels.en.changelog, link: '/en/about/02-changelog', activeMatch: '/en/about/' }
]

const zhNav: DefaultTheme.NavItem[] = [
  { text: navLabels.zh.guide, link: '/zh/getting-started/01-introduction', activeMatch: '/zh/(getting-started|concepts|guides)/' },
  { text: navLabels.zh.api, link: '/zh/api/01-overview', activeMatch: '/zh/api/' },
  { text: navLabels.zh.faq, link: '/zh/faq/faq', activeMatch: '/zh/faq/' },
  { text: navLabels.zh.changelog, link: '/zh/about/02-changelog', activeMatch: '/zh/about/' }
]

export default defineConfig({
  base,
  title: 'OpenViking',
  description: 'Open-source context database for AI Agents',
  cleanUrls: true,
  lastUpdated: true,
  // The existing Markdown corpus links to examples, bot docs, localhost snippets,
  // and historical design notes that are outside the VitePress page tree.
  ignoreDeadLinks: true,
  head: [
    ['link', { rel: 'icon', href: `${base}ov-logo.png` }]
  ],
  vite: {
    publicDir: 'images'
  },
  themeConfig: {
    logo: '/ov-logo.png',
    search: {
      provider: 'local'
    },
    nav: enNav,
    sidebar: {
      '/en/getting-started/': localizedGuideSidebarItems('en'),
      '/en/concepts/': localizedGuideSidebarItems('en'),
      '/en/guides/': localizedGuideSidebarItems('en'),
      '/en/api/': localizedReferenceSidebarItems('en'),
      '/en/about/': localizedAboutSidebarItems('en'),
      '/design/': designSidebar
    },
    socialLinks: [
      { icon: 'github', link: `https://github.com/${repo}` }
    ],
    footer: {
      message: 'Released under the Apache-2.0 License.',
      copyright: 'Copyright OpenViking contributors'
    }
  },
  locales: {
    root: {
      label: 'English',
      lang: 'en-US',
      link: '/en/getting-started/01-introduction'
    },
    zh: {
      label: '简体中文',
      lang: 'zh-CN',
      title: 'OpenViking',
      description: '面向 AI Agent 的开源上下文数据库',
      themeConfig: {
        nav: zhNav,
        sidebar: {
          '/zh/getting-started/': localizedGuideSidebarItems('zh'),
          '/zh/concepts/': localizedGuideSidebarItems('zh'),
          '/zh/guides/': localizedGuideSidebarItems('zh'),
          '/zh/api/': localizedReferenceSidebarItems('zh'),
          '/zh/about/': localizedAboutSidebarItems('zh')
        },
        outline: {
          label: '页面导航'
        },
        docFooter: {
          prev: '上一页',
          next: '下一页'
        },
        darkModeSwitchLabel: '外观',
        sidebarMenuLabel: '菜单',
        returnToTopLabel: '返回顶部',
        langMenuLabel: '切换语言'
      }
    }
  }
})
