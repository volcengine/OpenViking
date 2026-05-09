<script setup lang="ts">
import { useData, withBase } from 'vitepress'
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'

const { frontmatter, page } = useData()

const copied = ref(false)
const open = ref(false)
const wrapRef = ref<HTMLElement | null>(null)

// Build absolute llms.txt URL for this page (browser-only)
const llmsTxtUrl = computed(() => {
  if (typeof window === 'undefined') return ''
  const pagePath = page.value.relativePath.replace(/\.md$/, '')
  return `${window.location.origin}${withBase(`/${pagePath}/llms.txt`)}`
})

const items = computed(() => {
  const q = encodeURIComponent(`Read ${llmsTxtUrl.value}, I want to ask questions about it.`)
  return [
    {
      label: 'Open in ChatGPT',
      href: `https://chatgpt.com/?hints=search&q=${q}`,
      icon: `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M22.28 9.28a5.76 5.76 0 0 0-.496-4.727 5.83 5.83 0 0 0-6.275-2.8A5.826 5.826 0 0 0 11.12 0a5.83 5.83 0 0 0-5.563 4.038 5.826 5.826 0 0 0-3.9 2.83 5.83 5.83 0 0 0 .717 6.834 5.76 5.76 0 0 0 .496 4.727 5.83 5.83 0 0 0 6.274 2.8A5.826 5.826 0 0 0 12.88 24a5.83 5.83 0 0 0 5.564-4.038 5.826 5.826 0 0 0 3.9-2.83 5.83 5.83 0 0 0-.717-6.834l-.347.152zm-9.4 13.173a4.32 4.32 0 0 1-2.774-1.006l.137-.078 4.608-2.66a.76.76 0 0 0 .383-.662v-6.497l1.948 1.124a.072.072 0 0 1 .038.054v5.38a4.334 4.334 0 0 1-4.34 4.345zm-9.327-3.984a4.32 4.32 0 0 1-.517-2.912l.137.082 4.609 2.661a.76.76 0 0 0 .765 0l5.628-3.25v2.249a.072.072 0 0 1-.029.06L9.4 19.897a4.334 4.334 0 0 1-5.847-1.43zm-1.214-10.05a4.32 4.32 0 0 1 2.256-1.903v5.47a.76.76 0 0 0 .384.661l5.628 3.25-1.948 1.124a.072.072 0 0 1-.068.006L4.09 14.54a4.334 4.334 0 0 1-1.751-6.121zm16.018 3.724-5.628-3.25 1.948-1.123a.072.072 0 0 1 .068-.007l4.515 2.607a4.334 4.334 0 0 1-.672 7.818v-5.47a.76.76 0 0 0-.231-.575zm1.938-2.924-.137-.083-4.608-2.66a.76.76 0 0 0-.766 0L9.156 9.73V7.482a.072.072 0 0 1 .029-.06l4.515-2.607a4.334 4.334 0 0 1 6.613 4.49zm-12.184 4.01-1.948-1.124a.072.072 0 0 1-.038-.054V6.693a4.334 4.334 0 0 1 7.106-3.328l-.137.078-4.608 2.661a.76.76 0 0 0-.383.661v.001zm1.058-2.283 2.505-1.446 2.505 1.446v2.889l-2.505 1.447-2.505-1.447z"/></svg>`
    },
    {
      label: 'Open in Claude',
      href: `https://claude.ai/new?q=${q}`,
      icon: `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M4.709 15.955l4.72-2.647.08-.23-.08-.128H9.2l-.79-.048-2.698-.031-2.339-.047-1.089-.04-.703-.087-.128-.048.065-.176.128-.128 1.49-.518 3.294-1.04 3.295-1.04.87-.295-.016-.12-.136-.073-2.657-.37-2.63-.406-1.842-.287-.872-.16-.528-.127-.448-.256-.096-.335.016-.176.103-.16.24-.128.336-.048.415.016 1.055.128 2.155.255 3.12.4 3.121.4.504.064.047-.127-.287-.368-1.449-2.02-1.21-1.73-.62-.894-.447-.687-.128-.256-.064-.336.064-.239.207-.12.304-.065.384.065.415.224.814.654 1.73 1.4 1.714 1.384.623.495.16-.048.048-.144.016-2.837.015-1.457.065-1.039.16-.737.23-.639.383-.415.384-.128.4.064.351.224.24.4.112.606v.7l-.112 1.01-.128 1.217-.144 2.035-.016.32.144.064 1.09-.735 1.97-1.297 1.75-1.12.894-.527.719-.383.655-.16.543.064.463.303.24.496.016.51-.16.43-.384.4-.783.543-1.34.926-1.004.703-.655.48.016.096.08.063 2.403-.16 1.426-.015 1.04.063 1.137.24.719.383.383.48.16.623-.127.575-.384.464-.64.32-.815.144h-.384l-.4-.016-1.009-.127-2.307-.384-1.972-.32-.415-.048-.064.063.208.32 1.25 1.924.927 1.49.399.735.095.655-.127.559-.383.4-.48.143-.624-.016-.607-.24-.622-.511-1.562-1.67-1.307-1.42-.432-.512-.16.016-.112.159-.016.384.064 2.006.08 1.955.016.9.016.56-.16.654-.32.527-.512.304-.608.048-.512-.16-.432-.367-.32-.608-.192-.847-.16-1.36-.048-1.782-.016-1.115v-.44l-.016-.064-.112-.016-.256.08-1.99.647-2.16.687-.944.287-.687.128-.624-.032-.463-.191-.288-.4-.08-.432.031-.367.208-.32.384-.272.576-.192.768-.16 2.032-.48.127-.064z"/></svg>`
    },
  ]
})

