import path from 'node:path'
import type { DefaultTheme } from 'vitepress'
import { documentationSections } from '../docs-navigation.ts'

type CatalogPage = { title: string; href: string; file: string; group: string }

function collectPages(items: DefaultTheme.SidebarItem[], parents: string[] = []): CatalogPage[] {
  return items.flatMap(item => [
    ...(item.link ? [{
      title: item.text || item.link,
      href: item.link,
      file: `${path.basename(item.link)}.md`,
      group: parents.join(' / ')
    }] : []),
    ...collectPages(item.items || [], item.text ? [...parents, item.text] : parents)
  ])
}

export default {
  watch: ['../../en/**/*.md', '../../zh/**/*.md', '../docs-navigation.ts', './docs-sections.ts'],
  load() {
    return Object.fromEntries(['en', 'zh'].map((locale: 'en' | 'zh') => [locale,
      documentationSections(locale).map(section => {
        const pages = collectPages(section.items || [])
        const { items, text, groups, nextSteps, ...metadata } = section
        return { ...metadata, pages }
      })
    ]))
  }
}
