<script setup lang="ts">
import { computed } from 'vue'
import { useData, useRouter, withBase } from 'vitepress'

const { lang, page, hash } = useData()
const router = useRouter()
const locale = computed(() => lang.value.startsWith('zh') ? 'zh' : 'en')

function switchLocale(next: 'en' | 'zh') {
  if (next === locale.value) return
  const relative = page.value.relativePath
    .replace(/^(en|zh)\//, '')
    .replace(/(^|\/)index\.md$/, '$1')
    .replace(/\.md$/, '')
  // Unlocalized design notes have no counterpart; use the selected docs home.
  const target = /^(en|zh)\//.test(page.value.relativePath) ? relative : ''
  void router.go(withBase(`/${next}/${target}`) + hash.value)
}
</script>

<template>
  <div class="ov-locale-switch" role="group" :aria-label="locale === 'zh' ? '语言' : 'Language'">
    <button type="button" lang="en" :aria-pressed="locale === 'en'" aria-label="English" @click="switchLocale('en')">EN</button>
    <button type="button" lang="zh-CN" :aria-pressed="locale === 'zh'" aria-label="简体中文" @click="switchLocale('zh')">中</button>
  </div>
</template>

<style scoped>
/* Same segmented structure and measurements as blog.openviking.ai's b-seg. */
.ov-locale-switch { display: flex; flex: 0 0 auto; padding: 2px; border: 1px solid var(--vp-c-divider); border-radius: 999px; background: var(--vp-c-bg-alt); }
button { padding: 6px 12px; border: 0; border-radius: 999px; background: transparent; color: var(--vp-c-text-2); font: 11px/1.6 var(--vp-font-family-mono); cursor: pointer; }
button[aria-pressed='true'] { background: var(--vp-c-text-1); color: var(--vp-c-bg); }
button:hover:not([aria-pressed='true']) { color: var(--vp-c-text-1); }
button:focus-visible { outline: 2px solid var(--vp-c-brand-1); outline-offset: 3px; }
@media (max-width: 374px) { button { padding-inline: 8px; } }
</style>