async function copyToClipboard() {
  const md: string = frontmatter.value._rawMarkdown ?? ''
  if (!md) return
  await navigator.clipboard.writeText(md)
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}

async function handleCopy() {
  await copyToClipboard()
  open.value = false
}

function handleItem(href: string) {
  window.open(href, '_blank', 'noopener')
  open.value = false
}

function onOutsideClick(e: MouseEvent) {
  if (wrapRef.value && !wrapRef.value.contains(e.target as Node)) {
    open.value = false
  }
}

onMounted(() => document.addEventListener('click', onOutsideClick))
onBeforeUnmount(() => document.removeEventListener('click', onOutsideClick))

const isDoc = computed(() => page.value.relativePath !== 'index.md')
</script>

<template>
  <div v-if="isDoc" ref="wrapRef" class="copy-md-wrap">
    <div class="copy-md-group" :class="{ open }">
      <button class="copy-md-main" :class="{ copied }" @click="handleCopy">
        <span class="copy-md-icon">
          <svg v-if="!copied" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
          </svg>
          <svg v-else xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"/>
          </svg>
        </span>
        {{ copied ? 'Copied!' : 'Copy markdown' }}
      </button>
      <button class="copy-md-chevron" :class="{ open }" @click.stop="open = !open" aria-label="More options">
        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline v-if="open" points="18 15 12 9 6 15"/>
          <polyline v-else points="6 9 12 15 18 9"/>
        </svg>
      </button>
    </div>

    <Transition name="dropdown">
      <div v-if="open" class="copy-md-dropdown">
        <button
          v-for="item in items"
          :key="item.label"
          class="copy-md-dropdown-item"
          @click="handleItem(item.href)"
        >
          <span class="copy-md-dropdown-icon" v-html="item.icon" />
          <span>{{ item.label }}</span>
          <svg class="copy-md-ext" xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
            <polyline points="15 3 21 3 21 9"/>
            <line x1="10" y1="14" x2="21" y2="3"/>
          </svg>
        </button>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.copy-md-wrap {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 16px;
  position: relative;
}

.copy-md-group {
  display: inline-flex;
  align-items: stretch;
  border: 1px solid var(--vp-c-divider);
  border-radius: 8px;
  overflow: visible;
  background: var(--vp-c-bg-soft);
  transition: border-color 0.2s;
}

.copy-md-group:hover,
.copy-md-group.open {
  border-color: var(--vp-c-brand-1);
}

.copy-md-main {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: transparent;
  border: none;
  border-right: 1px solid var(--vp-c-divider);
  color: var(--vp-c-text-2);
  font-size: 13px;
  font-family: var(--vp-font-family-base);
  cursor: pointer;
  transition: color 0.2s;
  white-space: nowrap;
}

.copy-md-main:hover,
.copy-md-main.copied {
  color: var(--vp-c-brand-1);
}

.copy-md-icon {
  display: flex;
  align-items: center;
}

.copy-md-chevron {
  display: inline-flex;
  align-items: center;
  padding: 6px 8px;
  background: transparent;
  border: none;
  color: var(--vp-c-text-2);
  cursor: pointer;
  transition: color 0.2s;
}

.copy-md-chevron:hover {
  color: var(--vp-c-brand-1);
}

.copy-md-dropdown {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  min-width: 190px;
  background: var(--vp-c-bg-elv);
  border: 1px solid var(--vp-c-divider);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  z-index: 100;
  padding: 4px;
}

.copy-md-dropdown-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 12px;
  background: transparent;
  border: none;
  border-radius: 7px;
  color: var(--vp-c-text-1);
  font-size: 13px;
  font-family: var(--vp-font-family-base);
  cursor: pointer;
  text-align: left;
  transition: background 0.15s;
}

.copy-md-dropdown-item:hover {
  background: var(--vp-c-bg-soft);
}

.copy-md-dropdown-icon {
  display: flex;
  align-items: center;
  color: var(--vp-c-text-2);
  flex-shrink: 0;
}

.copy-md-ext {
  margin-left: auto;
  color: var(--vp-c-text-3, var(--vp-c-text-2));
  opacity: 0.5;
  flex-shrink: 0;
}

.dropdown-enter-active,
.dropdown-leave-active {
  transition: opacity 0.15s, transform 0.15s;
}
.dropdown-enter-from,
.dropdown-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
