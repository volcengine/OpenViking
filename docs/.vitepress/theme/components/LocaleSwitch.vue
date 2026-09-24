<script setup lang="ts">
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
let unsubscribe: (() => void) | undefined
onMounted(() => {
  preference.value = policy.read()
  unsubscribe = policy.subscribe(() => { preference.value = policy.read() })
})
onUnmounted(() => unsubscribe?.())

async function switchLocale(choice: 'auto' | 'en' | 'zh') {
  if (menu.value) menu.value.open = false
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
  <details ref="menu" class="ov-locale-switch"
    @keydown.esc="() => { if (menu) { menu.open = false; menu.querySelector('summary')?.focus() } }"
    @focusout="event => { if (menu && !menu.contains(event.relatedTarget as Node)) menu.open = false }">
    <summary :aria-label="locale === 'zh' ? '语言' : 'Language'">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M3 5h12M9 3v2m4 0c-1 7-5 10-10 12m2-9c1 4 4 7 8 9m0 4 5-13 5 13m-8-4h6"/></svg>
      {{ locale === 'zh' ? '中' : 'EN' }}{{ preference === 'auto' ? (locale === 'zh' ? ' · 自动' : ' · Auto') : '' }}
    </summary>
    <div class="ov-locale-options">
      <button type="button" :aria-pressed="preference === 'auto'" @click="switchLocale('auto')">{{ locale === 'zh' ? '跟随浏览器' : 'Follow browser' }}</button>
      <button type="button" lang="en" :aria-pressed="preference === 'en'" @click="switchLocale('en')">English</button>
      <button type="button" lang="zh-CN" :aria-pressed="preference === 'zh'" @click="switchLocale('zh')">简体中文</button>
    </div>
  </details>
</template>

<style scoped>
.ov-locale-switch { position: relative; border: 1px solid var(--vp-c-divider); border-radius: 999px; background: var(--vp-c-bg-alt); }
summary { display: flex; align-items: center; gap: 5px; padding: 5px 8px; font: 11px/1.6 var(--vp-font-family-mono); cursor: pointer; list-style: none; }
summary::-webkit-details-marker { display: none; }
.ov-locale-options { position: absolute; right: 0; top: calc(100% + 6px); z-index: 90; min-width: 170px; padding: 5px; border: 1px solid var(--vp-c-divider); border-radius: 10px; background: var(--vp-c-bg); box-shadow: 0 4px 16px #0002; }
button { display: block; width: 100%; padding: 8px 10px; text-align: left; font-size: 12px; border-radius: 5px; }
button[aria-pressed=true] { font-weight: 700; }
button:hover { background: var(--vp-c-bg-alt); }
button:focus-visible, summary:focus-visible { outline: 2px solid var(--vp-c-brand-1); outline-offset: 3px; }
</style>
