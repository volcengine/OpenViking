<script setup lang="ts">
import { nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vitepress'
import { watch } from 'vue'

const route = useRoute()
const STORAGE_KEY = 'openviking-api-example-language'
const CHANGE_EVENT = 'openviking-api-example-language-change'
let observer: MutationObserver | undefined
let frame = 0

function language(label: string) {
  if (/python/i.test(label)) return { key: 'python', label: 'Python' }
  if (/typescript|javascript/i.test(label)) return { key: 'typescript', label: 'TypeScript' }
  if (/go sdk/i.test(label)) return { key: 'go', label: 'Go' }
  if (/http api/i.test(label)) return { key: 'http', label: 'HTTP' }
  if (/^cli/i.test(label)) return { key: 'cli', label: 'CLI' }
  return undefined
}

function headingLanguage(element: Element) {
  if (!element.matches('p')) return undefined
  const strong = element.querySelector(':scope > strong:only-child')
  return strong ? language(strong.textContent?.trim() ?? '') : undefined
}

function activate(container: HTMLElement, key: string, broadcast = true) {
  const available = Array.from(container.querySelectorAll<HTMLElement>(':scope > [data-api-example]'))
  const selected = available.some((panel) => panel.dataset.apiExample === key)
    ? key
    : available[0]?.dataset.apiExample
  if (!selected) return

  for (const panel of available) panel.hidden = panel.dataset.apiExample !== selected
  for (const button of container.querySelectorAll<HTMLButtonElement>(':scope > [role="tablist"] button')) {
    const active = button.dataset.language === selected
    button.classList.toggle('is-active', active)
    button.setAttribute('aria-selected', String(active))
  }
  localStorage.setItem(STORAGE_KEY, selected)
  if (broadcast) window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: selected }))
}

function enhanceDocument() {
  const doc = document.querySelector('.vp-doc')
  if (!doc) return

  const headings = Array.from(doc.querySelectorAll('p')).filter(
    (element) => headingLanguage(element) && !element.closest('.api-example-tabs')
  )

  for (const firstHeading of headings) {
    if (!firstHeading.isConnected || firstHeading.closest('.api-example-tabs')) continue
    const parent = firstHeading.parentElement
    if (!parent) continue

    const groups: { language: { key: string; label: string }; nodes: Element[] }[] = []
    let node: Element | null = firstHeading
    while (node) {
      if (node.matches('h2, h3, h4, hr')) break
      const currentLanguage = headingLanguage(node)
      if (currentLanguage) {
        if (groups.some((group) => group.language.key === currentLanguage.key)) break
        groups.push({ language: currentLanguage, nodes: [node] })
      }
      else {
        if (!groups.length) break
        groups.at(-1)?.nodes.push(node)
      }
      node = node.nextElementSibling
    }

    if (groups.length < 2) continue
    const container = document.createElement('div')
    container.className = 'api-example-tabs'
    const tablist = document.createElement('div')
    tablist.className = 'api-example-tabs__tablist'
    tablist.setAttribute('role', 'tablist')
    tablist.setAttribute('aria-label', 'API examples')
    container.append(tablist)
    parent.insertBefore(container, firstHeading)

    for (const group of groups) {
      const button = document.createElement('button')
      button.type = 'button'
      button.setAttribute('role', 'tab')
      button.dataset.language = group.language.key
      button.textContent = group.language.label
      button.addEventListener('click', () => {
        const anchorTop = tablist.getBoundingClientRect().top
        activate(container, group.language.key)
        const offset = tablist.getBoundingClientRect().top - anchorTop
        if (offset) window.scrollBy({ top: offset, behavior: 'instant' })
      })
      tablist.append(button)

      const panel = document.createElement('div')
      panel.dataset.apiExample = group.language.key
      panel.className = 'api-example-tabs__panel'
      for (const child of group.nodes) panel.append(child)
      container.append(panel)
    }
    activate(container, localStorage.getItem(STORAGE_KEY) ?? groups[0].language.key, false)
  }
}

function scheduleEnhancement() {
  window.cancelAnimationFrame(frame)
  frame = window.requestAnimationFrame(enhanceDocument)
}

function sync(event: Event) {
  const key = (event as CustomEvent<string>).detail
  for (const container of document.querySelectorAll<HTMLElement>('.api-example-tabs')) {
    activate(container, key, false)
  }
}

async function enhanceAfterRender() {
  await nextTick()
  scheduleEnhancement()
}

watch(() => route.path, enhanceAfterRender)
onMounted(() => {
  enhanceAfterRender()
  observer = new MutationObserver(scheduleEnhancement)
  const content = document.querySelector('.VPContent')
  if (content) observer.observe(content, { childList: true, subtree: true })
  window.addEventListener(CHANGE_EVENT, sync)
})
onUnmounted(() => {
  observer?.disconnect()
  window.cancelAnimationFrame(frame)
  window.removeEventListener(CHANGE_EVENT, sync)
})
</script>

<template><span class="api-example-tabs-enhancer" aria-hidden="true" /></template>
