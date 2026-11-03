import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { channels, mockUtm, githubMockHtml, receiverMockHtml } from './utm-mock-server.mjs'

// 从仓库根目录分别启动两个终端：
// pnpm -C docs run docs:dev --port 5173 --strictPort
// pnpm -C docs exec node scripts/utm-mock-server.mjs
// 独立工具目录安装 Playwright 后，以 OV_UTM_PLAYWRIGHT 指向其 index.mjs：
// OV_UTM_PLAYWRIGHT=/绝对路径/node_modules/playwright/index.mjs pnpm -C docs exec node scripts/test-utm-browser.mjs
// 浏览器须与 Playwright 版本匹配；报告和截图默认写入 /tmp/openviking-utm-artifacts。
// Playwright 可安装在独立工具目录，通过环境变量提供，避免改变文档构建依赖。
const { chromium } = await import(process.env.OV_UTM_PLAYWRIGHT ?? 'playwright')
const docsOrigin = process.env.OV_UTM_DOCS_ORIGIN ?? 'http://127.0.0.1:5173'
const mockOrigin = process.env.OV_UTM_MOCK_ORIGIN ?? 'http://127.0.0.1:5174'
const docsUrl = docsOrigin + '/zh/getting-started/02-quickstart'
const output = process.env.OV_UTM_ARTIFACT_DIR ?? '/tmp/openviking-utm-artifacts'
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true, executablePath: process.env.OV_UTM_CHROMIUM_PATH, args: ['--no-sandbox'] })
const results = []
const keys = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term']
const utm = href => Object.fromEntries([...new URL(href).searchParams].filter(([key]) => keys.includes(key)))
const fallback = { utm_source: 'opensource_docs', utm_medium: 'referral', utm_campaign: 'getting-started_02-quickstart' }

async function check(name, run) {
  try { await run(); results.push({ name, passed: true }); console.log('通过：' + name) }
  catch (error) { results.push({ name, passed: false, error: error.stack }); console.error('失败：' + name + '\n' + error.stack) }
}

async function fixture(blockStorage = false) {
  const context = await browser.newContext({ locale: 'zh-CN', viewport: { width: 1360, height: 940 } })
  context.setDefaultTimeout(15000)
  const errors = []
  context.on('page', page => page.on('pageerror', error => errors.push(error.message)))
  if (blockStorage) await context.addInitScript(() => {
    Object.defineProperty(window, 'sessionStorage', { get() { throw new Error('Mock 禁用会话存储') } })
  })
  await context.route('**/*', async route => {
    const url = new URL(route.request().url())
    if ([new URL(docsOrigin).origin, new URL(mockOrigin).origin].includes(url.origin)) return route.continue()
    if (url.pathname.endsWith('/docs/search')) return route.fulfill({
      contentType: 'application/json', headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'POST, OPTIONS' },
      body: JSON.stringify({ results: [{ title: '部署指南', url: '/en/guides/03-deployment', relativePath: 'en/guides/03-deployment.md', snippet: 'Mock 搜索结果' }] })
    })
    if (url.origin === 'https://github.com') return route.fulfill({ contentType: 'text/html', body: githubMockHtml(docsUrl) })
    if (['https://www.volcengine.com', 'https://console.volcengine.com', 'https://www.openviking.ai', 'http://localhost:8080'].includes(url.origin)) {
      return route.fulfill({ contentType: 'text/html', body: receiverMockHtml(url.href) })
    }
    return route.abort()
  })
  const page = await context.newPage()
  return { context, page, errors }
}

