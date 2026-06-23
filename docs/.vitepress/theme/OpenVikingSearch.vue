<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useData, withBase } from 'vitepress'

import { searchCopyForLocale } from './openviking-search-i18n'
import { localizeSearchResultTitles } from './openviking-search-results'
import type { RemoteSearchFailureReason } from './openviking-search-i18n'
import type {
  DocsIndexRecord,
  DocsSearchResult,
  SearchLocale,
  SearchMode
} from './openviking-search-results'

type ResolvedDocsSearchResult = DocsSearchResult & {
  cleanedSnippet: string
  resolvedUrl: string
}

type RemoteSearchResponse =
  | { ok: true; results: DocsSearchResult[] }
  | { ok: false; reason: RemoteSearchFailureReason }

const PRODUCTION_SEARCH_URL = 'https://openviking.ai/studio/gateway/docs/search'
const SEARCH_LIMIT = 8
const REMOTE_SEARCH_DEBOUNCE_MS = 1000
const REMOTE_SEARCH_TIMEOUT_MS = 15000

const modeDefinitions: { command: string; value: SearchMode }[] = [
  { command: '/find', value: 'semantic' },
  { command: '/grep', value: 'keyword' },
  { command: '/glob', value: 'file' }
]

const { lang } = useData()

const inputRef = ref<HTMLInputElement | null>(null)
const triggerRef = ref<HTMLButtonElement | null>(null)
const dialogRef = ref<HTMLElement | null>(null)
const isOpen = ref(false)
const isMounted = ref(false)
const isLoading = ref(false)
const isModeMenuOpen = ref(false)
const mode = ref<SearchMode>('semantic')
const query = ref('')
const results = ref<DocsSearchResult[]>([])
const notice = ref('')

let debounceTimer: ReturnType<typeof window.setTimeout> | null = null
let searchSequence = 0
let localIndexPromise: Promise<DocsIndexRecord[]> | null = null
let activeSearchController: AbortController | null = null

const trimmedQuery = computed(() => query.value.trim())
const searchCopy = computed(() => searchCopyForLocale(docsLocale()))
const modes = computed(() =>
  modeDefinitions.map((item) => ({
    ...item,
    ...searchCopy.value.modes[item.value]
  }))
)
const activeMode = computed(() => modes.value.find((item) => item.value === mode.value) ?? modes.value[0])
const resolvedResults = computed<ResolvedDocsSearchResult[]>(() =>
  results.value.flatMap((result) => {
    const resolvedUrl = resultUrl(result)
    return resolvedUrl ? [{ ...result, cleanedSnippet: cleanSearchSnippet(result.snippet), resolvedUrl }] : []
  })
)

function docsLocale(): SearchLocale {
  const currentLang = String(lang.value ?? '').toLowerCase()
  if (currentLang.startsWith('zh')) return 'zh'
  if (currentLang.startsWith('en')) return 'en'

  if (typeof window === 'undefined') return 'en'

  const basePath = normalizeBasePath(import.meta.env.BASE_URL)
  const pathname = window.location.pathname
  const localizedPath = pathname.startsWith(basePath)
    ? pathname.slice(basePath.length - 1)
    : pathname

  return localizedPath.startsWith('/zh/') ? 'zh' : 'en'
}

function normalizeBasePath(basePath: string) {
  if (!basePath || basePath === '/') return '/'
  return `/${basePath.replace(/^\/+|\/+$/g, '')}/`
}

function openSearch() {
  isOpen.value = true
  void nextTick(() => inputRef.value?.focus())
  scheduleSearch()
}

function closeSearch() {
  const shouldRestoreFocus = isOpen.value
  isOpen.value = false
  isModeMenuOpen.value = false
  clearPendingSearch()
  cancelActiveSearch()
  if (shouldRestoreFocus) {
    void nextTick(() => triggerRef.value?.focus())
  }
}

function toggleModeMenu() {
  isModeMenuOpen.value = !isModeMenuOpen.value
}

function selectMode(value: SearchMode) {
  mode.value = value
  isModeMenuOpen.value = false
  void nextTick(() => inputRef.value?.focus())
}

