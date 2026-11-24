// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { afterEach, expect, it } from 'vitest'
import i18n from './core'

afterEach(async () => {
  document.head.querySelector('link[rel="manifest"]')?.remove()
  await i18n.changeLanguage('en')
})

it('switches the manifest with the UI language, preserving subpath deployments', async () => {
  const link = document.createElement('link')
  link.rel = 'manifest'
  link.href = '/studio/manifest.json'
  document.head.append(link)
  await i18n.changeLanguage('zh-CN')
  expect(new URL(link.href).pathname).toBe('/studio/manifest.zh-CN.json')
  await i18n.changeLanguage('en')
  expect(new URL(link.href).pathname).toBe('/studio/manifest.json')
})

it('does not rewrite custom or external manifests', async () => {
  const link = document.createElement('link')
  link.rel = 'manifest'
  document.head.append(link)
  for (const url of [
    'https://external.test/manifest.json',
    '/custom.webmanifest',
  ]) {
    link.href = url
    const previous = link.href
    await i18n.changeLanguage('zh-CN')
    expect(link.href).toBe(previous)
  }
})

it('keeps both manifests identical except for language and translated description', () => {
  const english = JSON.parse(readFileSync('public/manifest.json', 'utf8'))
  const chinese = JSON.parse(readFileSync('public/manifest.zh-CN.json', 'utf8'))
  expect(english.lang).toBe('en')
  expect(chinese.lang).toBe('zh-CN')
  expect(chinese.description).toBe(
    'OpenViking Studio — 面向智能体的上下文数据库界面',
  )
  expect({
    ...chinese,
    lang: english.lang,
    description: english.description,
  }).toEqual(english)
})
