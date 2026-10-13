import assert from 'node:assert/strict'
import test from 'node:test'
import { inferDescription, pageUrl, seoLinks } from './seo.ts'

const root = 'https://docs.openviking.ai/'

test('page URLs follow clean URLs and index pages', () => {
  assert.equal(pageUrl(root, 'index.md'), root)
  assert.equal(pageUrl(root, 'zh/index.md'), `${root}zh/`)
  assert.equal(pageUrl(root, 'en/guides/03-deployment.md'), `${root}en/guides/03-deployment`)
})

test('translated pages link every language version and default to English', () => {
  const head = seoLinks(root, 'zh/faq/faq.md', () => true)
  assert.deepEqual(head, [
    ['link', { rel: 'canonical', href: `${root}zh/faq/faq` }],
    ['link', { rel: 'alternate', hreflang: 'en-US', href: `${root}en/faq/faq` }],
    ['link', { rel: 'alternate', hreflang: 'zh-CN', href: `${root}zh/faq/faq` }],
    ['link', { rel: 'alternate', hreflang: 'x-default', href: `${root}en/faq/faq` }]
  ])
})

test('untranslated and root pages only carry a canonical', () => {
  assert.deepEqual(seoLinks(root, 'zh/qa/plan.md', path => path.startsWith('zh/')), [
    ['link', { rel: 'canonical', href: `${root}zh/qa/plan` }]
  ])
  assert.deepEqual(seoLinks(root, 'index.md', () => true), [['link', { rel: 'canonical', href: root }]])
})

test('description comes from the first prose paragraph', () => {
  const markdown = '---\ntitle: x\n---\n# OpenViking 能做什么\n\n> note\n\nOpenViking 是面向 **AI Agent** 的[开源](https://x)上下文数据库，配置 `api_key_file`。\n\n## More'
  assert.equal(inferDescription(markdown), 'OpenViking 是面向 AI Agent 的开源上下文数据库，配置 api_key_file。')
  assert.equal(inferDescription('# Title\n\n- only a list'), '')
  assert.equal(inferDescription('# T\n\n' + 'a'.repeat(200), 10), 'aaaaaaaaa…')
})
