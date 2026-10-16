import assert from 'node:assert/strict'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import catalog from './docs-catalog.data.ts'
import { documentationNav, documentationSidebars } from '../docs-navigation.ts'
import { legacyPages, sectionForPage, sections, type Locale } from './docs-sections.ts'

const data = catalog.load()
for (const locale of ['en', 'zh'] as Locale[]) {
  test(`${locale}: every published article has one owner and a working sidebar`, () => {
    const root = fileURLToPath(new URL(`../../${locale}/`, import.meta.url))
    const files = readdirSync(root, { recursive: true }) as string[]
    const expected = files.filter(file => file.endsWith('.md') && file !== 'index.md')
      .map(file => file.slice(0, -3)).filter(file => !(file in legacyPages)).sort()
    const pages = data[locale].flatMap(section => section.pages)
    assert.equal(new Set(pages.map(page => page.href)).size, pages.length)
    assert.deepEqual(pages.map(page => page.href.slice(locale.length + 2)).sort(), expected)
    const sidebars = documentationSidebars(locale)
    const nav = documentationNav(locale)
    for (const page of pages) {
      assert.ok(existsSync(fileURLToPath(new URL(`../../${page.href.slice(1)}.md`, import.meta.url))), page.href)
      const owner = sectionForPage(`${page.href.slice(1)}.md`)!
      assert.ok(owner, page.href)
      assert.ok(sidebars[page.href], page.href)
      for (const suffix of ['', '.html', '/']) {
        const matches = nav.filter(item => 'activeMatch' in item && new RegExp(item.activeMatch!).test(page.href + suffix))
        assert.equal(matches.length, 1, `${page.href}${suffix}`)
        assert.equal(matches[0].text, owner[locale])
      }
    }
    for (const page of Object.keys(legacyPages)) assert.ok(sidebars[`/${locale}/${page}`])
  })

  test(`${locale}: all concept articles stay together in learning order`, () => {
    const conceptPages = data[locale].find(section => section.id === 'concepts')!.pages
    const files = readdirSync(new URL(`../../${locale}/concepts/`, import.meta.url))
      .filter(file => file.endsWith('.md')).map(file => `/${locale}/concepts/${file.slice(0, -3)}`)
    assert.deepEqual(conceptPages.map(page => page.href).sort(), files.sort())
    for (const href of files) assert.equal(sectionForPage(`${href.slice(1)}.md`)?.id, 'concepts')
    assert.equal(conceptPages[0].href, `/${locale}/concepts/00-overview`)
  })

  test(`${locale}: onboarding exits use article titles without duplicating ownership`, () => {
    const start = sections.find(section => section.id === 'getting-started')!
    const sidebar = documentationSidebars(locale)[`/${locale}/${start.entry}`]
    assert.ok(Array.isArray(sidebar))
    const links = sidebar.flatMap(group => group.items ?? [])
    const owned = data[locale].find(section => section.id === start.id)!.pages
    for (const page of owned) {
      assert.equal(links.find(item => item.link === page.href)?.text, page.title)
    }
    const exits = start.nextSteps!.flatMap(group => group.pages)
    assert.ok(exits.length > 0)
    for (const exit of exits) {
      const href = `/${locale}/${exit.path}`
      const article = data[locale].flatMap(section => section.pages).find(page => page.href === href)
      assert.ok(article, `Exit has no owner: ${href}`)
      assert.notEqual(sectionForPage(`${locale}/${exit.path}.md`)?.id, start.id)
      assert.ok(!owned.some(page => page.href === href), `Exit duplicated in discovery: ${href}`)
      const item = links.find(item => item.link === href)!
      assert.equal(item.text?.split('<span')[0], article.title)
      assert.match(item.text!, /class="ov-sidebar-cross-link"/)
      assert.equal(item.target, undefined)
    }
  })

  test(`${locale}: every article uses its H1 as the sidebar and homepage title`, () => {
    for (const section of data[locale]) {
      for (const page of section.pages) {
        const source = readFileSync(new URL(`../../${page.href.slice(1)}.md`, import.meta.url), 'utf8')
        const heading = source.match(/^#\s+(.+)$/m)?.[1].trim()
        assert.ok(heading, `Missing H1: ${page.href}`)
        assert.equal(page.title, heading, page.href)
      }
    }
  })

  test(`${locale}: navigation uses task groups without excess nesting or retired pages`, () => {
    const sidebars = documentationSidebars(locale)
    for (const section of sections) {
      const sidebar = sidebars[`/${locale}/${section.entry}`]
      assert.ok(Array.isArray(sidebar))
      for (const group of sidebar) {
        assert.ok(group.items?.length)
        assert.ok(group.items.every(item => item.link && !item.items), group.text)
      }
    }
    const pages = data[locale].flatMap(section => section.pages)
    assert.ok(pages.some(page => page.href.endsWith('/api/12-acl')))
    assert.ok(pages.some(page => page.href.endsWith('/concepts/15-acl')))
    assert.ok(!pages.some(page => page.href.includes('/migration/') || page.href.includes('/design/')))
    assert.equal(sectionForPage(`${locale}/guides/20-private-deployment.md`)?.id, 'operate')
    assert.equal(sectionForPage(`${locale}/guides/09-ovpack.md`)?.id, 'build')
    assert.equal(sectionForPage(`${locale}/api/99-api-doc-writing-guide.md`)?.id, 'project')
    assert.equal(sectionForPage(`${locale}/configuration/01-server.md`)?.id, 'reference')
  })
}

test('English and Chinese discovery maps expose the same pages in the same order', () => {
  assert.deepEqual(data.en.map(s => s.pages.map(p => p.href.replace('/en/', '/'))),
    data.zh.map(s => s.pages.map(p => p.href.replace('/zh/', '/'))))
})
