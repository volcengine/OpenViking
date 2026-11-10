import assert from 'node:assert/strict'
import test from 'node:test'
import { createLanguagePreference } from './language-preference.js'
import { testBrowser } from './language-preference.contract.js'
import { docsLanguageEntry } from './language-entry.js'
import { localizedDocument } from './language-routing.ts'

for (const [pathname, base] of [
  ['/', '/'], ['/index.html', '/'],
  ['/guide/', '/guide/'], ['/guide', '/guide/'], ['/guide/index.html', '/guide/'],
]) {
  test(`entry ${pathname} resolves the localized homepage under base ${base}`, () => {
    const fixture = testBrowser({ languages: ['zh-Hant'], url: `https://docs.openviking.ai${pathname}?lang=zh&source=share#title` })
    const policy = createLanguagePreference(fixture.browser)
    const next = docsLanguageEntry(fixture.browser.location.href, base, policy)
    assert.equal(next, `https://docs.openviking.ai${base}zh/?lang=zh&source=share#title`)
    assert.equal(docsLanguageEntry(next!, base, policy), null)
    assert.deepEqual(fixture.writes, [])
  })
}

test('root query precedes manual preference; localized homepages and documents never redirect', () => {
  const fixture = testBrowser({ cookie: 'openviking-language-preference=zh', url: 'https://docs.openviking.net/en/?lang=en' })
  const policy = createLanguagePreference(fixture.browser)
  assert.equal(docsLanguageEntry('https://docs.openviking.net/?lang=en', '/', policy), 'https://docs.openviking.net/en/?lang=en')
  for (const path of ['/en/', '/zh', '/en/index.html', '/en/guides/03-deployment', '/zh/guides/03-deployment', '/outside/', '/guidebook/']) {
    assert.equal(docsLanguageEntry(`https://docs.openviking.net${path}`, '/', policy), null)
  }
  assert.equal(docsLanguageEntry('https://docs.openviking.net/en/', '/guide/', policy), null)
  assert.equal(docsLanguageEntry('https://docs.openviking.net/', '/', policy), 'https://docs.openviking.net/zh/')
  assert.equal(policy.read(), 'zh')
})

test('switches the same document or falls back to the target introduction', () => {
  const pages = ['en/guides/03-deployment.md', 'zh/guides/03-deployment.md', 'zh/index.md']
  assert.equal(localizedDocument('en/guides/03-deployment.md', 'zh', pages), '/zh/guides/03-deployment')
  assert.equal(localizedDocument('zh/guides/03-deployment.md', 'en', pages), '/en/guides/03-deployment')
  assert.equal(localizedDocument('en/guides/missing.md', 'zh', pages), '/zh/getting-started/01-introduction')
  assert.equal(localizedDocument('design/untranslated.md', 'en', pages), '/en/getting-started/01-introduction')
})
