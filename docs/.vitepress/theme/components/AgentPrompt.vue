<script lang="ts">
import { ref } from 'vue'

// One choice for the whole page: every prompt with the same editions follows
// the switch the reader last used, and the choice outlives the visit.
const STORAGE_KEY = 'ov-agent-prompt-edition'
const sharedEdition = ref('')
</script>

<script setup lang="ts">
// Collapsed prompt block whose header can copy the slotted code block
// without opening it. The prompt stays in the page DOM (and in llms.txt).
//
// With `editions="Personal|Team"` (and matching `ids="personal|team"`), the
// slot holds one code block per edition, in that order; the header gets a
// switch, only the chosen block shows, and Copy copies that one. A link can
// preselect an edition with `?edition=<id>`.
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useData } from 'vitepress'
import { trackEvent } from '../track'

const props = defineProps<{ label?: string; editions?: string; ids?: string; editionLabel?: string }>()
const { lang } = useData()
const zh = computed(() => lang.value.startsWith('zh'))
const root = ref<HTMLDetailsElement>()
const body = ref<HTMLDivElement>()
const copied = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined

const split = (value?: string) => (value ?? '').split('|').map((part) => part.trim()).filter(Boolean)
const editionLabels = computed(() => split(props.editions))
const editionIds = computed(() => {
  const ids = split(props.ids)
  return editionLabels.value.map((label, index) => ids[index] ?? String(index))
})
const hasEditions = computed(() => editionLabels.value.length > 1)
const current = computed(() =>
  editionIds.value.includes(sharedEdition.value) ? sharedEdition.value : (editionIds.value[0] ?? ''),
)

function blocks(): HTMLElement[] {
  return Array.from(body.value?.querySelectorAll<HTMLElement>(':scope > div[class*="language-"]') ?? [])
}

function showCurrent() {
  if (!hasEditions.value) return
  const active = editionIds.value.indexOf(current.value)
  blocks().forEach((block, index) => {
    block.style.display = index === active ? '' : 'none'
  })
}

function select(id: string, focus = false) {
  sharedEdition.value = id
  try { localStorage.setItem(STORAGE_KEY, id) } catch { /* Storage may be unavailable. */ }
  if (focus) nextTick(() => root.value?.querySelector<HTMLButtonElement>('.agent-prompt-edition[aria-checked="true"]')?.focus())
}

function onEditionKey(event: KeyboardEvent) {
  const step = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1
    : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0
  if (!step) return
  event.preventDefault()
  const ids = editionIds.value
  select(ids[(ids.indexOf(current.value) + step + ids.length) % ids.length], true)
}

onMounted(() => {
  if (hasEditions.value && !sharedEdition.value) {
    let initial = new URLSearchParams(location.search).get('edition') ?? ''
    if (!editionIds.value.includes(initial)) {
      try { initial = localStorage.getItem(STORAGE_KEY) ?? '' } catch { initial = '' }
    }
    if (editionIds.value.includes(initial)) sharedEdition.value = initial
  }
  showCurrent()
})
watch(current, showCurrent, { flush: 'post' })

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
  const block = hasEditions.value ? blocks()[editionIds.value.indexOf(current.value)] : undefined
  const code = (block ?? root.value)?.querySelector('pre code')
  if (!code) return
  await writeText((code.textContent ?? '').replace(/\n$/, ''))
  trackEvent('copy-agent-prompt', hasEditions.value ? { edition: current.value } : undefined)
  copied.value = true
  clearTimeout(timer)
  timer = setTimeout(() => { copied.value = false }, 2000)
}
</script>

<template>
  <details ref="root" class="details custom-block agent-prompt">
    <summary>
      <span class="agent-prompt-label">{{ props.label ?? (zh ? '给 Agent 的提示词' : 'Prompt for your agent') }}</span>
      <span
        v-if="hasEditions"
        class="agent-prompt-editions"
        role="radiogroup"
        :aria-label="props.editionLabel ?? (zh ? '版本' : 'Edition')"
        @click.prevent.stop
        @keydown.enter.stop
        @keydown.space.stop
        @keydown="onEditionKey"
      >
        <button
          v-for="(edition, index) in editionLabels"
          :key="editionIds[index]"
          type="button"
          role="radio"
          class="agent-prompt-edition"
          :aria-checked="current === editionIds[index]"
          :tabindex="current === editionIds[index] ? 0 : -1"
          @click.prevent.stop="select(editionIds[index])"
        >{{ edition }}</button>
      </span>
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
    <div ref="body" class="agent-prompt-body">
      <slot />
    </div>
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
.agent-prompt-editions {
  display: inline-flex;
  padding: 2px;
  border: 1px solid var(--vp-c-divider);
  border-radius: 8px;
  background: var(--vp-c-default-soft);
  cursor: default;
}
.agent-prompt-edition {
  padding: 1px 12px;
  border-radius: 6px;
  color: var(--vp-c-text-2);
  font-size: 13px;
  font-weight: 500;
  line-height: 22px;
  white-space: nowrap;
  transition: background-color 0.2s, color 0.2s, box-shadow 0.2s;
}
.agent-prompt-edition:hover {
  color: var(--vp-c-text-1);
}
.agent-prompt-edition[aria-checked='true'] {
  background: var(--vp-c-bg);
  color: var(--vp-c-brand-1);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
}
.agent-prompt-edition:focus-visible {
  outline: 2px solid var(--vp-c-brand-1);
  outline-offset: 1px;
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
@media (max-width: 480px) {
  .agent-prompt > summary {
    flex-wrap: wrap;
    row-gap: 8px;
  }
  .agent-prompt-label {
    flex-basis: calc(100% - 24px);
  }
  .agent-prompt-editions {
    margin-inline-start: 18px;
  }
  .agent-prompt-copy {
    margin-inline-start: auto;
  }
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

<style>
/* Edition blocks sit in one place: undo the theme's pull-up for adjacent code blocks. */
.vp-doc .agent-prompt .agent-prompt-body > div[class*='language-'] {
  margin: 8px 0;
}
</style>
