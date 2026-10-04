import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitepress'
import taskLists from 'markdown-it-task-lists'
import { hasPageLlmsTxt, pageLlmsTxtPath } from './theme/llms-txt'
import { titleFromMarkdown, docPageTitle, documentationNav, documentationSidebars, documentationSections } from './docs-navigation'

const docsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repo = process.env.GITHUB_REPOSITORY || 'volcengine/OpenViking'
const githubRepositoryUrl = `https://github.com/${repo}?utm_source=docs&utm_medium=referral&utm_campaign=docs`
const configuredBase = '/' + (process.env.DOCS_BASE || '/').split('/').filter(Boolean).join('/') + '/'
const base = configuredBase === '//' ? '/' : configuredBase
// Version the filename so social crawlers do not reuse the previous artwork.
const ogImageUrl = `${(process.env.DOCS_SITE_URL || 'https://docs.openviking.ai').replace(/\/$/, '')}${base}og-openviking-docs-392c2586.png`
const languageSource = fs.readFileSync(path.join(docsRoot, '.vitepress/theme/language-preference.js'), 'utf8').replace('export function', 'function')
const entrySource = fs.readFileSync(path.join(docsRoot, '.vitepress/theme/language-entry.js'), 'utf8').replace('export function', 'function')
const languageBootstrapScript = `${languageSource}\n${entrySource}\n;(() => {
  const target = docsLanguageEntry(location.href, ${JSON.stringify(base)}, createLanguagePreference())
  if (target) location.replace(target)
})()`
const preferenceBootstrapScript = `;(() => {
  const prefix = 'openviking-preferences:'
  const cookieKey = 'openviking-preferences'
  const readCookiePreference = () => {
    const cookie = document.cookie.split('; ').find((item) => item.startsWith(cookieKey + '='))
    if (!cookie) return {}
    return JSON.parse(decodeURIComponent(cookie.slice(cookieKey.length + 1)))
  }
  const readTransferPreference = () => {
    if (!window.name.startsWith(prefix)) return {}
    return JSON.parse(window.name.slice(prefix.length))
  }
  try {
    const preference = { ...readCookiePreference(), ...readTransferPreference() }
    if (preference.theme !== 'light' && preference.theme !== 'dark') return
    localStorage.setItem('vitepress-theme-appearance', preference.theme)
    document.documentElement.classList.toggle('dark', preference.theme === 'dark')
  } catch {}
})()`

const enNav = documentationNav('en')
const zhNav = documentationNav('zh')

function collectAllMdFiles(
  srcDir: string,
  options: { includeIndex?: boolean } = {}
): { relativePath: string; absPath: string }[] {
  const results: { relativePath: string; absPath: string }[] = []
  const ignored = new Set(['node_modules', '.vitepress'])
  const includeIndex = options.includeIndex ?? false

  function walk(dir: string) {
    for (const entry of fs.readdirSync(dir)) {
      if (ignored.has(entry) || (dir === srcDir && ['repository', 'review', 'images'].includes(entry))) continue
      const abs = path.join(dir, entry)
      const stat = fs.statSync(abs)
      if (stat.isDirectory()) {
        walk(abs)
      } else if (entry.endsWith('.md') && (includeIndex || entry !== 'index.md')) {
        results.push({ relativePath: path.relative(srcDir, abs), absPath: abs })
      }
    }
  }

  walk(srcDir)
  return results.sort((a, b) => a.relativePath.localeCompare(b.relativePath))
}

