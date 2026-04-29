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

function collectAllMdFiles(srcDir: string): { relativePath: string; absPath: string }[] {
  const results: { relativePath: string; absPath: string }[] = []
  const ignored = new Set(['node_modules', '.vitepress'])

  function walk(dir: string) {
    for (const entry of fs.readdirSync(dir)) {
      if (ignored.has(entry)) continue
      const abs = path.join(dir, entry)
      const stat = fs.statSync(abs)
      if (stat.isDirectory()) {
        walk(abs)
      } else if (entry.endsWith('.md') && entry !== 'index.md') {
        results.push({ relativePath: path.relative(srcDir, abs), absPath: abs })
      }
    }
  }

  walk(srcDir)
  return results.sort((a, b) => a.relativePath.localeCompare(b.relativePath))
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function buildLlmsTxt(siteConfig: any) {
  const siteUrl = process.env.DOCS_SITE_URL || 'https://volcengine.github.io/OpenViking'
  const base = (siteConfig.site.base || '/').replace(/\/$/, '')
  const srcDir = siteConfig.srcDir
  const outDir = siteConfig.outDir

  const files = collectAllMdFiles(srcDir)

  // Group by section prefix (e.g. en/getting-started, zh/concepts)
  const sections = new Map<string, { title: string; url: string }[]>()
  for (const { relativePath, absPath } of files) {
    const parts = relativePath.replace(/\\/g, '/').split('/')
    const section = parts.length >= 2 ? parts.slice(0, -1).join('/') : 'misc'
    const content = fs.readFileSync(absPath, 'utf-8')
    const heading = content.match(/^#\s+(.+)$/m)?.[1]?.trim() ?? path.basename(absPath, '.md')
    const urlPath = `/${relativePath.replace(/\\/g, '/').replace(/\.md$/, '')}`
    const url = `${siteUrl}${base}${urlPath}`
    if (!sections.has(section)) sections.set(section, [])
    sections.get(section)!.push({ title: heading, url })
  }

  const lines: string[] = [
    '# OpenViking',
    '',
    '> Open-source context database for AI Agents. OpenViking unifies memory, resources, and skills management for AI Agents through a file system paradigm.',
    '',
    `- Source: https://github.com/${process.env.GITHUB_REPOSITORY || 'volcengine/OpenViking'}`,
    '',
  ]

  for (const [section, pages] of sections) {
    lines.push(`## ${section}`, '')
    for (const { title, url } of pages) {
      lines.push(`- [${title}](${url})`)
    }
    lines.push('')
  }

  fs.writeFileSync(path.join(outDir, 'llms.txt'), lines.join('\n'), 'utf-8')

  // llms-full.txt: all content concatenated
  const fullLines: string[] = [
    '# OpenViking — Full Documentation',
    '',
    '> This file contains the complete documentation for LLM consumption.',
    '',
  ]
  for (const { relativePath, absPath } of files) {
    const content = fs.readFileSync(absPath, 'utf-8')
    fullLines.push(`\n\n---\n<!-- source: ${relativePath} -->\n\n${content}`)
  }
  fs.writeFileSync(path.join(outDir, 'llms-full.txt'), fullLines.join('\n'), 'utf-8')

  // Per-page llms.txt: /{page-path}/llms.txt returns the raw markdown of that page
  for (const { relativePath, absPath } of files) {
    const content = fs.readFileSync(absPath, 'utf-8')
    const pageDir = path.join(outDir, relativePath.replace(/\.md$/, ''))
    fs.mkdirSync(pageDir, { recursive: true })
    fs.writeFileSync(path.join(pageDir, 'llms.txt'), content, 'utf-8')
  }
}

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
  transformPageData(pageData, { siteConfig }) {
    const srcPath = path.join(siteConfig.srcDir, pageData.relativePath)
    try {
      pageData.frontmatter._rawMarkdown = fs.readFileSync(srcPath, 'utf-8')
    } catch {
      pageData.frontmatter._rawMarkdown = ''
    }
  },
  buildEnd(siteConfig) {
    buildLlmsTxt(siteConfig)
  },
  vite: {
    publicDir: 'images',
    plugins: [
      {
        name: 'llms-txt-dev',
        configureServer(server) {
          server.middlewares.use((req, res, next) => {
            if (!req.url?.endsWith('/llms.txt')) return next()
            const stripped = req.url.replace(/\/llms\.txt$/, '')
            const candidate = stripped ? path.join(docsRoot, stripped + '.md') : null
            if (candidate && fs.existsSync(candidate)) {
              res.setHeader('Content-Type', 'text/plain; charset=utf-8')
              res.end(fs.readFileSync(candidate, 'utf-8'))
            } else {
              next()
            }
          })
        }
      }
    ]
  },
  themeConfig: {
    logo: '/ov-logo.png',
    logoLink: 'https://openviking.ai/',
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
      link: '/zh/getting-started/01-introduction',
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
