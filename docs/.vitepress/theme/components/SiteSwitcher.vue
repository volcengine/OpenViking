<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useData, withBase } from 'vitepress'

const { lang, page } = useData()
const zh = computed(() => lang.value.startsWith('zh'))
const home = computed(() => withBase(zh.value ? '/zh/' : '/en/'))
const root = ref<HTMLElement>()
const trigger = ref<HTMLButtonElement>()
const panel = ref<HTMLElement>()
const open = ref(false)
let hoverOpened = false
let timer: ReturnType<typeof setTimeout> | undefined
const sites = computed(() => [
  { label: zh.value ? '官网' : 'Website', description: zh.value ? '认识 OpenViking' : 'Discover OpenViking', href: 'https://www.openviking.ai/', icon: 'globe' },
  { label: 'Docs', description: zh.value ? '学习、接入与开发' : 'Learn, integrate and build', href: home.value, icon: 'book' },
  { label: 'Blog', description: zh.value ? '产品进展与实践' : 'Updates and field notes', href: 'https://blog.openviking.ai/', icon: 'article' },
  { label: 'GitHub', description: zh.value ? '源码、Issues 与贡献' : 'Source, issues and contributions', href: 'https://github.com/volcengine/OpenViking', icon: 'github' }
])
function clearTimer() { clearTimeout(timer) }
function close(restoreFocus = false) {
  clearTimer()
  open.value = false
  hoverOpened = false
  if (restoreFocus) trigger.value?.focus()
}
function enter(event: PointerEvent) {
  clearTimer()
  if (event.pointerType !== 'mouse' || !matchMedia('(hover: hover)').matches || open.value) return
  timer = setTimeout(() => { hoverOpened = true; open.value = true }, 120)
}
function leave() {
  clearTimer()
  timer = setTimeout(() => {
    if (!root.value?.contains(document.activeElement)) close()
  }, 180)
}
function toggle() {
  clearTimer()
  if (hoverOpened) { hoverOpened = false; return }
  open.value = !open.value
}
async function focusFirst() {
  clearTimer()
  hoverOpened = false
  open.value = true
  await nextTick()
  panel.value?.querySelector('a')?.focus()
}
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && open.value) {
    event.preventDefault()
    event.stopPropagation()
    close(true)
  }
  if (!open.value || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
  const links = Array.from(panel.value?.querySelectorAll<HTMLAnchorElement>('a') ?? [])
  const index = links.indexOf(document.activeElement as HTMLAnchorElement)
  if (index < 0) return
  event.preventDefault()
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? links.length - 1
    : (index + (event.key === 'ArrowDown' ? 1 : -1) + links.length) % links.length
  links[next]?.focus()
}
function outside(event: PointerEvent) {
  if (!root.value?.contains(event.target as Node)) close()
}
function blur(event: FocusEvent) {
  if (!root.value?.contains(event.relatedTarget as Node)) close()
}
watch(() => page.value.relativePath, () => close())
onMounted(() => document.addEventListener('pointerdown', outside))
onUnmounted(() => { clearTimer(); document.removeEventListener('pointerdown', outside) })
</script>

<template>
  <div ref="root" class="ov-site-switcher" @pointerenter="enter" @pointerleave="leave" @focusout="blur" @keydown="onKeydown">
    <a class="ov-brand-link" :href="home" :aria-label="zh ? 'OpenViking 文档首页' : 'OpenViking documentation home'" @click="close()">
      <img class="ov-brand-light" :src="withBase('/brand-lockup-light.svg')" alt="OpenViking" width="136" height="26" />
      <img class="ov-brand-dark" :src="withBase('/brand-lockup-dark.svg')" alt="OpenViking" width="136" height="26" />
    </a>
    <span class="ov-brand-divider" aria-hidden="true">/</span>
    <button ref="trigger" type="button" class="ov-site-trigger" :aria-label="zh ? '切换站点' : 'Switch site'"
      :aria-expanded="open" aria-controls="ov-site-links" @click="toggle" @keydown.down.prevent.stop="focusFirst">
      <span>Docs</span>
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg>
    </button>
    <nav v-show="open" id="ov-site-links" ref="panel" class="ov-site-panel" :aria-label="zh ? 'OpenViking 站点' : 'OpenViking sites'">
      <a v-for="site in sites" :key="site.icon" :href="site.href" class="ov-site-option" :class="{ 'is-current': site.icon === 'book' }"
        :aria-current="site.icon === 'book' ? 'true' : undefined" @click="close()">
        <span class="ov-site-icon" aria-hidden="true">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <template v-if="site.icon === 'globe'"><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z" /></template>
            <path v-else-if="site.icon === 'book'" d="M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Zm0 0v15" />
            <path v-else-if="site.icon === 'github'" fill="currentColor" stroke="none" d="M12 0C5.37 0 0 5.37 0 12c0 5.3 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61-.546-1.385-1.335-1.755-1.335-1.755-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 21.795 24 17.295 24 12c0-6.63-5.37-12-12-12z" />
            <template v-else><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h5" /></template>
          </svg>
        </span>
        <span class="ov-site-copy"><span class="ov-site-name">{{ site.label }}</span><span class="ov-site-description">{{ site.description }}</span></span>
        <svg v-if="site.icon === 'book'" class="ov-site-status" width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m4 10 4 4 8-8" /></svg>
        <span v-else class="ov-site-arrow" aria-hidden="true">↗</span>
      </a>
    </nav>
  </div>
