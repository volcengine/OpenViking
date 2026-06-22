import assert from 'node:assert/strict'
import test from 'node:test'

import { searchCopyForLocale } from './openviking-search-i18n.ts'

test('returns Chinese search UI copy for zh docs', () => {
  const copy = searchCopyForLocale('zh')

  assert.equal(copy.trigger, '搜索文档')
  assert.equal(copy.compactTrigger, '搜索')
  assert.equal(copy.inputLabel, '搜索 OpenViking 文档')
  assert.equal(copy.modes.semantic.label, '语义搜索')
  assert.equal(copy.modes.keyword.placeholder, '搜索文档中的精确词句')
  assert.equal(copy.empty.noResults, '未找到相关结果。')
  assert.equal(copy.notice('timeout', 2), 'OpenViking 搜索超时。正在显示本地文档结果。')
  assert.equal(copy.notice('rate_limited', 0), 'OpenViking 搜索请求过多。未找到本地结果。')
})

test('keeps English search UI copy as the default locale', () => {
  const copy = searchCopyForLocale('en')

  assert.equal(copy.trigger, 'Search docs')
  assert.equal(copy.compactTrigger, 'Search')
  assert.equal(copy.inputLabel, 'Search OpenViking docs')
  assert.equal(copy.modes.semantic.label, 'Semantic')
  assert.equal(copy.empty.initial, 'Type a query to search the current language docs.')
})
