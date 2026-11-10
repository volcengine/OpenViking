<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { useData } from 'vitepress'
const { lang } = useData()
let nav: HTMLElement | null
let previouslyInert: [HTMLElement, boolean][] = []
let previousRole: string | null
function hamburger() { return nav?.querySelector<HTMLButtonElement>('.VPNavBarHamburger') }
function keydown(event: KeyboardEvent) {
  if (event.defaultPrevented || !nav?.contains(event.target as Node)) return
  if (event.key === 'Escape') {
    event.preventDefault()
    hamburger()?.click()
    hamburger()?.focus()
  }
  if (event.key !== 'Tab') return
  const items = [...nav.querySelectorAll<HTMLElement>('a[href], button, summary, input')]
    .filter(item => item.getClientRects().length && !item.closest('[inert]') && !item.hasAttribute('disabled') && item.tabIndex >= 0)
  const first = items[0], last = items[items.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
onMounted(() => {
  nav = document.querySelector('.VPNav')
  previousRole = nav?.getAttribute('role') ?? null
  nav?.setAttribute('role', 'dialog')
  nav?.setAttribute('aria-modal', 'true')
  nav?.setAttribute('aria-label', lang.value.startsWith('zh') ? '站点导航' : 'Site navigation')
  previouslyInert = [...document.querySelectorAll<HTMLElement>('.VPContent, .VPSidebar, .VPLocalNav, .VPFooter')].map(item => [item, item.inert])
  previouslyInert.forEach(([item]) => { item.inert = true })
  nav?.querySelector<HTMLAnchorElement>('.VPNavScreenMenu a')?.focus()
  document.addEventListener('keydown', keydown)
})
onUnmounted(() => {
  document.removeEventListener('keydown', keydown)
  previouslyInert.forEach(([item, inert]) => { item.inert = inert })
  if (previousRole) nav?.setAttribute('role', previousRole)
  else nav?.removeAttribute('role')
  nav?.removeAttribute('aria-modal')
  nav?.removeAttribute('aria-label')
})
</script>
<template>
  <a class="ov-drawer-github" href="https://github.com/volcengine/OpenViking" target="_blank" rel="noreferrer">GitHub ↗</a>
</template>
