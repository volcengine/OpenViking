import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, type DefaultTheme } from 'vitepress'

const docsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repo = process.env.GITHUB_REPOSITORY || 'volcengine/OpenViking'
const base = process.env.DOCS_BASE || '/'
const withTrailingSlash = (url: string) => (url.endsWith('/') ? url : `${url}/`)
const mainSiteBase = withTrailingSlash(process.env.OPENVIKING_SITE_BASE || 'https://www.openviking.ai/')
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

const sectionNames: Record<string, string> = {
  'getting-started': 'Getting Started',
  concepts: 'Concepts',
  guides: 'Guides',
  'agent-integrations': 'Agent Integrations',
  migration: 'Migration',
  api: 'API Reference',
  faq: 'FAQ',
  about: 'About',
  design: 'Design Notes'
}

const zhSectionNames: Record<string, string> = {
  'getting-started': '快速开始',
  concepts: '核心概念',
  guides: '指南',
  'agent-integrations': 'Agent 集成',
  migration: '迁移指南',
  api: 'API 参考',
  faq: '常见问题',
  about: '关于',
  design: '设计文档'
}

const navLabels = {
  en: {
    start: 'Getting Started',
    concepts: 'Concepts',
    guide: 'Guides',
    api: 'API Reference',
    faq: 'FAQ',
    about: 'About'
  },
  zh: {
    start: '快速开始',
    concepts: '核心概念',
    guide: '指南',
    api: 'API 参考',
    faq: '常见问题',
    about: '关于'
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

const agentIntegrationSidebar = {
  en: {
    overview: 'Integration Overview',
    groups: [
      {
        text: 'Developer Tools',
        items: [
          ['02-claude-code.md', 'Claude Code'],
          ['04-codex.md', 'Codex'],
          ['10-opencode.md', 'OpenCode'],
          ['12-cursor.md', 'Cursor'],
          ['13-trae.md', 'TRAE / TRAE CN']
        ]
      },
      {
        text: 'Agents & Frameworks',
        items: [
          ['03-openclaw.md', 'OpenClaw'],
          ['05-hermes.md', 'Hermes'],
          ['07-langchain-langgraph.md', 'LangChain / LangGraph'],
          ['11-pi.md', 'pi']
        ]
      },
      {
        text: 'General Integration',
        items: [
          ['06-mcp-clients.md', 'MCP Clients'],
          ['09-log-ingestion.md', 'Local Log Import'],
          ['08-community-plugins.md', 'Community Integrations']
        ]
      }
    ]
  },
  zh: {
    overview: '集成概览',
    groups: [
      {
        text: '开发工具',
        items: [
          ['02-claude-code.md', 'Claude Code'],
          ['04-codex.md', 'Codex'],
          ['10-opencode.md', 'OpenCode'],
          ['12-cursor.md', 'Cursor'],
          ['13-trae.md', 'TRAE / TRAE CN']
        ]
      },
      {
        text: 'Agent 与框架',
        items: [
          ['03-openclaw.md', 'OpenClaw'],
          ['05-hermes.md', 'Hermes'],
          ['07-langchain-langgraph.md', 'LangChain / LangGraph'],
          ['11-pi.md', 'pi']
        ]
      },
      {
        text: '通用接入',
        items: [
          ['06-mcp-clients.md', 'MCP 客户端'],
          ['09-log-ingestion.md', '本地日志导入'],
          ['08-community-plugins.md', '社区集成']
        ]
      }
    ]
  }
} as const

const apiReferenceSidebar = {
  en: {
    overview: 'Overview',
    groups: [
      {
        text: 'Core Capabilities',
        items: [
          ['02-resources.md', 'Resources'],
          ['03-filesystem.md', 'File System'],
          ['04-skills.md', 'Skills'],
          ['05-sessions.md', 'Sessions & Memory'],
          ['06-retrieval.md', 'Retrieval']
        ]
      },
      {
        text: 'Operations & Governance',
        items: [
          ['07-system.md', 'System'],
          ['08-admin.md', 'Multi-Tenancy'],
          ['09-metrics.md', 'Metrics'],
          ['10-privacy.md', 'Privacy'],
          ['11-snapshot.md', 'Snapshots']
        ]
      },
      {
        text: 'Contributing',
        items: [['99-api-doc-writing-guide.md', 'API Docs Guide']]
      }
    ]
  },
  zh: {
    overview: '概览',
    groups: [
      {
        text: '核心能力',
        items: [
          ['02-resources.md', '资源'],
          ['03-filesystem.md', '文件系统'],
          ['04-skills.md', '技能'],
          ['05-sessions.md', '会话与记忆'],
          ['06-retrieval.md', '检索']
        ]
      },
      {
        text: '运维与治理',
        items: [
          ['07-system.md', '系统管理'],
          ['08-admin.md', '多租户'],
          ['09-metrics.md', '监控指标'],
          ['10-privacy.md', '隐私配置'],
          ['11-snapshot.md', '快照管理']
        ]
      },
      {
        text: '文档贡献',
        items: [['99-api-doc-writing-guide.md', 'API 文档规范']]
      }
    ]
  }
} as const

const conceptsSidebar = {
  en: {
    overview: 'Overview',
    groups: [
      {
        text: 'Core Model',
        items: [
          ['02-context-types.md', 'Context Types'],
          ['03-context-layers.md', 'Context Layers'],
          ['04-viking-uri.md', 'Viking URI']
        ]
      },
      {
        text: 'Storage & Processing',
        items: [
          ['05-storage.md', 'Storage'],
          ['06-extraction.md', 'Extraction'],
          ['07-retrieval.md', 'Retrieval'],
          ['08-session.md', 'Sessions']
        ]
      },
      {
        text: 'Reliability & Governance',
        items: [
          ['09-transaction.md', 'Transactions & Recovery'],
          ['10-encryption.md', 'Encryption'],
          ['11-multi-tenant.md', 'Multi-Tenancy'],
          ['12-metrics.md', 'Metrics'],
          ['13-privacy.md', 'Privacy'],
          ['14-multi-write-storage.md', 'Multi-Write Storage']
        ]
      },
      {
        text: 'Example',
        items: [['15-vikingbot.md', 'VikingBot']]
      }
    ]
  },
  zh: {
    overview: '概览',
    groups: [
      {
        text: '核心模型',
        items: [
          ['02-context-types.md', '上下文类型'],
          ['03-context-layers.md', '上下文层级'],
          ['04-viking-uri.md', 'Viking URI']
        ]
      },
      {
        text: '存储与处理',
        items: [
          ['05-storage.md', '存储架构'],
          ['06-extraction.md', '上下文提取'],
          ['07-retrieval.md', '检索机制'],
          ['08-session.md', '会话管理']
        ]
      },
      {
        text: '可靠性与治理',
        items: [
          ['09-transaction.md', '事务与恢复'],
          ['10-encryption.md', '数据加密'],
          ['11-multi-tenant.md', '多租户'],
          ['12-metrics.md', '监控指标'],
          ['13-privacy.md', '隐私配置'],
          ['14-multi-write-storage.md', '多写存储']
        ]
      },
      {
        text: '应用案例',
        items: [['15-vikingbot.md', 'VikingBot']]
      }
    ]
  }
} as const

const guidesSidebar = {
  en: {
    groups: [
      {
        text: 'Configuration & Deployment',
        items: [
          ['01-configuration.md', 'Configuration'],
          ['02-volcengine-purchase-guide.md', 'Model Purchase'],
          ['03-deployment.md', 'Server Deployment'],
          ['04-authentication.md', 'Authentication'],
          ['08-encryption.md', 'Encryption'],
          ['11-oauth.md', 'OAuth 2.1'],
          ['12-public-access.md', 'Public Access']
        ]
      },
      {
        text: 'Integration & Extension',
        items: [
          ['06-mcp-integration.md', 'MCP Integration'],
          ['09-ovpack.md', 'OVPack'],
          ['10-prompt-guide.md', 'Prompt Customization'],
          ['17-vikingbot.md', 'VikingBot']
        ]
      },
      {
        text: 'Observability',
        items: [
          ['05-observability.md', 'Observability & Diagnostics'],
          ['07-operation-telemetry.md', 'Operation Telemetry'],
          ['11-grafana-prometheus.md', 'Prometheus / Grafana']
        ]
      },
      {
        text: 'Storage & Performance',
        items: [
          ['13-multi-write-storage.md', 'Multi-Write Storage'],
          ['14-ragfs-cache.md', 'RAGFS Cache'],
          ['15-snapshot.md', 'Snapshots'],
          ['16-cuvs.md', 'cuVS Vector Search']
        ]
      }
    ]
  },
  zh: {
    groups: [
      {
        text: '配置与部署',
        items: [
          ['01-configuration.md', '基础配置'],
          ['02-volcengine-purchase-guide.md', '模型购买'],
          ['03-deployment.md', '服务端部署'],
          ['04-authentication.md', '身份认证'],
          ['08-encryption.md', '数据加密'],
          ['11-oauth.md', 'OAuth 2.1'],
          ['12-public-access.md', '公网访问']
        ]
      },
      {
        text: '集成与扩展',
        items: [
          ['06-mcp-integration.md', 'MCP 集成'],
          ['09-ovpack.md', 'OVPack'],
          ['10-prompt-guide.md', 'Prompt 自定义'],
          ['17-vikingbot.md', 'VikingBot']
        ]
      },
      {
        text: '可观测性',
        items: [
          ['05-observability.md', '可观测性与排障'],
          ['07-operation-telemetry.md', '操作遥测'],
          ['11-grafana-prometheus.md', 'Prometheus / Grafana'],
          ['12-vikingbot-metrics-validation.md', 'VikingBot 指标验证']
        ]
      },
      {
        text: '存储与性能',
        items: [
          ['13-multi-write-storage.md', '多写存储'],
          ['14-ragfs-cache.md', 'RAGFS 缓存'],
          ['15-snapshot.md', '快照管理'],
          ['16-cuvs.md', 'cuVS 向量检索']
        ]
      }
    ]
  }
} as const

type StructuredSidebarCopy = {
  readonly overview: string
  readonly groups: ReadonlyArray<{
    readonly text: string
    readonly items: ReadonlyArray<readonly [string, string]>
  }>
}

type GroupedSidebarCopy = Pick<StructuredSidebarCopy, 'groups'>

function configuredSidebarItem(
  locale: 'en' | 'zh',
  section: string,
  [file, text]: readonly [string, string]
): DefaultTheme.SidebarItem {
  return {
    text,
    link: linkFor(path.join(docsRoot, locale, section, file))
  }
}

function configuredSidebarGroups(
  locale: 'en' | 'zh',
  section: string,
  groups: GroupedSidebarCopy['groups']
): DefaultTheme.SidebarItem[] {
  return groups.map((group) => ({
    text: group.text,
    collapsed: false,
    items: group.items.map((item) => configuredSidebarItem(locale, section, item))
  }))
}

function groupedSidebarSection(
  locale: 'en' | 'zh',
  section: string,
  title: string,
  copy: GroupedSidebarCopy,
  collapsed = true
): DefaultTheme.SidebarItem {
  return {
    text: title,
    collapsed,
    items: configuredSidebarGroups(locale, section, copy.groups)
  }
}

function structuredSidebarSection(
  locale: 'en' | 'zh',
  section: string,
  title: string,
  copy: StructuredSidebarCopy,
  collapsed = true,
  overviewFile = '01-overview.md'
): DefaultTheme.SidebarItem {
  return {
    text: title,
    collapsed,
    items: [
      configuredSidebarItem(locale, section, [overviewFile, copy.overview]),
      ...configuredSidebarGroups(locale, section, copy.groups)
    ]
  }
}

function agentIntegrationSection(
  locale: 'en' | 'zh',
  title: string,
  collapsed = true
): DefaultTheme.SidebarItem {
  return structuredSidebarSection(
    locale,
    'agent-integrations',
    title,
    agentIntegrationSidebar[locale],
    collapsed
  )
}

function apiReferenceSection(
  locale: 'en' | 'zh',
  title: string,
  collapsed = true
): DefaultTheme.SidebarItem {
  return structuredSidebarSection(locale, 'api', title, apiReferenceSidebar[locale], collapsed)
}

function conceptsSection(
  locale: 'en' | 'zh',
  title: string,
  collapsed = true
): DefaultTheme.SidebarItem {
  return structuredSidebarSection(
    locale,
    'concepts',
    title,
    conceptsSidebar[locale],
    collapsed,
    '01-architecture.md'
  )
}

function guidesSection(
  locale: 'en' | 'zh',
  title: string,
  collapsed = true
): DefaultTheme.SidebarItem {
  return groupedSidebarSection(locale, 'guides', title, guidesSidebar[locale], collapsed)
}

function migrationSection(
  locale: 'en' | 'zh',
  title: string,
  collapsed = true
): DefaultTheme.SidebarItem {
  return {
    text: title,
    collapsed,
    items: [
      {
        text: '0.3.x → 0.4.0',
        link: linkFor(path.join(docsRoot, locale, 'migration', '01-user-peer-model.md'))
      }
    ]
  }
}

type LocalizedSidebarSection =
  | 'getting-started'
  | 'concepts'
  | 'guides'
  | 'agent-integrations'
  | 'migration'

type LocalizedSidebarSectionBuilder = (
  locale: 'en' | 'zh',
  title: string,
  collapsed?: boolean
) => DefaultTheme.SidebarItem

const localizedSidebarSectionBuilders: Record<
  LocalizedSidebarSection,
  LocalizedSidebarSectionBuilder
> = {
  'getting-started': (locale, title, collapsed = true) =>
    sidebarSection(`${locale}/getting-started`, title, collapsed),
  concepts: conceptsSection,
  guides: guidesSection,
  'agent-integrations': agentIntegrationSection,
  migration: migrationSection
}

function localizedSidebarSection(
  locale: 'en' | 'zh',
  section: LocalizedSidebarSection,
  title: string,
  collapsed = true
): DefaultTheme.SidebarItem {
  return localizedSidebarSectionBuilders[section](locale, title, collapsed)
}

function localizedSectionSidebarItems(
  locale: 'en' | 'zh',
  section: LocalizedSidebarSection
): DefaultTheme.SidebarItem[] {
  const labels = locale === 'zh' ? zhSectionNames : sectionNames
  return [localizedSidebarSection(locale, section, labels[section], false)]
}

function localizedGroupedSidebarItems(
  locale: 'en' | 'zh',
  sections: ReadonlyArray<Exclude<LocalizedSidebarSection, 'concepts'>>
): DefaultTheme.SidebarItem[] {
  const labels = locale === 'zh' ? zhSectionNames : sectionNames

  return sections.map((section) =>
    localizedSidebarSection(locale, section, labels[section], false)
  )
}

function localizedReferenceSidebarItems(locale: 'en' | 'zh'): DefaultTheme.SidebarItem[] {
  const labels = locale === 'zh' ? zhSectionNames : sectionNames
  return [apiReferenceSection(locale, labels.api, false)]
}

function localizedAboutSidebarItems(locale: 'en' | 'zh'): DefaultTheme.SidebarItem[] {
  const labels = locale === 'zh' ? zhSectionNames : sectionNames
  return [sidebarSection(`${locale}/about`, labels.about, false)]
}

const designSidebar: DefaultTheme.SidebarItem[] = [
  sidebarSection('design', sectionNames.design, false)
]

const enNav: DefaultTheme.NavItem[] = [
  { text: navLabels.en.start, link: '/en/getting-started/01-introduction', activeMatch: '/en/(getting-started|agent-integrations)/' },
  { text: navLabels.en.concepts, link: '/en/concepts/01-architecture', activeMatch: '/en/concepts/' },
  { text: navLabels.en.guide, link: '/en/guides/01-configuration', activeMatch: '/en/(guides|migration)/' },
  { text: navLabels.en.api, link: '/en/api/01-overview', activeMatch: '/en/api/' },
  { text: navLabels.en.faq, link: '/en/faq/faq', activeMatch: '/en/faq/' },
  { text: navLabels.en.about, link: '/en/about/01-about-us', activeMatch: '/en/about/' }
]

const zhNav: DefaultTheme.NavItem[] = [
  { text: navLabels.zh.start, link: '/zh/getting-started/01-introduction', activeMatch: '/zh/(getting-started|agent-integrations)/' },
  { text: navLabels.zh.concepts, link: '/zh/concepts/01-architecture', activeMatch: '/zh/concepts/' },
  { text: navLabels.zh.guide, link: '/zh/guides/01-configuration', activeMatch: '/zh/(guides|migration)/' },
  { text: navLabels.zh.api, link: '/zh/api/01-overview', activeMatch: '/zh/api/' },
  { text: navLabels.zh.faq, link: '/zh/faq/faq', activeMatch: '/zh/faq/' },
  { text: navLabels.zh.about, link: '/zh/about/01-about-us', activeMatch: '/zh/about/' }
]

function collectAllMdFiles(
  srcDir: string,
  options: { includeIndex?: boolean } = {}
): { relativePath: string; absPath: string }[] {
  const results: { relativePath: string; absPath: string }[] = []
  const ignored = new Set(['node_modules', '.vitepress'])
  const includeIndex = options.includeIndex ?? false

  function walk(dir: string) {
    for (const entry of fs.readdirSync(dir)) {
      if (ignored.has(entry)) continue
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
    ['link', { rel: 'icon', type: 'image/x-icon', href: `${base}favicon.ico` }],
    ['link', { rel: 'icon', type: 'image/png', sizes: '32x32', href: `${base}favicon-32.png` }],
    ['link', { rel: 'apple-touch-icon', href: `${base}apple-touch-icon.png` }],
    ['script', {}, preferenceBootstrapScript]
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
    buildDocsSearchIndex(siteConfig)
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
    logo: '/ov-logo.png',
    logoLink: mainSiteBase,
    socialLinks: [
      { icon: 'github', link: `https://github.com/${repo}` }
    ],
    footer: {
      message: 'Released under the Apache-2.0 License.',
      copyright: 'Copyright OpenViking contributors'
    }
  },
  locales: {
    en: {
      label: 'English',
      lang: 'en-US',
      link: '/en/',
      themeConfig: {
        nav: enNav,
        outline: {
          level: [2, 3]
        },
        sidebar: {
          '/en/getting-started/': localizedGroupedSidebarItems('en', ['getting-started', 'agent-integrations']),
          '/en/concepts/': localizedSectionSidebarItems('en', 'concepts'),
          '/en/guides/': localizedGroupedSidebarItems('en', ['guides', 'migration']),
          '/en/agent-integrations/': localizedGroupedSidebarItems('en', ['getting-started', 'agent-integrations']),
          '/en/migration/': localizedGroupedSidebarItems('en', ['guides', 'migration']),
          '/en/api/': localizedReferenceSidebarItems('en'),
          '/en/about/': localizedAboutSidebarItems('en'),
          '/design/': designSidebar
        }
      }
    },
    zh: {
      label: '简体中文',
      lang: 'zh-CN',
      link: '/zh/',
      title: 'OpenViking',
      description: '面向 AI Agent 的开源上下文数据库',
      themeConfig: {
        nav: zhNav,
        sidebar: {
          '/zh/getting-started/': localizedGroupedSidebarItems('zh', ['getting-started', 'agent-integrations']),
          '/zh/concepts/': localizedSectionSidebarItems('zh', 'concepts'),
          '/zh/guides/': localizedGroupedSidebarItems('zh', ['guides', 'migration']),
          '/zh/agent-integrations/': localizedGroupedSidebarItems('zh', ['getting-started', 'agent-integrations']),
          '/zh/migration/': localizedGroupedSidebarItems('zh', ['guides', 'migration']),
          '/zh/api/': localizedReferenceSidebarItems('zh'),
          '/zh/about/': localizedAboutSidebarItems('zh')
        },
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
