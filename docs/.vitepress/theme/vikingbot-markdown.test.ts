import assert from 'node:assert/strict'
import test from 'node:test'
import { renderVikingBotMarkdown } from './vikingbot-markdown.ts'

test('renders supported VikingBot Markdown', () => {
  const result = renderVikingBotMarkdown('## Install\n\n```bash\nnpm install openviking\n```')

  assert.match(result, /<h2>Install<\/h2>/)
  assert.match(result, /<code class="language-bash">/)
})

test('sanitizes VikingBot Markdown output', () => {
  const result = renderVikingBotMarkdown([
    '<img src=x onerror=alert(1)>',
    '[unsafe](javascript:alert(1))',
    '[safe](https://openviking.ai)'
  ].join('\n\n'))

  assert.doesNotMatch(result, /<img/i)
  assert.doesNotMatch(result, /href="javascript:/i)
  assert.match(result, /href="https:\/\/openviking\.ai"/)
  assert.match(result, /target="_blank"/)
  assert.match(result, /rel="noopener noreferrer"/)
})