function shouldIgnoreShortcut(event: KeyboardEvent) {
  const target = event.target
  if (!(target instanceof HTMLElement)) return false

  const tagName = target.tagName.toLowerCase()
  return target.isContentEditable || ['input', 'textarea', 'select'].includes(tagName)
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && isModeMenuOpen.value) {
    isModeMenuOpen.value = false
    return
  }

  if (event.key === 'Escape' && isOpen.value) {
    closeSearch()
    return
  }

  const opensWithSlash = event.key === '/' && !shouldIgnoreShortcut(event)
  const opensWithK = event.key.toLowerCase() === 'k' && (event.metaKey || event.ctrlKey)
  if (!opensWithSlash && !opensWithK) return

  event.preventDefault()
  openSearch()
}

function handleDialogKeydown(event: KeyboardEvent) {
  if (event.key !== 'Tab') return

  const focusable = dialogFocusableElements()
  if (focusable.length === 0) {
    event.preventDefault()
    return
  }

  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  const activeElement = document.activeElement

  if (event.shiftKey && activeElement === first) {
    event.preventDefault()
    last.focus()
    return
  }

  if (!event.shiftKey && activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

function dialogFocusableElements() {
  const elements = dialogRef.value?.querySelectorAll<HTMLElement>(
    [
      'a[href]',
      'button:not([disabled])',
      'input:not([disabled])',
      'select:not([disabled])',
      'textarea:not([disabled])',
      '[tabindex]:not([tabindex="-1"])'
    ].join(',')
  )

  return Array.from(elements ?? []).filter((element) => element.tabIndex >= 0)
}

function scheduleSearch() {
  clearPendingSearch()

  debounceTimer = window.setTimeout(() => {
    void runSearch()
  }, REMOTE_SEARCH_DEBOUNCE_MS)
}

async function runSearch() {
  cancelActiveSearch()
  const currentQuery = trimmedQuery.value
  const currentSequence = ++searchSequence
  notice.value = ''

  if (!currentQuery) {
    results.value = []
    isLoading.value = false
    return
  }

  isLoading.value = true
  const controller = new AbortController()
  activeSearchController = controller

  try {
    const remoteResponse = await fetchRemoteResults(
      PRODUCTION_SEARCH_URL,
      currentQuery,
      mode.value,
      docsLocale(),
      controller
    )

    if (currentSequence !== searchSequence) return

    if (remoteResponse?.ok) {
      results.value = await localizeRemoteResultTitles(remoteResponse.results, docsLocale())
      return
    }

    const localResults = await searchLocalIndex(currentQuery, mode.value, docsLocale())
    if (currentSequence !== searchSequence) return

    results.value = localResults
    if (remoteResponse && !remoteResponse.ok) {
      notice.value = fallbackNotice(remoteResponse.reason, localResults.length)
    }
  } finally {
    if (activeSearchController === controller) {
      activeSearchController = null
    }
    if (currentSequence === searchSequence) {
      isLoading.value = false
    }
  }
}

async function localizeRemoteResultTitles(
  searchResults: DocsSearchResult[],
  locale: SearchLocale
) {
  return localizeSearchResultTitles(searchResults, locale, loadLocalIndex)
}

async function fetchRemoteResults(
  endpoint: string,
  searchQuery: string,
  searchMode: SearchMode,
  locale: SearchLocale,
  controller: AbortController
) {
  const timeout = window.setTimeout(() => {
    controller.abort('timeout')
  }, REMOTE_SEARCH_TIMEOUT_MS)

  try {
    const response = await fetch(endpoint, {
      body: JSON.stringify({
        limit: SEARCH_LIMIT,
        locale,
        mode: searchMode,
        query: searchQuery
      }),
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json'
      },
      method: 'POST',
      signal: controller.signal
    })

    if (response.status === 429) {
      return { ok: false, reason: 'rate_limited' } satisfies RemoteSearchResponse
    }
    if (!response.ok) {
      return { ok: false, reason: 'unavailable' } satisfies RemoteSearchResponse
    }

    const payload = (await response.json()) as { results?: DocsSearchResult[] }
    return {
      ok: true,
      results: Array.isArray(payload.results) ? payload.results : []
    } satisfies RemoteSearchResponse
  } catch (error) {
    const isTimeout =
      controller.signal.reason === 'timeout' ||
      (error instanceof DOMException && error.name === 'TimeoutError')
    return {
      ok: false,
      reason: isTimeout ? 'timeout' : 'unavailable'
    } satisfies RemoteSearchResponse
  } finally {
    window.clearTimeout(timeout)
  }
}

function cancelActiveSearch() {
  if (activeSearchController && !activeSearchController.signal.aborted) {
    activeSearchController.abort('superseded')
  }
  activeSearchController = null
  searchSequence += 1
}

function clearPendingSearch() {
  if (debounceTimer) {
    window.clearTimeout(debounceTimer)
    debounceTimer = null
  }
}

function fallbackNotice(reason: RemoteSearchFailureReason, localResultCount: number) {
  return searchCopy.value.notice(reason, localResultCount)
}

async function loadLocalIndex() {
  localIndexPromise ??= fetch(withBase('/docs-search-index.json'))
    .then((response) => {
      if (!response.ok) {
        localIndexPromise = null
        return []
      }

      return response.json()
    })
    .then((payload) => (Array.isArray(payload) ? (payload as DocsIndexRecord[]) : []))
    .catch((error) => {
      localIndexPromise = null
      console.warn('Failed to load local docs search index.', error)
      return []
    })

  return localIndexPromise
}

async function searchLocalIndex(
  searchQuery: string,
  searchMode: SearchMode,
  locale: SearchLocale
) {
  const index = await loadLocalIndex()
  const normalizedQuery = searchQuery.toLowerCase()

  return index
    .filter((item) => item.locale === locale)
    .map((item) => localScore(item, normalizedQuery, searchMode))
    .filter((item): item is DocsSearchResult & { score: number } => item.score > 0)
    .sort((a, b) => b.score - a.score || String(a.title).localeCompare(String(b.title)))
    .slice(0, SEARCH_LIMIT)
}

function localScore(record: DocsIndexRecord, normalizedQuery: string, searchMode: SearchMode) {
  const title = record.title.toLowerCase()
  const path = record.path.toLowerCase()
  const text = record.text.toLowerCase()
  const fileMode = searchMode === 'file'
  const titleMatch = title.includes(normalizedQuery)
  const pathMatch = path.includes(normalizedQuery)
  const textMatch = !fileMode && text.includes(normalizedQuery)
  const score = (titleMatch ? 5 : 0) + (pathMatch ? 3 : 0) + (textMatch ? 1 : 0)

  return {
    mode: searchMode,
    relativePath: record.path,
    score,
    snippet: fileMode
      ? record.path
      : localSnippet(record.text, normalizedQuery),
    title: record.title,
    url: record.url
  }
}

function localSnippet(text: string, normalizedQuery: string) {
  const normalizedText = text.toLowerCase()
  const index = normalizedText.indexOf(normalizedQuery)
  if (index < 0) return text.slice(0, 180)

  const start = Math.max(0, index - 70)
  const end = Math.min(text.length, index + normalizedQuery.length + 110)
  const prefix = start > 0 ? '...' : ''
  const suffix = end < text.length ? '...' : ''
  return `${prefix}${text.slice(start, end)}${suffix}`
}

function cleanSearchSnippet(value: string | undefined) {
  if (!value) return ''

  return value
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/(^|[\s([{"'（【])\*([^*\n]+)\*(?=$|[\s.,;:!?，。；：！？、）】\])}"'])/g, '$1$2')
    .replace(/(^|[\s([{"'（【])_([^_\n]+)_(?=$|[\s.,;:!?，。；：！？、）】\])}"'])/g, '$1$2')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\|?[\s:-]+\|[\s|:-]*$/gm, '')
    .split('\n')
    .map((line) => line
      .replace(/^\s*\|\s*/, '')
      .replace(/\s*\|\s*$/, '')
      .replace(/\s*\|\s*/g, ' - ')
      .trim())
    .filter(Boolean)
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function resultUrl(result: DocsSearchResult) {
  const url =
    result.url ||
    routeFromRelativePath(result.relativePath, docsLocale()) ||
    routeFromVikingUri(result.uri)

  if (!url) return ''

  if (/^https?:\/\//.test(url)) return url
  return withBase(url)
}

function routeFromRelativePath(path: string | undefined, locale: SearchLocale) {
  if (!path) return ''

  const normalizedPath = path.replace(/^\/+/, '').replace(/^docs\//, '')
  const localeMatch = normalizedPath.match(/^(en|zh)\/(.+)$/)
  if (localeMatch) {
    return `/${localeMatch[1]}/${stripMarkdownExtension(localeMatch[2])}`
  }

  return `/${locale}/${stripMarkdownExtension(normalizedPath)}`
}

function routeFromVikingUri(uri: string | undefined) {
  if (!uri) return ''

  const match = uri.match(/\/docs\/(en|zh)\/(.+)$/)
  if (!match) return ''

  return `/${match[1]}/${stripMarkdownExtension(match[2])}`
}

function stripMarkdownExtension(path: string) {
  return path.replace(/(?:\/index)?\.mdx?$/, '')
}

watch([query, mode], scheduleSearch)

onMounted(() => {
  isMounted.value = true
  window.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown)
  clearPendingSearch()
  cancelActiveSearch()
})
</script>

<template>
  <div class="ov-docs-search">
    <button
      ref="triggerRef"
      :aria-label="searchCopy.trigger"
      class="ov-docs-search-trigger"
      type="button"
      @click="openSearch"
    >
      <span class="ov-docs-search-trigger-label">{{ searchCopy.trigger }}</span>
      <span aria-hidden="true" class="ov-docs-search-trigger-compact">{{ searchCopy.compactTrigger }}</span>
      <kbd>Ctrl K</kbd>
    </button>

    <Teleport v-if="isMounted && isOpen" to="body">
      <div class="ov-docs-search-backdrop" @click.self="closeSearch">
        <section
          ref="dialogRef"
          :aria-label="searchCopy.dialogLabel"
          aria-modal="true"
          class="ov-docs-search-dialog"
          role="dialog"
          @keydown="handleDialogKeydown"
        >
          <div class="ov-docs-search-bar">
            <div class="ov-docs-search-mode-picker">
              <button
                aria-controls="ov-docs-search-mode-menu"
                :aria-expanded="isModeMenuOpen"
                aria-haspopup="listbox"
                :aria-label="searchCopy.modeLabel"
                class="ov-docs-search-mode-button"
                type="button"
                @click="toggleModeMenu"
              >
                <span>{{ activeMode.label }}</span>
                <span aria-hidden="true" class="ov-docs-search-mode-caret"></span>
              </button>

              <div
                v-if="isModeMenuOpen"
                id="ov-docs-search-mode-menu"
                :aria-label="searchCopy.modeOptionsLabel"
                class="ov-docs-search-mode-menu"
                role="listbox"
              >
                <button
                  v-for="item in modes"
                  :key="item.value"
                  :aria-selected="item.value === mode"
                  class="ov-docs-search-mode-option"
                  :class="{ 'is-selected': item.value === mode }"
                  role="option"
                  type="button"
                  @click="selectMode(item.value)"
                >
                  <span>{{ item.label }}</span>
                  <code>{{ item.command }}</code>
                </button>
              </div>
            </div>

            <input
              ref="inputRef"
              v-model="query"
              :aria-label="searchCopy.inputLabel"
              class="ov-docs-search-input"
              :placeholder="activeMode.placeholder"
              type="search"
              @focus="isModeMenuOpen = false"
            >
          </div>

          <p v-if="notice" class="ov-docs-search-notice">{{ notice }}</p>

          <div class="ov-docs-search-results">
            <p v-if="isLoading" class="ov-docs-search-empty">{{ searchCopy.empty.loading }}</p>
            <p v-else-if="trimmedQuery && resolvedResults.length === 0" class="ov-docs-search-empty">
              {{ searchCopy.empty.noResults }}
            </p>
            <p v-else-if="!trimmedQuery" class="ov-docs-search-empty">
              {{ searchCopy.empty.initial }}
            </p>
            <template v-else>
              <a
                v-for="result in resolvedResults"
                :key="`${result.resolvedUrl}-${result.line ?? ''}-${result.snippet ?? ''}`"
                class="ov-docs-search-result"
                :href="result.resolvedUrl"
                @click="closeSearch"
              >
                <span class="ov-docs-search-result-title">{{ result.title }}</span>
                <span v-if="result.cleanedSnippet" class="ov-docs-search-result-snippet">
                  {{ result.cleanedSnippet }}
                </span>
                <span class="ov-docs-search-result-path">{{ result.relativePath }}</span>
              </a>
            </template>
          </div>
        </section>
      </div>
    </Teleport>
  </div>
</template>
