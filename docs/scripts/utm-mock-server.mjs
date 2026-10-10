import { createServer } from 'node:http'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

export const channels = [
  ['wechat_bytetech', '微信公众号 字节技术团队', 'social'],
  ['wechat_byte_opensource', '微信公众号 字节开源', 'social'],
  ['wechat_volce_agent_community', '微信公众号 火山 Agent 社区', 'social'],
  ['rednote_volc_agent_community', '小红书 火山 Agent 社区', 'social'],
  ['mock_newsletter', '其他第三方 邮件推荐', 'referral'],
  ['mock_search', '其他第三方 付费搜索', 'cpc']
]

export function mockUtm(source, medium) {
  const params = { utm_source: source, utm_medium: medium, utm_campaign: `mock_${source}_20261010` }
  if (medium === 'cpc') params.utm_term = 'Agent memory 搜索词'
  else params.utm_content = '验收图文_中文 空格&位置=首屏'
  return params
}

export const githubProductUrl = readFileSync(new URL('../../README.md', import.meta.url), 'utf8')
  .match(/\]\((https:\/\/www\.volcengine\.com\/product\/openviking-service[^)]+)\)/)[1]

const escape = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]))
function html(title, content) {
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>${title}</title><style>body{font:16px/1.7 system-ui;max-width:960px;margin:48px auto;padding:0 24px;color:#17233b;background:#f5f7fb}h1{font-size:30px}article{background:white;border:1px solid #dce3ed;border-radius:12px;padding:20px;margin:16px 0}a{color:#165dff}code,pre{overflow-wrap:anywhere;white-space:pre-wrap;font-size:13px}small{color:#61718a}</style><h1>${title}</h1>${content}</html>`
}

export function githubMockHtml(docsUrl) {
  return html('Mock GitHub README', `<p>本页只验证 GitHub 自身来源；上游公众号、小红书 UTM 不透传。</p><article><a id="enter-docs" href="${escape(docsUrl)}">进入无上游 UTM 的开源文档</a></article><article><a id="github-product" href="${escape(githubProductUrl)}">README 的 SaaS 入口</a><pre>${escape(githubProductUrl)}</pre></article>`)
}

export function receiverMockHtml(href) {
  const url = new URL(href)
  const received = Object.fromEntries([...url.searchParams].filter(([key]) => /^utm_(source|medium|campaign|content|term)$/.test(key)))
  return html('Mock 出站接收页', `<p>本页由浏览器验收脚本拦截生成，用于核对文档出口参数。它不代表真实官网或控制台的采集能力。</p><pre id="received">${escape(JSON.stringify(received, null, 2))}</pre><pre>${escape(href)}</pre>`)
}

export function createMockServer(docsOrigin = 'http://127.0.0.1:5173') {
  const docsUrl = new URL('/zh/getting-started/02-quickstart', docsOrigin).href
  return createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost')
    let body
    if (url.pathname === '/github') body = githubMockHtml(docsUrl)
    else {
      const selected = channels.find(([source]) => url.pathname === `/channel/${source}`)
      const cards = (selected ? [selected] : channels).map(([source, label, medium]) => {
        const target = docsUrl + '?' + new URLSearchParams(mockUtm(source, medium))
        return `<article><h2>${label}</h2><a ${selected ? 'id="enter-docs"' : ''} href="${escape(target)}">携带 UTM 进入文档</a><pre>${escape(target)}</pre>${selected ? '' : `<a href="/channel/${source}">打开独立 Mock 渠道页</a>`}</article>`
      }).join('')
      body = html(selected ? selected[1] : 'OpenViking UTM 本地验收入口', `<p>这些入口模拟第三方携带标准 UTM 访问文档。自动化验收会拦截产品页和控制台为 Mock；手动点击文档出口会进入实际站点。</p>${cards}<article><a href="${escape(docsUrl)}">直接访问 无 UTM</a> · <a href="/github">Mock GitHub 不透传上游 UTM</a></article>`)
    }
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' })
    response.end(body)
  })
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.OV_UTM_MOCK_PORT ?? 5174)
  createMockServer(process.env.OV_UTM_DOCS_ORIGIN).listen(port, '0.0.0.0', () => console.log(`UTM Mock 入口：http://127.0.0.1:${port}/`))
}
