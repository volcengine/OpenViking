<script setup lang="ts">
import { useData, withBase } from 'vitepress'
import { computed, ref } from 'vue'

const { page } = useData()

const llmsUrl = computed(() => {
  const pagePath = page.value.relativePath.replace(/\.md$/, '')
  return withBase(`/${pagePath}/llms.txt`)
})

const isDoc = computed(() => page.value.relativePath !== 'index.md')
const copied = ref(false)
const copyFailed = ref(false)
let resetTimer: ReturnType<typeof window.setTimeout> | undefined

const isZh = computed(() => page.value.relativePath.startsWith('zh/'))
const copyLabel = computed(() => {
  if (copyFailed.value) return isZh.value ? '复制失败' : 'Copy failed'
  if (copied.value) return isZh.value ? '已复制' : 'Copied'
  return isZh.value ? '复制' : 'Copy'
})

async function writeClipboard(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    return
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.setAttribute('readonly', '')
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    const copied = document.execCommand('copy')
    textarea.remove()
    if (!copied) throw new Error('Copy command was rejected')
  }
}

async function copyLlmsTxt() {
  window.clearTimeout(resetTimer)
  copied.value = false
  copyFailed.value = false

  try {
    const response = await fetch(llmsUrl.value)
    if (!response.ok) throw new Error(`Failed to load llms.txt: ${response.status}`)
    await writeClipboard(await response.text())
    copied.value = true
  } catch {
    copyFailed.value = true
  }

  resetTimer = window.setTimeout(() => {
    copied.value = false
    copyFailed.value = false
  }, 2000)
}
</script>

<template>
  <div v-if="isDoc" class="llms-link-wrap">
    <a :href="llmsUrl" target="_blank" rel="noopener" class="llms-link">
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>
        <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
      </svg>
      llms.txt
    </a>
    <button type="button" class="llms-copy" :class="{ 'is-copied': copied }" @click="copyLlmsTxt">
      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>
        <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>
      </svg>
      {{ copyLabel }}
    </button>
  </div>
</template>

<style scoped>
.llms-link-wrap {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.llms-link,
.llms-copy {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--vp-c-text-2);
  font-size: 13px;
}

.llms-link {
  text-decoration: none;
  transition: color 0.2s;
}

.llms-copy {
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  font-family: inherit;
  transition: color 0.2s;
}

.llms-link:hover,
.llms-copy:hover,
.llms-copy.is-copied {
  color: var(--vp-c-brand-1);
}
</style>
