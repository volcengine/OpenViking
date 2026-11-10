/// <reference types="node" />
import { existsSync, readFileSync, readdirSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import {
  DOCS_PAGES,
  DOCS_SECTIONS,
} from '#/features/admin/gateway/lib/client-guides'

/** The repository's docs folder, five levels above this file. */
const DOCS = new URL('../../../../../../docs/', import.meta.url)

const LANGUAGES = ['en', 'zh']

/**
 * Every page of the gateway docs section: the entry and deployment pages,
 * which keep their `guides/` paths, and the gateway folder.
 */
const SECTION_PAGES = [
  'guides/15-gateway',
  'guides/22-gateway-operations',
  ...readdirSync(new URL('en/gateway/', DOCS))
    .filter((name) => name.endsWith('.md'))
    .sort()
    .map((name) => `gateway/${name.slice(0, -3)}`),
]

function pageUrl(language: string, page: string) {
  return new URL(`${language}/${page}.md`, DOCS)
}

/** The page's headings, outside code blocks. */
function headings(language: string, page: string) {
  const text = readFileSync(pageUrl(language, page), 'utf8').replace(
    /^(```|~~~)[^\n]*\n[\s\S]*?^\1[ \t]*$/gm,
    '',
  )
  return [...text.matchAll(/^#{2,6} .*$/gm)].map((match) => match[0])
}

/** Explicit `{#id}`s on the page's headings. */
function headingIds(language: string, page: string) {
  return headings(language, page).flatMap((heading) => {
    const id = /\{#([\w-]+)\}[ \t]*$/.exec(heading)?.[1]
    return id ? [id] : []
  })
}

// Studio links one id for every language, so each heading needs it pinned.
describe.each(LANGUAGES)('%s gateway docs', (language) => {
  it.each(Object.values(DOCS_PAGES))('have the page %s', (page) => {
    expect(existsSync(pageUrl(language, page))).toBe(true)
  })

  it.each(Object.entries(DOCS_SECTIONS))(
    'pin the heading Studio links as %s',
    (_, { page, id }) => {
      expect(headingIds(language, DOCS_PAGES[page])).toContain(id)
    },
  )

  it.each(SECTION_PAGES)('pin every H2 and H3 on %s', (page) => {
    const unpinned = headings(language, page).filter(
      (heading) =>
        /^#{2,3} /.test(heading) && !/\{#[\w-]+\}[ \t]*$/.test(heading),
    )
    expect(unpinned).toEqual([])
  })
})

// The same ids in every language, so a link works whichever one is open.
describe('gateway docs languages', () => {
  it('have the same gateway pages', () => {
    const pages = (language: string) =>
      readdirSync(new URL(`${language}/gateway/`, DOCS))
        .filter((name) => name.endsWith('.md'))
        .sort()
    expect(pages('zh')).toEqual(pages('en'))
  })

  it.each(SECTION_PAGES)('pin the same heading ids on %s', (page) => {
    expect(headingIds('zh', page)).toEqual(headingIds('en', page))
  })
})
