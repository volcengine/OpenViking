<script setup lang="ts">
import { bindLanguageMenu } from '../header-language.js'
import '../header-language.css'
import { computed, onMounted, onUnmounted, ref, nextTick } from 'vue'
import { useData, useRouter, withBase } from 'vitepress'
import type { LanguagePreference } from '../language-preference.js'
import { docsLanguagePreference } from '../language-state'
import { localizedDocument } from '../language-routing'
import { data as pages } from '../locale-pages.data'

const { lang, page } = useData()
const router = useRouter()
const policy = docsLanguagePreference
const preference = ref<LanguagePreference>('auto')
const menu = ref<HTMLDetailsElement>()
const locale = computed(() => lang.value.startsWith('zh') ? 'zh' : 'en')
let unbindMenu: (() => void) | undefined
let unsubscribe: (() => void) | undefined
onMounted(() => {
  if (menu.value) unbindMenu = bindLanguageMenu(menu.value)
  preference.value = policy.read()
  unsubscribe = policy.subscribe(() => { preference.value = policy.read() })
})
onUnmounted(() => { unsubscribe?.(); unbindMenu?.() })

async function switchLocale(choice: 'auto' | 'en' | 'zh') {
  if (menu.value) { menu.value.open = false; menu.value.querySelector('summary')?.focus() }
  policy.clearQuery()
  policy.save(choice)
  preference.value = choice
  const targetLocale = policy.resolve(null, true)
  const url = new URL(location.href)
  url.pathname = withBase(localizedDocument(page.value.relativePath, targetLocale, pages))
  await router.go(url.pathname + url.search + url.hash)
  await nextTick()
  if (url.hash) {
    let anchor: HTMLElement | null = null
    try { anchor = document.getElementById(decodeURIComponent(url.hash.slice(1))) } catch { /* Invalid fragment. */ }
    if (anchor) anchor.scrollIntoView()
    else {
      history.replaceState(history.state, '', url.pathname + url.search)
      window.scrollTo(0, 0)
    }
  }
}
</script>

<template>
  <details ref="menu" class="ov-language ov-locale-switch">
    <summary :aria-label="locale === 'zh' ? `语言：简体中文${preference === 'auto' ? '（跟随浏览器）' : ''}` : `Language: English${preference === 'auto' ? ' (following browser)' : ''}`">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M3 5h12M9 3v2m4 0c-1 7-5 10-10 12m2-9c1 4 4 7 8 9m0 4 5-13 5 13m-8-4h6"/></svg>
      <span>{{ locale === 'zh' ? '中' : 'EN' }}</span>
      <svg class="ov-language-chevron" width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m4 6 4 4 4-4"/></svg>
    </summary>
    <div class="ov-language-options">
      <button type="button" :aria-pressed="preference === 'auto'" @click="switchLocale('auto')">{{ locale === 'zh' ? '跟随浏览器' : 'Follow browser' }}<svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m4 10 4 4 8-8"/></svg></button>
      <button type="button" lang="en" :aria-pressed="preference === 'en'" @click="switchLocale('en')">English<svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m4 10 4 4 8-8"/></svg></button>
      <button type="button" lang="zh-CN" :aria-pressed="preference === 'zh'" @click="switchLocale('zh')">简体中文<svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m4 10 4 4 8-8"/></svg></button>
    </div>
  </details>
</template>
