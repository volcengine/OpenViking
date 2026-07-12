import assert from 'node:assert/strict'
import test from 'node:test'

import {
  exampleLanguage,
  isApiReferencePath,
  isSharedSectionLabel,
  preferredLanguage
} from './api-example-tabs.ts'

test('recognizes language headings with punctuation and qualifiers', () => {
  assert.equal(exampleLanguage('HTTP API：')?.key, 'http')
  assert.equal(exampleLanguage('CLI (subcommands of resources)')?.key, 'cli')
})

test('recognizes response and note variants as shared sections', () => {
  for (const label of [
    'Response',
    'Response Examples',
    'Response (File)',
    'Result fields',
    'HTTP API Response (JSON)',
    'Error Response',
    'CLI override flags',
    'Notes:',
    '响应（applied）',
    '响应示例（完成）',
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
    'CLI (via ovcli.conf)'
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
