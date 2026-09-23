import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import catalog from './docs-catalog.data.ts'

const data = catalog.load()
for (const locale of ['en', 'zh']) {
  test(`${locale} map links resolve without duplicate or retired entries`, () => {
    const pages = data[locale].flatMap(section => section.pages)
    assert.equal(new Set(pages.map(page => page.href)).size, pages.length)
    for (const page of pages) {
      assert.ok(existsSync(fileURLToPath(new URL(`../../${page.href.slice(1)}.md`, import.meta.url))), page.href)
    }
    assert.deepEqual(data[locale][0].pages.map(page => page.file), [
      '01-introduction.md', '02-quickstart.md', '04-setup-for-agent.md', '05-cli-setup.md'
    ])
  })

  test(`${locale} map preserves sidebar order and compilation hierarchy`, () => {
    const guides = data[locale].find(section => section.id === 'guides')!
    const deployment = guides.pages.findIndex(page => page.file === '03-deployment.md')
    assert.equal(guides.pages[deployment + 1].file, '04-authentication.md')
    assert.equal(guides.pages[deployment].group, guides.pages[deployment + 1].group)
    const compilation = guides.pages.filter(page => page.href.includes('/context-compilation/'))
    assert.ok(compilation.length > 0)
    assert.ok(compilation.every(page => page.group.includes(' / ')))
    assert.ok(!data[locale].some(section => section.id === 'context-compilation'))
  })
}