</template>

<style scoped>
.ov-site-switcher { position: relative; display: flex; align-items: center; gap: 10px; height: 64px; white-space: nowrap; }
.ov-brand-link { display: flex; align-items: center; flex-shrink: 0; min-height: 36px; border-radius: 5px; }
.ov-brand-link img { display: block; width: 136px; height: auto; }
.ov-brand-link .ov-brand-dark { display: none; }
:global(.dark .ov-brand-link .ov-brand-light) { display: none; }
:global(.dark .ov-brand-link .ov-brand-dark) { display: block; }
.ov-brand-divider { color: var(--vp-c-divider); font-size: 23px; font-weight: 300; }
.ov-site-trigger { display: flex; align-items: center; gap: 6px; min-height: 36px; padding: 0 4px; border-radius: 6px; font: 400 13px/20px var(--vp-font-family-base); letter-spacing: 0; color: var(--vp-c-text-2); cursor: pointer; }
.ov-site-trigger[aria-expanded=true] { color: var(--vp-c-text-1); background: var(--vp-c-bg-soft); }
.ov-site-trigger svg { transition: transform 160ms ease-out; }
.ov-site-trigger[aria-expanded=true] svg { transform: rotate(180deg); }
.ov-site-panel { position: absolute; top: calc(100% - 3px); left: 0; z-index: 110; width: min(292px, calc(100vw - 32px)); padding: 8px; border: 1px solid var(--vp-c-divider); border-radius: 14px; background: var(--vp-c-bg-elv); box-shadow: 0 12px 36px #00000014, 0 2px 8px #00000008; }
.ov-site-option { display: flex; align-items: center; gap: 12px; padding: 11px 10px; border-radius: 8px; color: var(--vp-c-text-1); }
.ov-site-option + .ov-site-option { margin-top: 3px; }
.ov-site-option.is-current { background: var(--vp-c-brand-soft); }
.ov-site-icon { display: grid; place-items: center; flex-shrink: 0; width: 36px; height: 36px; border: 1px solid var(--vp-c-divider); border-radius: 9px; color: var(--vp-c-text-2); }
.is-current .ov-site-icon { color: var(--vp-c-brand-1); border-color: transparent; background: var(--vp-c-bg); }
.ov-site-copy { display: flex; flex: 1; flex-direction: column; gap: 3px; }
.ov-site-name { font-size: 14px; font-weight: 600; line-height: 1.3; }
.ov-site-description { font-size: 12px; line-height: 1.4; color: var(--vp-c-text-2); }
.ov-site-arrow { color: var(--vp-c-text-3); font-size: 16px; }
.ov-site-status { color: var(--vp-c-brand-1); }
a:focus-visible, button:focus-visible { outline: 2px solid var(--vp-c-brand-1); outline-offset: 3px; }
@media (hover: hover) { .ov-site-option:hover, .ov-site-trigger:hover { background: var(--vp-c-bg-soft); } }
@media (prefers-reduced-motion: reduce) { .ov-site-trigger svg { transition: none; } }
@media (max-width: 720px) { .ov-site-switcher { height: 56px; gap: 4px; } .ov-brand-divider, .ov-site-trigger span { display: none; } .ov-site-trigger { padding: 0 2px; width: 16px; } }
@media (max-width: 374px) { .ov-brand-link img { width: 112px; } }
</style>
