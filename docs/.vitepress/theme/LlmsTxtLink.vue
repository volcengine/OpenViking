<script setup lang="ts">
import { useData, withBase } from 'vitepress'
import { computed, ref } from 'vue'
import { absoluteLlmsTxtUrl, hasPageLlmsTxt, pageLlmsTxtPath } from './llms-txt'

const { page } = useData()

const llmsUrl = computed(() => {
  return withBase(pageLlmsTxtPath(page.value.relativePath))
})

const isDoc = computed(() => hasPageLlmsTxt(page.value.relativePath))
const copied = ref(false)
const copyFailed = ref(false)
let resetTimer: ReturnType<typeof window.setTimeout> | undefined

const isZh = computed(() => page.value.relativePath.startsWith('zh/'))
const copyLabel = computed(() => {
  if (copyFailed.value) return isZh.value ? '链接复制失败' : 'Copy failed'
  if (copied.value) return isZh.value ? '链接已复制' : 'Link copied'
  return isZh.value ? '复制链接' : 'Copy link'
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

async function copyLlmsLink() {
  window.clearTimeout(resetTimer)
  copied.value = false
  copyFailed.value = false

  try {
    await writeClipboard(absoluteLlmsTxtUrl(llmsUrl.value, window.location.origin))
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
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>
        <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
      </svg>
      llms.txt
    </a>
    <button
      type="button"
      class="llms-copy"
      :class="{ 'is-copied': copied, 'is-failed': copyFailed }"
      @click="copyLlmsLink"
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
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
  white-space: nowrap;
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
  opacity: 0;
  pointer-events: none;
  transform: translateX(-4px);
  transition: color 0.2s, opacity 0.2s, transform 0.2s;
}

.llms-link-wrap:hover .llms-copy,
.llms-link-wrap:focus-within .llms-copy,
.llms-copy.is-copied,
.llms-copy.is-failed {
  opacity: 1;
  pointer-events: auto;
  transform: translateX(0);
}

.llms-link:hover,
.llms-copy:hover,
.llms-copy.is-copied {
  color: var(--vp-c-brand-1);
}

@media (hover: none) {
  .llms-copy {
    opacity: 1;
    pointer-events: auto;
    transform: translateX(0);
  }
}
</style>