async function ready(page, expected) {
  // SPA 会先更新地址栏；等待新页面标题，避免对上一页 DOM 继续操作。
  const pathname = new URL(page.url()).pathname.replace(/\.html$/, '')
  const markdown = await readFile(new URL(`..${pathname}.md`, import.meta.url), 'utf8')
  const title = markdown.match(/^#\s+(.+)$/m)[1]
  await page.locator('.vp-doc h1').filter({ hasText: title }).waitFor()
  await page.waitForFunction(source => {
    const link = document.querySelector('.vp-doc a[href*="console.volcengine.com"]')
    return link && new URL(link.href).searchParams.get('utm_source') === source
  }, expected.utm_source)
  for (const href of await page.locator('.vp-doc a[href*="openviking-service"], .vp-doc a[href*="console.volcengine.com"]').evaluateAll(links => links.map(link => link.href))) {
    assert.deepEqual(utm(href), expected)
  }
}

async function clickReceiver(context, page, link, expected, method = 'click') {
  const next = context.waitForEvent('page')
  if (method === 'keyboard') { await link.focus(); await link.press('Enter') }
  else await link.click(method === 'middle' ? { button: 'middle' } : {})
  const receiver = await next
  await receiver.bringToFront()
  await receiver.waitForSelector('#received')
  assert.deepEqual(utm(receiver.url()), expected)
  assert.deepEqual(JSON.parse(await receiver.locator('#received').innerText()), expected)
  await receiver.close()
}

try {
  for (const [source, label, medium] of channels) await check(label + ' 实际入口和出站接收', async () => {
    const { context, page, errors } = await fixture()
    try {
      const expected = mockUtm(source, medium)
      await page.goto(`${mockOrigin}/channel/${source}`, { waitUntil: 'domcontentloaded' })
      if (source === 'rednote_volc_agent_community') await page.screenshot({ path: path.join(output, 'utm-rednote-entry.png') })
      await page.locator('#enter-docs').click()
      await ready(page, expected)
      assert.deepEqual(utm(page.url()), expected)
      assert.deepEqual(JSON.parse(await page.evaluate(() => sessionStorage.getItem('openviking-docs-utm'))), expected)
      await clickReceiver(context, page, page.locator('.vp-doc a[href*="console.volcengine.com"]').first(), expected)
      assert.deepEqual(errors, [])
      if (source === 'rednote_volc_agent_community') await page.screenshot({ path: path.join(output, 'utm-rednote-docs.png') })
    } finally { await context.close() }
  })

  await check('动态链接增量处理，无关 DOM 和自身补参不重复扫描', async () => {
    const { context, page, errors } = await fixture()
    try {
      await context.addInitScript(() => {
        const metrics = window.__utmObserverMetrics = { fullScans: 0, targetParses: 0 }
        const query = Document.prototype.querySelectorAll
        Document.prototype.querySelectorAll = function(selector) {
          if (this === document && selector === 'a[href]') metrics.fullScans++
          return query.call(this, selector)
        }
        const NativeURL = window.URL
        window.URL = class extends NativeURL {
          constructor(href, base) {
            super(href, base)
            if (String(href).includes('observer_probe=')) metrics.targetParses++
          }
        }
      })
      const expected = mockUtm('wechat_bytetech', 'social')
      await page.goto(docsUrl + '?' + new URLSearchParams(expected), { waitUntil: 'domcontentloaded' })
      await ready(page, expected)
      const measurements = await page.evaluate(async () => {
        const settle = () => new Promise(resolve => setTimeout(resolve, 0))
        const reset = () => Object.assign(window.__utmObserverMetrics, { fullScans: 0, targetParses: 0 })
        const sample = () => ({ ...window.__utmObserverMetrics })
        const root = document.createElement('section')
        document.querySelector('.vp-doc').append(root)
        await settle()
        try {
          reset()
          const text = document.createElement('p')
          root.append(text)
          for (let index = 0; index < 20; index++) { text.textContent = String(index); await settle() }
          const unrelated = sample()

          reset()
          const wrapper = document.createElement('div')
          root.append(wrapper)
          const link = document.createElement('a')
          link.href = 'https://www.volcengine.com/product/openviking-service?observer_probe=added#plans'
          wrapper.append(link)
          await settle()
          const added = { ...sample(), href: link.href }

          reset()
          link.href = 'https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing?observer_probe=changed'
          await settle()
          const changed = { ...sample(), href: link.href }

          reset()
          const removedLink = document.createElement('a')
          removedLink.href = 'https://www.volcengine.com/product/openviking-service?observer_probe=removed'
          root.append(removedLink)
          removedLink.remove()
          await settle()
          const removed = sample()
          window.dispatchEvent(new PageTransitionEvent('pageshow'))
          await settle()
          const samePage = sample()
          return { unrelated, added, changed, removed, samePage }
        } finally { root.remove() }
      })
      assert.deepEqual(measurements.unrelated, { fullScans: 0, targetParses: 0 })
      assert.equal(measurements.added.fullScans, 0)
      assert.equal(measurements.added.targetParses, 1)
      assert.deepEqual(utm(measurements.added.href), expected)
      assert.equal(new URL(measurements.added.href).hash, '#plans')
      assert.equal(measurements.changed.fullScans, 0)
      assert.equal(measurements.changed.targetParses, 1)
      assert.deepEqual(utm(measurements.changed.href), expected)
      assert.equal(new URL(measurements.changed.href).pathname, '/vikingdb/openviking/region:openviking+cn-beijing')
      assert.deepEqual(measurements.removed, { fullScans: 0, targetParses: 0 })
      assert.deepEqual(measurements.samePage, { fullScans: 0, targetParses: 0 })
      assert.deepEqual(errors, [])
    } finally { await context.close() }
  })

  await check('侧栏、语言切换、搜索动态链接、刷新及浏览器返回', async () => {
    const { context, page, errors } = await fixture()
    const expected = mockUtm('wechat_bytetech', 'social')
    try {
      await page.goto(docsUrl + '?' + new URLSearchParams(expected), { waitUntil: 'domcontentloaded' })
      await ready(page, expected)
      await page.locator('.VPSidebar a[href*="/zh/getting-started/05-cli-setup"]').first().click()
      await page.waitForURL('**/zh/getting-started/05-cli-setup?**')
      await ready(page, expected)
      await page.locator('.ov-locale-switch summary').click()
      await page.locator('.ov-locale-switch button[lang="en"]').click()
      await page.waitForURL('**/en/getting-started/05-cli-setup?**')
      await ready(page, expected)
      await page.locator('.ov-docs-search-trigger').click()
      await page.locator('.ov-docs-search-input').fill('CLI')
      const search = page.locator('.ov-docs-search-result').first()
      await search.waitFor()
      assert.deepEqual(utm(await search.getAttribute('href')), expected)
      await search.click()
      await page.waitForURL('**/en/guides/03-deployment?**')
      await ready(page, expected)
      await page.reload({ waitUntil: 'domcontentloaded' })
      await ready(page, expected)
      await page.goBack({ waitUntil: 'domcontentloaded' })
      await ready(page, expected)
      assert.deepEqual(utm(page.url()), expected)
      assert.deepEqual(errors, [])
    } finally { await context.close() }
  })

  for (const method of ['keyboard', 'middle']) await check(method === 'keyboard' ? '键盘打开产品页' : '中键新标签页打开产品页', async () => {
    const { context, page } = await fixture()
    try {
      const expected = mockUtm('rednote_volc_agent_community', 'social')
      await page.goto(docsUrl + '?' + new URLSearchParams(expected), { waitUntil: 'domcontentloaded' })
      await ready(page, expected)
      const link = page.locator('.vp-doc a[href*="openviking-service"]').first()
      assert.deepEqual(utm(await link.getAttribute('href')), expected)
      await clickReceiver(context, page, link, expected, method)
    } finally { await context.close() }
  })

  await check('站内新标签页通过目标 href 继承，不依赖共享缓存', async () => {
    const { context, page } = await fixture()
    try {
      const expected = mockUtm('wechat_byte_opensource', 'social')
      await page.goto(docsUrl + '?' + new URLSearchParams(expected), { waitUntil: 'domcontentloaded' })
      await ready(page, expected)
      const link = page.locator('.VPSidebar a[href*="/zh/getting-started/05-cli-setup"]').first()
      assert.deepEqual(utm(await link.getAttribute('href')), expected)
      const next = context.waitForEvent('page')
      await link.click({ button: 'middle' })
      const tab = await next
      await tab.bringToFront()
      await ready(tab, expected)
      assert.deepEqual(utm(tab.url()), expected)
    } finally { await context.close() }
  })

  await check('公众号历史后直接访问和 GitHub 入站均不恢复历史来源', async () => {
    const { context, page } = await fixture()
    try {
      await page.goto(docsUrl + '?' + new URLSearchParams(mockUtm('wechat_bytetech', 'social')), { waitUntil: 'domcontentloaded' })
      await ready(page, mockUtm('wechat_bytetech', 'social'))
      await page.goto(docsUrl, { waitUntil: 'domcontentloaded' })
      await ready(page, fallback)
      assert.equal(await page.evaluate(() => sessionStorage.getItem('openviking-docs-utm')), null)
      await page.goto(docsUrl + '?' + new URLSearchParams(mockUtm('wechat_bytetech', 'social')), { waitUntil: 'domcontentloaded' })
      await ready(page, mockUtm('wechat_bytetech', 'social'))
      await page.goto('https://github.com/volcengine/OpenViking', { waitUntil: 'domcontentloaded' })
      assert.deepEqual(utm(await page.locator('#github-product').getAttribute('href')), { utm_source: 'github', utm_medium: 'referral', utm_campaign: 'readme' })
      await page.locator('#enter-docs').click()
      await ready(page, fallback)
      assert.deepEqual(utm(page.url()), {})
      assert.equal(await page.evaluate(() => sessionStorage.getItem('openviking-docs-utm')), null)
    } finally { await context.close() }
  })

  await check('新的第三方入口替换旧来源，缺省字段不残留', async () => {
    const { context, page } = await fixture()
    try {
      await page.goto(docsUrl + '?' + new URLSearchParams(mockUtm('wechat_bytetech', 'social')), { waitUntil: 'domcontentloaded' })
      await ready(page, mockUtm('wechat_bytetech', 'social'))
      const expected = { utm_source: 'rednote_volc_agent_community', utm_medium: 'social', utm_campaign: 'new_mock_20261010' }
      await page.goto(docsUrl + '?' + new URLSearchParams(expected), { waitUntil: 'domcontentloaded' })
      await ready(page, expected)
    } finally { await context.close() }
  })

  await check('语言首页入口重定向保留 UTM，GitHub 出口不继承', async () => {
    const { context, page } = await fixture()
    try {
      const expected = mockUtm('rednote_volc_agent_community', 'social')
      await page.goto(docsOrigin + '/?' + new URLSearchParams(expected), { waitUntil: 'domcontentloaded' })
      await page.waitForURL('**/zh/?**')
      await page.waitForFunction(source => new URL(document.querySelector('.ov-site-option[href*="www.openviking.ai"]').href).searchParams.get('utm_source') === source, expected.utm_source)
      assert.deepEqual(utm(page.url()), expected)
      for (const href of await page.locator('a[href*="github.com"]').evaluateAll(links => links.map(link => link.href))) assert.notEqual(new URL(href).searchParams.get('utm_source'), expected.utm_source)
    } finally { await context.close() }
  })

  await check('UTM 会话存储禁用时继续透传且页面无异常', async () => {
    const { context, page, errors } = await fixture(true)
    try {
      const expected = mockUtm('wechat_volce_agent_community', 'social')
      await page.goto(docsUrl + '?' + new URLSearchParams(expected), { waitUntil: 'domcontentloaded' })
      await ready(page, expected)
      assert.deepEqual(errors, [])
    } finally { await context.close() }
  })

  await check('重复 UTM 整组降级为文档来源', async () => {
    const { context, page } = await fixture()
    try {
      await page.goto(docsUrl + '?utm_source=wechat&utm_source=rednote&utm_medium=social&utm_campaign=duplicate', { waitUntil: 'domcontentloaded' })
      await ready(page, fallback)
    } finally { await context.close() }
  })
} finally {
  await browser.close()
  const report = { scope: '本地文档参数传递与 Mock 接收；不代表真实官网、登录或订单归因已验收', docsOrigin, mockOrigin, passed: results.filter(result => result.passed).length, failed: results.filter(result => !result.passed).length, results }
  await writeFile(path.join(output, 'utm-browser-report.json'), JSON.stringify(report, null, 2))
  console.log(`浏览器验收：${report.passed} 项通过，${report.failed} 项失败；报告 ${output}/utm-browser-report.json`)
  if (report.failed) process.exitCode = 1
}
