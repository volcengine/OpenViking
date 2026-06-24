import type { SearchLocale, SearchMode } from './openviking-search-results'

export type RemoteSearchFailureReason = 'rate_limited' | 'timeout' | 'unavailable'

type ModeCopy = {
  label: string
  placeholder: string
}

type SearchCopy = {
  compactTrigger: string
  dialogLabel: string
  empty: {
    initial: string
    loading: string
    noResults: string
  }
  inputLabel: string
  modeLabel: string
  modeOptionsLabel: string
  modes: Record<SearchMode, ModeCopy>
  notice: (reason: RemoteSearchFailureReason, localResultCount: number) => string
  trigger: string
}

const searchCopy: Record<SearchLocale, SearchCopy> = {
  en: {
    compactTrigger: 'Search',
    dialogLabel: 'OpenViking docs search',
    empty: {
      initial: 'Type a query to search the current language docs.',
      loading: 'Searching...',
      noResults: 'No results found.'
    },
    inputLabel: 'Search OpenViking docs',
    modeLabel: 'Search mode',
    modeOptionsLabel: 'Search mode options',
    modes: {
      file: {
        label: 'File',
        placeholder: 'Find docs by path or filename'
      },
      keyword: {
        label: 'Keyword',
        placeholder: 'Search exact words in the docs'
      },
      semantic: {
        label: 'Semantic',
        placeholder: 'Ask a question about the docs'
      }
    },
    notice: (reason, localResultCount) => {
      const prefix =
        reason === 'rate_limited'
          ? 'OpenViking search is rate limited.'
          : reason === 'timeout'
            ? 'OpenViking search timed out.'
            : 'OpenViking search is unavailable.'

      return localResultCount > 0
        ? `${prefix} Showing local docs results.`
        : `${prefix} No local results found.`
    },
    trigger: 'Search docs'
  },
  zh: {
    compactTrigger: '搜索',
    dialogLabel: 'OpenViking 文档搜索',
    empty: {
      initial: '输入关键词，搜索当前语言的文档。',
      loading: '搜索中...',
      noResults: '未找到相关结果。'
    },
    inputLabel: '搜索 OpenViking 文档',
    modeLabel: '搜索模式',
    modeOptionsLabel: '搜索模式选项',
    modes: {
      file: {
        label: '文件搜索',
        placeholder: '按路径或文件名查找文档'
      },
      keyword: {
        label: '关键词搜索',
        placeholder: '搜索文档中的精确词句'
      },
      semantic: {
        label: '语义搜索',
        placeholder: '询问文档内容'
      }
    },
    notice: (reason, localResultCount) => {
      const prefix =
        reason === 'rate_limited'
          ? 'OpenViking 搜索请求过多。'
          : reason === 'timeout'
            ? 'OpenViking 搜索超时。'
            : 'OpenViking 搜索暂不可用。'

      return localResultCount > 0
        ? `${prefix}正在显示本地文档结果。`
        : `${prefix}未找到本地结果。`
    },
    trigger: '搜索文档'
  }
}

export function searchCopyForLocale(locale: SearchLocale) {
  return searchCopy[locale] ?? searchCopy.en
}
