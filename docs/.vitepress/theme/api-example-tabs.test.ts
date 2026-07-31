import assert from 'node:assert/strict'
import test from 'node:test'

import {
  exampleLanguage,
  responseExample,
  isApiReferencePath,
  isSharedSectionLabel,
  preferredLanguage,
  shouldSynchronizeExampleTabs
} from './api-example-tabs.ts'
import { absoluteLlmsTxtUrl, hasPageLlmsTxt, pageLlmsTxtPath } from './llms-txt.ts'

test('hides llms.txt actions on index pages without generated artifacts', () => {
  assert.equal(hasPageLlmsTxt('index.md'), false)
  assert.equal(hasPageLlmsTxt('en/index.md'), false)
  assert.equal(hasPageLlmsTxt('zh/index.md'), false)
  assert.equal(hasPageLlmsTxt('en/api/01-overview.md'), true)
})

test('builds the absolute per-page llms.txt link copied by the page action', () => {
  const path = pageLlmsTxtPath('zh/guides/03-cli-config.md')
  assert.equal(path, '/zh/guides/03-cli-config/llms.txt')
  assert.equal(
    absoluteLlmsTxtUrl(`/OpenViking${path}`, 'https://docs.openviking.net'),
    'https://docs.openviking.net/OpenViking/zh/guides/03-cli-config/llms.txt'
  )
})

test('recognizes language headings with punctuation and qualifiers', () => {
  assert.equal(exampleLanguage('HTTP API：')?.key, 'http')
  assert.equal(exampleLanguage('CLI (subcommands of resources)')?.key, 'cli')
})

test('recognizes transport and result response variants', () => {
  assert.deepEqual(responseExample('HTTP API 响应 (JSON, `wait=true`)'), {
    key: 'response-http', label: 'HTTP (wait=true)', kind: 'response'
  })
  assert.equal(responseExample('CLI 响应 (JSON 格式，使用 -o json)')?.label, 'CLI JSON')
  assert.equal(responseExample('Response (Directory)')?.label, 'Directory')
  assert.equal(responseExample('响应（Memory）')?.label, 'Memory')
  assert.equal(responseExample('异步响应（`wait=false`）')?.key, 'response-async')
})

test('keeps localized response variant keys distinct', () => {
  const file = responseExample('响应（文件）')
  const directory = responseExample('响应（目录）')
  const importing = responseExample('响应示例（资源导入进行中）')
  const completed = responseExample('响应示例（完成）')

  assert.equal(file?.key, 'response-文件')
  assert.equal(directory?.key, 'response-目录')
  assert.notEqual(file?.key, directory?.key)
  assert.notEqual(importing?.key, completed?.key)
})

test('synchronizes language tabs without resetting response tabs', () => {
  assert.equal(shouldSynchronizeExampleTabs('language'), true)
  assert.equal(shouldSynchronizeExampleTabs('response'), false)
  assert.equal(shouldSynchronizeExampleTabs(undefined), false)
})

test('recognizes response and note variants as shared sections', () => {
  for (const label of [
    'Response',
    'Response Examples',
    'Result fields',
    'Error Response',
    'CLI override flags',
    'MCP (agent control plane)',
    'Notes:',
    '返回字段说明',
    '说明：'
  ]) {
    assert.equal(isSharedSectionLabel(label), true, label)
  }
  for (const label of [
    'Python SDK (Embedded / HTTP)',
    'TypeScript SDK',
    'Go SDK',
    'HTTP API',
    'CLI (via ovcli.conf)',
    'Response (File)',
    'HTTP API Response (JSON)',
    '响应（applied）',
    '响应示例（完成）'
  ]) {
    assert.equal(isSharedSectionLabel(label), false, label)
  }
  for (const label of [
    'Basic Search',
    'Image Search',
    'Search with Target URI Limitation',
    '基础搜索',
    '图片搜索',
    '使用 Target URI 限定搜索范围'
  ]) {
    assert.equal(isSharedSectionLabel(label), false, label)
  }
})

test('limits enhancement to localized API reference pages', () => {
  assert.equal(isApiReferencePath('/en/api/03-filesystem'), true)
  assert.equal(isApiReferencePath('/zh/api/04-skills'), true)
  assert.equal(isApiReferencePath('/OpenViking/en/api/11-snapshot'), true)
  assert.equal(isApiReferencePath('/en/guides/04-authentication'), false)
})

test('keeps one initial language across all groups', () => {
  assert.equal(preferredLanguage(null, undefined, 'typescript'), 'typescript')
  assert.equal(preferredLanguage(null, 'typescript', 'python'), 'typescript')
  assert.equal(preferredLanguage('go', 'typescript', 'python'), 'go')
})