function markdownToSearchText(content: string): string {
  return content
    .replace(/^---[\s\S]*?---\s*/m, '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]+\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, ' ')
    .replace(/^\s{0,3}>\s?/gm, ' ')
    .replace(/^\s*[-*+]\s+/gm, ' ')
    .replace(/^\s*\|?[\s:-]+\|[\s|:-]*$/gm, ' ')
    .replace(/\*\*([^*\n]+)\*\*/g, '$1')
    .replace(/__([^_\n]+)__/g, '$1')
    .replace(/~~([^~\n]+)~~/g, '$1')
    .replace(/(^|[\s([{"'（【])\*([^*\n]+)\*(?=$|[\s.,;:!?，。；：！？、）】\])}"'])/g, '$1$2')
    .replace(/(^|[\s([{"'（【])_([^_\n]+)_(?=$|[\s.,;:!?，。；：！？、）】\])}"'])/g, '$1$2')
    .replace(/\s+/g, ' ')
    .trim()
}

function docsSearchLocale(relativePath: string): 'en' | 'zh' | null {
  if (relativePath.startsWith('en/')) return 'en'
  if (relativePath.startsWith('zh/')) return 'zh'
  return null
}

function buildDocsSearchRecords(srcDir: string) {
  return collectAllMdFiles(srcDir, { includeIndex: true })
    .map(({ relativePath, absPath }) => {
      const normalizedPath = relativePath.replace(/\\/g, '/')
      const locale = docsSearchLocale(normalizedPath)
      if (!locale) return null

      const content = fs.readFileSync(absPath, 'utf-8')
      const url = `/${normalizedPath.replace(/\.md$/, '')}`.replace(/\/index$/, '')
      return {
        locale,
        path: normalizedPath,
        text: markdownToSearchText(content),
        title: titleFromMarkdown(absPath),
        url
      }
    })
    .filter((record): record is NonNullable<typeof record> => record !== null)
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function buildDocsSearchIndex(siteConfig: any) {
  fs.writeFileSync(
    path.join(siteConfig.outDir, 'docs-search-index.json'),
    JSON.stringify(buildDocsSearchRecords(siteConfig.srcDir)),
    'utf-8'
  )
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function buildLlmsTxt(siteConfig: any) {
  const siteUrl = (process.env.DOCS_SITE_URL || '').replace(/\/$/, '')
  const base = (siteConfig.site.base || '/').replace(/\/$/, '')
  const srcDir = siteConfig.srcDir
  const outDir = siteConfig.outDir

  const allFiles = collectAllMdFiles(srcDir)
  const files = documentationSections('en').flatMap(section =>
    section.groups.flatMap(group => group.pages.map(page => ({
      relativePath: `en/${page.path}.md`, absPath: path.join(srcDir, `en/${page.path}.md`)
    })))
  )
  const sections = new Map<string, { title: string; url: string }[]>()
  for (const section of documentationSections('en')) {
    sections.set(section.en, section.groups.flatMap(group => group.pages.map(page => ({
      title: `${group.en} — ${docPageTitle('en', page.path)}`, url: `${siteUrl}${base}/en/${page.path}`
    }))))
  }

  const lines: string[] = [
    '# OpenViking',
    '',
    '> Open-source context database for AI Agents. OpenViking unifies memory, resources, and skills management for AI Agents through a file system paradigm.',
    '',
    `- Source: ${githubRepositoryUrl}`,
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
    '> This file contains the English documentation for LLM consumption.',
    '',
  ]
  for (const { relativePath, absPath } of files) {
    const content = fs.readFileSync(absPath, 'utf-8')
    fullLines.push(`\n\n---\n<!-- source: ${relativePath} -->\n\n${content}`)
  }
  fs.writeFileSync(path.join(outDir, 'llms-full.txt'), fullLines.join('\n'), 'utf-8')

  // Keep existing URLs, including /zh/, but serve their English counterpart.
  for (const { relativePath, absPath } of allFiles) {
    const normalized = relativePath.replaceAll(path.sep, '/')
    const englishPath = normalized.startsWith('zh/')
      ? path.join(srcDir, normalized.replace(/^zh\//, 'en/'))
      : normalized.startsWith('en/') ? absPath : null
    const content = englishPath && fs.existsSync(englishPath)
      ? fs.readFileSync(englishPath, 'utf-8')
      : '# English documentation\n\nNo English version is available for this page. See the [documentation index](' + siteUrl + base + '/llms.txt).\n'
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
  markdown: { config: md => md.use(taskLists, { label: true }) },
  // Repository guides are read on GitHub; review notes are local artifacts.
  // images/ is the public asset directory, including raw Markdown consumed by other apps.
  srcExclude: ['repository/**', 'review/**', 'images/**'],
  head: [
    ['link', { rel: 'icon', type: 'image/x-icon', href: `${base}favicon.ico` }],
    ['link', { rel: 'icon', type: 'image/png', sizes: '32x32', href: `${base}favicon-32.png` }],
    ['link', { rel: 'icon', type: 'image/svg+xml', href: `${base}favicon.svg` }],
    ['link', { rel: 'apple-touch-icon', sizes: '180x180', href: `${base}apple-touch-icon.png` }],
    ['meta', { property: 'og:image', content: ogImageUrl }],
    ['meta', { property: 'og:image:type', content: 'image/png' }],
    ['meta', { property: 'og:image:alt', content: 'OpenViking / docs — Guides and API reference for AI agent context.' }],
    ['meta', { property: 'og:image:width', content: '1200' }],
    ['meta', { property: 'og:image:height', content: '630' }],
    ['link', { rel: 'alternate', type: 'text/plain', title: 'LLM documentation index', href: `${base}llms.txt` }],
    ['meta', { name: 'twitter:card', content: 'summary_large_image' }],
    ['meta', { name: 'twitter:image', content: ogImageUrl }],
    ['meta', { name: 'twitter:image:alt', content: 'OpenViking / docs — Guides and API reference for AI agent context.' }],
    ['script', {}, preferenceBootstrapScript],
    ['script', {}, languageBootstrapScript]
  ],
  transformPageData(pageData, { siteConfig }) {
    if (hasPageLlmsTxt(pageData.relativePath)) {
      const head = pageData.frontmatter.head ??= []
      head.push(['link', { rel: 'alternate', type: 'text/plain', title: 'LLM page content', href: `${base.replace(/\/$/, '')}${pageLlmsTxtPath(pageData.relativePath)}` }])
    }
    const srcPath = path.join(siteConfig.srcDir, pageData.relativePath)
    try {
      // Raw SFC closing tags must not terminate VitePress's generated script block.
      pageData.frontmatter._rawMarkdownEncoded = encodeURIComponent(fs.readFileSync(srcPath, 'utf-8'))
    } catch {
      pageData.frontmatter._rawMarkdownEncoded = ''
    }
  },
  buildEnd(siteConfig) {
    buildLlmsTxt(siteConfig)
    buildDocsSearchIndex(siteConfig)
  },
  vite: {
    resolve: {
      alias: [
        { find: './VPNavBarTitle.vue', replacement: path.join(docsRoot, '.vitepress/theme/components/SiteSwitcher.vue') },
        { find: './VPSidebarGroup.vue', replacement: path.join(docsRoot, '.vitepress/theme/components/SidebarGroups.vue') }
      ]
    },
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
              const english = candidate.replace(/([/\\])zh([/\\])/, '$1en$2')
              res.end(fs.existsSync(english)
                ? fs.readFileSync(english, 'utf-8')
                : '# English documentation\n\nNo English version is available for this page. See /llms.txt.\n')
            } else {
              next()
            }
          })
          server.middlewares.use((req, res, next) => {
            const pathname = req.url?.split('?')[0]
            if (pathname !== '/docs-search-index.json') return next()

            res.setHeader('Content-Type', 'application/json; charset=utf-8')
            res.end(JSON.stringify(buildDocsSearchRecords(docsRoot)))
          })
        }
      }
    ]
  },
  themeConfig: {
    siteTitle: false,
    logo: { light: '/brand-lockup-light.svg', dark: '/brand-lockup-dark.svg', alt: 'OpenViking' },
    logoLink: base,
    nav: enNav,
    socialLinks: [
      { icon: 'github', link: githubRepositoryUrl }
    ],
    footer: {
      message: `Open source under the AGPL-3.0 License. <a href="${base}font-licenses.html" target="_self">Font licenses</a>`,
      copyright: 'Copyright OpenViking contributors'
    }
  },
  locales: {
    en: {
      label: 'English',
      lang: 'en-US',
      link: '/en/',
      themeConfig: {
        logoLink: `${base}en/`,
        nav: enNav,
        outline: {
          level: [2, 3]
        },
        sidebar: documentationSidebars('en')
      }
    },
    zh: {
      label: '简体中文',
      lang: 'zh-CN',
      link: '/zh/',
      title: 'OpenViking',
      description: '面向 AI Agent 的开源上下文数据库',
      themeConfig: {
        logoLink: `${base}zh/`,
        nav: zhNav,
        sidebar: documentationSidebars('zh'),
        outline: {
          label: '页面导航',
          level: [2, 3]
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
