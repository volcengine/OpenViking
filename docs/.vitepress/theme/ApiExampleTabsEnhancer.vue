<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vitepress'
import {
  exampleLanguage,
  isApiReferencePath,
  isSharedSectionLabel,
  preferredLanguage,
  type ExampleLanguage
} from './api-example-tabs'

const route = useRoute()
const STORAGE_KEY = 'openviking-api-example-language'
const CHANGE_EVENT = 'openviking-api-example-language-change'
let observer: MutationObserver | undefined
let frame = 0
let tabGroup = 0

function strongOnlyChildLabel(element: Element) {
  if (!element.matches('p')) return undefined
  const strong = element.querySelector(':scope > strong:only-child')
  return strong?.textContent?.trim()
}

function headingLanguage(element: Element) {
  const label = strongOnlyChildLabel(element)
  return label ? exampleLanguage(label) : undefined
}

function isSharedSection(element: Element) {
  const label = strongOnlyChildLabel(element)
  return label ? isSharedSectionLabel(label) : false
}

function activate(container: HTMLElement, key: string, broadcast = true) {
  const available = Array.from(
    container.querySelectorAll<HTMLElement>(':scope > [data-api-example]')
  )
  const selected = available.some((panel) => panel.dataset.apiExample === key)
    ? key
    : available[0]?.dataset.apiExample
  if (!selected) return

  for (const panel of available) panel.hidden = panel.dataset.apiExample !== selected
  const buttons = container.querySelectorAll<HTMLButtonElement>(
    ':scope > [role="tablist"] button'
  )
  for (const button of buttons) {
    const active = button.dataset.language === selected
    button.classList.toggle('is-active', active)
    button.setAttribute('aria-selected', String(active))
    button.tabIndex = active ? 0 : -1
  }
  if (broadcast) {
    localStorage.setItem(STORAGE_KEY, selected)
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: selected }))
  }
}

function handleTabKeydown(event: KeyboardEvent, container: HTMLElement) {
  const buttons = Array.from(
    container.querySelectorAll<HTMLButtonElement>(':scope > [role="tablist"] button')
  )
  const currentIndex = buttons.indexOf(event.currentTarget as HTMLButtonElement)
  if (currentIndex < 0) return

  let nextIndex: number | undefined
  if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + buttons.length) % buttons.length
  else if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % buttons.length
  else if (event.key === 'Home') nextIndex = 0
  else if (event.key === 'End') nextIndex = buttons.length - 1
  if (nextIndex === undefined) return

  event.preventDefault()
  const nextButton = buttons[nextIndex]
  nextButton.focus()
  activate(container, nextButton.dataset.language ?? '')
}

function enhanceDocument() {
  if (!isApiReferencePath(route.path)) return
  const doc = document.querySelector('.vp-doc')
  if (!doc) return
  const storedLanguage = localStorage.getItem(STORAGE_KEY)
  let initialLanguage: string | undefined

  const headings = Array.from(doc.querySelectorAll('p')).filter(
    (element) => headingLanguage(element) && !element.closest('.api-example-tabs')
  )

  for (const firstHeading of headings) {
    if (!firstHeading.isConnected || firstHeading.closest('.api-example-tabs')) continue
    const parent = firstHeading.parentElement
    if (!parent) continue

    const groups: { language: ExampleLanguage; nodes: Element[] }[] = []
    let node: Element | null = firstHeading
    while (node) {
      if (node.matches('h2, h3, h4, hr')) break
      if (groups.length && isSharedSection(node)) break
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
    const groupId = `api-example-tabs-${tabGroup++}`

    for (const group of groups) {
      const button = document.createElement('button')
      button.type = 'button'
      button.setAttribute('role', 'tab')
      button.id = `${groupId}-tab-${group.language.key}`
      button.setAttribute('aria-controls', `${groupId}-panel-${group.language.key}`)
      button.dataset.language = group.language.key
      button.textContent = group.language.label
      button.addEventListener('click', () => {
        const anchorTop = tablist.getBoundingClientRect().top
        activate(container, group.language.key)
        const offset = tablist.getBoundingClientRect().top - anchorTop
        if (offset) window.scrollBy({ top: offset, behavior: 'instant' })
      })
      button.addEventListener('keydown', (event) => handleTabKeydown(event, container))
      tablist.append(button)

      const panel = document.createElement('div')
      panel.id = `${groupId}-panel-${group.language.key}`
      panel.setAttribute('role', 'tabpanel')
      panel.setAttribute('aria-labelledby', button.id)
      panel.dataset.apiExample = group.language.key
      panel.className = 'api-example-tabs__panel'
      for (const child of group.nodes) panel.append(child)
      container.append(panel)
    }
    initialLanguage = preferredLanguage(
      storedLanguage,
      initialLanguage,
      groups[0].language.key
    )
    activate(container, initialLanguage, false)
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
