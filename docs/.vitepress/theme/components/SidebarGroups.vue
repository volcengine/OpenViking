<script setup lang="ts">
import { useData, withBase } from 'vitepress'
import type { DefaultTheme } from 'vitepress/theme'

// docs-sections owns a flat list of pages within each editorial group.
// Group headings describe the links; they are neither folders nor controls.
defineProps<{ items: DefaultTheme.SidebarItem[] }>()
const { page } = useData()
// Brand assets match the website integrations; keep monochrome marks legible in dark mode.
const brands: Record<string, { file: string; invert?: boolean; dark?: string }> = {
  '02-claude-code': { file: 'claude-code.svg' },
  '04-codex': { file: 'codex.svg', invert: true },
  '12-cursor': { file: 'cursor.svg', invert: true },
  '13-trae': { file: 'trae.svg' },
  '10-opencode': { file: 'opencode.svg', invert: true },
  '17-dsh': { file: 'logos/dsh.svg', dark: 'logos/dsh-dark.svg' },
  '03-openclaw': { file: 'openclaw.jpg' },
  '05-hermes': { file: 'hermes-agent.png' },
  '11-pi': { file: 'pi.svg', invert: true },
  '07-langchain-langgraph': { file: 'langchain.svg' }
}
function brand(link?: string) {
  return brands[link?.split('/').at(-1) ?? '']
}
function normalize(path: string) {
  return path.split(/[?#]/)[0].replace(/^\//, '').replace(/\.(md|html)$/, '').replace(/\/$/, '')
}
function active(link?: string) {
  return !!link && normalize(link) === normalize(page.value.relativePath)
}
</script>

<template>
  <section v-for="group in items" :key="group.text" class="group ov-sidebar-group">
    <h2 v-if="group.text" class="ov-sidebar-heading">{{ group.text }}</h2>
    <ul class="ov-sidebar-pages">
      <li v-for="item in group.items" :key="item.link">
        <a v-if="item.link" class="ov-sidebar-page" :class="{ 'has-brand': group.items?.some(entry => brand(entry.link)) }" :href="withBase(item.link)"
          :aria-current="active(item.link) ? 'page' : undefined" :target="item.target" :rel="item.rel">
          <span v-if="brand(item.link)" class="ov-sidebar-brand" aria-hidden="true">
            <img :src="withBase('/integrations/' + brand(item.link).file)" alt="" width="18" height="18"
              :class="{ 'invert-dark': brand(item.link).invert, 'light-only': brand(item.link).dark }" />
            <img v-if="brand(item.link).dark" :src="withBase('/integrations/' + brand(item.link).dark)"
              alt="" width="18" height="18" class="dark-only" />
          </span>
          <svg v-else-if="group.items?.some(entry => brand(entry.link))" class="ov-sidebar-brand" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /><circle cx="9" cy="7" r="4" />
          </svg>
          <span v-html="item.text" />
        </a>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.ov-sidebar-group { margin-inline: -8px; padding: 20px 0 4px; }
.ov-sidebar-group + .ov-sidebar-group { padding-top: 24px; }
.ov-sidebar-group:last-of-type { padding-bottom: 24px; }
.ov-sidebar-heading { margin: 0 0 8px; padding: 0 10px; color: #6d706b; font-size: 12px; font-weight: 700; line-height: 20px; letter-spacing: .06em; cursor: default; }
.dark .ov-sidebar-heading { color: #a3a59e; }
.ov-sidebar-pages { list-style: none; margin: 0; padding: 0; }
.ov-sidebar-pages li + li { margin-top: 2px; }
.ov-sidebar-page { display: block; padding: 6px 8px; border-left: 2px solid transparent; border-radius: 4px; color: var(--vp-c-text-1); font-size: 14px; font-weight: 400; line-height: 22px; overflow-wrap: anywhere; }
.ov-sidebar-page[aria-current=page] { border-left-color: var(--vp-c-brand-1); background: var(--vp-c-brand-soft); color: var(--vp-c-brand-1); font-weight: 500; }
.ov-sidebar-page:focus-visible { outline: 2px solid var(--vp-c-brand-1); outline-offset: 2px; }
.ov-sidebar-page.has-brand { display: flex; align-items: flex-start; gap: 10px; }
.ov-sidebar-brand { display: block; flex: 0 0 18px; width: 18px; height: 18px; margin-top: 2px; }
.ov-sidebar-brand img { display: block; width: 18px; height: 18px; object-fit: contain; }
.ov-sidebar-brand img[src$="/pi.svg"] { transform: scale(1.6); }
.ov-sidebar-brand .dark-only { display: none; }
.dark .ov-sidebar-brand .invert-dark { filter: invert(1); }
.dark .ov-sidebar-brand .light-only { display: none; }
.dark .ov-sidebar-brand .dark-only { display: block; }
@media (hover: hover) { .ov-sidebar-page:hover { background: var(--vp-c-bg-soft); color: var(--vp-c-text-1); } }
</style>
