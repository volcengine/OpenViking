import { useEffect, useMemo, useRef, useState, lazy, Suspense } from 'react'
import type { ComponentProps, ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import hljs from 'highlight.js/lib/core'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import rehypeRaw from 'rehype-raw'
import rehypeSanitize from 'rehype-sanitize'
import remarkBreaks from 'remark-breaks'
import remarkGfm from 'remark-gfm'
import { X, Pencil, Save, XCircle, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import { ScrollArea } from '#/components/ui/scroll-area'
import { client } from '#/gen/ov-client/client.gen'
import { getContentDownload, ovClient } from '#/lib/ov-client'
import { parseOkfSidecarMarkdown } from '#/lib/okf-markdown'
import { fileNameFromUri } from '#/lib/viking-uri'
import type { GetContentDownloadData } from '#/gen/ov-client/types.gen'
import type { ContentDownloadQuery } from '@ov-server/api/v1/content'

import {
  formatSize,
  normalizeDirUri,
  normalizeReadContent,
} from '../-lib/normalize'
import { fetchDirectorySidecarContent, saveFileContent } from '../-lib/api'
import {
  useVikingFilePreview,
  useVikingFsStat,
  useInvalidateVikingFs,
} from '../-hooks/viking-fm'
import { useJsonFormat } from '../-hooks/use-json-format'
import type { VikingFsEntry } from '../-types/viking-fm'
import type { CodeEditorHandle } from './code-editor'
import { OkfMetadataPanel } from './okf-metadata-panel'

const LazyCodeEditor = lazy(() =>
  import('./code-editor').then((m) => ({ default: m.CodeEditor })),
)

const languageLoaders: Partial<
  Record<
    string,
    () => Promise<{ default: Parameters<typeof hljs.registerLanguage>[1] }>
  >
> = {
  bash: () => import('highlight.js/lib/languages/bash'),
  c: () => import('highlight.js/lib/languages/c'),
  cpp: () => import('highlight.js/lib/languages/cpp'),
  csharp: () => import('highlight.js/lib/languages/csharp'),
  css: () => import('highlight.js/lib/languages/css'),
  dart: () => import('highlight.js/lib/languages/dart'),
  diff: () => import('highlight.js/lib/languages/diff'),
  dockerfile: () => import('highlight.js/lib/languages/dockerfile'),
  elixir: () => import('highlight.js/lib/languages/elixir'),
  erlang: () => import('highlight.js/lib/languages/erlang'),
  go: () => import('highlight.js/lib/languages/go'),
  graphql: () => import('highlight.js/lib/languages/graphql'),
  haskell: () => import('highlight.js/lib/languages/haskell'),
  ini: () => import('highlight.js/lib/languages/ini'),
  java: () => import('highlight.js/lib/languages/java'),
  javascript: () => import('highlight.js/lib/languages/javascript'),
  json: () => import('highlight.js/lib/languages/json'),
  kotlin: () => import('highlight.js/lib/languages/kotlin'),
  latex: () => import('highlight.js/lib/languages/latex'),
  less: () => import('highlight.js/lib/languages/less'),
  lua: () => import('highlight.js/lib/languages/lua'),
  makefile: () => import('highlight.js/lib/languages/makefile'),
  markdown: () => import('highlight.js/lib/languages/markdown'),
  nginx: () => import('highlight.js/lib/languages/nginx'),
  objectivec: () => import('highlight.js/lib/languages/objectivec'),
  perl: () => import('highlight.js/lib/languages/perl'),
  php: () => import('highlight.js/lib/languages/php'),
  plaintext: () => import('highlight.js/lib/languages/plaintext'),
  protobuf: () => import('highlight.js/lib/languages/protobuf'),
  python: () => import('highlight.js/lib/languages/python'),
  r: () => import('highlight.js/lib/languages/r'),
  ruby: () => import('highlight.js/lib/languages/ruby'),
  rust: () => import('highlight.js/lib/languages/rust'),
  scala: () => import('highlight.js/lib/languages/scala'),
  scss: () => import('highlight.js/lib/languages/scss'),
  shell: () => import('highlight.js/lib/languages/shell'),
  sql: () => import('highlight.js/lib/languages/sql'),
  swift: () => import('highlight.js/lib/languages/swift'),
  typescript: () => import('highlight.js/lib/languages/typescript'),
  wasm: () => import('highlight.js/lib/languages/wasm'),
  xml: () => import('highlight.js/lib/languages/xml'),
  yaml: () => import('highlight.js/lib/languages/yaml'),
}

const loadedLanguages = new Set<string>()
const markdownLanguageAliases: Record<string, string> = {
  cjs: 'javascript',
  js: 'javascript',
  jsx: 'javascript',
  mjs: 'javascript',
  sh: 'bash',
  ts: 'typescript',
  tsx: 'typescript',
  yml: 'yaml',
  zsh: 'bash',
}

async function ensureLanguage(lang: string): Promise<void> {
  if (loadedLanguages.has(lang)) return
  const loader = languageLoaders[lang]
  if (!loader) return
  const mod = await loader()
  hljs.registerLanguage(lang, mod.default)
  loadedLanguages.add(lang)
}

interface FilePreviewProps {
  file: VikingFsEntry | null
  hideDirectoryHeader?: boolean
  onClose: () => void
  onNavigate?: (uri: string) => void
  showCloseButton?: boolean
}

const vikingPrefix = 'viking://'
const contentDownloadUrl: GetContentDownloadData['url'] =
  '/api/v1/content/download'

type DirectoryLevelId = 'abstract' | 'overview'

const DIRECTORY_LEVEL_META: Array<{
  id: DirectoryLevelId
  label: string
  name: string
  title: string
}> = [
  {
    id: 'abstract',
    label: 'Abstract',
    name: 'L0',
    title: 'Short semantic abstract',
  },
  {
    id: 'overview',
    label: 'Overview',
    name: 'L1',
    title: 'Directory overview',
  },
]

const JSONL_MESSAGE_PREVIEW_LIMIT = 720
const LARGE_FILE_PREVIEW_BYTES = 2 * 1024 * 1024
const JSONL_TOOLCALL_STORAGE_KEY = 'openviking.playground.jsonlToolCall'
const COLLAPSE_SYMBOL = '▾'
const EXPAND_SYMBOL = '▸'

type JsonlRecord = {
  error: Error | null
  index: number
  line: string
  parsed: unknown
}

/** One renderable unit of a JSONL message, classified by its structural type. */
type JsonlPart =
  | { input: unknown; kind: 'tool-call'; toolName: string }
  | { kind: 'raw'; text: string }
  | { kind: 'text'; text: string }
  | { kind: 'tool-result'; text: string; toolName: string }

type JsonlMessage = {
  id: string
  label: string
  lineNo: number
  parts: JsonlPart[]
  role: 'agent' | 'assistant' | 'invalid' | 'other' | 'user'
  roleId: string
  time: string
  toolShape: 'call' | 'none' | 'result'
}

function toDownloadUrl(vikingUri: string): string {
  const query: ContentDownloadQuery = { uri: vikingUri }
  return client.buildUrl({
    baseURL: ovClient.getOptions().baseUrl,
    query,
    url: contentDownloadUrl,
  })
}

function withCacheBust(url: string, cacheKey: string): string {
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}_t=${encodeURIComponent(cacheKey)}`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function cleanSummaryContent(value: unknown): string {
  if (value === undefined || value === null) return ''
  const normalized = normalizeReadContent(value)
  const text = typeof normalized === 'string' ? normalized.trim() : ''
  if (!text) return ''
  if (
    /\[directory (overview|abstract) is not (generated|ready)\]/i.test(text)
  ) {
    return ''
  }
  return text
}

function directoryLevelPreview(
  directoryUri: string,
  level: DirectoryLevelId,
  ...values: unknown[]
) {
  const rawContent =
    values.map(cleanSummaryContent).find((content) => content.length > 0) ?? ''
  const document = rawContent
    ? parseOkfSidecarMarkdown(
        `${normalizeDirUri(directoryUri)}.${level}.md`,
        rawContent,
      )
    : null

  return {
    content: document?.body ?? rawContent,
    document,
  }
}

function useDirectoryPreview(file: VikingFsEntry | null) {
  const enabled = Boolean(file?.isDir)
  const abstractQuery = useQuery({
    enabled,
    queryKey: ['viking-directory-sidecar', file?.uri, 'abstract'],
    queryFn: () => fetchDirectorySidecarContent(file!.uri, 'abstract'),
    staleTime: 30_000,
  })
  const overviewQuery = useQuery({
    enabled,
    queryKey: ['viking-directory-sidecar', file?.uri, 'overview'],
    queryFn: () => fetchDirectorySidecarContent(file!.uri, 'overview'),
    staleTime: 30_000,
  })

  return useMemo(() => {
    const directoryUri = file?.uri ?? ''
    return {
      isLoading: abstractQuery.isLoading || overviewQuery.isLoading,
      levels: [
        {
          ...DIRECTORY_LEVEL_META[0],
          ...directoryLevelPreview(
            directoryUri,
            'abstract',
            abstractQuery.data,
            file?.abstract,
          ),
          error: abstractQuery.error,
        },
        {
          ...DIRECTORY_LEVEL_META[1],
          ...directoryLevelPreview(
            directoryUri,
            'overview',
            overviewQuery.data,
            file?.overview,
          ),
          error: overviewQuery.error,
        },
      ],
    }
  }, [
    abstractQuery.data,
    abstractQuery.error,
    abstractQuery.isLoading,
    file?.abstract,
    file?.overview,
    overviewQuery.data,
    overviewQuery.error,
    overviewQuery.isLoading,
  ])
}

function dirnameVikingUri(fileUri: string): string {
  if (fileUri === vikingPrefix) {
    return vikingPrefix
  }

  const trimmed = fileUri.endsWith('/') ? fileUri.slice(0, -1) : fileUri
  const idx = trimmed.lastIndexOf('/')
  if (idx < vikingPrefix.length) {
    return vikingPrefix
  }
  return `${trimmed.slice(0, idx + 1)}`
}

function resolveRelativeVikingUri(
  baseFileUri: string,
  rawPath: string,
): string {
  const baseDir = dirnameVikingUri(baseFileUri)
  const baseBody = baseDir.slice(vikingPrefix.length, -1)

  const pathPart = rawPath.split('#')[0]?.split('?')[0] || ''
  const suffix = rawPath.slice(pathPart.length)

  const baseSegments = baseBody ? baseBody.split('/').filter(Boolean) : []
  const relativeSegments = pathPart.split('/').filter(Boolean)

  const merged = [...baseSegments]
  for (const segment of relativeSegments) {
    if (segment === '.') {
      continue
    }
    if (segment === '..') {
      merged.pop()
      continue
    }
    merged.push(segment)
  }

  const resolved = `${vikingPrefix}${merged.join('/')}`
  return `${resolved}${suffix}`
}

type MarkdownAssetTarget =
  | { kind: 'external'; value: string }
  | { kind: 'raw'; value: string }
  | { kind: 'viking'; value: string }

function safeDecodeUri(value: string): string {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

function resolveMarkdownAssetTarget(
  assetPath: string,
  fileUri: string,
): MarkdownAssetTarget {
  const trimmed = assetPath.trim()
  if (!trimmed || trimmed.startsWith('#')) {
    return { kind: 'raw', value: trimmed }
  }

  if (/^(https?:|data:|blob:|mailto:|tel:)/i.test(trimmed)) {
    return { kind: 'external', value: trimmed }
  }

  // react-markdown percent-encodes the URL it passes via `src`/`href`
  // (e.g. Chinese characters become %E4%BA%92). Decode it back to the literal
  // form so the API client's query serializer encodes it exactly once and we
  // avoid a double-encoded URI that the backend rejects with HTTP 400.
  const decoded = safeDecodeUri(trimmed)

  const vikingUri = decoded.startsWith(vikingPrefix)
    ? decoded
    : resolveRelativeVikingUri(fileUri, decoded)
  return { kind: 'viking', value: vikingUri }
}

function resolveMarkdownAssetUrl(assetPath: string, fileUri: string): string {
  const target = resolveMarkdownAssetTarget(assetPath, fileUri)
  if (target.kind === 'viking') {
    return toDownloadUrl(target.value)
  }
  return target.value
}

function transformDirectoryMarkdownUrl(url: string): string {
  return url.startsWith(vikingPrefix) ? url : defaultUrlTransform(url)
}

function MarkdownLink({
  children,
  fileUri,
  href,
  onNavigate,
}: {
  children?: ReactNode
  fileUri: string
  href?: string
  onNavigate?: (uri: string) => void
}) {
  const target = href ? resolveMarkdownAssetTarget(String(href), fileUri) : null
  const isInternal = target?.kind === 'viking' && Boolean(onNavigate)
  const resolvedHref = target
    ? isInternal
      ? target.value
      : resolveMarkdownAssetUrl(target.value, fileUri)
    : ''
  const isExternal = /^(https?:|mailto:|tel:)/i.test(resolvedHref)

  return (
    <a
      href={resolvedHref}
      onClick={(event) => {
        if (target?.kind === 'viking' && onNavigate) {
          event.preventDefault()
          onNavigate(target.value)
        }
      }}
      target={isExternal ? '_blank' : undefined}
      rel={isExternal ? 'noreferrer noopener' : undefined}
    >
      {children}
    </a>
  )
}

function DirectoryMarkdownLink({
  children,
  fileUri,
  href,
  onNavigate,
}: {
  children?: ReactNode
  fileUri: string
  href?: string
  onNavigate?: (uri: string) => void
}) {
  const decodedHref = href ? safeDecodeUri(href.trim()) : ''
  if (!decodedHref.startsWith(vikingPrefix)) {
    return <a href={href}>{children}</a>
  }

  return (
    <MarkdownLink href={decodedHref} fileUri={fileUri} onNavigate={onNavigate}>
      {children}
    </MarkdownLink>
  )
}

function MarkdownImage({
  src,
  alt,
  fileUri,
}: {
  src?: string
  alt?: string
  fileUri: string
}) {
  const target = useMemo(
    () => (src ? resolveMarkdownAssetTarget(String(src), fileUri) : null),
    [src, fileUri],
  )
  const [objectUrl, setObjectUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!target || target.kind !== 'viking') {
      return
    }

    let alive = true
    let created: string | null = null
    setObjectUrl(null)
    setFailed(false)

    const run = async () => {
      try {
        const response = await getContentDownload({
          query: { uri: target.value },
          responseType: 'blob',
          throwOnError: true,
        })
        if (!alive) return
        const blob = response.data as Blob
        if (blob.size === 0) {
          throw new Error('empty blob')
        }
        created = URL.createObjectURL(blob)
        setObjectUrl(created)
      } catch {
        if (alive) setFailed(true)
      }
    }

    void run()
    return () => {
      alive = false
      if (created) {
        URL.revokeObjectURL(created)
      }
    }
  }, [target])

  const resolvedSrc =
    target?.kind === 'viking'
      ? objectUrl || ''
      : target
        ? target.value
        : String(src || '')

  if (target?.kind === 'viking' && !resolvedSrc) {
    if (failed) {
      return (
        <span className="text-xs text-muted-foreground">
          [{alt || fileNameFromUri(target.value)}]
        </span>
      )
    }
    return null
  }

  return (
    <img
      src={resolvedSrc}
      alt={alt || ''}
      loading="lazy"
      className="max-w-full rounded-md outline outline-1 -outline-offset-1 outline-black/10 dark:outline-white/10"
    />
  )
}

function detectCodeLanguage(filename: string): string | null {
  const lower = filename.toLowerCase()
  const ext = lower.includes('.') ? lower.split('.').pop() || '' : ''

  const extMap: Record<string, string> = {
    ts: 'typescript',
    tsx: 'typescript',
    js: 'javascript',
    jsx: 'javascript',
    mjs: 'javascript',
    cjs: 'javascript',
    py: 'python',
    pyw: 'python',
    go: 'go',
    rs: 'rust',
    java: 'java',
    c: 'c',
    h: 'c',
    cpp: 'cpp',
    cc: 'cpp',
    cxx: 'cpp',
    hpp: 'cpp',
    hxx: 'cpp',
    cs: 'csharp',
    json: 'json',
    yml: 'yaml',
    yaml: 'yaml',
    md: 'markdown',
    markdown: 'markdown',
    html: 'xml',
    xml: 'xml',
    svg: 'xml',
    xhtml: 'xml',
    css: 'css',
    scss: 'scss',
    less: 'less',
    sql: 'sql',
    sh: 'bash',
    bash: 'bash',
    zsh: 'bash',
    toml: 'ini',
    ini: 'ini',
    cfg: 'ini',
    conf: 'ini',
    dockerfile: 'dockerfile',
    dart: 'dart',
    kt: 'kotlin',
    kts: 'kotlin',
    swift: 'swift',
    rb: 'ruby',
    rake: 'ruby',
    gemspec: 'ruby',
    php: 'php',
    lua: 'lua',
    r: 'r',
    rmd: 'r',
    scala: 'scala',
    ex: 'elixir',
    exs: 'elixir',
    erl: 'erlang',
    hrl: 'erlang',
    hs: 'haskell',
    lhs: 'haskell',
    m: 'objectivec',
    mm: 'objectivec',
    pl: 'perl',
    pm: 'perl',
    proto: 'protobuf',
    graphql: 'graphql',
    gql: 'graphql',
    tex: 'latex',
    latex: 'latex',
    makefile: 'makefile',
    nginx: 'nginx',
    wasm: 'wasm',
    wat: 'wasm',
    diff: 'diff',
    patch: 'diff',
  }

  if (ext && extMap[ext]) return extMap[ext]

  const basename = lower.split('/').pop() || ''
  if (basename === 'dockerfile' || basename.startsWith('dockerfile.'))
    return 'dockerfile'
  if (basename === 'makefile' || basename === 'gnumakefile') return 'makefile'
  if (
    basename === '.bashrc' ||
    basename === '.zshrc' ||
    basename === '.bash_profile'
  )
    return 'bash'

  return null
}

function escapeHtml(raw: string): string {
  return raw.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

const MEMORY_FIELDS_RE = /<!--\s*MEMORY_FIELDS\s*([\s\S]*?)\s*-->/

function parseMemoryFields(content: string): Record<string, unknown> | null {
  const match = MEMORY_FIELDS_RE.exec(content)
  if (!match?.[1]) return null

  try {
    const parsed = JSON.parse(match[1].trim()) as unknown
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null
  } catch {
    return null
  }
}

function stripMemoryFields(content: string): string {
  return content.replace(MEMORY_FIELDS_RE, '').trim()
}

function memoryFieldsDisplayContent(content: string): string {
  const fields = parseMemoryFields(content)
  if (!fields) return content

  const body = stripMemoryFields(content)
  if (body) return body

  const fieldContent = fields.content
  if (typeof fieldContent === 'string' && fieldContent.trim()) {
    return fieldContent.trim()
  }

  return JSON.stringify(fields, null, 2)
}

function textFromReactNode(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') {
    return String(node)
  }
  if (Array.isArray(node)) {
    return node.map(textFromReactNode).join('')
  }
  return ''
}

function normalizeMarkdownLanguage(className: string | undefined): string {
  const match = /language-([\w-]+)/.exec(className || '')
  const raw = match?.[1]?.toLowerCase() || ''
  return markdownLanguageAliases[raw] ?? raw
}

function MarkdownCode({
  className,
  children,
}: ComponentProps<'code'> & { inline?: boolean }) {
  const rawText = textFromReactNode(children).replace(/\n$/, '')
  const language = normalizeMarkdownLanguage(className)
  const isBlock = Boolean(language || rawText.includes('\n'))
  const [html, setHtml] = useState(() => escapeHtml(rawText))

  useEffect(() => {
    if (!isBlock) {
      setHtml(escapeHtml(rawText))
      return
    }

    let cancelled = false
    const run = async () => {
      try {
        if (language) {
          await ensureLanguage(language)
        }
        if (cancelled) return
        const highlighted =
          language && hljs.getLanguage(language)
            ? hljs.highlight(rawText, {
                ignoreIllegals: true,
                language,
              }).value
            : escapeHtml(rawText)
        setHtml(highlighted)
      } catch {
        if (!cancelled) setHtml(escapeHtml(rawText))
      }
    }

    void run()
    return () => {
      cancelled = true
    }
  }, [isBlock, language, rawText])

  if (!isBlock) {
    return (
      <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.92em] text-foreground">
        {children}
      </code>
    )
  }

  return (
    <code
      className={`hljs block overflow-x-auto whitespace-pre font-mono text-xs leading-6 ${className || ''}`}
      dangerouslySetInnerHTML={{ __html: html || escapeHtml(rawText) }}
    />
  )
}

function MarkdownPre({ children }: ComponentProps<'pre'>) {
  return (
    <pre className="overflow-x-auto rounded-md border bg-muted/30 p-3 text-xs leading-6 text-foreground dark:bg-muted-foreground/20">
      {children}
    </pre>
  )
}

const markdownComponents = {
  code: MarkdownCode,
  pre: MarkdownPre,
  table: ({ children }: ComponentProps<'table'>) => (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  td: ({ children, colSpan, rowSpan }: ComponentProps<'td'>) => (
    <td
      className="border px-3 py-2 text-center align-middle"
      colSpan={colSpan}
      rowSpan={rowSpan}
    >
      {children}
    </td>
  ),
  th: ({ children, colSpan, rowSpan }: ComponentProps<'th'>) => (
    <th
      className="border bg-muted/50 px-3 py-2 text-center align-middle font-medium"
      colSpan={colSpan}
      rowSpan={rowSpan}
    >
      {children}
    </th>
  ),
}

export function parseJsonlRecords(text: string): JsonlRecord[] {
  return text
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map((line, index) => ({ line, index }))
    .filter((record) => record.line.trim().length > 0)
    .map((record) => {
      try {
        return {
          ...record,
          error: null,
          parsed: JSON.parse(record.line) as unknown,
        }
      } catch (error) {
        return {
          ...record,
          error: error instanceof Error ? error : new Error(String(error)),
          parsed: null,
        }
      }
    })
}

export function normalizeJsonlDisplayText(text: string): string {
  return text.replace(/↵|⏎|\r\n?/g, '\n')
}

/** Flatten a tool_result `content` (string, or a list of text blocks) to text. */
function jsonlContentToText(value: unknown): string {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) {
    return value
      .map((item) =>
        typeof item === 'string'
          ? item
          : isRecord(item) && typeof item.text === 'string'
            ? item.text
            : JSON.stringify(item, null, 2),
      )
      .join('\n')
  }
  if (value === undefined || value === null) return ''
  return JSON.stringify(value, null, 2)
}

/**
 * Classify one content entry by its structural `type` field, never by a
 * rendered string prefix. Covers the Anthropic transcript shape
 * (`text` / `tool_use` / `tool_result`) and the OpenViking parts shape
 * (`text` / `tool`, see openviking/message/part.py). An OpenViking `tool`
 * part carries both the call and its output, so it expands to two entries.
 */
function toJsonlParts(part: unknown): JsonlPart[] {
  if (typeof part === 'string') return [{ kind: 'text', text: part }]
  if (!isRecord(part)) return [{ kind: 'raw', text: JSON.stringify(part) }]

  const type = String(part.type ?? '')

  if (type === 'tool_use') {
    return [
      {
        input: part.input ?? part.arguments ?? {},
        kind: 'tool-call',
        toolName: typeof part.name === 'string' ? part.name : 'tool',
      },
    ]
  }

  if (type === 'tool_result') {
    return [
      {
        kind: 'tool-result',
        text: jsonlContentToText(part.content ?? part.result ?? ''),
        toolName: '',
      },
    ]
  }

  if (type === 'tool') {
    const toolName = typeof part.tool_name === 'string' ? part.tool_name : ''
    const output = typeof part.tool_output === 'string' ? part.tool_output : ''
    // A tool result arrives as its own record with `tool_output` and no
    // `tool_input` (the call was logged on the preceding message). Inventing an
    // empty call part there would classify the record as a call and print `{}`.
    const hasInput =
      isRecord(part.tool_input) && Object.keys(part.tool_input).length > 0
    const parts: JsonlPart[] = []
    if (hasInput || !output) {
      parts.push({ input: part.tool_input ?? {}, kind: 'tool-call', toolName })
    }
    if (output) parts.push({ kind: 'tool-result', text: output, toolName })
    return parts
  }

  if (type === 'text' || typeof part.text === 'string') {
    return [
      { kind: 'text', text: typeof part.text === 'string' ? part.text : '' },
    ]
  }

  // Unknown part type: dump it verbatim. The `[type]` tag here is display-only
  // and is never parsed back into a classification.
  const payload = { ...part }
  delete payload.type
  const body = Object.keys(payload).length
    ? JSON.stringify(payload, null, 2)
    : ''
  const label = type || 'part'
  return [{ kind: 'raw', text: body ? `[${label}]\n${body}` : `[${label}]` }]
}

function toJsonlMessageParts(content: unknown): JsonlPart[] {
  if (typeof content === 'string') {
    return content ? [{ kind: 'text', text: content }] : []
  }
  if (Array.isArray(content)) return content.flatMap(toJsonlParts)
  if (content === undefined || content === null) return []
  return [{ kind: 'raw', text: JSON.stringify(content, null, 2) }]
}

export function hasJsonlToolPart(parts: JsonlPart[]): boolean {
  return parts.some(
    (part) => part.kind === 'tool-call' || part.kind === 'tool-result',
  )
}

/**
 * How the card should be styled. A message counts as tool-shaped only when it
 * carries no text of its own: an assistant turn holding both `text` and
 * `tool_use` is styled as a plain assistant message, because the text is the
 * readable content and the calls are an aside. Pure `tool_result` messages
 * (which arrive with role=user in Anthropic transcripts) keep the dashed
 * tool-result look, so the tool-result check has to win over the role.
 */
function getJsonlToolShape(parts: JsonlPart[]): JsonlMessage['toolShape'] {
  if (parts.some((part) => part.kind === 'text')) return 'none'
  if (parts.some((part) => part.kind === 'tool-call')) return 'call'
  if (parts.some((part) => part.kind === 'tool-result')) return 'result'
  return 'none'
}

export function getJsonlMessage(record: JsonlRecord): JsonlMessage {
  const { error, index, line, parsed } = record
  if (error || !isRecord(parsed)) {
    return {
      id: '',
      label: 'invalid',
      lineNo: index + 1,
      parts: [{ kind: 'raw', text: line }],
      role: 'invalid',
      roleId: '',
      time: '',
      toolShape: 'none',
    }
  }

  const nestedMessage = isRecord(parsed.message) ? parsed.message : null
  const source = nestedMessage ?? parsed
  const rawRole = String(source.role ?? parsed.role ?? parsed.type ?? 'message')
    .trim()
    .toLowerCase()
  const content =
    source.content ?? source.parts ?? parsed.parts ?? parsed.content ?? parsed
  const parts = toJsonlMessageParts(content)
  const toolShape = getJsonlToolShape(parts)

  return {
    id: String(parsed.uuid ?? parsed.id ?? source.id ?? ''),
    label: toolShape === 'result' ? 'tool-result' : rawRole,
    lineNo: index + 1,
    parts,
    role:
      rawRole === 'user'
        ? 'user'
        : rawRole === 'assistant' || rawRole === 'agent'
          ? rawRole
          : 'other',
    roleId: String(parsed.peer_id ?? source.peer_id ?? ''),
    time: String(
      parsed.timestamp ?? parsed.created_at ?? source.created_at ?? '',
    ),
    toolShape,
  }
}

function isJsonlMarkdownMessage(role: JsonlMessage['role']): boolean {
  return role === 'assistant' || role === 'agent' || role === 'user'
}

/** Trim text parts against a shared character budget; tool parts pass through. */
function collapseJsonlParts(parts: JsonlPart[], limit: number): JsonlPart[] {
  let budget = limit
  const collapsed: JsonlPart[] = []
  for (const part of parts) {
    if (part.kind !== 'text') {
      collapsed.push(part)
      continue
    }
    if (budget <= 0) continue
    if (part.text.length <= budget) {
      collapsed.push(part)
      budget -= part.text.length
      continue
    }
    collapsed.push({
      kind: 'text',
      text: `${part.text.slice(0, budget).trimEnd()}...`,
    })
    budget = 0
  }
  return collapsed
}

function formatJsonlTime(value: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString([], {
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    month: 'short',
  })
}

function JsonlRawRow({ record }: { record: JsonlRecord }) {
  const [open, setOpen] = useState(false)
  const parsed = record.parsed
  const keys = isRecord(parsed) ? Object.keys(parsed) : []
  const titleKey = keys.find((key) =>
    ['name', 'title', 'event', 'type', 'role', 'method'].includes(key),
  )

  return (
    <div
      className={`grid grid-cols-[3.5rem_1fr] border-b transition-colors hover:bg-muted/40 ${
        open ? 'bg-muted/30' : ''
      }`}
    >
      <button
        type="button"
        className="flex items-center justify-end gap-1 border-r px-2 py-2 font-mono text-[11px] text-muted-foreground hover:text-foreground"
        onClick={() => setOpen((current) => !current)}
      >
        <span>{record.index + 1}</span>
        <span aria-hidden="true">{open ? COLLAPSE_SYMBOL : EXPAND_SYMBOL}</span>
      </button>
      <div className="min-w-0 px-3 py-2">
        {open ? (
          <pre className="overflow-auto whitespace-pre-wrap break-words text-xs leading-5">
            {record.error
              ? record.line
              : JSON.stringify(record.parsed, null, 2)}
          </pre>
        ) : record.error ? (
          <div className="truncate text-xs text-destructive">{record.line}</div>
        ) : titleKey && isRecord(parsed) ? (
          <div className="flex min-w-0 items-center gap-2 text-xs">
            <span className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase text-primary">
              {titleKey}
            </span>
            <span className="truncate font-medium">
              {String(parsed[titleKey])}
            </span>
            <span className="shrink-0 text-muted-foreground">
              {keys
                .filter((key) => key !== titleKey)
                .slice(0, 3)
                .join(', ')}
            </span>
          </div>
        ) : (
          <div className="truncate text-xs text-muted-foreground">
            {keys.slice(0, 6).join(', ') || record.line}
          </div>
        )}
      </div>
    </div>
  )
}

function JsonlToolBody({ input }: { input: unknown }) {
  const { t } = useTranslation('resources')
  // The input is already structured, so no normalization and no re-parsing.
  const text =
    input === undefined ||
    input === null ||
    (isRecord(input) && Object.keys(input).length === 0)
      ? ''
      : typeof input === 'string'
        ? input
        : JSON.stringify(input, null, 2)

  return text ? (
    <pre className="max-h-96 overflow-auto rounded border bg-muted/30 p-2 text-xs leading-5">
      {text}
    </pre>
  ) : (
    <pre className="whitespace-pre-wrap break-words text-xs leading-5">
      {t('filePreview.jsonl.noArguments')}
    </pre>
  )
}

function JsonlMarkdownBody({ content }: { content: string }) {
  const { t } = useTranslation('resources')
  return (
    <div className="prose prose-sm max-w-none break-words dark:prose-invert dark:prose-pre:bg-muted-foreground/20">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        components={markdownComponents}
      >
        {content || t('filePreview.jsonl.emptyMessage')}
      </ReactMarkdown>
    </div>
  )
}

/**
 * Render one part by its kind: text goes through Markdown (for roles that get
 * Markdown at all), a tool call shows its structured input as JSON, everything
 * else falls back to a `pre`. Only free text is normalized — a tool input is a
 * structured object, and raw dumps are already JSON.
 */
function JsonlPartBody({
  markdown,
  part,
}: {
  markdown: boolean
  part: JsonlPart
}) {
  const { t } = useTranslation('resources')
  if (part.kind === 'tool-call') return <JsonlToolBody input={part.input} />
  if (part.kind === 'text' && markdown) {
    return <JsonlMarkdownBody content={normalizeJsonlDisplayText(part.text)} />
  }
  if (part.kind === 'tool-result') {
    return (
      <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded border bg-muted/30 p-2 text-xs leading-5">
        {normalizeJsonlDisplayText(part.text) ||
          t('filePreview.jsonl.emptyMessage')}
      </pre>
    )
  }
  const text =
    part.kind === 'raw' ? part.text : normalizeJsonlDisplayText(part.text)
  return (
    <pre
      className={`whitespace-pre-wrap break-words text-xs leading-5 ${
        part.kind === 'raw' ? 'text-muted-foreground' : ''
      }`}
    >
      {text || t('filePreview.jsonl.emptyMessage')}
    </pre>
  )
}

function JsonlMessageCard({ message }: { message: JsonlMessage }) {
  const { t } = useTranslation('resources')
  const [expanded, setExpanded] = useState(false)
  const textLength = message.parts.reduce(
    (total, part) => (part.kind === 'text' ? total + part.text.length : total),
    0,
  )
  const needsExpand = textLength > JSONL_MESSAGE_PREVIEW_LIMIT
  const displayParts =
    expanded || !needsExpand
      ? message.parts
      : collapseJsonlParts(message.parts, JSONL_MESSAGE_PREVIEW_LIMIT)
  const toolNames = [
    ...new Set(
      message.parts.flatMap((part) =>
        (part.kind === 'tool-call' || part.kind === 'tool-result') &&
        part.toolName
          ? [part.toolName]
          : [],
      ),
    ),
  ]
  const markdown = isJsonlMarkdownMessage(message.role)
  // Tool-result first: it must win over role=user, since Anthropic transcripts
  // deliver tool results as user messages.
  const alignClass =
    message.toolShape === 'result'
      ? 'border-dashed bg-muted/50'
      : message.role === 'user'
        ? 'bg-primary/10 border-primary/25'
        : message.role === 'invalid'
          ? 'border-destructive/30 bg-muted/40'
          : message.role === 'other'
            ? 'bg-muted/40'
            : message.toolShape === 'call'
              ? 'border-dashed bg-background'
              : 'border-l-2 border-l-primary/50 bg-background'

  return (
    <article
      className={`w-full min-w-0 max-w-full rounded-lg border p-3 text-sm shadow-sm ${alignClass}`}
    >
      <div className="mb-2 flex min-w-0 items-center gap-2">
        <span className="text-xs font-semibold">{message.label}</span>
        {message.roleId ? (
          <span className="truncate text-xs text-muted-foreground">
            {message.roleId}
          </span>
        ) : null}
        {toolNames.map((toolName) => (
          <span
            key={toolName}
            className="truncate rounded border bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
          >
            {toolName}
          </span>
        ))}
        <span className="ml-auto shrink-0 font-mono text-[11px] text-muted-foreground">
          #{message.lineNo}
        </span>
      </div>

      {displayParts.length ? (
        <div className="grid gap-2">
          {displayParts.map((part, index) => (
            <JsonlPartBody
              key={`${part.kind}-${index}`}
              markdown={markdown}
              part={part}
            />
          ))}
        </div>
      ) : (
        <pre className="whitespace-pre-wrap break-words text-xs leading-5">
          {t('filePreview.jsonl.emptyMessage')}
        </pre>
      )}

      <div className="mt-2 flex items-center gap-2 border-t pt-2 text-[11px] text-muted-foreground">
        {message.time ? (
          <time dateTime={message.time}>{formatJsonlTime(message.time)}</time>
        ) : null}
        {message.id ? <span className="truncate">{message.id}</span> : null}
        {needsExpand ? (
          <button
            type="button"
            className="ml-auto rounded border px-2 py-0.5 font-medium text-primary hover:border-primary"
            onClick={() => setExpanded((current) => !current)}
          >
            {expanded
              ? t('filePreview.jsonl.collapse')
              : t('filePreview.jsonl.expand')}
          </button>
        ) : null}
      </div>
    </article>
  )
}

function JsonlPreview({ content }: { content: string }) {
  const { t } = useTranslation('resources')
  const [dialogMode, setDialogMode] = useState(true)
  const [showTools, setShowTools] = useState(() => {
    if (typeof window === 'undefined') return true
    const stored = window.localStorage.getItem(JSONL_TOOLCALL_STORAGE_KEY)
    return stored === null ? true : stored === 'true'
  })
  const records = useMemo(() => parseJsonlRecords(content), [content])
  const messages = useMemo(() => records.map(getJsonlMessage), [records])
  // Hiding tool calls strips the tool parts but keeps any text the same message
  // carried; a message left with nothing to show drops out entirely.
  const visibleMessages = useMemo(() => {
    if (showTools) return messages
    return messages
      .map((message) => ({
        ...message,
        parts: message.parts.filter(
          (part) => part.kind !== 'tool-call' && part.kind !== 'tool-result',
        ),
      }))
      .filter((message) => message.parts.length > 0)
  }, [messages, showTools])
  // Raw rows cannot be split part by part, so a record hidden in dialog mode is
  // hidden here too; a mixed text+tool record keeps its whole line.
  const visibleLineNos = useMemo(
    () => new Set(visibleMessages.map((message) => message.lineNo)),
    [visibleMessages],
  )
  const hasTools = useMemo(
    () => messages.some((message) => hasJsonlToolPart(message.parts)),
    [messages],
  )

  if (!records.length) {
    return (
      <div className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">
        {t('filePreview.jsonl.emptyJsonl')}
      </div>
    )
  }

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
        <span className="font-medium text-primary">
          {t('filePreview.jsonl.recordCount', { count: records.length })}
        </span>
        <div className="flex items-center gap-2">
          {hasTools ? (
            <label className="inline-flex cursor-pointer items-center gap-2">
              <span className="font-medium">
                {t('filePreview.jsonl.toolcall')}
              </span>
              <input
                type="checkbox"
                className="peer sr-only"
                checked={showTools}
                onChange={(event) => {
                  setShowTools(event.target.checked)
                  window.localStorage.setItem(
                    JSONL_TOOLCALL_STORAGE_KEY,
                    String(event.target.checked),
                  )
                }}
              />
              <span className="h-5 w-9 rounded-full border bg-muted transition-colors after:block after:size-3.5 after:translate-x-0.5 after:translate-y-0.5 after:rounded-full after:bg-muted-foreground after:transition-transform peer-checked:border-primary peer-checked:bg-primary/15 peer-checked:after:translate-x-[18px] peer-checked:after:bg-primary" />
            </label>
          ) : null}
          <label className="inline-flex cursor-pointer items-center gap-2">
            <span className="font-medium">
              {t('filePreview.jsonl.dialogMode')}
            </span>
            <input
              type="checkbox"
              className="peer sr-only"
              checked={dialogMode}
              onChange={(event) => setDialogMode(event.target.checked)}
            />
            <span className="h-5 w-9 rounded-full border bg-muted transition-colors after:block after:size-3.5 after:translate-x-0.5 after:translate-y-0.5 after:rounded-full after:bg-muted-foreground after:transition-transform peer-checked:border-primary peer-checked:bg-primary/15 peer-checked:after:translate-x-[18px] peer-checked:after:bg-primary" />
          </label>
        </div>
      </div>

      {dialogMode ? (
        <div className="flex min-w-0 flex-col gap-3">
          {visibleMessages.map((message) => (
            <JsonlMessageCard key={message.lineNo} message={message} />
          ))}
        </div>
      ) : (
        <div className="overflow-hidden rounded-md border">
          {records
            .filter((record) => visibleLineNos.has(record.index + 1))
            .map((record) => (
              <JsonlRawRow key={record.index} record={record} />
            ))}
        </div>
      )}
    </div>
  )
}

export function FilePreview({
  file,
  hideDirectoryHeader = false,
  onClose,
  onNavigate,
  showCloseButton = true,
}: FilePreviewProps) {
  const { t } = useTranslation('resources')
  const isJsonPath = Boolean(
    file && !file.isDir && file.name.toLowerCase().endsWith('.json'),
  )
  const needsMetadata = Boolean(
    isJsonPath && file && (file.sizeBytes === null || !file.modTime),
  )
  const statQuery = useVikingFsStat(needsMetadata ? file?.uri : undefined)
  const resolvedFile = useMemo(() => {
    if (!file || !statQuery.data || statQuery.data.uri !== file.uri) {
      return file
    }
    return {
      ...file,
      ...statQuery.data,
    }
  }, [file, statQuery.data])
  const isJsonFile = isJsonPath
  const previewQuery = useVikingFilePreview(
    resolvedFile,
    {
      maxAutoReadBytes: LARGE_FILE_PREVIEW_BYTES,
      defaultReadLimit: -1,
      requireKnownSize: isJsonFile,
    },
    {
      raw: true,
    },
  )
  const preview = previewQuery.preview
  const metadataLoading = needsMetadata && statQuery.isPending
  const hasPreviewContent =
    Boolean(preview?.shouldAutoRead) || previewQuery.isContentLoaded
  const previewLoading = metadataLoading || previewQuery.isLoading
  const displayContent = useMemo(
    () => memoryFieldsDisplayContent(preview?.content || ''),
    [preview?.content],
  )
  const okfDocument = useMemo(
    () =>
      file && preview?.fileType === 'markdown'
        ? parseOkfSidecarMarkdown(file.uri, displayContent)
        : null,
    [displayContent, file, preview?.fileType],
  )
  const jsonFormat = useJsonFormat(
    displayContent,
    isJsonFile && hasPreviewContent,
  )
  const directoryPreview = useDirectoryPreview(file)
  const [markdownMode, setMarkdownMode] = useState<'preview' | 'source'>(
    'preview',
  )
  const [jsonMode, setJsonMode] = useState<'preview' | 'source'>('preview')
  const [activeDirectoryLevels, setActiveDirectoryLevels] = useState<
    Set<DirectoryLevelId>
  >(new Set(['abstract', 'overview']))
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const editorRef = useRef<CodeEditorHandle>(null)
  const { invalidatePreview } = useInvalidateVikingFs()

  const canEdit =
    !file?.isDir &&
    preview?.shouldAutoRead &&
    (preview.fileType === 'code' ||
      preview.fileType === 'markdown' ||
      preview.fileType === 'jsonl' ||
      preview.fileType === 'text')

  useEffect(() => {
    setMarkdownMode('preview')
    setJsonMode('preview')
    setEditing(false)
    setActiveDirectoryLevels(new Set(['abstract', 'overview']))
  }, [file?.uri])

  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [imageLoading, setImageLoading] = useState(false)
  const [imageError, setImageError] = useState<string | null>(null)

  const imageSrc = useMemo(() => {
    if (!file || preview?.fileType !== 'image') {
      return null
    }
    return withCacheBust(
      toDownloadUrl(file.uri),
      file.modTime || Date.now().toString(),
    )
  }, [file, preview?.fileType])

  const [highlightedCodeHtml, setHighlightedCodeHtml] = useState('')

  const needsHighlight =
    (preview?.fileType === 'code' && !isJsonFile) ||
    (preview?.fileType === 'markdown' && markdownMode === 'source')

  useEffect(() => {
    if (!preview || !needsHighlight) {
      setHighlightedCodeHtml('')
      return
    }

    const content = displayContent || ''
    if (!content) {
      setHighlightedCodeHtml('')
      return
    }

    let cancelled = false
    const language = detectCodeLanguage(file?.name || '')

    const run = async () => {
      try {
        if (language) {
          await ensureLanguage(language)
          if (cancelled) return
          setHighlightedCodeHtml(hljs.highlight(content, { language }).value)
        } else {
          setHighlightedCodeHtml(hljs.highlightAuto(content).value)
        }
      } catch {
        if (!cancelled) setHighlightedCodeHtml(escapeHtml(content))
      }
    }

    void run()
    return () => {
      cancelled = true
    }
  }, [preview, file?.name, needsHighlight, displayContent])

  useEffect(() => {
    let alive = true

    const loadWithAuthClient = async () => {
      if (!file || preview?.fileType !== 'image') {
        setImageUrl(null)
        setImageError(null)
        setImageLoading(false)
        return
      }

      setImageLoading(true)
      setImageError(null)

      try {
        const response = await getContentDownload({
          query: { uri: file.uri },
          responseType: 'blob',
          throwOnError: true,
        })

        if (!alive) {
          return
        }

        const blob = response.data as Blob
        if (blob.size === 0) {
          throw new Error('empty blob')
        }

        const nextUrl = URL.createObjectURL(blob)
        setImageUrl((prev) => {
          if (prev) {
            URL.revokeObjectURL(prev)
          }
          return nextUrl
        })
        setImageLoading(false)
      } catch (error) {
        if (!alive) {
          return
        }
        setImageLoading(false)
        setImageError(String(error))
      }
    }

    if (preview?.fileType === 'image') {
      void loadWithAuthClient()
    }

    return () => {
      alive = false
    }
  }, [file, preview?.fileType])

  const handleSave = async () => {
    if (!file || !editorRef.current) return
    setSaving(true)
    try {
      await saveFileContent(file.uri, editorRef.current.getContent())
      invalidatePreview(file.uri)
      setEditing(false)
    } catch (err) {
      console.error('Save failed:', err)
    } finally {
      setSaving(false)
    }
  }

  if (!file) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {t('filePreview.emptyPrompt')}
      </div>
    )
  }

  const isMarkdown = preview?.fileType === 'markdown'
  const isDark = document.documentElement.classList.contains('dark')
  const emptyFileText = t('filePreview.emptyFile')
  const availableDirectoryLevels = directoryPreview.levels.filter(
    (level) => level.document !== null || level.content.trim(),
  )
  const visibleDirectoryLevels = availableDirectoryLevels.filter((level) =>
    activeDirectoryLevels.has(level.id),
  )
  const showHeader = !(hideDirectoryHeader && file.isDir)
  const previewFile = resolvedFile || file
  const showJsonPreview =
    !editing &&
    !previewLoading &&
    preview?.fileType === 'code' &&
    hasPreviewContent &&
    isJsonFile

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      {showHeader ? (
        <div className="flex min-h-14 shrink-0 items-center justify-between border-b px-4">
          <div className="flex min-w-0 items-center gap-2">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium leading-5">
                {file.name}
              </div>
              {!file.isDir ? (
                <div className="text-xs leading-5 text-muted-foreground">
                  {formatSize(previewFile.sizeBytes ?? previewFile.size)} ·{' '}
                  {previewFile.modTime || '-'}
                </div>
              ) : null}
            </div>
            {editing ? (
              <div className="flex items-center gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={saving}
                  onClick={() => setEditing(false)}
                >
                  <XCircle className="mr-1 size-3.5" />
                  {t('filePreview.cancel')}
                </Button>
                <Button
                  size="sm"
                  className="active:scale-[0.96] transition-transform"
                  disabled={saving}
                  onClick={handleSave}
                >
                  {saving ? (
                    <Loader2 className="mr-1 size-3.5 animate-spin" />
                  ) : (
                    <Save className="mr-1 size-3.5" />
                  )}
                  {t('filePreview.save')}
                </Button>
              </div>
            ) : (
              canEdit && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setEditing(true)}
                >
                  <Pencil className="mr-1 size-3.5" />
                  {t('filePreview.edit')}
                </Button>
              )
            )}
          </div>
          {showCloseButton ? (
            <Button
              size="icon"
              variant="ghost"
              className="size-10"
              onClick={onClose}
            >
              <X className="size-4" />
            </Button>
          ) : null}
        </div>
      ) : null}

      {editing && preview?.content != null ? (
        <div className="min-h-0 flex-1 p-2">
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                <Loader2 className="mr-2 size-4 animate-spin" />
                {t('filePreview.loadingEditor')}
              </div>
            }
          >
            <LazyCodeEditor
              ref={editorRef}
              initialContent={preview.content}
              filename={file.name}
              isDark={isDark}
            />
          </Suspense>
        </div>
      ) : showJsonPreview ? (
        <div className="min-h-0 flex-1 p-2">
          <div className="flex h-full min-h-0 flex-col">
            <div className="mb-2 inline-flex shrink-0 self-start overflow-hidden rounded-md border">
              <button
                type="button"
                className={`px-3 py-1.5 text-xs ${
                  jsonMode === 'preview'
                    ? 'bg-muted font-medium text-foreground'
                    : 'text-muted-foreground hover:bg-muted/60'
                }`}
                onClick={() => setJsonMode('preview')}
              >
                {t('filePreview.markdownPreview')}
              </button>
              <button
                type="button"
                className={`px-3 py-1.5 text-xs ${
                  jsonMode === 'source'
                    ? 'bg-muted font-medium text-foreground'
                    : 'text-muted-foreground hover:bg-muted/60'
                }`}
                onClick={() => setJsonMode('source')}
              >
                {t('filePreview.markdownSource')}
              </button>
            </div>

            {jsonMode === 'preview' && jsonFormat.isFormatting ? (
              <div className="flex min-h-0 flex-1 items-center justify-center text-sm text-muted-foreground">
                <Loader2 className="mr-2 size-4 animate-spin" />
                {t('filePreview.formattingJson')}
              </div>
            ) : (
              <div className="flex min-h-0 flex-1 flex-col gap-2">
                {jsonMode === 'preview' && jsonFormat.error ? (
                  <div className="shrink-0 text-xs text-destructive">
                    {t('filePreview.jsonFormatFailed')}
                  </div>
                ) : null}
                <div className="min-h-0 flex-1">
                  <Suspense
                    fallback={
                      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                        <Loader2 className="mr-2 size-4 animate-spin" />
                        {t('filePreview.loadingEditor')}
                      </div>
                    }
                  >
                    <LazyCodeEditor
                      initialContent={
                        jsonMode === 'preview'
                          ? jsonFormat.content
                          : displayContent
                      }
                      filename={previewFile.name}
                      isDark={isDark}
                      readOnly
                      appearance={jsonMode === 'preview' ? 'plain' : 'editor'}
                      enableLanguageSupport={
                        (previewFile.sizeBytes ?? 0) <= LARGE_FILE_PREVIEW_BYTES
                      }
                      lineWrapping={
                        jsonMode === 'source' || Boolean(jsonFormat.error)
                      }
                    />
                  </Suspense>
                </div>
              </div>
            )}
          </div>
        </div>
      ) : (
        <ScrollArea className="min-h-0 flex-1">
          <div className="mx-auto min-h-full w-full max-w-5xl p-4">
            {isMarkdown && !editing ? (
              <div className="mb-3 inline-flex overflow-hidden rounded-md border">
                <button
                  type="button"
                  className={`px-3 py-1.5 text-xs ${markdownMode === 'preview' ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:bg-muted/60'}`}
                  onClick={() => setMarkdownMode('preview')}
                >
                  {t('filePreview.markdownPreview')}
                </button>
                <button
                  type="button"
                  className={`px-3 py-1.5 text-xs ${markdownMode === 'source' ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:bg-muted/60'}`}
                  onClick={() => setMarkdownMode('source')}
                >
                  {t('filePreview.markdownSource')}
                </button>
              </div>
            ) : null}

            {file.isDir ? (
              <div className="grid gap-4">
                {availableDirectoryLevels.length > 0 ? (
                  <div className="flex flex-wrap gap-2 border-b pb-3">
                    {availableDirectoryLevels.map((level) => {
                      const active = activeDirectoryLevels.has(level.id)
                      return (
                        <button
                          key={level.id}
                          type="button"
                          className={`inline-flex items-baseline gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors ${
                            active
                              ? 'border-border bg-muted text-foreground'
                              : 'border-border bg-background text-muted-foreground hover:border-foreground/30 hover:text-foreground'
                          }`}
                          title={level.title}
                          onClick={() =>
                            setActiveDirectoryLevels((current) => {
                              const next = new Set(current)
                              if (next.has(level.id)) {
                                next.delete(level.id)
                              } else {
                                next.add(level.id)
                              }
                              return next
                            })
                          }
                        >
                          <span className="font-mono text-[10px] font-semibold uppercase tracking-wide text-primary">
                            {level.name}
                          </span>
                          <span className="font-medium">{level.label}</span>
                        </button>
                      )
                    })}
                  </div>
                ) : null}

                {directoryPreview.isLoading &&
                availableDirectoryLevels.length === 0 ? (
                  <div className="text-sm text-muted-foreground">
                    {t('filePreview.loadingContent')}
                  </div>
                ) : availableDirectoryLevels.length === 0 ? (
                  <div className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">
                    {t('filePreview.noDirectoryContext')}
                  </div>
                ) : visibleDirectoryLevels.length === 0 ? (
                  <div className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">
                    {t('filePreview.selectDirectoryContext')}
                  </div>
                ) : (
                  <div className="grid gap-5">
                    {visibleDirectoryLevels.map((level, index) => (
                      <section key={level.id} className="grid gap-2">
                        {index > 0 ? <div className="border-t" /> : null}
                        <header className="flex items-center gap-2 text-xs">
                          <span className="font-mono font-semibold uppercase tracking-wide text-primary">
                            {level.name}
                          </span>
                          <span className="font-medium">{level.label}</span>
                          <span className="text-muted-foreground">
                            {level.title}
                          </span>
                        </header>
                        {level.document ? (
                          <OkfMetadataPanel
                            metadata={level.document.metadata}
                            onNavigate={onNavigate}
                            rawFrontmatter={level.document.rawFrontmatter}
                          />
                        ) : null}
                        {level.content.trim() ? (
                          <article className="prose prose-sm max-w-none break-words rounded-md border bg-muted/20 p-3 dark:prose-invert dark:prose-pre:bg-muted-foreground/20">
                            <ReactMarkdown
                              remarkPlugins={[remarkGfm]}
                              urlTransform={transformDirectoryMarkdownUrl}
                              components={{
                                ...markdownComponents,
                                a: ({ href, children }) => (
                                  <DirectoryMarkdownLink
                                    href={href}
                                    fileUri={file.uri}
                                    onNavigate={onNavigate}
                                  >
                                    {children}
                                  </DirectoryMarkdownLink>
                                ),
                              }}
                            >
                              {level.content}
                            </ReactMarkdown>
                          </article>
                        ) : null}
                      </section>
                    ))}
                  </div>
                )}
              </div>
            ) : null}

            {preview?.fileType === 'image' ? (
              imageLoading ? (
                <div className="text-sm text-muted-foreground">
                  {t('filePreview.imageLoading')}
                </div>
              ) : imageUrl ? (
                <img
                  src={imageUrl}
                  alt={file.name}
                  className="max-h-[70vh] max-w-full rounded-md object-contain outline outline-1 -outline-offset-1 outline-black/10 dark:outline-white/10"
                />
              ) : imageSrc ? (
                <div className="space-y-3">
                  <img
                    src={imageSrc}
                    alt={file.name}
                    className="max-h-[70vh] max-w-full rounded-md object-contain outline outline-1 -outline-offset-1 outline-black/10 dark:outline-white/10"
                    onError={() => setImageError('direct img failed')}
                  />
                  {imageError ? (
                    <div className="text-xs text-muted-foreground">
                      {imageError}
                    </div>
                  ) : null}
                </div>
              ) : (
                <div className="space-y-1 text-sm text-muted-foreground">
                  <div>{t('filePreview.imageFailed')}</div>
                  {imageError ? (
                    <div className="text-xs">{imageError}</div>
                  ) : null}
                </div>
              )
            ) : null}

            {previewLoading && preview?.fileType !== 'image' ? (
              <div className="text-sm text-muted-foreground">
                {t('filePreview.loadingContent')}
              </div>
            ) : null}

            {!previewLoading &&
            preview &&
            !file.isDir &&
            preview.fileType !== 'image' &&
            !hasPreviewContent ? (
              <div className="space-y-3 text-sm text-muted-foreground">
                <div>
                  {preview.reason === 'binary'
                    ? t('filePreview.unsupportedBinary')
                    : t('filePreview.largeFileSkipped')}
                </div>
                {previewQuery.canLoadContent && isJsonFile ? (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={previewQuery.isFetching}
                    onClick={() => void previewQuery.refetch()}
                  >
                    {previewQuery.isFetching ? (
                      <Loader2 className="mr-2 size-4 animate-spin" />
                    ) : null}
                    {t('filePreview.loadFile')}
                  </Button>
                ) : null}
              </div>
            ) : null}

            {!previewLoading &&
            preview?.fileType === 'markdown' &&
            hasPreviewContent &&
            markdownMode === 'preview' ? (
              <div className="space-y-5">
                {okfDocument ? (
                  <OkfMetadataPanel
                    metadata={okfDocument.metadata}
                    onNavigate={onNavigate}
                    rawFrontmatter={okfDocument.rawFrontmatter}
                  />
                ) : null}
                <article className="prose prose-sm max-w-none break-words dark:prose-invert dark:prose-pre:bg-muted-foreground/20">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    rehypePlugins={[rehypeRaw, rehypeSanitize]}
                    urlTransform={(url) => url}
                    components={{
                      ...markdownComponents,
                      img: ({ src, alt }) => (
                        <MarkdownImage
                          src={src ? String(src) : undefined}
                          alt={alt}
                          fileUri={file.uri}
                        />
                      ),
                      a: ({ href, children }) => (
                        <MarkdownLink
                          href={href}
                          fileUri={file.uri}
                          onNavigate={onNavigate}
                        >
                          {children}
                        </MarkdownLink>
                      ),
                    }}
                  >
                    {okfDocument
                      ? okfDocument.body || emptyFileText
                      : displayContent || emptyFileText}
                  </ReactMarkdown>
                </article>
              </div>
            ) : null}

            {!previewLoading &&
            preview?.fileType === 'markdown' &&
            hasPreviewContent &&
            markdownMode === 'source' ? (
              <pre className="overflow-auto rounded-md border bg-muted/20 p-3 text-xs leading-6">
                <code
                  className="hljs block"
                  dangerouslySetInnerHTML={{
                    __html:
                      highlightedCodeHtml ||
                      escapeHtml(displayContent || emptyFileText),
                  }}
                />
              </pre>
            ) : null}

            {!previewLoading &&
            preview?.fileType === 'code' &&
            hasPreviewContent &&
            !isJsonFile ? (
              <pre className="overflow-auto rounded-md border bg-muted/20 p-3 text-xs leading-6">
                <code
                  className="hljs block"
                  dangerouslySetInnerHTML={{
                    __html:
                      highlightedCodeHtml ||
                      escapeHtml(displayContent || emptyFileText),
                  }}
                />
              </pre>
            ) : null}

            {!previewLoading &&
            preview?.fileType === 'jsonl' &&
            hasPreviewContent ? (
              <JsonlPreview content={displayContent || ''} />
            ) : null}

            {!previewLoading &&
            preview &&
            preview.fileType !== 'image' &&
            preview.fileType !== 'markdown' &&
            preview.fileType !== 'jsonl' &&
            preview.fileType !== 'code' &&
            hasPreviewContent ? (
              <pre className="whitespace-pre-wrap break-words text-xs leading-6">
                {displayContent || emptyFileText}
              </pre>
            ) : null}
          </div>
        </ScrollArea>
      )}
    </div>
  )
}
