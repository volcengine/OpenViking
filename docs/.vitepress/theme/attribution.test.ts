import assert from 'node:assert/strict'
import test from 'node:test'
import { attributionUrl, docsCampaign, readUtm } from './attribution.ts'

const docs = 'https://docs.openviking.ai/zh/getting-started/02-quickstart'
const product = 'https://www.volcengine.com/product/openviking-service'
const consoleUrl = 'https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing'
const incoming = { utm_source: 'wechat_bytetech', utm_medium: 'social', utm_campaign: '验收_20261010', utm_content: '图文 & 位置=首屏' }
const tagged = docs + '?' + new URLSearchParams(incoming)

test('原始 UTM 整组覆盖默认参数，缺省 term 不被补入', () => {
  const fallback = product + '?plan=enterprise&utm_source=opensource_docs&utm_medium=referral&utm_campaign=quickstart&utm_term=old#plans'
  const result = new URL(attributionUrl(fallback, tagged)!)
  assert.deepEqual(readUtm(result.href), incoming)
  assert.equal(result.searchParams.get('plan'), 'enterprise')
  assert.equal(result.hash, '#plans')
  assert.equal(result.searchParams.has('utm_term'), false)
})

test('控制台区域路径的加号保持不变，站内链接继承来源', () => {
  assert.equal(new URL(attributionUrl(consoleUrl, tagged)!).pathname, '/vikingdb/openviking/region:openviking+cn-beijing')
  const next = attributionUrl('/zh/guides/03-deployment?lang=zh#cloud', tagged)!
  assert.deepEqual(readUtm(next), incoming)
  assert.equal(new URL(next).searchParams.get('lang'), 'zh')
  assert.equal(new URL(next).hash, '#cloud')
})

test('显式的新入口不和当前触点混组', () => {
  const next = '/en/?utm_source=rednote_volc_agent_community&utm_medium=social&utm_campaign=new'
  assert.equal(attributionUrl(next, tagged), undefined)
})

test('无参数的直接访问只生成文档默认来源，不向站内导航添加默认触点', () => {
  assert.deepEqual(readUtm(attributionUrl(product, docs)!), {
    utm_source: 'opensource_docs', utm_medium: 'referral', utm_campaign: 'getting-started_02-quickstart'
  })
  assert.equal(attributionUrl('/zh/guides/03-deployment', docs), undefined)
  assert.equal(attributionUrl(product + '?utm_source=opensource_docs&utm_medium=referral&utm_campaign=static', docs), undefined)
})

test('文档默认 campaign 在语言、扩展名和部署 base 下保持一致', () => {
  assert.equal(docsCampaign('/zh/getting-started/02-quickstart'), docsCampaign('/en/getting-started/02-quickstart.html'))
  assert.equal(docsCampaign('/guide/en/guides/03-deployment.html', '/guide/'), 'guides_03-deployment')
  assert.equal(docsCampaign('/guide/zh/index.html', '/guide/'), 'main')
  assert.equal(docsCampaign('/zh/'), 'main')
  assert.deepEqual(readUtm(attributionUrl('/guide/en/guides/03-deployment', 'https://docs.openviking.ai/guide/zh/?' + new URLSearchParams(incoming), '/guide/')!), incoming)
})

test('GitHub、非 OV 路径、第三方、下载资源及 API 不透传', () => {
  for (const target of [
    'https://github.com/volcengine/OpenViking?utm_source=docs',
    'https://blog.openviking.ai/', 'https://www.volcengine.com/product/other',
    'https://www.volcengine.com.evil.example/product/openviking-service',
    'https://console.volcengine.com/vikingdb/openviking-other',
    'https://api.vikingdb.cn-beijing.volces.com/openviking',
    '/zh/llms.txt', '/agents/zh/hermes.md', '/brand.svg',
    'mailto:contact@example.com', 'javascript:void(0)'
  ]) assert.equal(attributionUrl(target, tagged), undefined, target)
  assert.deepEqual(readUtm(attributionUrl('https://www.openviking.ai/enterprise', tagged)!), incoming)
})

test('重复、缺失和超长字段整体无效，不产生混合来源', () => {
  for (const query of [
    'utm_source=wechat&utm_medium=social',
    'utm_source=wechat&utm_medium=social&utm_campaign=a&utm_source=rednote',
    'utm_source=wechat&utm_campaign=a',
    'utm_source=%20&utm_medium=referral',
    'utm_source=wechat&utm_medium=social&utm_campaign=' + 'a'.repeat(2049)
  ]) {
    const entry = docs + '?' + query
    assert.equal(readUtm(entry), undefined)
    assert.equal(readUtm(attributionUrl(product, entry)!)?.utm_source, 'opensource_docs')
  }
  assert.equal(readUtm('not a URL'), undefined)
})

test('付费搜索保留 term，空的可选字段保留原值', () => {
  const params = { utm_source: 'search_mock', utm_medium: 'cpc', utm_campaign: 'campaign', utm_term: 'agent memory', utm_content: '' }
  assert.deepEqual(readUtm(attributionUrl(product, docs + '?' + new URLSearchParams(params))!), params)
})
