import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { DefaultTheme } from 'vitepress'
import { sections, legacyPages, sectionForPage, type Locale, type DocSection } from './theme/docs-sections.ts'

const docsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

export function titleFromMarkdown(filePath: string): string {
  const content = fs.readFileSync(filePath, 'utf8')
  const heading = content.match(/^#\s+(.+)$/m)?.[1]
  return (heading || path.basename(filePath, '.md')).replace(/^\d+[-_]/, '').trim()
}

export function docPageTitle(locale: Locale, pagePath: string): string {
  return titleFromMarkdown(path.join(docsRoot, locale, `${pagePath}.md`))
}

export function sectionSidebar(locale: Locale, section: DocSection, includeNextSteps = true): DefaultTheme.SidebarItem[] {
  const groups = [...section.groups, ...(includeNextSteps ? section.nextSteps ?? [] : [])]
  return groups.map(group => ({
    text: group[locale],
    items: group.pages.map(page => {
      const title = docPageTitle(locale, page.path)
      const crossSection = sectionForPage(`${locale}/${page.path}.md`)?.id !== section.id
      const hint = locale === 'zh' ? '跳转到其他章节' : 'Go to another section'
      const icon = `<span class="ov-sidebar-cross-link" role="img" aria-label="${hint}" title="${hint}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 3h7v7m0-7L10 14M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/></svg></span>`
      return { text: title + (crossSection ? icon : ''), link: `/${locale}/${page.path}` }
    })
  }))
}

export function documentationSections(locale: Locale) {
  return sections.map(section => ({ ...section, text: section[locale], items: sectionSidebar(locale, section, false) }))
}

export function documentationNav(locale: Locale): DefaultTheme.NavItem[] {
  return sections.map(section => {
    const paths = [
      ...section.groups.flatMap(group => group.pages.map(page => page.path)),
      ...Object.keys(legacyPages).filter(page => legacyPages[page] === section.id)
    ]
    const escaped = paths.map(page => page.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    return {
      text: section[locale], link: `/${locale}/${section.entry}`,
      activeMatch: `^/${locale}/(?:${escaped.join('|')})(?:\\.html)?/?$`
    }
  })
}

// Match individual pages: a physical directory can serve several reader tasks.
export function documentationSidebars(locale: Locale): DefaultTheme.SidebarMulti {
  const sidebar: DefaultTheme.SidebarMulti = {}
  for (const section of sections) {
    const items = sectionSidebar(locale, section)
    for (const group of section.groups) {
      for (const page of group.pages) sidebar[`/${locale}/${page.path}`] = items
    }
    for (const [page, owner] of Object.entries(legacyPages)) {
      if (owner === section.id) sidebar[`/${locale}/${page}`] = items
    }
  }
  return sidebar
}
