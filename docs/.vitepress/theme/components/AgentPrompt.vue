<script setup lang="ts">
// Collapsed prompt block whose header can copy the slotted code block
// without opening it. The prompt stays in the page DOM (and in llms.txt).
import { computed, ref } from 'vue'
import { useData } from 'vitepress'

const props = defineProps<{ label?: string }>()
const { lang } = useData()
const zh = computed(() => lang.value.startsWith('zh'))
const root = ref<HTMLDetailsElement>()
const copied = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined

async function writeText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.cssText = 'position:fixed;opacity:0'
    document.body.appendChild(area)
    area.select()
    document.execCommand('copy')
    area.remove()
  }
}

async function copy() {
  const code = root.value?.querySelector('pre code')
  if (!code) return
  await writeText((code.textContent ?? '').replace(/\n$/, ''))
  copied.value = true
  clearTimeout(timer)
  timer = setTimeout(() => { copied.value = false }, 2000)
}
</script>

<template>
  <details ref="root" class="details custom-block agent-prompt">
    <summary>
      <span class="agent-prompt-label">{{ props.label ?? (zh ? '给 Agent 的提示词' : 'Prompt for your agent') }}</span>
      <button
        type="button"
        class="agent-prompt-copy"
        :class="{ copied }"
        :aria-label="zh ? '复制 Agent 提示词' : 'Copy the agent prompt'"
        @click.prevent.stop="copy"
        @keydown.enter.stop
        @keydown.space.stop
      >{{ copied ? (zh ? '已复制' : 'Copied') : (zh ? '复制' : 'Copy') }}</button>
      <span class="sr-only" aria-live="polite">{{ copied ? (zh ? '已复制' : 'Copied') : '' }}</span>
    </summary>
    <slot />
  </details>
</template>

<style scoped>
.agent-prompt > summary {
  display: flex;
  align-items: center;
  gap: 12px;
}
.agent-prompt > summary::-webkit-details-marker {
  display: none;
}
.agent-prompt > summary::before {
  content: '';
  width: 0;
  height: 0;
  border-block: 5px solid transparent;
  border-inline-start: 6px solid currentColor;
  transition: transform 0.2s;
}
.agent-prompt[open] > summary::before {
  transform: rotate(90deg);
}
.agent-prompt-label {
  flex: 1;
}
.agent-prompt-copy {
  padding: 2px 12px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 6px;
  background: var(--vp-c-bg);
  color: var(--vp-c-text-1);
  font-size: 13px;
  font-weight: 500;
  line-height: 24px;
  transition: border-color 0.2s, color 0.2s;
}
.agent-prompt-copy:hover,
.agent-prompt-copy:focus-visible {
  border-color: var(--vp-c-brand-1);
  color: var(--vp-c-brand-1);
}
.agent-prompt-copy.copied {
  border-color: var(--vp-c-brand-1);
  color: var(--vp-c-brand-1);
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
